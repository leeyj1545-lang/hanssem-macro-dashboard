from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import pandas as pd


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS indicator_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_code TEXT NOT NULL,
    indicator_name TEXT NOT NULL,
    bucket TEXT NOT NULL,
    source TEXT NOT NULL,
    source_series_code TEXT,
    frequency TEXT NOT NULL,
    region_code TEXT NOT NULL DEFAULT 'KR',
    region_name TEXT NOT NULL DEFAULT 'Korea',
    observation_date TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    meta_json TEXT,
    loaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(indicator_code, source, frequency, region_code, observation_date)
);

CREATE TABLE IF NOT EXISTS staging_indicator_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    indicator_code TEXT NOT NULL,
    indicator_name TEXT NOT NULL,
    bucket TEXT NOT NULL,
    source TEXT NOT NULL,
    source_series_code TEXT,
    frequency TEXT NOT NULL,
    region_code TEXT NOT NULL DEFAULT 'KR',
    region_name TEXT NOT NULL DEFAULT 'Korea',
    observation_date TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    meta_json TEXT,
    staged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS etl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS etl_run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    run_started_at TEXT NOT NULL,
    run_finished_at TEXT NOT NULL,
    indicator_id TEXT NOT NULL,
    verification_status TEXT,
    collect_status TEXT,
    stage_status TEXT,
    rows_collected INTEGER NOT NULL DEFAULT 0,
    rows_staged INTEGER NOT NULL DEFAULT 0,
    rows_loaded INTEGER NOT NULL DEFAULT 0,
    latest_period TEXT,
    message TEXT,
    error_type TEXT
);
"""


@contextmanager
def connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize_database(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def upsert_observations(db_path: Path, records: Iterable[dict]) -> int:
    rows = list(records)
    if not rows:
        return 0

    sql = """
    INSERT INTO indicator_observations (
        indicator_code, indicator_name, bucket, source, source_series_code,
        frequency, region_code, region_name, observation_date, value, unit, meta_json
    ) VALUES (
        :indicator_code, :indicator_name, :bucket, :source, :source_series_code,
        :frequency, :region_code, :region_name, :observation_date, :value, :unit, :meta_json
    )
    ON CONFLICT(indicator_code, source, frequency, region_code, observation_date)
    DO UPDATE SET
        indicator_name = excluded.indicator_name,
        bucket = excluded.bucket,
        source_series_code = excluded.source_series_code,
        value = excluded.value,
        unit = excluded.unit,
        meta_json = excluded.meta_json,
        loaded_at = CURRENT_TIMESTAMP
    """
    prepared = []
    for row in rows:
        payload = dict(row)
        payload["meta_json"] = json.dumps(payload.get("meta_json", {}), ensure_ascii=False)
        prepared.append(payload)

    with connect(db_path) as conn:
        conn.executemany(sql, prepared)
    return len(prepared)


def log_etl_run(db_path: Path, run_mode: str, status: str, message: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO etl_runs (run_mode, status, message) VALUES (?, ?, ?)",
            (run_mode, status, message),
        )


def stage_observations(db_path: Path, batch_id: str, records: Iterable[dict]) -> int:
    rows = list(records)
    if not rows:
        return 0

    sql = """
    INSERT INTO staging_indicator_observations (
        batch_id, indicator_code, indicator_name, bucket, source, source_series_code,
        frequency, region_code, region_name, observation_date, value, unit, meta_json
    ) VALUES (
        :batch_id, :indicator_code, :indicator_name, :bucket, :source, :source_series_code,
        :frequency, :region_code, :region_name, :observation_date, :value, :unit, :meta_json
    )
    """
    prepared = []
    for row in rows:
        payload = dict(row)
        payload["batch_id"] = batch_id
        payload["meta_json"] = json.dumps(payload.get("meta_json", {}), ensure_ascii=False)
        prepared.append(payload)

    with connect(db_path) as conn:
        conn.executemany(sql, prepared)
    return len(prepared)


def load_staged_indicator(db_path: Path, batch_id: str, indicator_code: str) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(
            """
            SELECT batch_id, indicator_code, indicator_name, bucket, source, source_series_code,
                   frequency, region_code, region_name, observation_date, value, unit, meta_json
            FROM staging_indicator_observations
            WHERE batch_id = ? AND indicator_code = ?
            ORDER BY observation_date
            """,
            conn,
            params=(batch_id, indicator_code),
        )


def promote_staged_indicator(db_path: Path, batch_id: str, indicator_code: str) -> int:
    sql = """
    INSERT INTO indicator_observations (
        indicator_code, indicator_name, bucket, source, source_series_code,
        frequency, region_code, region_name, observation_date, value, unit, meta_json
    )
    SELECT
        indicator_code, indicator_name, bucket, source, source_series_code,
        frequency, region_code, region_name, observation_date, value, unit, meta_json
    FROM staging_indicator_observations
    WHERE batch_id = ? AND indicator_code = ?
    ON CONFLICT(indicator_code, source, frequency, region_code, observation_date)
    DO UPDATE SET
        indicator_name = excluded.indicator_name,
        bucket = excluded.bucket,
        source_series_code = excluded.source_series_code,
        value = excluded.value,
        unit = excluded.unit,
        meta_json = excluded.meta_json,
        loaded_at = CURRENT_TIMESTAMP
    """
    with connect(db_path) as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM staging_indicator_observations WHERE batch_id = ? AND indicator_code = ?",
            (batch_id, indicator_code),
        )
        row_count = int(cursor.fetchone()[0])
        conn.execute(sql, (batch_id, indicator_code))
    return row_count


def clear_staging_batch(db_path: Path, batch_id: str) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM staging_indicator_observations WHERE batch_id = ?", (batch_id,))


def insert_etl_run_history(db_path: Path, records: Iterable[dict]) -> int:
    rows = list(records)
    if not rows:
        return 0
    sql = """
    INSERT INTO etl_run_history (
        run_id, run_started_at, run_finished_at, indicator_id, verification_status,
        collect_status, stage_status, rows_collected, rows_staged, rows_loaded,
        latest_period, message, error_type
    ) VALUES (
        :run_id, :run_started_at, :run_finished_at, :indicator_id, :verification_status,
        :collect_status, :stage_status, :rows_collected, :rows_staged, :rows_loaded,
        :latest_period, :message, :error_type
    )
    """
    with connect(db_path) as conn:
        conn.executemany(sql, rows)
    return len(rows)


def load_observations(db_path: Path) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(
            """
            SELECT indicator_code, indicator_name, bucket, source, frequency,
                   region_code, region_name, observation_date, value, unit
            FROM indicator_observations
            ORDER BY observation_date
            """,
            conn,
        )


def load_etl_run_history(db_path: Path, limit: int = 200) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(
            """
            SELECT run_id, run_started_at, run_finished_at, indicator_id, verification_status,
                   collect_status, stage_status, rows_collected, rows_staged, rows_loaded,
                   latest_period, message, error_type
            FROM etl_run_history
            ORDER BY run_started_at DESC, indicator_id ASC
            LIMIT ?
            """,
            conn,
            params=(limit,),
        )
