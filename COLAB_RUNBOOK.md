# Colab Re-Run Guide

## 1. Refresh from GitHub

```python
!rm -rf /content/hanssem-macro-dashboard
!git clone https://github.com/leeyj1545-lang/hanssem-macro-dashboard.git
%cd /content/hanssem-macro-dashboard
!pip install -r requirements.txt
```

## 2. Authenticate and configure

Run the notebook cells for:

1. Google authentication
2. Environment variables
3. BigQuery datamart initialization

Recommended values:

```python
BQ_PROJECT_ID = "cellular-client-310600"
BQ_DATASET = "Yunjae_Workspace"
BQ_LOCATION = "US"
```

## 3. Re-run only MOLIT indicators

```python
!python -m hanssem_macro_dashboard.pipeline run-bq --indicator completion_volume --indicator housing_permits --indicator unsold_units
```

## 4. Validate BigQuery production rows

```sql
SELECT indicator_id, COUNT(*) AS cnt
FROM `cellular-client-310600.Yunjae_Workspace.macro_indicator_observations`
GROUP BY indicator_id
ORDER BY indicator_id;
```

Expected indicators after success:

- `completion_volume`
- `housing_permits`
- `jeonse_price_index`
- `sale_price_index`
- `unsold_units`

## 5. Validate HMI view

```sql
SELECT
  date,
  price_index,
  completion_volume,
  unsold_units,
  hmi,
  signal,
  market_phase
FROM `cellular-client-310600.Yunjae_Workspace.vw_hanssem_macro_hmi`
ORDER BY date DESC
LIMIT 20;
```

If supply or risk indicators are still missing, `signal` should now show `not_ready` instead of a misleading negative state.
