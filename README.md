Overview
========

This repo contains the full Airflow project for the [From Tech Specs to Business Impact: How to Design A Truly End-to-End Airflow Project](https://airflowsummit.org/sessions/2024/from-tech-specs-to-business-impact-how-to-design-a-truly-end-to-end-airflow-project/) at Airflow Summit 2024.

Project Contents
================

At a higher level, this Astro project contains the following files and folders:

- dags: This folder contains the Python files for your Airflow DAGs.
- Dockerfile: This file contains a versioned Astro Runtime Docker image that provides a differentiated Airflow experience. If you want to execute other commands or overrides at runtime, specify them here.
- packages.txt: Install OS-level packages needed for your project by adding them to this file. It is empty by default.
- requirements.txt: Install Python packages needed for your project by adding them to this file. It is empty by default.
- [airflow_settings.example.yaml](airflow_settings.example.yaml): This is an example of what a local-only airflow_settings.yaml file would look like for this project. Use this file to specify Airflow Connections, Variables, and Pools instead of entering them in the Airflow UI as you develop DAGs in this project.

# Important Files and Objects By Presentation Section

## Ingest
The [extract](https://github.com/TaylorFacen/revenue-recognition/blob/3e5e9e053822df72ae4dfe49555b70e847b36a77/dags/rev-rec.py#L54-L116) task group is used to support fetching data from the Stripe API, loading the results to Google Cloud Storage, and transfering the data to Google BigQuery.

First, `SimpleHttpOperator` is used to fetch basic details about the revenue recognition report, including when it was last updated in Stripe. `ShortCircuitOperator`is used to only proceed if the report has been updated recently. If the report has been updated recently, the HTTP operator is used again to create a report run. Finally, an `HttpSensor` polls the report run endpoint to wait for Stripe to complete the report before proceeding. Within this final task, if the report is complete, the report url is pushed to xcom to use later on in the DAG.

The [load](https://github.com/TaylorFacen/revenue-recognition/blob/3e5e9e053822df72ae4dfe49555b70e847b36a77/dags/rev-rec.py#L120-L163) task group loads the Stripe data into the data warehouse by:
1. Uploading the csv contents of the report data url generated in the previous task to Google Cloud Storage using a `PythonOperator`. 
2. Transferring the data to Google Big Query via the `GCSToBigQueryOperator` so that the data is accessable to query.

## Transform and Test
Thanks to Cosmos, it's possible to run entire dbt projects from within a DAG. In this case, we have a simple [dbt project](dags/dbt) that cleans up one of the columns in the raw data and materializes the Stripe data into an end-user-accessible `stripe.revenue_recognition` table. 

The DBT project also includes [basic tests](https://github.com/TaylorFacen/revenue-recognition/blob/3e5e9e053822df72ae4dfe49555b70e847b36a77/dags/dbt/models/staging/stripe/schema.yml#L9-L21) like null checks as well as [a test](https://github.com/TaylorFacen/revenue-recognition/blob/3e5e9e053822df72ae4dfe49555b70e847b36a77/dags/dbt/models/staging/stripe/schema.yml#L23-L26) to see if any accounting period has missing data. 

The code used in the recognitzed revenue DAG that relates to running DBT and sending a Slack alert when there's a warning within the test can be found here `https://github.com/TaylorFacen/revenue-recognition/blob/3e5e9e053822df72ae4dfe49555b70e847b36a77/dags/rev-rec.py#L166-L208`

## Report
Lastly, the [report](https://github.com/TaylorFacen/revenue-recognition/blob/3e5e9e053822df72ae4dfe49555b70e847b36a77/dags/rev-rec.py#L211-L249) task group handles running a [report in Hex](https://app.hex.tech/b08aae54-69be-490e-a260-67071718613f/app/68931401-2767-4c16-bec6-08d63d7fd3b6/latest) by using the `HexRunProjectOperator`. This operator also handles sending a message with a screenshot of the report to a specified Slack channel. Because this shouldn't run on every run, the task group uses a `ShortCircuitOperator` to check to see if the current DAG run is the first DAG run on Monday.

if the current run is 


Deploy Your Project Locally
===========================

1. Start Airflow on your local machine by running 'astro dev start'.

This command will spin up 4 Docker containers on your machine, each for a different Airflow component:

- Postgres: Airflow's Metadata Database
- Webserver: The Airflow component responsible for rendering the Airflow UI
- Scheduler: The Airflow component responsible for monitoring and triggering tasks
- Triggerer: The Airflow component responsible for triggering deferred tasks

2. Verify that all 4 Docker containers were created by running 'docker ps'.

Note: Running 'astro dev start' will start your project with the Airflow Webserver exposed at port 8080 and Postgres exposed at port 5432. If you already have either of those ports allocated, you can either [stop your existing Docker containers or change the port](https://www.astronomer.io/docs/astro/cli/troubleshoot-locally#ports-are-not-available-for-my-local-airflow-webserver).

3. Access the Airflow UI for your local Airflow project. To do so, go to http://localhost:8080/ and log in with 'admin' for both your Username and Password.

You should also be able to access your Postgres Database at 'localhost:5432/postgres'.

Deploy Your Project to Astronomer
=================================

If you have an Astronomer account, pushing code to a Deployment on Astronomer is simple. For deploying instructions, refer to Astronomer documentation: https://www.astronomer.io/docs/astro/deploy-code/
