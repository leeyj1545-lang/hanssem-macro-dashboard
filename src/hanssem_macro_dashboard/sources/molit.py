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

TRANSACTION_REGION_SPECS = [
    {"lawd_code": "11000", "region_name": "서울", "region_level": "sido"},
    {"lawd_code": "26000", "region_name": "부산", "region_level": "sido"},
    {"lawd_code": "27000", "region_name": "대구", "region_level": "sido"},
    {"lawd_code": "28000", "region_name": "인천", "region_level": "sido"},
    {"lawd_code": "29000", "region_name": "광주", "region_level": "sido"},
    {"lawd_code": "30000", "region_name": "대전", "region_level": "sido"},
    {"lawd_code": "31000", "region_name": "울산", "region_level": "sido"},
    {"lawd_code": "36000", "region_name": "세종", "region_level": "sido"},
    {"lawd_code": "41000", "region_name": "경기", "region_level": "sido"},
    {"lawd_code": "42000", "region_name": "강원", "region_level": "sido"},
    {"lawd_code": "43000", "region_name": "충북", "region_level": "sido"},
    {"lawd_code": "44000", "region_name": "충남", "region_level": "sido"},
    {"lawd_code": "45000", "region_name": "전북", "region_level": "sido"},
    {"lawd_code": "46000", "region_name": "전남", "region_level": "sido"},
    {"lawd_code": "47000", "region_name": "경북", "region_level": "sido"},
    {"lawd_code": "48000", "region_name": "경남", "region_level": "sido"},
    {"lawd_code": "50000", "region_name": "제주", "region_level": "sido"},
    {"lawd_code": "11110", "region_name": "서울 종로구", "region_level": "sigungu"},
    {"lawd_code": "11680", "region_name": "서울 강남구", "region_level": "sigungu"},
    {"lawd_code": "28177", "region_name": "인천 미추홀구", "region_level": "sigungu"},
    {"lawd_code": "41135", "region_name": "경기 성남시 분당구", "region_level": "sigungu"},
]


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
        self.region_specs = (
            [{"lawd_code": lawd_code, "region_name": lawd_code, "region_level": "custom"} for lawd_code in lawd_codes]
            if lawd_codes
            else TRANSACTION_REGION_SPECS
        )
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
        trade_counter: Counter[tuple[str, str, str, str]] = Counter()
        rent_counter: Counter[tuple[str, str, str, str]] = Counter()
        trade_definition = INDICATORS["apt_trade_volume"]
        rent_definition = INDICATORS["apt_rent_volume"]

        for month in months:
            for spec in self.region_specs:
                lawd_code = spec["lawd_code"]
                if "apt_trade_volume" in self.indicator_ids:
                    trade_counter[(month, lawd_code, spec["region_name"], spec["region_level"])] += self._fetch_xml_count(
                        self.trade_url,
                        lawd_code,
                        month,
                    )
                if "apt_rent_volume" in self.indicator_ids:
                    rent_counter[(month, lawd_code, spec["region_name"], spec["region_level"])] += self._fetch_xml_count(
                        self.rent_url,
                        lawd_code,
                        month,
                    )

        rows: list[dict] = []
        if "apt_trade_volume" in self.indicator_ids:
            national_counter: Counter[str] = Counter()
            for (month, lawd_code, region_name, region_level), value in sorted(trade_counter.items()):
                if region_level == "sido":
                    national_counter[month] += value
                rows.append(
                    {
                        "indicator_code": trade_definition.indicator_id,
                        "indicator_name": trade_definition.name_kr,
                        "bucket": trade_definition.category,
                        "source": trade_definition.source,
                        "source_series_code": trade_definition.source_series_code,
                        "frequency": trade_definition.frequency,
                        "region_code": lawd_code,
                        "region_name": region_name,
                        "observation_date": f"{month[:4]}-{month[4:6]}-01",
                        "value": float(value),
                        "unit": trade_definition.unit,
                        "meta_json": {
                            "lawd_code": lawd_code,
                            "region_level": region_level,
                            "direction": trade_definition.direction,
                            "hanssem_logic": trade_definition.hanssem_logic,
                        },
                    }
                )
            for month, value in sorted(national_counter.items()):
                rows.append(
                    {
                        "indicator_code": trade_definition.indicator_id,
                        "indicator_name": trade_definition.name_kr,
                        "bucket": trade_definition.category,
                        "source": trade_definition.source,
                        "source_series_code": trade_definition.source_series_code,
                        "frequency": trade_definition.frequency,
                        "region_code": "KR",
                        "region_name": "전국",
                        "observation_date": f"{month[:4]}-{month[4:6]}-01",
                        "value": float(value),
                        "unit": trade_definition.unit,
                        "meta_json": {
                            "region_level": "nationwide",
                            "direction": trade_definition.direction,
                            "hanssem_logic": trade_definition.hanssem_logic,
                        },
                    }
                )
        if "apt_rent_volume" in self.indicator_ids:
            national_counter: Counter[str] = Counter()
            for (month, lawd_code, region_name, region_level), value in sorted(rent_counter.items()):
                if region_level == "sido":
                    national_counter[month] += value
                rows.append(
                    {
                        "indicator_code": rent_definition.indicator_id,
                        "indicator_name": rent_definition.name_kr,
                        "bucket": rent_definition.category,
                        "source": rent_definition.source,
                        "source_series_code": rent_definition.source_series_code,
                        "frequency": rent_definition.frequency,
                        "region_code": lawd_code,
                        "region_name": region_name,
                        "observation_date": f"{month[:4]}-{month[4:6]}-01",
                        "value": float(value),
                        "unit": rent_definition.unit,
                        "meta_json": {
                            "lawd_code": lawd_code,
                            "region_level": region_level,
                            "direction": rent_definition.direction,
                            "hanssem_logic": rent_definition.hanssem_logic,
                        },
                    }
                )
            for month, value in sorted(national_counter.items()):
                rows.append(
                    {
                        "indicator_code": rent_definition.indicator_id,
                        "indicator_name": rent_definition.name_kr,
                        "bucket": rent_definition.category,
                        "source": rent_definition.source,
                        "source_series_code": rent_definition.source_series_code,
                        "frequency": rent_definition.frequency,
                        "region_code": "KR",
                        "region_name": "전국",
                        "observation_date": f"{month[:4]}-{month[4:6]}-01",
                        "value": float(value),
                        "unit": rent_definition.unit,
                        "meta_json": {
                            "region_level": "nationwide",
                            "direction": rent_definition.direction,
                            "hanssem_logic": rent_definition.hanssem_logic,
                        },
                    }
                )
        return rows
