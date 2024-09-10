with source as (
    select * from {{ source('airflow_stripe', 'revenue_recognition')}}
),

renamed as (

    select
        coalesce(open_accounting_period, accounting_period) accounting_period,
        currency,
        debit,
        credit,
        amount
    from source

)

select * from renamed