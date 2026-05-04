from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from hanssem_macro_dashboard.config import (
    BIGQUERY_DATASET,
    BIGQUERY_LOCATION,
    BIGQUERY_PROJECT_ID,
    DB_PATH,
    INDICATORS,
    ROOT_DIR,
    VERIFICATION_STATE_PATH,
    load_verification_overrides,
)

SQL_DIR = ROOT_DIR / "sql" / "bigquery"


def bigquery_is_configured() -> bool:
    return bool(BIGQUERY_PROJECT_ID and BIGQUERY_DATASET)


def import_bigquery():
    try:
        from google.cloud import bigquery
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "google-cloud-bigquery is not installed. Install with `pip install .[bigquery]` or in Colab install google-cloud-bigquery."
        ) from exc
    return bigquery


def get_bq_client(project_id: str | None = None):
    bigquery = import_bigquery()
    return bigquery.Client(project=project_id or BIGQUERY_PROJECT_ID or None)


def table_id(table_name: str, project_id: str | None = None, dataset: str | None = None) -> str:
    return f"{project_id or BIGQUERY_PROJECT_ID}.{dataset or BIGQUERY_DATASET}.{table_name}"


def render_sql(template_name: str, project_id: str | None = None, dataset: str | None = None) -> str:
    content = (SQL_DIR / template_name).read_text(encoding="utf-8")
    return (
        content.replace("{{project}}", project_id or BIGQUERY_PROJECT_ID)
        .replace("{{dataset}}", dataset or BIGQUERY_DATASET)
        .replace("{{location}}", BIGQUERY_LOCATION)
    )


def execute_sql(client, sql: str) -> None:
    client.query(sql, location=BIGQUERY_LOCATION).result()


def initialize_bigquery_datamart(project_id: str | None = None, dataset: str | None = None) -> None:
    client = get_bq_client(project_id=project_id)
    execute_sql(client, render_sql("create_dataset.sql", project_id=project_id, dataset=dataset))
    execute_sql(client, render_sql("create_tables.sql", project_id=project_id, dataset=dataset))
    execute_sql(client, render_sql("vw_macro_latest.sql", project_id=project_id, dataset=dataset))
    execute_sql(client, render_sql("vw_hanssem_macro_hmi.sql", project_id=project_id, dataset=dataset))
    execute_sql(client, render_sql("vw_macro_sales_join.sql", project_id=project_id, dataset=dataset))
    execute_sql(client, render_sql("vw_etl_status_summary.sql", project_id=project_id, dataset=dataset))
    execute_sql(client, render_sql("vw_source_health.sql", project_id=project_id, dataset=dataset))


def load_to_bq(df: pd.DataFrame, destination: str, project_id: str | None = None, write_disposition: str = "WRITE_TRUNCATE") -> None:
    if df.empty:
        return
    client = get_bq_client(project_id=project_id)
    bigquery = import_bigquery()
    job_config = bigquery.LoadJobConfig(write_disposition=write_disposition)
    client.load_table_from_dataframe(df, destination, job_config=job_config).result()


def sync_bigquery_tables(project_id: str | None = None, dataset: str | None = None, db_path: Path = DB_PATH) -> dict[str, int]:
    if not bigquery_is_configured() and not project_id:
        return {}

    target_project = project_id or BIGQUERY_PROJECT_ID
    target_dataset = dataset or BIGQUERY_DATASET
    initialize_bigquery_datamart(project_id=target_project, dataset=target_dataset)

    observations = build_macro_indicator_dataframe(db_path=db_path)
    verification = build_source_verification_dataframe()
    history = build_etl_run_history_dataframe(db_path=db_path)
    sales = build_hanssem_sales_empty_dataframe()

    load_to_bq(observations, table_id("macro_indicator_observations", target_project, target_dataset), target_project)
    load_to_bq(verification, table_id("source_verification", target_project, target_dataset), target_project)
    load_to_bq(history, table_id("etl_run_history", target_project, target_dataset), target_project)
    if not sales.empty:
        load_to_bq(sales, table_id("hanssem_sales_monthly", target_project, target_dataset), target_project)

    return {
        "macro_indicator_observations": len(observations),
        "source_verification": len(verification),
        "etl_run_history": len(history),
        "hanssem_sales_monthly": len(sales),
    }


def observation_records_to_bq_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=["date", "indicator_id", "region", "value", "raw_value", "unit", "source"])
    rows: list[dict[str, Any]] = []
    for record in records:
        meta = record.get("meta_json", {}) or {}
        rows.append(
            {
                "date": pd.to_datetime(record.get("observation_date"), errors="coerce").date() if record.get("observation_date") else pd.NaT,
                "indicator_id": record.get("indicator_code"),
                "region": record.get("region_name"),
                "value": record.get("value"),
                "raw_value": meta.get("raw_value", meta.get("cumulative_value")),
                "unit": record.get("unit"),
                "source": record.get("source"),
            }
        )
    return pd.DataFrame(rows)


def build_macro_indicator_dataframe(db_path: Path = DB_PATH) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT
                observation_date AS date,
                indicator_code AS indicator_id,
                region_name AS region,
                value,
                unit,
                source,
                meta_json,
                loaded_at AS created_at
            FROM indicator_observations
            ORDER BY observation_date, indicator_code
            """,
            conn,
        )
    if df.empty:
        return pd.DataFrame(columns=["date", "indicator_id", "region", "value", "raw_value", "unit", "source", "created_at"])

    raw_values: list[float | None] = []
    for payload in df["meta_json"].fillna("{}"):
        raw_value = None
        try:
            meta = json.loads(payload)
            raw_value = meta.get("raw_value", meta.get("cumulative_value"))
        except json.JSONDecodeError:
            raw_value = None
        raw_values.append(raw_value)
    df["raw_value"] = raw_values
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    return df[["date", "indicator_id", "region", "value", "raw_value", "unit", "source", "created_at"]]


def build_source_verification_dataframe() -> pd.DataFrame:
    overrides = load_verification_overrides()
    rows: list[dict[str, Any]] = []
    for indicator_id, definition in INDICATORS.items():
        override = overrides.get(indicator_id, {})
        rows.append(
            {
                "indicator_id": indicator_id,
                "source_name": definition.source_detail.source_name,
                "provider": definition.source_detail.provider,
                "verification_status": override.get("verification_status", definition.source_detail.verification_status),
                "error_type": override.get("error_type", definition.source_detail.error_type),
                "row_count": int(override.get("rows", definition.source_detail.rows) or 0),
                "last_verified_at": _to_timestamp(override.get("last_verified_at", definition.source_detail.last_verified_at)),
                "message": override.get("verification_message", definition.source_detail.verification_message),
            }
        )
        if definition.fallback_source is not None:
            rows.append(
                {
                    "indicator_id": f"{indicator_id}__fallback",
                    "source_name": definition.fallback_source.source_name,
                    "provider": definition.fallback_source.provider,
                    "verification_status": override.get(
                        "fallback_verification_status",
                        definition.fallback_source.verification_status,
                    ),
                    "error_type": override.get("fallback_error_type", definition.fallback_source.error_type),
                    "row_count": int(override.get("fallback_rows", definition.fallback_source.rows) or 0),
                    "last_verified_at": _to_timestamp(
                        override.get("fallback_last_verified_at", definition.fallback_source.last_verified_at)
                    ),
                    "message": override.get(
                        "fallback_verification_message",
                        definition.fallback_source.verification_message,
                    ),
                }
            )
    return pd.DataFrame(rows)


def build_etl_run_history_dataframe(db_path: Path = DB_PATH) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT
                run_id,
                indicator_id,
                collect_status,
                stage_status,
                rows_loaded,
                latest_period,
                run_started_at AS started_at,
                run_finished_at AS finished_at,
                message
            FROM etl_run_history
            ORDER BY id
            """,
            conn,
        )
    if df.empty:
        return pd.DataFrame(
            columns=[
                "run_id",
                "indicator_id",
                "collect_status",
                "stage_status",
                "rows_loaded",
                "latest_period",
                "started_at",
                "finished_at",
                "message",
            ]
        )
    df["latest_period"] = pd.to_datetime(df["latest_period"], errors="coerce").dt.date
    df["started_at"] = pd.to_datetime(df["started_at"], errors="coerce")
    df["finished_at"] = pd.to_datetime(df["finished_at"], errors="coerce")
    return df


def build_hanssem_sales_empty_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "sales", "category", "region"])


def _to_timestamp(value: Any):
    if not value:
        return pd.NaT
    return pd.to_datetime(value, errors="coerce")
