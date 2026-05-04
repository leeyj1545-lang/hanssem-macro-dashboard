from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
import sys

import pandas as pd

from hanssem_macro_dashboard.bq import bigquery_is_configured, observation_records_to_bq_dataframe, sync_bigquery_tables
from hanssem_macro_dashboard.bq_repository import (
    clear_staging_run as clear_bq_staging_run,
    load_staging as bq_load_staging,
    promote_to_production as bq_promote_to_production,
    validate_staging as bq_validate_staging,
    write_etl_run_history as bq_write_etl_run_history,
    write_source_verification as bq_write_source_verification,
)
from hanssem_macro_dashboard.config import BIGQUERY_DATASET, BIGQUERY_PROJECT_ID, DB_PATH, INDICATORS, ROOT_DIR, load_verification_overrides
from hanssem_macro_dashboard.db import (
    clear_staging_batch,
    initialize_database,
    insert_etl_run_history,
    load_staged_indicator,
    log_etl_run,
    promote_staged_indicator,
    stage_observations,
    upsert_observations,
)
from hanssem_macro_dashboard.demo import build_demo_rows
from hanssem_macro_dashboard.sources.base import SourceError
from hanssem_macro_dashboard.sources.ecos import EcosSource
from hanssem_macro_dashboard.sources.kosis import KosisSource
from hanssem_macro_dashboard.sources.molit_stat_file import MolitStatFileSource
from hanssem_macro_dashboard.sources.molit import MolitTransactionSource
from hanssem_macro_dashboard.sources.rone import RoneSource

LOG_DIR = ROOT_DIR / "logs"

KOSIS_SERIES_CONFIG = {
    "completion_volume": {
        "field_map": {"period": "PRD_DE", "value": "DT"},
    },
    "housing_permits": {
        "field_map": {"period": "PRD_DE", "value": "DT"},
    },
}


def run_demo() -> int:
    initialize_database(DB_PATH)
    inserted = upsert_observations(DB_PATH, build_demo_rows())
    log_etl_run(DB_PATH, run_mode="demo", status="success", message=f"Inserted {inserted} demo rows.")
    sync_message = maybe_sync_bigquery()
    if sync_message:
        print(sync_message)
    print(f"Inserted {inserted} demo rows into {DB_PATH}")
    return 0


def run_live(selected_indicators: list[str] | None = None) -> int:
    initialize_database(DB_PATH)
    effective_status = load_effective_statuses(selected_indicators=selected_indicators)
    log_path = build_log_path()
    started_at = datetime.now()
    batch_id = started_at.strftime("%Y%m%d%H%M%S")
    summary_rows: list[dict] = []
    had_failures = False

    try:
        rows_by_indicator, had_failures = collect_live_rows(
            effective_status=effective_status,
            summary_rows=summary_rows,
            selected_indicators=selected_indicators,
        )
        staged_result = stage_and_promote(batch_id=batch_id, rows_by_indicator=rows_by_indicator, summary_rows=summary_rows)
        had_failures = had_failures or staged_result
        for row in summary_rows:
            log_message(
                log_path,
                f"[{row['status'].upper()}] indicator_id={row['indicator_id']} source={row['source']} "
                f"stage_status={row['stage_status']} rows_inserted={row['rows_inserted']} "
                f"latest_period={row['latest_period']} message={row['message']}",
            )
    except SourceError as exc:
        log_message(log_path, f"[FAILED] {exc}")
        log_etl_run(DB_PATH, run_mode="live", status="failed", message=str(exc))
        print(f"ETL failed: {exc}", file=sys.stderr)
        print_summary(summary_rows)
        return 1
    except Exception as exc:  # pragma: no cover
        log_message(log_path, f"[FAILED] {exc!r}")
        log_etl_run(DB_PATH, run_mode="live", status="failed", message=repr(exc))
        print(f"ETL failed: {exc!r}", file=sys.stderr)
        print_summary(summary_rows)
        return 1
    finally:
        clear_staging_batch(DB_PATH, batch_id)

    inserted = sum(row["rows_inserted"] for row in summary_rows if row["stage_status"] == "production_loaded")
    final_status = "failed" if had_failures else "success"
    finished_at = datetime.now()
    log_etl_run(DB_PATH, run_mode="live", status=final_status, message=f"Inserted {inserted} rows.")
    insert_etl_run_history(DB_PATH, build_history_rows(batch_id, started_at, finished_at, summary_rows))
    log_message(log_path, f"[{final_status.upper()}] inserted_rows={inserted}")
    sync_message = maybe_sync_bigquery()
    if sync_message:
        log_message(log_path, sync_message)
    print_summary(summary_rows)
    if sync_message:
        print(sync_message)
    print(f"Inserted {inserted} rows into {DB_PATH}")
    print(f"Log written to {log_path}")
    return 1 if had_failures else 0


def run_bigquery(selected_indicators: list[str] | None = None) -> int:
    if not (BIGQUERY_PROJECT_ID and BIGQUERY_DATASET):
        print("BQ_PROJECT_ID/BQ_DATASET or BIGQUERY_PROJECT_ID/BIGQUERY_DATASET is missing.", file=sys.stderr)
        return 1

    effective_status = load_effective_statuses(selected_indicators=selected_indicators)
    log_path = build_log_path()
    started_at = datetime.now()
    run_id = started_at.strftime("%Y%m%d%H%M%S")
    summary_rows: list[dict] = []
    had_failures = False

    try:
        rows_by_indicator, had_failures = collect_live_rows(
            effective_status=effective_status,
            summary_rows=summary_rows,
            selected_indicators=selected_indicators,
        )
        bq_write_source_verification(project_id=BIGQUERY_PROJECT_ID, dataset=BIGQUERY_DATASET)
        had_failures = stage_and_promote_bigquery(
            run_id=run_id,
            rows_by_indicator=rows_by_indicator,
            summary_rows=summary_rows,
            had_failures=had_failures,
        )
        for row in summary_rows:
            log_message(
                log_path,
                f"[{row['status'].upper()}] indicator_id={row['indicator_id']} source={row['source']} "
                f"stage_status={row['stage_status']} rows_staged={row['rows_staged']} rows_loaded={row['rows_inserted']} "
                f"latest_period={row['latest_period']} message={row['message']}",
            )
    except Exception as exc:  # pragma: no cover
        log_message(log_path, f"[FAILED_BQ] {exc!r}")
        print(f"BigQuery ETL failed: {exc!r}", file=sys.stderr)
        print_summary(summary_rows, bq_mode=True)
        return 1
    finally:
        try:
            clear_bq_staging_run(run_id=run_id, project_id=BIGQUERY_PROJECT_ID, dataset=BIGQUERY_DATASET)
        except Exception:
            pass

    finished_at = datetime.now()
    bq_write_etl_run_history(
        build_bq_history_rows(run_id, started_at, finished_at, summary_rows),
        project_id=BIGQUERY_PROJECT_ID,
        dataset=BIGQUERY_DATASET,
    )
    print_summary(summary_rows, bq_mode=True)
    print(f"BigQuery run_id={run_id} dataset={BIGQUERY_PROJECT_ID}.{BIGQUERY_DATASET}")
    print(f"Log written to {log_path}")
    return 1 if had_failures else 0


def load_effective_statuses(selected_indicators: list[str] | None = None) -> dict[str, str]:
    overrides = load_verification_overrides()
    statuses: dict[str, str] = {}
    target_ids = selected_indicators or list(INDICATORS.keys())
    for indicator_id in target_ids:
        definition = INDICATORS[indicator_id]
        statuses[indicator_id] = overrides.get(indicator_id, {}).get(
            "verification_status",
            definition.source_detail.verification_status,
        )
    return statuses


def load_candidate_params() -> dict[str, dict[str, str]]:
    overrides = load_verification_overrides()
    return {
        indicator_id: value.get("candidate_params", {})
        for indicator_id, value in overrides.items()
        if isinstance(value, dict) and value.get("candidate_params")
    }


def collect_live_rows(
    effective_status: dict[str, str],
    summary_rows: list[dict],
    selected_indicators: list[str] | None = None,
) -> tuple[dict[str, list[dict]], bool]:
    overrides = load_verification_overrides()
    verified_by_source: dict[str, list[str]] = defaultdict(list)
    had_failures = False
    target_ids = selected_indicators or list(INDICATORS.keys())
    for indicator_id in target_ids:
        definition = INDICATORS[indicator_id]
        status = effective_status.get(indicator_id, "pending")
        if status == "verified":
            verified_by_source[definition.source].append(indicator_id)
            continue
        fallback_status = overrides.get(indicator_id, {}).get("fallback_verification_status", "")
        if fallback_status == "verified" and definition.fallback_source is not None:
            verified_by_source[definition.fallback_source.provider].append(indicator_id)
        else:
            summary_rows.append(
                build_summary_row(
                    indicator_id=indicator_id,
                    source=definition.source,
                    verification_status=status,
                    status="skip" if status != "demo_only" else "demo_only",
                    stage_status="skipped",
                    rows_collected=0,
                    rows_staged=0,
                    rows_inserted=0,
                    latest_period="",
                    message=f"verification_status={status}",
                    error_type="",
                )
            )

    rows_by_indicator: dict[str, list[dict]] = {}
    if verified_by_source.get("ECOS"):
        had_failures = fetch_with_summary(
            source_name="ECOS",
            indicator_ids=verified_by_source["ECOS"],
            fetcher=lambda: list(EcosSource(indicator_ids=verified_by_source["ECOS"]).fetch()),
            rows_by_indicator=rows_by_indicator,
            summary_rows=summary_rows,
            had_failures=had_failures,
        )
    if verified_by_source.get("MOLIT"):
        had_failures = fetch_with_summary(
            source_name="MOLIT",
            indicator_ids=verified_by_source["MOLIT"],
            fetcher=lambda: list(MolitTransactionSource(indicator_ids=verified_by_source["MOLIT"]).fetch()),
            rows_by_indicator=rows_by_indicator,
            summary_rows=summary_rows,
            had_failures=had_failures,
        )
    if verified_by_source.get("KOSIS"):
        had_failures = fetch_with_summary(
            source_name="KOSIS",
            indicator_ids=verified_by_source["KOSIS"],
            fetcher=lambda: fetch_kosis_verified_rows(verified_by_source["KOSIS"]),
            rows_by_indicator=rows_by_indicator,
            summary_rows=summary_rows,
            had_failures=had_failures,
        )
    if verified_by_source.get("MOLIT_STAT"):
        had_failures = fetch_with_summary(
            source_name="MOLIT_STAT_FILE",
            indicator_ids=verified_by_source["MOLIT_STAT"],
            fetcher=lambda: list(MolitStatFileSource(indicator_ids=verified_by_source["MOLIT_STAT"]).fetch()),
            rows_by_indicator=rows_by_indicator,
            summary_rows=summary_rows,
            had_failures=had_failures,
        )
    if verified_by_source.get("R-ONE"):
        had_failures = fetch_with_summary(
            source_name="R-ONE",
            indicator_ids=verified_by_source["R-ONE"],
            fetcher=lambda: fetch_rone_verified_rows(verified_by_source["R-ONE"]),
            rows_by_indicator=rows_by_indicator,
            summary_rows=summary_rows,
            had_failures=had_failures,
        )
    return rows_by_indicator, had_failures


def fetch_kosis_verified_rows(indicator_ids: list[str]) -> list[dict]:
    source = KosisSource()
    rows: list[dict] = []
    candidate_params_by_indicator = load_candidate_params()
    for indicator_id in indicator_ids:
        definition = INDICATORS[indicator_id]
        series = KOSIS_SERIES_CONFIG.get(indicator_id)
        if not series:
            continue
        candidate_params = candidate_params_by_indicator.get(indicator_id, {})
        rows.extend(
            source.fetch_series(
                indicator_code=definition.indicator_id,
                indicator_name=definition.name_kr,
                bucket=definition.category,
                tbl_id=definition.source_detail.table_id,
                org_id=definition.source_detail.org_id or "101",
                field_map=series["field_map"],
                frequency=definition.frequency,
                unit=definition.unit,
                itm_id=candidate_params.get("itmId", definition.source_detail.item_code),
                obj_l1=candidate_params.get("objL1", definition.source_detail.obj_l1),
                obj_l2=candidate_params.get("objL2", definition.source_detail.obj_l2),
                obj_l3=candidate_params.get("objL3", definition.source_detail.obj_l3),
            )
        )
    return rows


def fetch_rone_verified_rows(indicator_ids: list[str]) -> list[dict]:
    source = RoneSource()
    rows: list[dict] = []
    candidate_params_by_indicator = load_candidate_params()
    for indicator_id in indicator_ids:
        rows.extend(
            source.fetch_indicator_rows(
                indicator_id=indicator_id,
                candidate_params=candidate_params_by_indicator.get(indicator_id, {}),
            )
        )
    return rows


def fetch_with_summary(
    source_name: str,
    indicator_ids: list[str],
    fetcher,
    rows_by_indicator: dict[str, list[dict]],
    summary_rows: list[dict],
    had_failures: bool,
) -> bool:
    try:
        fetched = fetcher()
    except Exception as exc:
        for indicator_id in indicator_ids:
            summary_rows.append(
                build_summary_row(
                    indicator_id=indicator_id,
                    source=source_name,
                    verification_status="verified",
                    status="failed",
                    stage_status="not_staged",
                    rows_collected=0,
                    rows_staged=0,
                    rows_inserted=0,
                    latest_period="",
                    message=str(exc),
                    error_type=type(exc).__name__,
                )
            )
        return True

    split_rows(rows_by_indicator, fetched)
    extend_summary(summary_rows, fetched, source=source_name)
    return had_failures


def split_rows(rows_by_indicator: dict[str, list[dict]], rows: list[dict]) -> None:
    for row in rows:
        rows_by_indicator.setdefault(row["indicator_code"], []).append(row)


def extend_summary(summary_rows: list[dict], rows: list[dict], source: str) -> None:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["indicator_code"]].append(row)
    for indicator_id, indicator_rows in grouped.items():
        latest_period = max(row["observation_date"] for row in indicator_rows)
        summary_rows.append(
            build_summary_row(
                indicator_id=indicator_id,
                source=source,
                verification_status="verified",
                status="collected",
                stage_status="not_staged",
                rows_collected=len(indicator_rows),
                rows_staged=0,
                rows_inserted=0,
                latest_period=latest_period,
                message="verified source collected",
                error_type="",
            )
        )


def stage_and_promote(batch_id: str, rows_by_indicator: dict[str, list[dict]], summary_rows: list[dict]) -> bool:
    had_failures = False
    for summary_row in summary_rows:
        if summary_row["status"] != "collected":
            continue

        indicator_id = summary_row["indicator_id"]
        rows = rows_by_indicator.get(indicator_id, [])
        if not rows:
            summary_row["status"] = "failed_staging"
            summary_row["stage_status"] = "failed_staging"
            summary_row["error_type"] = "failed_staging"
            summary_row["message"] = "no rows available for staging"
            had_failures = True
            continue

        staged_rows = stage_observations(DB_PATH, batch_id=batch_id, records=rows)
        summary_row["stage_status"] = "staging_loaded"
        summary_row["rows_staged"] = staged_rows
        validation = validate_staged_indicator(batch_id=batch_id, indicator_id=indicator_id)
        if not validation["ok"]:
            summary_row["status"] = "failed_staging"
            summary_row["stage_status"] = "failed_staging"
            summary_row["rows_inserted"] = 0
            summary_row["error_type"] = "failed_staging"
            summary_row["message"] = validation["message"]
            had_failures = True
            continue

        promoted_rows = promote_staged_indicator(DB_PATH, batch_id=batch_id, indicator_code=indicator_id)
        summary_row["stage_status"] = "production_loaded"
        summary_row["rows_inserted"] = promoted_rows
        summary_row["latest_period"] = validation["latest_period"]
        summary_row["error_type"] = ""
        summary_row["message"] = f"staged_rows={staged_rows}; {validation['message']}"
    return had_failures


def validate_staged_indicator(batch_id: str, indicator_id: str) -> dict[str, str | bool]:
    staged = load_staged_indicator(DB_PATH, batch_id=batch_id, indicator_code=indicator_id)
    if staged.empty:
        return {"ok": False, "message": "staging rows must be greater than zero", "latest_period": ""}

    required_columns = ["observation_date", "value", "region_code"]
    if staged[required_columns].isnull().any().any():
        return {"ok": False, "message": "date/value/region contains null in staging", "latest_period": ""}

    duplicate_count = staged.duplicated(subset=["indicator_code", "observation_date", "region_code"]).sum()
    if duplicate_count > 0:
        return {"ok": False, "message": f"duplicate staging keys detected: {duplicate_count}", "latest_period": ""}

    latest_period = str(staged["observation_date"].max())
    latest_ts = pd.to_datetime(latest_period)
    current_month = pd.Timestamp(date.today().replace(day=1))
    source_name = str(staged["source"].iloc[0]) if "source" in staged.columns and not staged.empty else ""
    minimum_month = current_month - pd.DateOffset(months=3)
    if indicator_id in {"completion_volume", "housing_permits"} and source_name == "MOLIT_STAT_FILE":
        minimum_month = current_month - pd.DateOffset(months=24)
    if latest_ts < minimum_month or latest_ts > current_month:
        return {
            "ok": False,
            "message": f"latest_period out of expected range: {latest_period}",
            "latest_period": latest_period,
        }

    return {
        "ok": True,
        "message": "staging validation passed",
        "latest_period": latest_period,
    }


def build_summary_row(
    indicator_id: str,
    source: str,
    verification_status: str,
    status: str,
    stage_status: str,
    rows_collected: int,
    rows_staged: int,
    rows_inserted: int,
    latest_period: str,
    message: str,
    error_type: str,
) -> dict:
    return {
        "indicator_id": indicator_id,
        "source": source,
        "verification_status": verification_status,
        "status": status,
        "stage_status": stage_status,
        "rows_collected": rows_collected,
        "rows_staged": rows_staged,
        "rows_inserted": rows_inserted,
        "latest_period": latest_period,
        "message": message,
        "error_type": error_type,
    }


def build_log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"etl_run_{date.today().strftime('%Y%m%d')}.log"


def log_message(log_path: Path, message: str) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def print_summary(summary_rows: list[dict], bq_mode: bool = False) -> None:
    if not summary_rows:
        return
    columns = (
        ["indicator_id", "source", "status", "stage_status", "rows_staged", "rows_inserted", "latest_period", "message"]
        if bq_mode
        else ["indicator_id", "source", "status", "stage_status", "rows_inserted", "latest_period", "message"]
    )
    widths = {column: len(column) for column in columns}
    for row in summary_rows:
        for column in columns:
            widths[column] = min(max(widths[column], len(str(row[column]))), 100)

    header = " | ".join(column.ljust(widths[column]) for column in columns)
    divider = "-+-".join("-" * widths[column] for column in columns)
    print(header)
    print(divider)
    for row in summary_rows:
        print(" | ".join(str(row[column])[: widths[column]].ljust(widths[column]) for column in columns))


def build_history_rows(run_id: str, started_at: datetime, finished_at: datetime, summary_rows: list[dict]) -> list[dict]:
    history_rows: list[dict] = []
    for row in summary_rows:
        history_rows.append(
            {
                "run_id": run_id,
                "run_started_at": started_at.isoformat(timespec="seconds"),
                "run_finished_at": finished_at.isoformat(timespec="seconds"),
                "indicator_id": row["indicator_id"],
                "verification_status": row["verification_status"],
                "collect_status": row["status"],
                "stage_status": row["stage_status"],
                "rows_collected": row["rows_collected"],
                "rows_staged": row["rows_staged"],
                "rows_loaded": row["rows_inserted"],
                "latest_period": row["latest_period"],
                "message": row["message"],
                "error_type": row["error_type"],
            }
        )
    return history_rows


def maybe_sync_bigquery() -> str:
    if not bigquery_is_configured():
        return ""
    try:
        counts = sync_bigquery_tables()
    except Exception as exc:  # pragma: no cover
        return f"[BIGQUERY_SYNC_FAILED] {exc}"
    rendered = ", ".join(f"{table}={count}" for table, count in counts.items())
    return f"[BIGQUERY_SYNC] {rendered}"


def stage_and_promote_bigquery(
    run_id: str,
    rows_by_indicator: dict[str, list[dict]],
    summary_rows: list[dict],
    had_failures: bool,
) -> bool:
    allowed_lag_months = {"completion_volume": 24, "housing_permits": 24}
    for summary_row in summary_rows:
        if summary_row["status"] != "collected":
            continue
        indicator_id = summary_row["indicator_id"]
        rows = rows_by_indicator.get(indicator_id, [])
        if not rows:
            summary_row["status"] = "failed_staging"
            summary_row["stage_status"] = "failed_staging"
            summary_row["error_type"] = "failed_staging"
            summary_row["message"] = "no rows available for staging"
            had_failures = True
            continue

        frame = observation_records_to_bq_dataframe(rows)
        staged_rows = bq_load_staging(frame, run_id=run_id, project_id=BIGQUERY_PROJECT_ID, dataset=BIGQUERY_DATASET)
        summary_row["rows_staged"] = staged_rows
        summary_row["stage_status"] = "staging_loaded"

        validation = bq_validate_staging(
            run_id=run_id,
            project_id=BIGQUERY_PROJECT_ID,
            dataset=BIGQUERY_DATASET,
            allowed_lag_months=allowed_lag_months,
        )
        indicator_validation = validation[validation["indicator_id"] == indicator_id]
        if indicator_validation.empty:
            summary_row["status"] = "failed_staging"
            summary_row["stage_status"] = "failed_staging"
            summary_row["error_type"] = "failed_staging"
            summary_row["message"] = "validation result not found"
            had_failures = True
            continue

        row = indicator_validation.iloc[0]
        summary_row["latest_period"] = "" if pd.isna(row["latest_period"]) else str(pd.to_datetime(row["latest_period"]).date())
        if row["validation_status"] != "validated":
            summary_row["status"] = "failed_staging"
            summary_row["stage_status"] = "failed_staging"
            summary_row["error_type"] = "failed_staging"
            summary_row["message"] = str(row["validation_message"])
            had_failures = True
            continue

        bq_promote_to_production(run_id=run_id, project_id=BIGQUERY_PROJECT_ID, dataset=BIGQUERY_DATASET)
        summary_row["status"] = "collected"
        summary_row["stage_status"] = "production_loaded"
        summary_row["rows_inserted"] = int(row["rows_staged"])
        summary_row["message"] = str(row["validation_message"])
    return had_failures


def build_bq_history_rows(run_id: str, started_at: datetime, finished_at: datetime, summary_rows: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for row in summary_rows:
        rows.append(
            {
                "run_id": run_id,
                "indicator_id": row["indicator_id"],
                "collect_status": row["status"],
                "stage_status": row["stage_status"],
                "rows_loaded": row["rows_inserted"],
                "latest_period": row["latest_period"] or None,
                "started_at": started_at.isoformat(timespec="seconds"),
                "finished_at": finished_at.isoformat(timespec="seconds"),
                "message": row["message"],
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Hanssem macro ETL runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("demo", help="Load demo rows into SQLite")
    run_parser = subparsers.add_parser("run", help="Run live API ETL")
    run_parser.add_argument("--indicator", dest="indicators", action="append", choices=sorted(INDICATORS.keys()))
    run_bq_parser = subparsers.add_parser("run-bq", help="Run live ETL directly into BigQuery staging/production")
    run_bq_parser.add_argument("--indicator", dest="indicators", action="append", choices=sorted(INDICATORS.keys()))
    args = parser.parse_args()

    if args.command == "demo":
        return run_demo()
    if args.command == "run-bq":
        return run_bigquery(selected_indicators=args.indicators)
    return run_live(selected_indicators=getattr(args, "indicators", None))


if __name__ == "__main__":
    raise SystemExit(main())
