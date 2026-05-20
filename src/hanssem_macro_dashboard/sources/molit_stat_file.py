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
MOLIT_STAT_VIEW_URL = "https://stat.molit.go.kr/portal/cate/statView.do"
MOLIT_STAT_COLUMNS_URL = "https://stat.molit.go.kr/portal/stat/columns.do"
MOLIT_STAT_DATA_URL = "https://stat.molit.go.kr/portal/stat/data.do"
MOLIT_DOWNLOAD_URL = "https://stat.molit.go.kr/portal/common/downLoadFile.do"

NATIONWIDE = "전국"
MONTHLY_POSTING_MARKER = "홈페이지게시용"


VALID_UNSOLD_REGIONS = {
    NATIONWIDE,
    "전국",
    "서울",
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "세종",
    "경기",
    "강원",
    "충북",
    "충남",
    "전북",
    "전남",
    "경북",
    "경남",
    "제주",
    "수도권",
    "지방",
}
VALID_MOLIT_REGIONS = {
    NATIONWIDE,
    "전국",
    "서울",
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "세종",
    "경기",
    "강원",
    "충북",
    "충남",
    "전북",
    "전남",
    "경북",
    "경남",
    "제주",
}


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

        if indicator_id in {"completion_volume", "housing_permits"}:
            standardized = self.fetch_indicator_rows_from_stat_api(indicator_id=indicator_id)
            if not standardized.empty:
                processed_path = processed_path_for(indicator_id)
                processed_path.parent.mkdir(parents=True, exist_ok=True)
                standardized.to_csv(processed_path, index=False, encoding="utf-8-sig")
                return self.to_observation_rows(indicator_id=indicator_id, standardized=standardized)

        catalog = self.fetch_file_catalog(h_rs_id=fallback.h_rs_id, h_form_id=fallback.h_form_id)
        ranked_entries = self.rank_catalog_entries(indicator_id=indicator_id, catalog=catalog)
        if not ranked_entries:
            raise SourceError(f"No preferred MOLIT fallback file candidate was found for {indicator_id}.")
        errors: list[str] = []
        for entry in ranked_entries:
            try:
                local_path = self.download_entry(indicator_id=indicator_id, entry=entry)
                standardized = self.parse_indicator_workbook(indicator_id=indicator_id, path=local_path)
                if standardized.empty:
                    errors.append(f"{entry['real_name']}: no standardized rows")
                    continue

                processed_path = processed_path_for(indicator_id)
                processed_path.parent.mkdir(parents=True, exist_ok=True)
                standardized.to_csv(processed_path, index=False, encoding="utf-8-sig")
                return self.to_observation_rows(indicator_id=indicator_id, standardized=standardized)
            except Exception as exc:
                errors.append(f"{entry['real_name']}: {exc}")

        raise SourceError("Fallback workbook was downloaded but no standardized rows were produced. " + " | ".join(errors[:5]))

    def fetch_latest_available_period(self, indicator_id: str) -> str:
        definition = INDICATORS[indicator_id]
        fallback = definition.fallback_source
        if fallback is None:
            return ""
        params = {"hRsId": fallback.h_rs_id, "hFormId": fallback.h_form_id}
        response = self.request_with_retry(MOLIT_STAT_VIEW_URL, params=params)
        response.raise_for_status()
        periods = sorted(set(re.findall(r"20\d{4}", response.text)))
        return periods[-1] if periods else ""

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

    def fetch_indicator_rows_from_stat_api(self, indicator_id: str) -> pd.DataFrame:
        definition = INDICATORS[indicator_id]
        fallback = definition.fallback_source
        if fallback is None or not fallback.h_form_id:
            return pd.DataFrame()

        latest_period = self.fetch_latest_available_period(indicator_id)
        if not latest_period:
            return pd.DataFrame()

        start_period = self.shift_period(latest_period, months=23)
        columns = self.fetch_stat_columns(form_id=fallback.h_form_id)
        data = self.fetch_stat_data(
            form_id=fallback.h_form_id,
            start_period=start_period,
            end_period=latest_period,
        )
        return self.standardize_stat_rows(
            indicator_id=indicator_id,
            columns=columns,
            data=data,
        )

    def fetch_stat_columns(self, form_id: str) -> list[dict]:
        response = self.request_with_retry(
            MOLIT_STAT_COLUMNS_URL,
            params={"formId": form_id, "styleNum": "1"},
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("data", []) if isinstance(payload, dict) else []

    def fetch_stat_data(self, form_id: str, start_period: str, end_period: str) -> list[dict]:
        response = self.request_with_retry(
            MOLIT_STAT_DATA_URL,
            params={
                "formId": form_id,
                "styleNum": "1",
                "apprYn": "Y",
                "startDate": start_period,
                "endDate": end_period,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("data", []) if isinstance(payload, dict) else []

    def standardize_stat_rows(self, indicator_id: str, columns: list[dict], data: list[dict]) -> pd.DataFrame:
        if not columns or not data:
            return pd.DataFrame()

        dimension_map: dict[str, str] = {}
        metric_keys: list[str] = []
        for column in columns:
            key = str(column.get("DATA_DIV_ID"))
            data_div = str(column.get("DATA_DIV", ""))
            name = str(column.get("DATA_DIV_NM", "")).strip()
            if data_div == "D":
                dimension_map[key] = name
            elif data_div == "M":
                metric_keys.append(key)

        if not metric_keys:
            return pd.DataFrame()
        value_key = metric_keys[0]

        rows: list[dict] = []
        for record in data:
            period_text = str(record.get("0", "")).strip()
            region = self.clean_region(str(record.get("3", "")).strip(), valid_regions=VALID_MOLIT_REGIONS)
            value = self.safe_float(record.get(value_key))
            parsed_period = self.parse_molit_period_label(period_text)
            if parsed_period is None or not region or value is None:
                continue

            group_name = self.normalize_space(str(record.get("1", "")).strip())
            sector_name = self.normalize_space(str(record.get("2", "")).strip())
            if not self.is_total_row(group_name, sector_name):
                continue

            rows.append(
                {
                    "indicator_id": indicator_id,
                    "date": f"{parsed_period[:4]}-{parsed_period[4:6]}-01",
                    "region": region,
                    "value": value,
                    "unit": "호",
                    "source": "MOLIT_STAT_FILE",
                }
            )

        return self.standardized_frame(rows)

    def parse_molit_period_label(self, text: str) -> str | None:
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 6:
            return digits[:6]
        return None

    def normalize_space(self, value: str) -> str:
        return re.sub(r"\s+", "", value)

    def is_total_row(self, group_name: str, sector_name: str) -> bool:
        total_tokens = {"총계", "총계", "총계", "총계", "총계", "총계", "총계", "총계", "총계", "총계"}
        normalized_group = self.normalize_space(group_name)
        normalized_sector = self.normalize_space(sector_name)
        return normalized_group in {"총계", "총계", "총계", "총계", "총계"} and normalized_sector in {"총계", "총계", "총계", "총계", "총계"}

    def shift_period(self, period: str, months: int) -> str:
        year = int(period[:4])
        month = int(period[4:6])
        total = year * 12 + (month - 1) - months
        shifted_year = total // 12
        shifted_month = total % 12 + 1
        return f"{shifted_year:04d}{shifted_month:02d}"

    def choose_preferred_entry(self, indicator_id: str, catalog: list[dict]) -> dict | None:
        if not catalog:
            return None

        ranked = self.rank_catalog_entries(indicator_id=indicator_id, catalog=catalog)
        return ranked[0] if ranked else None

    def rank_catalog_entries(self, indicator_id: str, catalog: list[dict]) -> list[dict]:
        return sorted(
            catalog,
            key=lambda item: self.score_catalog_entry(indicator_id=indicator_id, item=item),
            reverse=True,
        )

    def score_catalog_entry(self, indicator_id: str, item: dict) -> tuple[int, int, int, int, str]:
        name = f"{item.get('original_name', '')} {item.get('real_name', '')}"
        extension = str(item.get("extension", "")).lower()
        monthly_period = self.extract_monthly_period(name)
        period_score = monthly_period or self.extract_annual_period(name)

        posting_score = 0
        if MONTHLY_POSTING_MARKER in name:
            posting_score += 1000

        if indicator_id in {"completion_volume", "housing_permits"}:
            if monthly_period:
                posting_score += 500
            if "연도별" in name and "월별" in name and "지역별" in name:
                posting_score += 200
        elif indicator_id == "unsold_units":
            if monthly_period:
                posting_score += 500
            if "통계누리" in name:
                posting_score += 200

        extension_score = {".xlsx": 30, ".xls": 20, ".csv": 10, ".txt": 5}.get(extension, 0)
        monthly_flag = 1 if monthly_period else 0
        return (posting_score, monthly_flag, period_score, extension_score, name)

    def extract_monthly_period(self, text: str) -> int:
        monthly_patterns = [
            r"(\d{2,4})년\s*(\d{1,2})월",
            r"(\d{4})년(\d{1,2})월말",
            r"(\d{2})\.(\d{1,2})월",
        ]
        for pattern in monthly_patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            year = self.normalize_year(match.group(1))
            month = int(match.group(2))
            if 1 <= month <= 12:
                return year * 100 + month
        return 0

    def extract_annual_period(self, text: str) -> int:
        range_match = re.search(r"(\d{2,4})년\s*-\s*(\d{2,4})년", text)
        if range_match:
            return self.normalize_year(range_match.group(2)) * 100 + 12
        single_match = re.search(r"(\d{2,4})년", text)
        if single_match:
            return self.normalize_year(single_match.group(1)) * 100 + 12
        return 0

    def normalize_year(self, value: str) -> int:
        year = int(value)
        if year >= 1000:
            return year
        current_two_digits = pd.Timestamp.today().year % 100
        century = 2000 if year <= current_two_digits + 1 else 1900
        return century + year

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
        if "text/html" in content_type and "file not found" in response.text.lower():
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
        if indicator_id == "completion_volume":
            return self.parse_completion_volume_workbook(path)
        if indicator_id == "housing_permits":
            return self.parse_housing_permits_workbook(path)
        if indicator_id == "unsold_units":
            return self.parse_unsold_units_workbook(path)
        raise SourceError(f"No workbook parser is implemented for {indicator_id}.")

    def parse_completion_volume_workbook(self, path: Path) -> pd.DataFrame:
        rows: list[dict] = []
        year_hint = self.parse_year_from_filename(path.name)
        for sheet_name, sheet in self.iter_sheets(path):
            year = self.parse_sheet_year(sheet_name) or year_hint
            if year is None or sheet.shape[0] < 4:
                continue

            header_row_idx, month_columns = self.find_month_columns(sheet)
            if header_row_idx is None or not month_columns:
                continue

            total_row_idx = header_row_idx + 1
            if total_row_idx < len(sheet):
                rows.extend(
                    self.build_monthly_rows(
                        indicator_id="completion_volume",
                        year=year,
                        region=NATIONWIDE,
                        month_columns=month_columns,
                        row_values=sheet.iloc[total_row_idx],
                        unit="호",
                    )
                )

            region_start = header_row_idx + 3
            for row_idx in range(region_start, len(sheet)):
                region = self.clean_region(sheet.iat[row_idx, 0], valid_regions=VALID_MOLIT_REGIONS)
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
        year_hint = self.parse_year_from_filename(path.name)
        for sheet_name, sheet in self.iter_sheets(path):
            year = self.parse_sheet_year(sheet_name) or year_hint
            if year is None or sheet.shape[0] < 5:
                continue

            header_row_idx, month_columns = self.find_month_columns(sheet)
            if header_row_idx is None or not month_columns:
                continue

            total_monthly_idx = header_row_idx + 1
            total_cumulative_idx = header_row_idx + 2
            if total_monthly_idx < len(sheet):
                monthly_row = sheet.iloc[total_monthly_idx]
                cumulative_row = sheet.iloc[total_cumulative_idx] if total_cumulative_idx < len(sheet) else None
                rows.extend(
                    self.build_housing_permits_rows(
                        year=year,
                        region=NATIONWIDE,
                        month_columns=month_columns,
                        monthly_row=monthly_row,
                        cumulative_row=cumulative_row,
                    )
                )

            row_idx = header_row_idx + 3
            while row_idx < len(sheet):
                region = self.clean_region(sheet.iat[row_idx, 0], valid_regions=VALID_MOLIT_REGIONS)
                if not region:
                    row_idx += 1
                    continue
                monthly_row = sheet.iloc[row_idx]
                next_row = sheet.iloc[row_idx + 1] if row_idx + 1 < len(sheet) else None
                cumulative_row = None
                if next_row is not None and self.is_cumulative_row(next_row):
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
        rows: list[dict] = []
        for _, sheet in self.iter_sheets(path):
            if sheet.empty or sheet.shape[0] < 5:
                continue

            header_row_idx = self.find_best_period_header_row(sheet)
            if header_row_idx is None:
                continue

            header_row = sheet.iloc[header_row_idx].tolist()
            for row_idx in range(header_row_idx + 1, len(sheet)):
                region = self.clean_region(
                    sheet.iat[row_idx, 0] if sheet.shape[1] > 0 else None,
                    valid_regions=VALID_UNSOLD_REGIONS,
                )
                if not region:
                    continue
                row_values = sheet.iloc[row_idx].tolist()
                for col_idx, header in enumerate(header_row[1:], start=1):
                    parsed = self.parse_compact_period(str(header).strip())
                    if parsed is None:
                        continue
                    year, month = parsed
                    value = self.safe_float(row_values[col_idx] if col_idx < len(row_values) else None)
                    if value is None:
                        continue
                    rows.append(
                        {
                            "indicator_id": "unsold_units",
                            "date": f"{year:04d}-{month:02d}-01",
                            "region": region,
                            "value": value,
                            "unit": "호",
                            "source": "MOLIT_STAT_FILE",
                        }
                    )
        combined = self.standardized_frame(rows)
        return combined[combined["indicator_id"] == "unsold_units"].reset_index(drop=True)

    def iter_sheets(self, path: Path) -> list[tuple[str, pd.DataFrame]]:
        suffix = path.suffix.lower()
        engines: list[str | None]
        if suffix == ".xls":
            engines = ["xlrd", None]
        elif suffix == ".xlsx":
            engines = ["openpyxl", "xlrd", None]
        else:
            engines = [None]

        last_error: Exception | None = None
        for engine in engines:
            try:
                workbook = pd.ExcelFile(path, engine=engine)
                return [
                    (
                        sheet_name,
                        pd.read_excel(path, sheet_name=sheet_name, header=None, engine=engine),
                    )
                    for sheet_name in workbook.sheet_names
                ]
            except Exception as exc:
                last_error = exc

        try:
            tables = pd.read_html(path)
        except Exception as exc:
            raise SourceError(f"Unsupported MOLIT workbook format for {path.name}: {last_error or exc}") from exc
        return [(f"table_{idx}", table) for idx, table in enumerate(tables, start=1)]

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
            cumulative_value = self.safe_float(cumulative_row.iloc[col_idx] if cumulative_row is not None and col_idx < len(cumulative_row) else None)

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
        rows: list[dict] = []
        for row in standardized.to_dict(orient="records"):
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
                    "region_code": "KR" if row["region"] == NATIONWIDE else row["region"],
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
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date", "value", "region"])
        frame["region"] = frame["region"].replace({"전국계": NATIONWIDE, "총계": NATIONWIDE})
        frame = frame.drop_duplicates(subset=["indicator_id", "date", "region"], keep="last")
        frame = frame.sort_values(["indicator_id", "date", "region"]).reset_index(drop=True)
        frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
        return frame

    def find_month_columns(self, sheet: pd.DataFrame) -> tuple[int | None, list[tuple[int, int]]]:
        for row_idx in range(min(6, len(sheet))):
            month_columns = self.extract_month_columns(sheet.iloc[row_idx].tolist())
            if month_columns:
                return row_idx, month_columns
        return None, []

    def find_best_period_header_row(self, sheet: pd.DataFrame) -> int | None:
        best_row: int | None = None
        best_count = 0
        for row_idx in range(min(8, len(sheet))):
            count = 0
            for value in sheet.iloc[row_idx].tolist():
                if self.parse_compact_period(str(value).strip()) is not None:
                    count += 1
            if count > best_count:
                best_row = row_idx
                best_count = count
        return best_row if best_count > 0 else None

    def extract_month_columns(self, header_row: list) -> list[tuple[int, int]]:
        month_columns: list[tuple[int, int]] = []
        seen: set[int] = set()
        for col_idx, header in enumerate(header_row):
            month = self.parse_month_header(str(header).strip())
            if month is None or month in seen:
                continue
            seen.add(month)
            month_columns.append((col_idx, month))
        return month_columns

    def parse_sheet_year(self, sheet_name: str) -> int | None:
        match = re.search(r"(\d{2,4})\s*년", str(sheet_name))
        if match:
            return self.normalize_year(match.group(1))
        match = re.search(r"(\d{2})$", str(sheet_name))
        if match:
            return self.normalize_year(match.group(1))
        return None

    def parse_year_from_filename(self, filename: str) -> int | None:
        match = re.search(r"(\d{2,4})\s*년\s*\d{1,2}\s*월", filename)
        if match:
            return self.normalize_year(match.group(1))
        return None

    def parse_month_header(self, text: str) -> int | None:
        match = re.search(r"(\d{1,2})", text)
        if not match:
            return None
        month = int(match.group(1))
        return month if 1 <= month <= 12 else None

    def parse_compact_period(self, text: str) -> tuple[int, int] | None:
        patterns = [
            r"(\d{4})[.\-/](\d{1,2})",
            r"(\d{2})[.\-/](\d{1,2})",
            r"(\d{4})년\s*(\d{1,2})월",
            r"(\d{2})년\s*(\d{1,2})월",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            year = self.normalize_year(match.group(1))
            month = int(match.group(2))
            if 1 <= month <= 12:
                return year, month
        return None

    def find_row_index(self, sheet: pd.DataFrame, keywords: set[str]) -> int | None:
        for row_idx in range(len(sheet)):
            first = str(sheet.iat[row_idx, 0]).strip() if sheet.shape[1] > 0 else ""
            if first in keywords:
                return row_idx
        return None

    def is_cumulative_row(self, row: pd.Series) -> bool:
        first = str(row.iloc[0]).strip() if len(row) > 0 and not pd.isna(row.iloc[0]) else ""
        second = str(row.iloc[1]).strip() if len(row) > 1 and not pd.isna(row.iloc[1]) else ""
        if first:
            return first in {"누계", "(누계)", "계", "총계"}
        return second in {"누계", "(누계)", "계", "총계"}

    def normalize_region_label(self, value) -> str:
        text = re.sub(r"\s+", "", str(value)).strip()
        if text == "전국계":
            return NATIONWIDE
        if text == "강원도":
            return "강원"
        if text == "제주도":
            return "제주"
        return text

    def clean_region(self, value, valid_regions: set[str] | None = None) -> str | None:
        if pd.isna(value):
            return None
        text = self.normalize_region_label(value)
        if not text:
            return None
        if text in {"구분", "전월계", "(전월계)", "누계", "(누계)", "계", "총계", "공공부문", "민간부문"}:
            return None
        if text.startswith("("):
            return None
        if valid_regions is not None and text not in valid_regions:
            return None
        return text

    def safe_float(self, value) -> float | None:
        if pd.isna(value):
            return None
        text = str(value).replace(",", "").replace(" ", "").strip()
        if text in {"", "-", "nan", "None"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
