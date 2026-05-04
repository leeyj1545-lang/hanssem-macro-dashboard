from __future__ import annotations

from datetime import datetime

from hanssem_macro_dashboard.config import INDICATORS


def build_demo_rows() -> list[dict]:
    months = [dt.strftime("%Y-%m-01") for dt in _month_starts("2023-01-01", "2026-03-01")]
    demo_series = {
        "base_rate": lambda idx: 3.75 - min(idx, 18) * 0.04 + (idx % 3) * 0.01,
        "mortgage_rate": lambda idx: 5.95 - min(idx, 20) * 0.05 + (idx % 4) * 0.02,
        "consumer_sentiment": lambda idx: 91 + idx * 0.45 + (idx % 5) * 0.8,
        "household_loan_balance": lambda idx: 1020 + idx * 7.4 + (idx % 4) * 2.5,
        "apt_trade_volume": lambda idx: 7600 + idx * 190 + (idx % 4) * 410,
        "apt_rent_volume": lambda idx: 12800 + idx * 110 + (idx % 3) * 360,
        "sale_price_index": lambda idx: 96 + idx * 0.42 + (idx % 6) * 0.12,
        "jeonse_price_index": lambda idx: 94 + idx * 0.36 + (idx % 5) * 0.10,
        "completion_volume": lambda idx: 23500 + idx * 240 + (idx % 6) * 900,
        "housing_permits": lambda idx: 28500 + idx * 120 + (idx % 5) * 1100,
        "unsold_units": lambda idx: 8200 - idx * 45 + (idx % 6) * 130,
    }

    rows: list[dict] = []
    for idx, month in enumerate(months):
        for indicator_id, value_fn in demo_series.items():
            definition = INDICATORS[indicator_id]
            rows.append(
                {
                    "indicator_code": definition.indicator_id,
                    "indicator_name": definition.name_kr,
                    "bucket": definition.category,
                    "source": definition.source,
                    "source_series_code": definition.source_series_code,
                    "frequency": definition.frequency,
                    "region_code": definition.region_code,
                    "region_name": definition.region,
                    "observation_date": month,
                    "value": round(value_fn(idx), 2),
                    "unit": definition.unit,
                    "meta_json": {
                        "demo": True,
                        "direction": definition.direction,
                        "hanssem_logic": definition.hanssem_logic,
                        "verification_status": definition.source_detail.verification_status,
                    },
                }
            )
    return rows


def _month_starts(start: str, end: str):
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    current = start_dt
    while current <= end_dt:
        yield current
        year = current.year + (current.month // 12)
        month = (current.month % 12) + 1
        current = current.replace(year=year, month=month)
