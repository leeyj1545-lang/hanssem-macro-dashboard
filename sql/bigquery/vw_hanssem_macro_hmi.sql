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
hmi AS (
  SELECT
    *,
    (price_yoy * 0.4 + completion_yoy * 0.3 - unsold_yoy * 0.3) AS hmi
  FROM yoy
),
labeled AS (
  SELECT
    *,
    CASE
      WHEN hmi >= 1.0 THEN 'strong_positive'
      WHEN hmi >= 0.3 THEN 'positive'
      WHEN hmi > -0.3 THEN 'neutral'
      WHEN hmi > -1.0 THEN 'negative'
      ELSE 'strong_negative'
    END AS signal,
    CASE
      WHEN hmi >= 1.0 THEN '회복 가속'
      WHEN hmi >= 0.3 THEN '회복'
      WHEN hmi > -0.3 THEN '혼조'
      WHEN hmi > -1.0 THEN '둔화'
      ELSE '침체'
    END AS market_phase,
    CASE
      WHEN price_yoy > 0 AND completion_yoy > 0 AND unsold_yoy < 0
        THEN '실수요 기반 시장 회복 -> 강한 매출 기회 구간'
      WHEN price_yoy > 0 AND unsold_yoy > 0
        THEN '가격 상승 대비 미분양 누적 -> 제한적 매출 성장 구간'
      WHEN price_yoy < 0 AND unsold_yoy > 0
        THEN '시장 침체와 재고 증가 동반 -> 매출 하방 리스크 구간'
      WHEN completion_yoy > 0 AND unsold_yoy < 0 AND jeonse_yoy > 0
        THEN '입주 확대와 미분양 해소, 전세 강세 동반 -> 주거 이동 수요 우호 구간'
      ELSE '시장 신호 혼조 -> 지역별 전략 대응 필요'
    END AS insight_text
  FROM hmi
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
