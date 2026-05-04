CREATE OR REPLACE VIEW `{{project}}.{{dataset}}.vw_hanssem_macro_hmi` AS
WITH base AS (
  SELECT
    date,
    MAX(IF(indicator_id = 'sale_price_index', value, NULL)) AS price_index,
    MAX(IF(indicator_id = 'jeonse_price_index', value, NULL)) AS jeonse_index,
    MAX(IF(indicator_id = 'completion_volume', value, NULL)) AS completion_volume,
    MAX(IF(indicator_id = 'unsold_units', value, NULL)) AS unsold_units
  FROM `{{project}}.{{dataset}}.macro_indicator_observations`
  WHERE region = '전국'
  GROUP BY date
),
yoy AS (
  SELECT
    date,
    price_index,
    jeonse_index,
    completion_volume,
    unsold_units,
    SAFE_DIVIDE(price_index - LAG(price_index, 12) OVER (ORDER BY date), LAG(price_index, 12) OVER (ORDER BY date)) * 100 AS price_yoy,
    SAFE_DIVIDE(jeonse_index - LAG(jeonse_index, 12) OVER (ORDER BY date), LAG(jeonse_index, 12) OVER (ORDER BY date)) * 100 AS jeonse_yoy,
    SAFE_DIVIDE(completion_volume - LAG(completion_volume, 12) OVER (ORDER BY date), LAG(completion_volume, 12) OVER (ORDER BY date)) * 100 AS completion_yoy,
    SAFE_DIVIDE(unsold_units - LAG(unsold_units, 12) OVER (ORDER BY date), LAG(unsold_units, 12) OVER (ORDER BY date)) * 100 AS unsold_yoy
  FROM base
),
scored AS (
  SELECT
    *,
    CASE
      WHEN completion_yoy IS NULL OR unsold_yoy IS NULL THEN NULL
      ELSE (price_yoy * 0.4 + completion_yoy * 0.3 - unsold_yoy * 0.3)
    END AS hmi
  FROM yoy
),
labeled AS (
  SELECT
    s.*,
    CASE
      WHEN s.hmi IS NULL THEN 'not_ready'
      WHEN s.hmi >= 1.0 THEN 'strong_positive'
      WHEN s.hmi >= 0.3 THEN 'positive'
      WHEN s.hmi > -0.3 THEN 'neutral'
      WHEN s.hmi > -1.0 THEN 'negative'
      ELSE 'strong_negative'
    END AS signal,
    CASE
      WHEN s.hmi IS NULL THEN 'not_ready'
      WHEN s.hmi >= 0.3 THEN 'recovery'
      WHEN s.hmi <= -0.3 THEN 'slowdown'
      ELSE 'mixed'
    END AS market_phase,
    CASE
      WHEN s.hmi IS NULL THEN 'Macro dataset is not ready because supply or risk indicators are still missing.'
      WHEN s.price_yoy > 0 AND s.completion_yoy > 0 AND s.unsold_yoy < 0 THEN 'Demand-led recovery with healthy supply and lower unsold inventory.'
      WHEN s.price_yoy > 0 AND s.unsold_yoy > 0 THEN 'Price strength is offset by rising unsold inventory.'
      WHEN s.price_yoy < 0 AND s.unsold_yoy > 0 THEN 'Weak pricing and rising unsold inventory imply downside risk.'
      WHEN s.completion_yoy > 0 AND s.unsold_yoy < 0 AND s.jeonse_yoy > 0 THEN 'Move-in demand and lower unsold inventory support housing activity.'
      ELSE 'Mixed macro signals require selective execution.'
    END AS insight_text
  FROM scored s
)
SELECT
  date,
  price_index,
  jeonse_index,
  completion_volume,
  unsold_units,
  price_yoy,
  jeonse_yoy,
  completion_yoy,
  unsold_yoy,
  hmi,
  signal,
  market_phase,
  insight_text
FROM labeled;
