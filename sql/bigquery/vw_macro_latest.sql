CREATE OR REPLACE VIEW `{{project}}.{{dataset}}.vw_macro_latest` AS
SELECT
  indicator_id,
  region,
  value,
  raw_value,
  unit,
  source,
  date,
  created_at
FROM `{{project}}.{{dataset}}.macro_indicator_observations`
QUALIFY ROW_NUMBER() OVER (PARTITION BY indicator_id, region ORDER BY date DESC, created_at DESC) = 1;
