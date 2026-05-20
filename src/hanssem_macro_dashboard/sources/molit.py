from __future__ import annotations

from datetime import date
from typing import Iterable
from xml.etree import ElementTree

import requests

from hanssem_macro_dashboard.config import (
    DATA_GO_KR_API_KEY,
    INDICATORS,
    MOLIT_APT_RENT_URL,
    MOLIT_APT_TRADE_URL,
    MOLIT_INDICATOR_IDS,
)
from hanssem_macro_dashboard.sources.base import BaseSource, SourceError

# NOTE:
# The public RTMS APIs reliably return transaction counts for sigungu-level
# LAWD_CD values. Direct sido-level codes (e.g. 11000 for Seoul) often respond
# with 0 rows even when transactions exist, which creates misleading data.
# Until we add a full nationwide sigungu code master for proper rollups,
# keep transaction indicators at "key sigungu" granularity only.
TRANSACTION_REGION_SPECS = [
    {"lawd_code": "11110", "region_name": "서울 종로구", "region_level": "key_sigungu"},
    {"lawd_code": "11680", "region_name": "서울 강남구", "region_level": "key_sigungu"},
    {"lawd_code": "28177", "region_name": "인천 미추홀구", "region_level": "key_sigungu"},
    {"lawd_code": "41135", "region_name": "경기 성남시 분당구", "region_level": "key_sigungu"},
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
            params={
                "serviceKey": self.api_key,
                "LAWD_CD": lawd_code,
                "DEAL_YMD": deal_ymd,
                "pageNo": 1,
                "numOfRows": 1,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()

        root = ElementTree.fromstring(response.content)
        result_code = root.findtext(".//resultCode", default="").strip()
        result_msg = root.findtext(".//resultMsg", default="").strip()
        if result_code and result_code not in {"00", "000"}:
            raise SourceError(
                f"MOLIT transaction API error resultCode={result_code} resultMsg={result_msg} "
                f"lawd_code={lawd_code} deal_ymd={deal_ymd}"
            )

        total_count = root.findtext(".//totalCount", default="").strip()
        if total_count:
            try:
                return int(total_count)
            except ValueError:
                pass

        items = root.findall(".//item")
        return len(items)

    def _build_observation_row(
        self,
        indicator_id: str,
        lawd_code: str,
        region_name: str,
        region_level: str,
        month: str,
        value: int,
    ) -> dict:
        definition = INDICATORS[indicator_id]
        return {
            "indicator_code": definition.indicator_id,
            "indicator_name": definition.name_kr,
            "bucket": definition.category,
            "source": definition.source,
            "source_series_code": definition.source_series_code,
            "frequency": definition.frequency,
            "region_code": lawd_code,
            "region_name": region_name,
            "observation_date": f"{month[:4]}-{month[4:6]}-01",
            "value": float(value),
            "unit": definition.unit,
            "meta_json": {
                "lawd_code": lawd_code,
                "region_level": region_level,
                "direction": definition.direction,
                "hanssem_logic": definition.hanssem_logic,
            },
        }

    def fetch(self) -> Iterable[dict]:
        months = self._month_range()
        rows: list[dict] = []

        for month in months:
            for spec in self.region_specs:
                lawd_code = spec["lawd_code"]
                region_name = spec["region_name"]
                region_level = spec["region_level"]

                if "apt_trade_volume" in self.indicator_ids:
                    trade_count = self._fetch_xml_count(self.trade_url, lawd_code, month)
                    rows.append(
                        self._build_observation_row(
                            indicator_id="apt_trade_volume",
                            lawd_code=lawd_code,
                            region_name=region_name,
                            region_level=region_level,
                            month=month,
                            value=trade_count,
                        )
                    )

                if "apt_rent_volume" in self.indicator_ids:
                    rent_count = self._fetch_xml_count(self.rent_url, lawd_code, month)
                    rows.append(
                        self._build_observation_row(
                            indicator_id="apt_rent_volume",
                            lawd_code=lawd_code,
                            region_name=region_name,
                            region_level=region_level,
                            month=month,
                            value=rent_count,
                        )
                    )

        return rows
