from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from hanssem_macro_dashboard.bq import (
    BIGQUERY_LOCATION,
    build_source_verification_dataframe,
    get_bq_client,
    import_bigquery,
    load_to_bq,
    render_sql,
    table_id,
)


def load_staging(
    df: pd.DataFrame,
    run_id: str,
    project_id: str | None = None,
    dataset: str | None = None,
) -> int:
    if df.empty:
        return 0
    staging = df.copy()
    staging["run_id"] = run_id
    staging["loaded_at"] = pd.Timestamp.utcnow()
    required_columns = ["run_id", "indicator_id", "date", "region", "value", "raw_value", "unit", "source", "loaded_at"]
    for column in required_columns:
        if column not in staging.columns:
            staging[column] = pd.NA
    staging["date"] = pd.to_datetime(staging["date"], errors="coerce").dt.date
    destination = table_id("staging_macro_indicator_observations", project_id=project_id, dataset=dataset)
    load_to_bq(staging[required_columns], destination, project_id=project_id, write_disposition="WRITE_APPEND")
    return len(staging)


def validate_staging(
    run_id: str,
    project_id: str | None = None,
    dataset: str | None = None,
    allowed_lag_months: dict[str, int] | None = None,
) -> pd.DataFrame:
    client = get_bq_client(project_id=project_id)
    bigquery = import_bigquery()
    sql = render_sql("validate_staging.sql", project_id=project_id, dataset=dataset)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    )
    query_job = client.query(sql, job_config=job_config, location=BIGQUERY_LOCATION)
    rows = [dict(row.items()) for row in query_job.result()]
    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(
            columns=[
                "indicator_id",
                "rows_staged",
                "null_rows",
                "duplicate_rows",
                "latest_period",
                "validation_status",
                "validation_message",
            ]
        )

    rules = allowed_lag_months or {}
    current_month = pd.Timestamp(date.today().replace(day=1))
    statuses: list[str] = []
    messages: list[str] = []
    for row in result.itertuples(index=False):
        indicator_id = str(row.indicator_id)
        latest_period = pd.to_datetime(row.latest_period, errors="coerce")
        minimum_month = current_month - pd.DateOffset(months=rules.get(indicator_id, 3))
        if int(row.rows_staged or 0) <= 0:
            statuses.append("failed_staging")
            messages.append("rows_staged must be greater than zero")
        elif int(row.null_rows or 0) > 0:
            statuses.append("failed_staging")
            messages.append("date/value/region contains null")
        elif int(row.duplicate_rows or 0) > 0:
            statuses.append("failed_staging")
            messages.append(f"duplicate rows detected: {int(row.duplicate_rows)}")
        elif pd.isna(latest_period):
            statuses.append("failed_staging")
            messages.append("latest_period is null")
        elif latest_period < minimum_month or latest_period > current_month:
            statuses.append("failed_staging")
            messages.append(f"latest_period out of expected range: {latest_period.date()}")
        else:
            statuses.append("validated")
            messages.append("staging validation passed")

    validated = result.copy()
    validated["validation_status"] = statuses
    validated["validation_message"] = messages
    return validated


def promote_to_production(
    run_id: str,
    project_id: str | None = None,
    dataset: str | None = None,
) -> None:
    client = get_bq_client(project_id=project_id)
    bigquery = import_bigquery()
    sql = render_sql("merge_staging_to_production.sql", project_id=project_id, dataset=dataset)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    )
    client.query(sql, job_config=job_config, location=BIGQUERY_LOCATION).result()


def write_etl_run_history(
    rows: list[dict[str, Any]] | pd.DataFrame,
    project_id: str | None = None,
    dataset: str | None = None,
) -> int:
    history = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if history.empty:
        return 0
    if "latest_period" in history.columns:
        history["latest_period"] = pd.to_datetime(history["latest_period"], errors="coerce").dt.date
    if "started_at" in history.columns:
        history["started_at"] = pd.to_datetime(history["started_at"], errors="coerce")
    if "finished_at" in history.columns:
        history["finished_at"] = pd.to_datetime(history["finished_at"], errors="coerce")
    destination = table_id("etl_run_history", project_id=project_id, dataset=dataset)
    load_to_bq(history, destination, project_id=project_id, write_disposition="WRITE_APPEND")
    return len(history)


def clear_staging_run(run_id: str, project_id: str | None = None, dataset: str | None = None) -> None:
    client = get_bq_client(project_id=project_id)
    bigquery = import_bigquery()
    sql = f"DELETE FROM `{table_id('staging_macro_indicator_observations', project_id=project_id, dataset=dataset)}` WHERE run_id = @run_id"
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter('run_id', 'STRING', run_id)]
    )
    client.query(sql, job_config=job_config, location=BIGQUERY_LOCATION).result()


def write_source_verification(
    project_id: str | None = None,
    dataset: str | None = None,
) -> int:
    verification = build_source_verification_dataframe()
    if verification.empty:
        return 0
    destination = table_id("source_verification", project_id=project_id, dataset=dataset)
    load_to_bq(verification, destination, project_id=project_id, write_disposition="WRITE_TRUNCATE")
    return len(verification)
