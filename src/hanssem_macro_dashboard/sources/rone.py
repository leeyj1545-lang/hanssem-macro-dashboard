from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlencode

import pandas as pd
import requests

from hanssem_macro_dashboard.config import INDICATORS, RONE_API_KEY, RONE_BASE_URL
from hanssem_macro_dashboard.sources.base import SourceError

DEFAULT_STATBL_IDS = {
    "sale_price_index": "A_2024_00178",
    "jeonse_price_index": "A_2024_00182",
}

DEFAULT_REGION_NAME = "전국"
DEFAULT_REGION_CLS_ID = "500001"
DEFAULT_ITEM_ID = "100001"


class RoneSource:
    def __init__(self, indicator_ids: list[str] | None = None, timeout: int = 30):
        self.indicator_ids = indicator_ids or ["sale_price_index", "jeonse_price_index"]
        self.timeout = timeout

    def fetch(self) -> list[dict]:
        rows: list[dict] = []
        for indicator_id in self.indicator_ids:
            rows.extend(self.fetch_indicator_rows(indicator_id))
        return rows

    def build_service_code_candidates(
        self,
        indicator_id: str,
        candidate_params: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        definition = INDICATORS[indicator_id]
        detail = definition.source_detail
        candidates: list[dict[str, str]] = []

        if candidate_params and candidate_params.get("statbl_id"):
            candidates.append(
                {
                    "service_code": candidate_params.get("service_code", "SttsApiTblData.do"),
                    "statbl_id": candidate_params["statbl_id"],
                    "region_name": candidate_params.get("region_name", DEFAULT_REGION_NAME),
                    "region_cls_id": candidate_params.get("region_cls_id", DEFAULT_REGION_CLS_ID),
                    "item_id": candidate_params.get("item_id", DEFAULT_ITEM_ID),
                    "dtacycle_cd": candidate_params.get("dtacycle_cd", "MM"),
                    "label": "saved_candidate",
                }
            )

        default_statbl_id = DEFAULT_STATBL_IDS.get(indicator_id, "")
        candidates.append(
            {
                "service_code": detail.stat_code or "SttsApiTblData.do",
                "statbl_id": default_statbl_id,
                "region_name": DEFAULT_REGION_NAME,
                "region_cls_id": DEFAULT_REGION_CLS_ID,
                "item_id": DEFAULT_ITEM_ID,
                "dtacycle_cd": "MM",
                "label": "default_statbl_id",
            }
        )

        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            key = (candidate["service_code"], candidate["statbl_id"])
            if not candidate["statbl_id"] or key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def build_probe(
        self,
        statbl_id: str,
        service_code: str = "SttsApiTblData.do",
        page_index: int = 1,
        page_size: int = 1000,
        dtacycle_cd: str = "MM",
    ) -> dict[str, object]:
        return {
            "url": f"{RONE_BASE_URL}/{service_code}",
            "params": {
                "KEY": RONE_API_KEY or "sample",
                "Type": "json",
                "STATBL_ID": statbl_id,
                "DTACYCLE_CD": dtacycle_cd,
                "pIndex": str(page_index),
                "pSize": str(page_size),
            },
        }

    def build_probe_text(
        self,
        statbl_id: str,
        service_code: str = "SttsApiTblData.do",
        page_index: int = 1,
        page_size: int = 1000,
        dtacycle_cd: str = "MM",
    ) -> str:
        probe = self.build_probe(
            statbl_id=statbl_id,
            service_code=service_code,
            page_index=page_index,
            page_size=page_size,
            dtacycle_cd=dtacycle_cd,
        )
        return f"{probe['url']}?{urlencode(probe['params'])}"

    def fetch_price_index_data(
        self,
        statbl_id: str,
        service_code: str = "SttsApiTblData.do",
        dtacycle_cd: str = "MM",
    ) -> list[dict]:
        if not RONE_API_KEY:
            raise SourceError("RONE_API_KEY is missing.")

        page_index = 1
        page_size = 1000
        rows: list[dict] = []
        total_count = None
        while True:
            probe = self.build_probe(
                statbl_id=statbl_id,
                service_code=service_code,
                page_index=page_index,
                page_size=page_size,
                dtacycle_cd=dtacycle_cd,
            )
            response = requests.get(str(probe["url"]), params=probe["params"], timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            page_rows, page_total = self.extract_rows(payload)
            if total_count is None:
                total_count = page_total
            if not page_rows:
                break
            rows.extend(page_rows)
            if total_count is None or len(rows) >= total_count or len(page_rows) < page_size:
                break
            page_index += 1
        return rows

    def fetch_indicator_rows(self, indicator_id: str, candidate_params: dict[str, str] | None = None) -> list[dict]:
        candidates = self.build_service_code_candidates(indicator_id=indicator_id, candidate_params=candidate_params)
        if not candidates:
            raise SourceError(f"No R-ONE candidate is configured for {indicator_id}.")

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                raw_rows = self.fetch_price_index_data(
                    statbl_id=candidate["statbl_id"],
                    service_code=candidate["service_code"],
                    dtacycle_cd=candidate["dtacycle_cd"],
                )
                standardized = self.standardize_rows(
                    indicator_id=indicator_id,
                    rows=raw_rows,
                    region_name=candidate["region_name"],
                    region_cls_id=candidate.get("region_cls_id"),
                    item_id=candidate.get("item_id"),
                )
                if not standardized.empty:
                    return self.to_observation_rows(indicator_id=indicator_id, standardized=standardized, candidate=candidate)
            except Exception as exc:  # pragma: no cover
                last_error = exc
        raise SourceError(str(last_error) if last_error else f"R-ONE returned no standardized rows for {indicator_id}.")

    def extract_rows(self, payload: dict) -> tuple[list[dict], int | None]:
        if not isinstance(payload, dict):
            return [], None
        wrapper = payload.get("SttsApiTblData")
        if not isinstance(wrapper, list) or len(wrapper) < 2:
            return [], None
        head_block = wrapper[0].get("head", []) if isinstance(wrapper[0], dict) else []
        row_block = wrapper[1].get("row", []) if isinstance(wrapper[1], dict) else []
        total_count = None
        if isinstance(head_block, list):
            for item in head_block:
                if isinstance(item, dict) and "list_total_count" in item:
                    total_count = int(item["list_total_count"])
                    break
        rows = [row for row in row_block if isinstance(row, dict)] if isinstance(row_block, list) else []
        return rows, total_count

    def standardize_rows(
        self,
        indicator_id: str,
        rows: list[dict],
        region_name: str = DEFAULT_REGION_NAME,
        region_cls_id: str | None = DEFAULT_REGION_CLS_ID,
        item_id: str | None = DEFAULT_ITEM_ID,
    ) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=["indicator_id", "date", "region", "value", "unit", "source"])

        frame = pd.DataFrame(rows)
        required = {"WRTTIME_IDTFR_ID", "DTA_VAL", "CLS_NM"}
        if not required.issubset(frame.columns):
            return pd.DataFrame(columns=["indicator_id", "date", "region", "value", "unit", "source"])

        if region_cls_id and "CLS_ID" in frame.columns:
            region_filtered = frame[frame["CLS_ID"].astype(str) == str(region_cls_id)].copy()
            if not region_filtered.empty:
                frame = region_filtered
            else:
                frame = frame[frame["CLS_NM"].astype(str).str.strip() == region_name].copy()
        else:
            frame = frame[frame["CLS_NM"].astype(str).str.strip() == region_name].copy()

        if item_id and "ITM_ID" in frame.columns:
            item_filtered = frame[frame["ITM_ID"].astype(str) == str(item_id)].copy()
            if not item_filtered.empty:
                frame = item_filtered
            elif "ITM_NM" in frame.columns:
                frame = frame[frame["ITM_NM"].astype(str).str.contains("지수", na=False)].copy()
        elif "ITM_NM" in frame.columns:
            frame = frame[frame["ITM_NM"].astype(str).str.contains("지수", na=False)].copy()

        date_token = frame["WRTTIME_IDTFR_ID"].astype(str).str.replace(r"\D", "", regex=True).str[:6]
        frame["date"] = pd.to_datetime(date_token + "01", format="%Y%m%d", errors="coerce")
        frame["value"] = pd.to_numeric(frame["DTA_VAL"], errors="coerce")
        frame = frame.dropna(subset=["date", "value"])
        frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")

        region_series = frame["CLS_NM"].astype(str).str.strip()
        if region_cls_id and "CLS_ID" in frame.columns:
            region_series = region_series.where(frame["CLS_ID"].astype(str) != str(region_cls_id), DEFAULT_REGION_NAME)

        standardized = pd.DataFrame(
            {
                "indicator_id": indicator_id,
                "date": frame["date"].dt.strftime("%Y-%m-%d"),
                "region": region_series,
                "value": frame["value"].astype(float),
                "unit": frame["UI_NM"].fillna(INDICATORS[indicator_id].unit),
                "source": "R-ONE",
            }
        )
        return standardized.reset_index(drop=True)

    def to_observation_rows(self, indicator_id: str, standardized: pd.DataFrame, candidate: dict[str, str]) -> list[dict]:
        definition = INDICATORS[indicator_id]
        rows: list[dict] = []
        for row in standardized.to_dict(orient="records"):
            rows.append(
                {
                    "indicator_code": definition.indicator_id,
                    "indicator_name": definition.name_kr,
                    "bucket": definition.category,
                    "observation_date": row["date"],
                    "region_code": "KR",
                    "region_name": row["region"],
                    "frequency": definition.frequency,
                    "unit": row["unit"],
                    "value": float(row["value"]),
                    "source": "R-ONE",
                    "source_series_code": candidate["statbl_id"],
                    "meta_json": {
                        "provider": "R-ONE",
                        "direction": definition.direction,
                        "hanssem_logic": definition.hanssem_logic,
                        "service_code": candidate["service_code"],
                        "statbl_id": candidate["statbl_id"],
                    },
                }
            )
        return rows

    def build_candidate_metadata(self, indicator_id: str) -> dict:
        definition = INDICATORS[indicator_id]
        return {
            "indicator_id": indicator_id,
            "source_detail": asdict(definition.source_detail),
            "fallback_source": asdict(definition.fallback_source) if definition.fallback_source else {},
            "service_code_candidates": self.build_service_code_candidates(indicator_id=indicator_id),
        }
