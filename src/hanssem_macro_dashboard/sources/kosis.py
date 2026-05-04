from __future__ import annotations

import json
import re
from typing import Any, Iterable

import requests

from hanssem_macro_dashboard.config import KOSIS_API_KEY, KOSIS_BASE_URL
from hanssem_macro_dashboard.sources.base import BaseSource, SourceError


class KosisSource(BaseSource):
    def __init__(self, timeout: int = 30):
        self.api_key = KOSIS_API_KEY
        self.base_url = KOSIS_BASE_URL
        self.timeout = timeout

    def fetch_series(
        self,
        indicator_code: str,
        indicator_name: str,
        bucket: str,
        tbl_id: str,
        org_id: str,
        field_map: dict[str, str],
        frequency: str = "M",
        unit: str = "count",
        itm_id: str = "",
        obj_l1: str = "",
        obj_l2: str = "",
        obj_l3: str = "",
    ) -> list[dict]:
        if not self.api_key:
            raise SourceError("KOSIS_API_KEY is missing.")
        params = {
            "method": "getList",
            "apiKey": self.api_key,
            "format": "json",
            "jsonVD": "Y",
            "userStatsId": "",
            "prdSe": "M",
            "newEstPrdCnt": "",
            "orgId": org_id,
            "tblId": tbl_id,
        }
        if itm_id:
            params["itmId"] = itm_id
        if obj_l1:
            params["objL1"] = obj_l1
        if obj_l2:
            params["objL2"] = obj_l2
        if obj_l3:
            params["objL3"] = obj_l3
        response = requests.get(self.base_url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise SourceError(f"Unexpected KOSIS response for {tbl_id}: {payload}")

        rows = []
        for item in payload:
            period = item.get(field_map["period"])
            value = item.get(field_map["value"])
            if not period or value in (None, ""):
                continue
            rows.append(
                {
                    "indicator_code": indicator_code,
                    "indicator_name": indicator_name,
                    "bucket": bucket,
                    "source": "KOSIS",
                    "source_series_code": tbl_id,
                    "frequency": frequency,
                    "region_code": "KR",
                    "region_name": "Korea",
                    "observation_date": f"{period[:4]}-{period[4:6]}-01",
                    "value": float(str(value).replace(",", "")),
                    "unit": unit,
                    "meta_json": {"org_id": org_id, "tbl_id": tbl_id},
                }
            )
        return rows

    def fetch_table_meta(self, org_id: str, tbl_id: str) -> Any:
        if not self.api_key:
            raise SourceError("KOSIS_API_KEY is missing.")
        params = {
            "method": "getMeta",
            "type": "TBL",
            "apiKey": self.api_key,
            "format": "json",
            "content": "json",
            "orgId": org_id,
            "tblId": tbl_id,
        }
        response = requests.get(self.base_url, params=params, timeout=self.timeout)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            text = response.text.strip()
            try:
                normalized = re.sub(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', text)
                return json.loads(normalized)
            except Exception as exc:  # pragma: no cover
                raise SourceError(f"Failed to parse KOSIS meta response for {tbl_id}: {exc}; raw={text[:500]}") from exc

    def fetch_table_items(self, org_id: str, tbl_id: str) -> Any:
        if not self.api_key:
            raise SourceError("KOSIS_API_KEY is missing.")
        params = {
            "method": "getMeta",
            "type": "ITM",
            "apiKey": self.api_key,
            "format": "json",
            "content": "json",
            "orgId": org_id,
            "tblId": tbl_id,
        }
        response = requests.get(self.base_url, params=params, timeout=self.timeout)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            text = response.text.strip()
            try:
                normalized = re.sub(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', text)
                return json.loads(normalized)
            except Exception as exc:  # pragma: no cover
                raise SourceError(f"Failed to parse KOSIS item meta response for {tbl_id}: {exc}; raw={text[:500]}") from exc

    def fetch(self) -> Iterable[dict]:
        return []
