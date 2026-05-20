CREATE OR REPLACE VIEW `{{project}}.{{dataset}}.vw_macro_latest_all_regions` AS
SELECT
  indicator_id,
  region,
  region_level,
  value,
  raw_value,
  unit,
  source,
  date,
  created_at
FROM `{{project}}.{{dataset}}.vw_macro_latest`;
