MERGE `{{project}}.{{dataset}}.macro_indicator_observations` T
USING (
  SELECT *
  FROM `{{project}}.{{dataset}}.staging_macro_indicator_observations`
  WHERE run_id = @run_id
) S
ON T.indicator_id = S.indicator_id
AND T.date = S.date
AND T.region = S.region
AND T.source = S.source
WHEN MATCHED THEN UPDATE SET
  value = S.value,
  raw_value = S.raw_value,
  unit = S.unit,
  created_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
  date, indicator_id, region, value, raw_value, unit, source, created_at
)
VALUES (
  S.date, S.indicator_id, S.region, S.value, S.raw_value, S.unit, S.source, CURRENT_TIMESTAMP()
);
