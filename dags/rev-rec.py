from datetime import datetime, timezone, timedelta
import json
import os
import requests
from urllib.parse import urlencode

from airflow.decorators import dag, task_group
from airflow.providers.http.operators.http import SimpleHttpOperator
from airflow.providers.http.sensors.http import HttpSensor
from airflow.operators.python import ShortCircuitOperator, PythonOperator
from airflow.providers.google.cloud.hooks.gcs import GCSHook
from airflow.providers.google.cloud.transfers.gcs_to_bigquery import GCSToBigQueryOperator
from airflow.utils.context import Context
from airflow.providers.slack.hooks.slack_webhook import SlackWebhookHook
from airflow_provider_hex.operators.hex import HexRunProjectOperator
from airflow.models import DagRun
from cosmos import DbtTaskGroup, RenderConfig, ProjectConfig, ProfileConfig, ExecutionConfig
from cosmos.profiles import GoogleCloudServiceAccountDictProfileMapping

DAG_ID='revenue_recognition'
GCP_PROJECT = 'stripe-rev-rec'
REPORT_TYPE = "revenue_recognition.debit_credit_summary.1"
GCS_BUCKET_NAME = 'airflow-summit-stripe'
DBT_PROJECT_PATH = f"{os.environ['AIRFLOW_HOME']}/dags/dbt"
DBT_EXECUTABLE_PATH = f"{os.environ['AIRFLOW_HOME']}/dbt_venv/bin/dbt"
HEX_PROJECT_ID="68931401-2767-4c16-bec6-08d63d7fd3b6"
ACCOUNTING_SLACK_CHANNEL="C07LJDXMBHS"

cosmos_profile_config = ProfileConfig(
    profile_name="default",
    target_name="dev",
    profile_mapping=GoogleCloudServiceAccountDictProfileMapping(
        conn_id='google_cloud_default',
        profile_args={
            "dataset": "stripe"
        }
    ),
)

@dag(
    start_date=datetime(2024, 1, 1),
    schedule="@hourly",
    catchup=False,
    tags=["stripe"],
    user_defined_filters={'fromjson': lambda s: json.loads(s)},
    dag_id=DAG_ID
)
def revenue_recognition():
    """
    Fetches revenue recognition data from Stripe using their API
    and loads them into the data warehouse
    """
    @task_group(group_id='extract')
    def extract():
        get_rev_rec_report_metadata = SimpleHttpOperator(
            task_id='get_rev_rec_report_metadata',
            method='GET',
            http_conn_id='stripe_api',
            endpoint=f'/v1/reporting/report_types/{REPORT_TYPE}',
            headers={"Authorization": f"Bearer {os.getenv('STRIPE_API_KEY')}"}
        )

        def check_updated_property(ti, **kwargs):
            response = ti.xcom_pull(task_ids='extract.get_rev_rec_report_metadata')
            data = json.loads(response)
            updated_timestamp = data.get("updated")

            updated_datetime = datetime.fromtimestamp(updated_timestamp, tz=timezone.utc)
            dag_start_time = kwargs['dag_run'].logical_date

            return updated_datetime >= dag_start_time

        check_for_updated_report = ShortCircuitOperator(
            task_id='check_for_updated_report',
            python_callable=check_updated_property,
            provide_context=True
        )

        create_report_run = SimpleHttpOperator(
            task_id='create_report_run',
            method='POST',
            http_conn_id='stripe_api',
            endpoint='/v1/reporting/report_runs',
            data=urlencode({
                "report_type": f"{REPORT_TYPE}"
            }),
            headers={
                "Authorization": f"Bearer {os.getenv('STRIPE_API_KEY')}", 
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )

        def sensor_response_check(response, ti):
            data = response.json()
            print('data', data)

            status = data.get('status')
            if status == 'succeeded':
                url = data.get('result', {}).get('url')
                ti.xcom_push(key='report_url', value=url)

            return status != 'pending'

        check_for_finished_report = HttpSensor(
            task_id="check_for_finished_report", 
            response_check=sensor_response_check,
            method='GET',
            http_conn_id='stripe_api',
            endpoint="/v1/reporting/report_runs/{{ (ti.xcom_pull(task_ids='extract.create_report_run') | fromjson)['id'] }}",
            headers={"Authorization": f"Bearer {os.getenv('STRIPE_API_KEY')}"},
            poke_interval=30,  # Wait 30 seconds between each poke
            timeout=150,  # Timeout after 150 seconds (5 * 30 seconds)
            mode='poke'
        )

        get_rev_rec_report_metadata >> check_for_updated_report >> create_report_run >> check_for_finished_report


    @task_group(group_id='load')
    def load():    
        def download_and_upload_to_gcs(ti, **kwargs):
            # Get the report run object from the previous task
            csv_url = ti.xcom_pull(task_ids='extract.check_for_finished_report', key='report_url')
            print('CSV url', csv_url)

            # Download the CSV file
            response = requests.get(csv_url, headers={"Authorization": f"Bearer {os.getenv('STRIPE_API_KEY')}"})
            response.raise_for_status()  # Ensure the request was successful
            csv_data = response.content

            # Use GCSHook for authentication and upload
            gcs_hook = GCSHook(gcp_conn_id='google_cloud_default')
            object_name = f"revenue_recognition/{kwargs['execution_date'].strftime('%Y-%m-%d_%H-%M-%S')}.csv"

            # Upload to Google Cloud Storage
            gcs_hook.upload(
                bucket_name=GCS_BUCKET_NAME, 
                object_name=object_name, 
                data=csv_data, 
                mime_type='text/csv')

            ti.xcom_push(key='storage_object_name', value=object_name)

            print(f"File uploaded to {GCS_BUCKET_NAME}/{object_name}")

        upload_csv_to_gcs = PythonOperator(
            task_id='upload_csv_to_gcs',
            python_callable=download_and_upload_to_gcs,
            provide_context=True
        )

        load_to_bigquery = GCSToBigQueryOperator(
            task_id='load_to_bigquery',
            bucket=GCS_BUCKET_NAME,
            source_objects=[
                "{{ ti.xcom_pull(task_ids='load.upload_csv_to_gcs', key='storage_object_name') }}"],
            destination_project_dataset_table=f'{GCP_PROJECT}.airflow_stripe.revenue_recognition',
            write_disposition='WRITE_TRUNCATE',
            source_format='CSV',
            autodetect=True
        )

        upload_csv_to_gcs >> load_to_bigquery


    def warning_callback_func(context: Context):
        tests = context.get("test_names")
        results = context.get("test_results")

        warning_msgs = ""
        for test, result in zip(tests, results):
            warning_msg = f"""
            *Test*: {test}
            *Result*: {result}
            """
            warning_msgs += warning_msg

        if warning_msgs:
            slack_msg = f"""
            :large_yellow_circle: Airflow-DBT task with WARN.
            *Task*: {context.get('task_instance').task_id}
            *Dag*: {context.get('task_instance').dag_id}
            *Execution Time*: {context.get('execution_date')}
            *Log Url*: {context.get('task_instance').log_url}
            {warning_msgs}
            """

            slack_hook = SlackWebhookHook(
                slack_webhook_conn_id="airflow-alerts-slack-channel",
            )
            slack_hook.send(text=slack_msg)

    transform = DbtTaskGroup(
        group_id="transform",
        project_config=ProjectConfig(DBT_PROJECT_PATH),
        profile_config=cosmos_profile_config,
        execution_config = ExecutionConfig(
            dbt_executable_path=DBT_EXECUTABLE_PATH,
        ),
        default_args={"retries": 0},
        operator_args={
            "install_deps": True
        },
        render_config=RenderConfig(
            select=["stg_stripe__revenue_recognition"]
        ),
        on_warning_callback=warning_callback_func
    )

    @task_group(group_id='report')
    def report():
        def monday_check(**kwargs):
            is_monday = kwargs['dag_run'].logical_date.weekday() == 0
            if not is_monday:
                return False

            dag_runs = DagRun.find(
                dag_id=DAG_ID,
                execution_start_date=(kwargs['dag_run'].logical_date - timedelta(days=1)),
                execution_end_date=kwargs['dag_run'].logical_date,
                state="success"
            )

            if len(dag_runs) == 0:
                return True
            dag_runs.sort(key=lambda x: x.execution_date, reverse=True)
            last_dag_run = dag_runs[0]
            return last_dag_run.execution_date.weekday() != 0

        should_post_report = ShortCircuitOperator(
            task_id='should_post_report',
            python_callable=monday_check,
            provide_context=True
        )

        report = HexRunProjectOperator(
            task_id="report",
            hex_conn_id="hex_default",
            project_id=HEX_PROJECT_ID,
            notifications=[
                {
                    "type": "SUCCESS",
                    "includeSuccessScreenshot": True,
                    "slackChannelIds": [ACCOUNTING_SLACK_CHANNEL]
                }
            ]
        )

        should_post_report >> report


    extract() >> load() >> transform >> report()

revenue_recognition()