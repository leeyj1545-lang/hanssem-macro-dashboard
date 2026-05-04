SELECT
  indicator_id,
  COUNT(*) AS rows_staged,
  COUNTIF(date IS NULL OR value IS NULL OR region IS NULL) AS null_rows,
  COUNT(*) - COUNT(DISTINCT CONCAT(indicator_id, '|', CAST(date AS STRING), '|', region, '|', source)) AS duplicate_rows,
  MAX(date) AS latest_period
FROM `{{project}}.{{dataset}}.staging_macro_indicator_observations`
WHERE run_id = @run_id
GROUP BY indicator_id
ORDER BY indicator_id;
