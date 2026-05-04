from __future__ import annotations

from datetime import date
from typing import Iterable

import requests

from hanssem_macro_dashboard.config import ECOS_API_KEY, ECOS_BASE_URL, ECOS_INDICATOR_IDS, INDICATORS
from hanssem_macro_dashboard.sources.base import BaseSource, SourceError


class EcosSource(BaseSource):
    def __init__(
        self,
        indicator_ids: list[str] | None = None,
        start_period: str = "202001",
        end_period: str | None = None,
        timeout: int = 30,
    ):
        self.api_key = ECOS_API_KEY
        self.base_url = ECOS_BASE_URL.rstrip("/")
        self.indicator_ids = indicator_ids or ECOS_INDICATOR_IDS
        self.start_period = start_period
        self.end_period = end_period or date.today().strftime("%Y%m")
        self.timeout = timeout

    def _build_url(self, stat_code: str, item_code1: str, cycle: str) -> str:
        return (
            f"{self.base_url}/StatisticSearch/{self.api_key}/json/kr/1/1000/"
            f"{stat_code}/{cycle}/{self.start_period}/{self.end_period}/{item_code1}"
        )

    def _fetch_indicator(self, definition) -> list[dict]:
        if not self.api_key:
            raise SourceError("ECOS_API_KEY is missing.")

        stat_code, item_code1 = definition.source_series_code.split("/", maxsplit=1)
        response = requests.get(
            self._build_url(stat_code=stat_code, item_code1=item_code1, cycle=definition.frequency),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("StatisticSearch", {}).get("row", [])
        if not rows:
            raise SourceError(
                f"No ECOS rows for {definition.indicator_id}. Check source_series_code={definition.source_series_code}."
            )

        observations = []
        for row in rows:
            time_value = row.get("TIME")
            observations.append(
                {
                    "indicator_code": definition.indicator_id,
                    "indicator_name": definition.name_kr,
                    "bucket": definition.category,
                    "source": definition.source,
                    "source_series_code": definition.source_series_code,
                    "frequency": definition.frequency,
                    "region_code": definition.region_code,
                    "region_name": definition.region,
                    "observation_date": f"{time_value[:4]}-{time_value[4:6]}-01",
                    "value": float(row["DATA_VALUE"]),
                    "unit": definition.unit,
                    "meta_json": {
                        "stat_code": stat_code,
                        "item_code1": item_code1,
                        "notes": definition.notes,
                        "direction": definition.direction,
                        "hanssem_logic": definition.hanssem_logic,
                    },
                }
            )
        return observations

    def fetch(self) -> Iterable[dict]:
        results: list[dict] = []
        for indicator_id in self.indicator_ids:
            definition = INDICATORS[indicator_id]
            results.extend(self._fetch_indicator(definition))
        return results
