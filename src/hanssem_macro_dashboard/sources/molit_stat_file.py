from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from hanssem_macro_dashboard.config import INDICATORS, ROOT_DIR
from hanssem_macro_dashboard.sources.base import SourceError

RAW_BASE_DIR = ROOT_DIR / "data" / "raw" / "molit"
PROCESSED_BASE_DIR = ROOT_DIR / "data" / "processed"
MOLIT_META_URL = "https://stat.molit.go.kr/portal/cate/statMetaView.do"
MOLIT_DOWNLOAD_URL = "https://stat.molit.go.kr/portal/common/downLoadFile.do"


def raw_dir_for(indicator_id: str) -> Path:
    return RAW_BASE_DIR / indicator_id


def processed_path_for(indicator_id: str) -> Path:
    return PROCESSED_BASE_DIR / f"{indicator_id}_sample.csv"


class MolitStatFileSource:
    def __init__(
        self,
        indicator_ids: list[str] | None = None,
        timeout: int = 30,
        retry_delays: list[int] | None = None,
    ):
        self.indicator_ids = indicator_ids or ["completion_volume"]
        self.timeout = timeout
        self.retry_delays = retry_delays or [5, 15, 30]

    def fetch(self) -> Iterable[dict]:
        rows: list[dict] = []
        for indicator_id in self.indicator_ids:
            rows.extend(self.fetch_indicator_rows(indicator_id))
        return rows

    def fetch_indicator_rows(self, indicator_id: str) -> list[dict]:
        definition = INDICATORS[indicator_id]
        fallback = definition.fallback_source
        if fallback is None:
            raise SourceError(f"{indicator_id} fallback_source is not configured.")

        catalog = self.fetch_file_catalog(h_rs_id=fallback.h_rs_id, h_form_id=fallback.h_form_id)
        entry = self.choose_preferred_entry(indicator_id=indicator_id, catalog=catalog)
        if not entry:
            raise SourceError(f"No preferred MOLIT fallback file candidate was found for {indicator_id}.")

        local_path = self.download_entry(indicator_id=indicator_id, entry=entry)
        standardized = self.parse_indicator_workbook(indicator_id=indicator_id, path=local_path)
        if standardized.empty:
            raise SourceError("Fallback workbook was downloaded but no standardized rows were produced.")

        processed_path = processed_path_for(indicator_id)
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        standardized.to_csv(processed_path, index=False, encoding="utf-8-sig")
        return self.to_observation_rows(indicator_id=indicator_id, standardized=standardized)

    def fetch_file_catalog(self, h_rs_id: str, h_form_id: str) -> list[dict]:
        params = {"hRsId": h_rs_id, "hFormId": h_form_id}
        response = self.request_with_retry(MOLIT_META_URL, params=params)
        response.raise_for_status()
        matches = re.findall(r"downFile\('([^']+)','([^']+)','([^']+)','([^']+)'\)", response.text)
        return [
            {
                "original_name": original_name,
                "real_name": real_name,
                "midpath": midpath,
                "frame_name": frame_name,
                "extension": Path(real_name).suffix.lower(),
            }
            for original_name, real_name, midpath, frame_name in matches
        ]

    def choose_preferred_entry(self, indicator_id: str, catalog: list[dict]) -> dict | None:
        if indicator_id == "completion_volume":
            preferences = [
                lambda item: "준공_연도별,월별,지역별.xlsx" in item["original_name"],
                lambda item: item["original_name"].endswith("준공실적(홈페이지게시용).xls"),
                lambda item: item["extension"] == ".xlsx",
            ]
        elif indicator_id == "housing_permits":
            preferences = [
                lambda item: "인허가_연도별,월별,지역별.xlsx" in item["original_name"],
                lambda item: item["original_name"].endswith("인허가실적(홈페이지게시용).xls"),
                lambda item: item["extension"] == ".xlsx",
            ]
        elif indicator_id == "unsold_units":
            preferences = [
                lambda item: item["original_name"].startswith("미분양주택현황") and item["extension"] == ".xlsx",
                lambda item: "미분양주택현황" in item["original_name"] and item["extension"] == ".xlsx",
                lambda item: item["extension"] == ".xlsx",
            ]
        else:
            preferences = [lambda item: item["extension"] in {".xlsx", ".zip", ".xls"}]

        for predicate in preferences:
            for item in catalog:
                if predicate(item):
                    return item
        return catalog[0] if catalog else None

    def download_entry(self, indicator_id: str, entry: dict) -> Path:
        directory = raw_dir_for(indicator_id)
        directory.mkdir(parents=True, exist_ok=True)
        params = {
            "oFileName": entry["original_name"],
            "rFileName": entry["real_name"],
            "midpath": entry["midpath"],
        }
        response = self.request_with_retry(MOLIT_DOWNLOAD_URL, params=params)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type and "파일이없습니다" in response.text:
            raise SourceError("MOLIT fallback file download returned 'file not found'.")

        path = directory / entry["real_name"]
        path.write_bytes(response.content)
        return path

    def request_with_retry(self, url: str, params: dict) -> requests.Response:
        errors: list[str] = []
        total_attempts = len(self.retry_delays) + 1
        for attempt in range(total_attempts):
            try:
                return requests.get(url, params=params, timeout=self.timeout)
            except (requests.RequestException, TimeoutError, ConnectionResetError) as exc:
                errors.append(f"attempt={attempt + 1}: {exc!r}")
                if attempt >= len(self.retry_delays):
                    raise SourceError("; ".join(errors)) from exc
                time.sleep(self.retry_delays[attempt])
        raise SourceError("; ".join(errors))

    def parse_indicator_workbook(self, indicator_id: str, path: Path) -> pd.DataFrame:
        if path.suffix.lower() != ".xlsx":
            raise SourceError(f"Unsupported fallback file format for current runtime: {path.suffix}")
        if indicator_id == "completion_volume":
            return self.parse_completion_volume_workbook(path)
        if indicator_id == "housing_permits":
            return self.parse_housing_permits_workbook(path)
        if indicator_id == "unsold_units":
            return self.parse_unsold_units_workbook(path)
        raise SourceError(f"No workbook parser is implemented for {indicator_id}.")

    def parse_completion_volume_workbook(self, path: Path) -> pd.DataFrame:
        rows: list[dict] = []
        excel = pd.ExcelFile(path)
        for sheet_name in excel.sheet_names:
            year = self.parse_sheet_year(sheet_name)
            if year is None:
                continue
            sheet = pd.read_excel(path, sheet_name=sheet_name, header=None)
            if sheet.shape[0] < 6:
                continue

            month_columns = self.extract_month_columns(sheet.iloc[2].tolist())
            total_row = sheet.iloc[3] if len(sheet) > 3 else None
            if total_row is not None and str(total_row.iloc[0]).strip() == "년월계":
                rows.extend(
                    self.build_monthly_rows(
                        indicator_id="completion_volume",
                        year=year,
                        region="전국",
                        month_columns=month_columns,
                        row_values=total_row,
                        unit="호",
                    )
                )

            for row_idx in range(5, len(sheet)):
                region = self.clean_region(sheet.iat[row_idx, 0])
                if not region:
                    continue
                rows.extend(
                    self.build_monthly_rows(
                        indicator_id="completion_volume",
                        year=year,
                        region=region,
                        month_columns=month_columns,
                        row_values=sheet.iloc[row_idx],
                        unit="호",
                    )
                )

        return self.standardized_frame(rows)

    def parse_housing_permits_workbook(self, path: Path) -> pd.DataFrame:
        rows: list[dict] = []
        excel = pd.ExcelFile(path)
        for sheet_name in excel.sheet_names:
            year = self.parse_sheet_year(sheet_name)
            if year is None:
                continue
            sheet = pd.read_excel(path, sheet_name=sheet_name, header=None)
            if sheet.shape[0] < 7:
                continue

            month_columns = self.extract_month_columns(sheet.iloc[2].tolist())
            total_monthly_row = sheet.iloc[3] if len(sheet) > 3 else None
            total_cumulative_row = sheet.iloc[4] if len(sheet) > 4 else None
            if (
                total_monthly_row is not None
                and total_cumulative_row is not None
                and str(total_monthly_row.iloc[0]).strip() == "년월계"
                and "누계" in str(total_cumulative_row.iloc[0]).strip()
            ):
                rows.extend(
                    self.build_housing_permits_rows(
                        year=year,
                        region="전국",
                        month_columns=month_columns,
                        monthly_row=total_monthly_row,
                        cumulative_row=total_cumulative_row,
                    )
                )

            row_idx = 5
            while row_idx < len(sheet):
                region = self.clean_region(sheet.iat[row_idx, 0])
                if not region:
                    row_idx += 1
                    continue
                monthly_row = sheet.iloc[row_idx]
                next_row = sheet.iloc[row_idx + 1] if row_idx + 1 < len(sheet) else None
                cumulative_row = None
                if next_row is not None and pd.isna(next_row.iloc[0]):
                    cumulative_row = next_row
                rows.extend(
                    self.build_housing_permits_rows(
                        year=year,
                        region=region,
                        month_columns=month_columns,
                        monthly_row=monthly_row,
                        cumulative_row=cumulative_row,
                    )
                )
                row_idx += 2 if cumulative_row is not None else 1

        return self.standardized_frame(rows)

    def parse_unsold_units_workbook(self, path: Path) -> pd.DataFrame:
        sheet = pd.read_excel(path, sheet_name="총괄★", header=None)
        if sheet.empty or sheet.shape[0] < 5:
            return self.standardized_frame([])

        header_row = sheet.iloc[2].tolist()
        total_row = sheet.iloc[4].tolist()
        completed_row = sheet.iloc[7].tolist() if sheet.shape[0] > 7 else []

        rows: list[dict] = []
        for col_idx, header in enumerate(header_row[1:], start=1):
            parsed = self.parse_compact_period(str(header).strip())
            if parsed is None:
                continue
            year, month = parsed
            total_value = self.safe_float(total_row[col_idx] if col_idx < len(total_row) else None)
            if total_value is not None:
                rows.append(
                    {
                        "indicator_id": "unsold_units",
                        "date": f"{year:04d}-{month:02d}-01",
                        "region": "전국",
                        "value": total_value,
                        "unit": "호",
                        "source": "MOLIT_STAT_FILE",
                    }
                )
            completed_value = self.safe_float(completed_row[col_idx] if col_idx < len(completed_row) else None)
            if completed_value is not None:
                rows.append(
                    {
                        "indicator_id": "unsold_units_completed",
                        "date": f"{year:04d}-{month:02d}-01",
                        "region": "전국",
                        "value": completed_value,
                        "unit": "호",
                        "source": "MOLIT_STAT_FILE",
                    }
                )
        combined = self.standardized_frame(rows)
        return combined[combined["indicator_id"] == "unsold_units"].reset_index(drop=True)

    def build_monthly_rows(
        self,
        indicator_id: str,
        year: int,
        region: str,
        month_columns: list[tuple[int, int]],
        row_values: pd.Series,
        unit: str,
    ) -> list[dict]:
        rows: list[dict] = []
        for col_idx, month in month_columns:
            numeric_value = self.safe_float(row_values.iloc[col_idx] if col_idx < len(row_values) else None)
            if numeric_value is None:
                continue
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "date": f"{year:04d}-{month:02d}-01",
                    "region": region,
                    "value": numeric_value,
                    "unit": unit,
                    "source": "MOLIT_STAT_FILE",
                }
            )
        return rows

    def build_housing_permits_rows(
        self,
        year: int,
        region: str,
        month_columns: list[tuple[int, int]],
        monthly_row: pd.Series | None,
        cumulative_row: pd.Series | None,
    ) -> list[dict]:
        rows: list[dict] = []
        previous_cumulative: float | None = None
        for col_idx, month in month_columns:
            monthly_value = self.safe_float(monthly_row.iloc[col_idx] if monthly_row is not None and col_idx < len(monthly_row) else None)
            cumulative_value = self.safe_float(
                cumulative_row.iloc[col_idx] if cumulative_row is not None and col_idx < len(cumulative_row) else None
            )
            derived_value: float | None
            if cumulative_value is not None:
                derived_value = cumulative_value if previous_cumulative is None or month == 1 else cumulative_value - previous_cumulative
                previous_cumulative = cumulative_value
            else:
                derived_value = monthly_value
                if monthly_value is not None:
                    previous_cumulative = (previous_cumulative or 0.0) + monthly_value

            if derived_value is None:
                continue
            row = {
                "indicator_id": "housing_permits",
                "date": f"{year:04d}-{month:02d}-01",
                "region": region,
                "value": derived_value,
                "unit": "호",
                "source": "MOLIT_STAT_FILE",
            }
            if cumulative_value is not None:
                row["cumulative_value"] = cumulative_value
            if monthly_value is not None:
                row["raw_value"] = monthly_value
            if cumulative_value is not None and derived_value < 0:
                row["warning_negative_monthly"] = True
            if monthly_value is not None and derived_value != monthly_value:
                row["monthly_diff_vs_sheet"] = derived_value - monthly_value
            rows.append(row)
        return rows

    def to_observation_rows(self, indicator_id: str, standardized: pd.DataFrame) -> list[dict]:
        definition = INDICATORS[indicator_id]
        fallback = definition.fallback_source
        nationwide = standardized[standardized["region"] == "전국"].copy()
        rows: list[dict] = []
        for row in nationwide.to_dict(orient="records"):
            meta = {
                "provider": "MOLIT_STAT",
                "fallback": True,
                "direction": definition.direction,
                "hanssem_logic": definition.hanssem_logic,
            }
            if "cumulative_value" in row and pd.notna(row["cumulative_value"]):
                meta["cumulative_value"] = float(row["cumulative_value"])
            if "raw_value" in row and pd.notna(row["raw_value"]):
                meta["raw_value"] = float(row["raw_value"])
            if "warning_negative_monthly" in row and pd.notna(row["warning_negative_monthly"]):
                meta["warning_negative_monthly"] = bool(row["warning_negative_monthly"])
            if "monthly_diff_vs_sheet" in row and pd.notna(row["monthly_diff_vs_sheet"]):
                meta["monthly_diff_vs_sheet"] = float(row["monthly_diff_vs_sheet"])
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
                    "source": "MOLIT_STAT_FILE",
                    "source_series_code": f"hRsId={fallback.h_rs_id}&hFormId={fallback.h_form_id}",
                    "meta_json": meta,
                }
            )
        return rows

    def standardized_frame(self, rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=["indicator_id", "date", "region", "value", "unit", "source"])
        frame = pd.DataFrame(rows)
        frame = frame.drop_duplicates(subset=["indicator_id", "date", "region"], keep="last")
        frame = frame.sort_values(["indicator_id", "date", "region"]).reset_index(drop=True)
        return frame

    def extract_month_columns(self, header_row: list) -> list[tuple[int, int]]:
        month_columns: list[tuple[int, int]] = []
        for col_idx, header in enumerate(header_row):
            month = self.parse_month_header(str(header).strip())
            if month is not None:
                month_columns.append((col_idx, month))
        return month_columns

    def parse_sheet_year(self, sheet_name: str) -> int | None:
        match = re.search(r"(\d{2})년", str(sheet_name))
        if not match:
            return None
        year_two_digits = int(match.group(1))
        current_two_digits = pd.Timestamp.today().year % 100
        century = 2000 if year_two_digits <= current_two_digits + 1 else 1900
        return century + year_two_digits

    def parse_month_header(self, text: str) -> int | None:
        match = re.search(r"(\d{1,2})월", text)
        if not match:
            return None
        month = int(match.group(1))
        return month if 1 <= month <= 12 else None

    def parse_compact_period(self, text: str) -> tuple[int, int] | None:
        match = re.search(r"(\d{2})\.(\d{1,2})", text)
        if not match:
            return None
        return 2000 + int(match.group(1)), int(match.group(2))

    def clean_region(self, value) -> str | None:
        if pd.isna(value):
            return None
        text = str(value).strip()
        if not text:
            return None
        if text in {"구분", "년월계", "(년누계)"}:
            return None
        if text.startswith("("):
            return None
        return text

    def safe_float(self, value) -> float | None:
        if pd.isna(value):
            return None
        text = str(value).replace(",", "").replace(" ", "").strip()
        if text in {"", "-", "nan"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
