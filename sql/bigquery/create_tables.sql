CREATE TABLE IF NOT EXISTS `{{project}}.{{dataset}}.macro_indicator_observations` (
  date DATE,
  indicator_id STRING,
  region STRING,
  value FLOAT64,
  raw_value FLOAT64,
  unit STRING,
  source STRING,
  created_at TIMESTAMP
)
PARTITION BY DATE(date)
CLUSTER BY indicator_id, region;

CREATE TABLE IF NOT EXISTS `{{project}}.{{dataset}}.staging_macro_indicator_observations` (
  run_id STRING,
  indicator_id STRING,
  date DATE,
  region STRING,
  value FLOAT64,
  raw_value FLOAT64,
  unit STRING,
  source STRING,
  loaded_at TIMESTAMP
)
PARTITION BY DATE(date)
CLUSTER BY run_id, indicator_id, region;

CREATE TABLE IF NOT EXISTS `{{project}}.{{dataset}}.source_verification` (
  indicator_id STRING,
  source_name STRING,
  provider STRING,
  verification_status STRING,
  error_type STRING,
  rows INT64,
  last_verified_at TIMESTAMP,
  message STRING
);

CREATE TABLE IF NOT EXISTS `{{project}}.{{dataset}}.etl_run_history` (
  run_id STRING,
  indicator_id STRING,
  collect_status STRING,
  stage_status STRING,
  rows_loaded INT64,
  latest_period DATE,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  message STRING
)
PARTITION BY DATE(started_at)
CLUSTER BY indicator_id, stage_status;

CREATE TABLE IF NOT EXISTS `{{project}}.{{dataset}}.hanssem_sales_monthly` (
  date DATE,
  sales FLOAT64,
  category STRING,
  region STRING
)
PARTITION BY DATE(date)
CLUSTER BY category, region;
