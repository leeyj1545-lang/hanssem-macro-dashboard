CREATE OR REPLACE VIEW `{{project}}.{{dataset}}.vw_macro_latest` AS
WITH latest AS (
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
  QUALIFY ROW_NUMBER() OVER (PARTITION BY indicator_id, region ORDER BY date DESC, created_at DESC) = 1
)
SELECT
  indicator_id,
  region,
  CASE
    WHEN region = '전국' THEN 'nationwide'
    WHEN region IN ('서울', '부산', '대구', '인천', '광주', '대전', '울산', '세종', '경기', '강원', '충북', '충남', '전북', '전남', '경북', '경남', '제주') THEN 'sido'
    WHEN region IN ('서울 종로구', '서울 강남구', '인천 미추홀구', '경기 성남시 분당구') THEN 'key_sigungu'
    ELSE 'other'
  END AS region_level,
  value,
  raw_value,
  unit,
  source,
  date,
  created_at
FROM latest;
