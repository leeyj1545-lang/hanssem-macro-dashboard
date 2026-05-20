CREATE OR REPLACE VIEW `{{project}}.{{dataset}}.vw_macro_latest_sido` AS
SELECT
  indicator_id,
  region,
  value,
  raw_value,
  unit,
  source,
  date,
  created_at
FROM `{{project}}.{{dataset}}.vw_macro_latest`
WHERE region_level = 'sido';
