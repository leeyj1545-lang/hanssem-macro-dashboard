CREATE OR REPLACE VIEW `{{project}}.{{dataset}}.vw_macro_sales_join` AS
WITH sales_enriched AS (
  SELECT
    date,
    sales,
    category AS sales_category,
    region AS sales_region,
    LAG(sales, 1) OVER (PARTITION BY category, region ORDER BY date) AS sales_prev_month,
    LAG(sales, 12) OVER (PARTITION BY category, region ORDER BY date) AS sales_prev_year
  FROM `{{project}}.{{dataset}}.hanssem_sales_monthly`
)
SELECT
  m.date,
  m.hmi,
  m.signal,
  m.market_phase,
  m.insight_text,
  m.price_index,
  m.jeonse_index,
  m.completion_volume,
  m.unsold_units,
  m.price_yoy,
  m.jeonse_yoy,
  m.completion_yoy,
  m.unsold_yoy,
  s.sales,
  s.sales_category,
  s.sales_region,
  SAFE_DIVIDE(s.sales - s.sales_prev_month, s.sales_prev_month) * 100 AS sales_mom,
  SAFE_DIVIDE(s.sales - s.sales_prev_year, s.sales_prev_year) * 100 AS sales_yoy,
  CASE
    WHEN s.sales IS NULL THEN 0
    ELSE 1
  END AS has_sales,
  m.hmi * s.sales AS hmi_sales_product
FROM `{{project}}.{{dataset}}.vw_hanssem_macro_hmi` m
LEFT JOIN sales_enriched s
  ON m.date = s.date;
