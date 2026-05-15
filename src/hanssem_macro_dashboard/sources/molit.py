from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable
from xml.etree import ElementTree

import requests

from hanssem_macro_dashboard.config import (
    DATA_GO_KR_API_KEY,
    INDICATORS,
    MOLIT_APT_RENT_URL,
    MOLIT_INDICATOR_IDS,
    MOLIT_APT_TRADE_URL,
)
from hanssem_macro_dashboard.sources.base import BaseSource, SourceError


class MolitTransactionSource(BaseSource):
    def __init__(
        self,
        indicator_ids: list[str] | None = None,
        lawd_codes: list[str] | None = None,
        start_month: str = "202301",
        end_month: str | None = None,
        timeout: int = 30,
    ):
        self.api_key = DATA_GO_KR_API_KEY
        self.trade_url = MOLIT_APT_TRADE_URL
        self.rent_url = MOLIT_APT_RENT_URL
        self.indicator_ids = indicator_ids or MOLIT_INDICATOR_IDS
        self.lawd_codes = lawd_codes or ["11110", "11680", "41135", "28177"]
        self.start_month = start_month
        self.end_month = end_month or date.today().strftime("%Y%m")
        self.timeout = timeout

    def _month_range(self) -> list[str]:
        year = int(self.start_month[:4])
        month = int(self.start_month[4:6])
        end_year = int(self.end_month[:4])
        end_month = int(self.end_month[4:6])
        months: list[str] = []
        while (year, month) <= (end_year, end_month):
            months.append(f"{year:04d}{month:02d}")
            month += 1
            if month > 12:
                year += 1
                month = 1
        return months

    def _fetch_xml_count(self, base_url: str, lawd_code: str, deal_ymd: str) -> int:
        if not self.api_key:
            raise SourceError("DATA_GO_KR_API_KEY is missing.")
        response = requests.get(
            base_url,
            params={"serviceKey": self.api_key, "LAWD_CD": lawd_code, "DEAL_YMD": deal_ymd},
            timeout=self.timeout,
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        result_code = root.findtext(".//resultCode", default="")
        result_msg = root.findtext(".//resultMsg", default="")
        normalized_result_code = result_code.strip()
        if normalized_result_code and normalized_result_code not in {"00", "000"}:
            raise SourceError(
                f"MOLIT transaction API error resultCode={normalized_result_code} resultMsg={result_msg} "
                f"lawd_code={lawd_code} deal_ymd={deal_ymd}"
            )
        items = root.findall(".//item")
        return len(items)

    def fetch(self) -> Iterable[dict]:
        months = self._month_range()
        trade_counter: Counter[str] = Counter()
        rent_counter: Counter[str] = Counter()
        trade_definition = INDICATORS["apt_trade_volume"]
        rent_definition = INDICATORS["apt_rent_volume"]

        for month in months:
            for lawd_code in self.lawd_codes:
                if "apt_trade_volume" in self.indicator_ids:
                    trade_counter[month] += self._fetch_xml_count(self.trade_url, lawd_code, month)
                if "apt_rent_volume" in self.indicator_ids:
                    rent_counter[month] += self._fetch_xml_count(self.rent_url, lawd_code, month)

        rows: list[dict] = []
        if "apt_trade_volume" in self.indicator_ids:
            for month, value in sorted(trade_counter.items()):
                rows.append(
                    {
                        "indicator_code": trade_definition.indicator_id,
                        "indicator_name": trade_definition.name_kr,
                        "bucket": trade_definition.category,
                        "source": trade_definition.source,
                        "source_series_code": trade_definition.source_series_code,
                        "frequency": trade_definition.frequency,
                        "region_code": trade_definition.region_code,
                        "region_name": trade_definition.region,
                        "observation_date": f"{month[:4]}-{month[4:6]}-01",
                        "value": float(value),
                        "unit": trade_definition.unit,
                        "meta_json": {
                            "lawd_codes": self.lawd_codes,
                            "direction": trade_definition.direction,
                            "hanssem_logic": trade_definition.hanssem_logic,
                        },
                    }
                )
        if "apt_rent_volume" in self.indicator_ids:
            for month, value in sorted(rent_counter.items()):
                rows.append(
                    {
                        "indicator_code": rent_definition.indicator_id,
                        "indicator_name": rent_definition.name_kr,
                        "bucket": rent_definition.category,
                        "source": rent_definition.source,
                        "source_series_code": rent_definition.source_series_code,
                        "frequency": rent_definition.frequency,
                        "region_code": rent_definition.region_code,
                        "region_name": rent_definition.region,
                        "observation_date": f"{month[:4]}-{month[4:6]}-01",
                        "value": float(value),
                        "unit": rent_definition.unit,
                        "meta_json": {
                            "lawd_codes": self.lawd_codes,
                            "direction": rent_definition.direction,
                            "hanssem_logic": rent_definition.hanssem_logic,
                        },
                    }
                )
        return rows
