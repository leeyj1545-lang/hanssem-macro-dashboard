CREATE OR REPLACE VIEW `{{project}}.{{dataset}}.vw_source_health` AS
SELECT
  indicator_id,
  source_name,
  provider,
  verification_status,
  error_type,
  rows,
  last_verified_at,
  message,
  CASE
    WHEN verification_status = 'verified' THEN 'healthy'
    WHEN verification_status = 'pending_condition_check' THEN 'warning'
    WHEN verification_status LIKE 'failed%' THEN 'critical'
    ELSE 'pending'
  END AS health_status
FROM `{{project}}.{{dataset}}.source_verification`;
