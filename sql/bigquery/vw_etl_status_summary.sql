CREATE OR REPLACE VIEW `{{project}}.{{dataset}}.vw_etl_status_summary` AS
WITH run_level AS (
  SELECT
    run_id,
    MIN(started_at) AS started_at,
    MAX(finished_at) AS finished_at,
    COUNT(*) AS indicator_count,
    COUNTIF(stage_status = 'production_loaded') AS loaded_indicator_count,
    COUNTIF(stage_status = 'failed_staging') AS failed_indicator_count,
    SUM(rows_loaded) AS total_rows_loaded
  FROM `{{project}}.{{dataset}}.etl_run_history`
  GROUP BY run_id
)
SELECT
  run_id,
  started_at,
  finished_at,
  indicator_count,
  loaded_indicator_count,
  failed_indicator_count,
  total_rows_loaded,
  CASE
    WHEN failed_indicator_count = 0 AND loaded_indicator_count > 0 THEN 'success'
    WHEN loaded_indicator_count > 0 THEN 'partial_success'
    ELSE 'failed'
  END AS run_status
FROM run_level;
