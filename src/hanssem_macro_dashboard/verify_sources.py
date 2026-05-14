from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from pathlib import Path

import pandas as pd
import requests

from hanssem_macro_dashboard.config import (
    INDICATORS,
    KOSIS_API_KEY,
    KOSIS_BASE_URL,
    RONE_API_KEY,
    SUPPLY_PRIORITY_INDICATOR_IDS,
    VERIFICATION_TARGET_INDICATOR_IDS,
    WAREHOUSE_DIR,
    load_verification_overrides,
    save_verification_overrides,
)
from hanssem_macro_dashboard.sources.kosis import KosisSource
from hanssem_macro_dashboard.sources.molit_stat_file import MolitStatFileSource, processed_path_for
from hanssem_macro_dashboard.sources.rone import RoneSource
from hanssem_macro_dashboard.sources.base import SourceError

VERIFIED_CRITERIA = [
    "API 호출 성공",
    "최근 3개월 샘플 데이터 1건 이상 존재",
    "date, value, region 표준 컬럼으로 변환 가능",
    "ETL 적재 포맷과 호환",
]

CANDIDATE_PATH = WAREHOUSE_DIR / "completion_volume_candidates.json"
PRICE_CANDIDATE_PATH = WAREHOUSE_DIR / "price_index_candidates.json"


def month_window(months: int) -> list[str]:
    today = date.today()
    year = today.year
    month = today.month
    values: list[str] = []
    for _ in range(months):
        values.append(f"{year:04d}{month:02d}")
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    values.reverse()
    return values


def load_completion_volume_candidates() -> list[dict[str, Any]]:
    if not CANDIDATE_PATH.exists():
        return []
    try:
        payload = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def save_price_index_candidates() -> list[dict[str, Any]]:
    candidates = [
        {
            "candidate_id": "rone_sale_price_api",
            "indicator_id": "sale_price_index",
            "provider": "R-ONE",
            "source_type": "open_api",
            "service_code": "SttsApiTblData.do",
            "statbl_id": "A_2024_00178",
            "table_name": "(월) 지역별 매매지수_아파트",
            "item_name": "지수",
            "frequency": "M",
            "period_type": "월간 지수",
            "region_dimension": True,
            "item_dimension": True,
            "confidence": "high",
        },
        {
            "candidate_id": "rone_jeonse_price_api",
            "indicator_id": "jeonse_price_index",
            "provider": "R-ONE",
            "source_type": "open_api",
            "service_code": "SttsApiTblData.do",
            "statbl_id": "A_2024_00182",
            "table_name": "(월) 지역별 전세지수_아파트",
            "item_name": "지수",
            "frequency": "M",
            "period_type": "월간 지수",
            "region_dimension": True,
            "item_dimension": True,
            "confidence": "high",
        },
        {
            "candidate_id": "rone_monthly_report_excel",
            "indicator_id": "sale_price_index,jeonse_price_index",
            "provider": "R-ONE",
            "source_type": "file_download",
            "table_name": "전국주택가격동향조사 월간 공표 엑셀",
            "item_name": "매매가격지수/전세가격지수",
            "frequency": "M",
            "period_type": "월간 지수",
            "region_dimension": True,
            "item_dimension": True,
            "confidence": "medium",
        },
    ]
    PRICE_CANDIDATE_PATH.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    return candidates


def find_candidate(candidate_id: str | None) -> dict[str, Any] | None:
    if not candidate_id:
        return None
    for candidate in load_completion_volume_candidates():
        if candidate.get("candidate_id") == candidate_id:
            return candidate
    return None


def build_kosis_params(
    indicator_id: str,
    months: int,
    candidate_params: dict[str, str] | None = None,
    source_override: dict[str, str] | None = None,
) -> dict[str, str]:
    definition = INDICATORS[indicator_id]
    detail = definition.source_detail
    periods = month_window(months)
    override_org_id = source_override.get("org_id", "") if source_override else ""
    override_table_id = source_override.get("table_id", "") if source_override else ""
    params = {
        "method": "getList",
        "apiKey": KOSIS_API_KEY or "YOUR_KOSIS_API_KEY",
        "format": "json",
        "jsonVD": "Y",
        "prdSe": detail.frequency,
        "orgId": override_org_id or detail.org_id or "101",
        "tblId": override_table_id or detail.table_id,
        "startPrdDe": periods[0],
        "endPrdDe": periods[-1],
        "newEstPrdCnt": str(months),
    }
    effective_item = candidate_params.get("itmId") if candidate_params else detail.item_code
    effective_obj_l1 = candidate_params.get("objL1") if candidate_params else detail.obj_l1
    effective_obj_l2 = candidate_params.get("objL2") if candidate_params else detail.obj_l2
    effective_obj_l3 = candidate_params.get("objL3") if candidate_params else detail.obj_l3
    if effective_item:
        params["itmId"] = effective_item
    if effective_obj_l1:
        params["objL1"] = effective_obj_l1
    if effective_obj_l2:
        params["objL2"] = effective_obj_l2
    if effective_obj_l3:
        params["objL3"] = effective_obj_l3
    return params


def build_metadata_probe(indicator_id: str, months: int) -> dict[str, str]:
    definition = INDICATORS[indicator_id]
    detail = definition.source_detail
    return {
        "provider": detail.provider,
        "source_name": detail.source_name,
        "org_id": detail.org_id or "101",
        "table_id": detail.table_id or "pending",
        "item_code": detail.item_code or "pending",
        "sample_periods": ",".join(month_window(months)),
        "sample_region": detail.sample_region or definition.region,
    }


def build_probe(indicator_id: str, months: int, source_override: dict[str, str] | None = None) -> dict[str, Any]:
    definition = INDICATORS[indicator_id]
    overrides = load_verification_overrides()
    candidate_params = overrides.get(indicator_id, {}).get("candidate_params", {})
    if indicator_id in SUPPLY_PRIORITY_INDICATOR_IDS[:2]:
        return {
            "indicator_id": indicator_id,
            "provider": definition.source_detail.provider,
            "mode": "http",
            "url": KOSIS_BASE_URL,
            "params": build_kosis_params(
                indicator_id,
                months=months,
                candidate_params=candidate_params,
                source_override=source_override,
            ),
        }
    return {
        "indicator_id": indicator_id,
        "provider": definition.source_detail.provider,
        "mode": "metadata_only",
        "url": "",
        "params": build_metadata_probe(indicator_id, months=months),
    }


def verify_indicator(
    indicator_id: str,
    dry_run: bool,
    timeout: int,
    debug_response: bool,
    source_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    definition = INDICATORS[indicator_id]
    sample_region = definition.source_detail.sample_region or definition.region
    recent_period = ",".join(month_window(3))

    if dry_run:
        probe = build_probe(indicator_id, months=3, source_override=source_override)
        message = (
            f"DRY_RUN {probe['url']}?{urlencode(probe['params'])}"
            if probe["mode"] == "http"
            else f"DRY_RUN metadata check: {probe['params']}"
        )
        if debug_response:
            print(f"[debug] indicator_id={indicator_id}")
            print(f"[debug] request={message}")
        return build_override(definition, "pending", "", message, recent_period, sample_region, 0, [], [])

    probe_3m = build_probe(indicator_id, months=3, source_override=source_override)
    if probe_3m["mode"] != "http":
        return build_override(
            definition, "failed_code", "failed_code", "실제 API 엔드포인트 또는 인증 방식이 아직 확정되지 않았습니다.", recent_period, sample_region, 0, [], []
        )

    primary = execute_http_probe(indicator_id, probe_3m, timeout, debug_response, label="3m")
    if primary["result_type"] != "ok":
        return build_override(
            definition,
            primary["status"],
            primary["error_type"],
            primary["message"],
            recent_period,
            sample_region,
            primary["rows"],
            primary["sample_keys"],
            primary["sample_rows"],
        )

    if primary["rows"] > 0 and primary["normalized"]:
        return build_override(
            definition,
            "verified",
            "",
            "검증 성공: 최근 3개월 데이터 존재, 표준 컬럼 변환 가능, ETL 적재 포맷과 호환됩니다.",
            recent_period,
            sample_region,
            primary["rows"],
            primary["sample_keys"],
            primary["sample_rows"],
        )

    fallback_period = ",".join(month_window(24))
    probe_24m = build_probe(indicator_id, months=24, source_override=source_override)
    fallback = execute_http_probe(indicator_id, probe_24m, timeout, debug_response, label="24m")

    if fallback["result_type"] == "ok" and fallback["rows"] > 0:
        return build_override(
            definition,
            "pending_condition_check",
            "failed_empty",
            "최근 3개월은 비어 있지만 최근 24개월 fallback에는 데이터가 있습니다. 기간/지역 조건을 재확인해야 합니다.",
            fallback_period,
            sample_region,
            fallback["rows"],
            fallback["sample_keys"],
            fallback["sample_rows"],
        )

    message = "API 호출은 성공했지만 최근 3개월 샘플 데이터가 없습니다."
    if fallback["result_type"] == "ok":
        message = "최근 3개월과 최근 24개월 fallback 모두 데이터가 없습니다."
    return build_override(
        definition,
        "failed_empty",
        "failed_empty",
        message,
        recent_period,
        sample_region,
        0,
        primary["sample_keys"] or fallback.get("sample_keys", []),
        primary["sample_rows"] or fallback.get("sample_rows", []),
    )


def execute_http_probe(indicator_id: str, probe: dict[str, Any], timeout: int, debug_response: bool, label: str) -> dict[str, Any]:
    if debug_response:
        print(f"[debug] {indicator_id} {label} request_url={probe['url']}")
        print(f"[debug] {indicator_id} {label} request_params={json.dumps(probe['params'], ensure_ascii=False)}")

    try:
        response = requests.get(probe["url"], params=probe["params"], timeout=timeout)
    except requests.exceptions.Timeout:
        return failure_result("failed_network", "요청 시간이 초과되었습니다.")
    except requests.exceptions.ConnectionError:
        return failure_result("failed_network", "네트워크 연결 또는 서버 응답에 실패했습니다.")
    except requests.exceptions.RequestException as exc:
        return failure_result("failed_network", f"요청 중 예외가 발생했습니다: {exc}")

    if response.status_code in (401, 403):
        return failure_result("failed_auth", f"인증 실패 또는 API 키 권한 문제입니다. status_code={response.status_code}")
    if response.status_code >= 400:
        return failure_result("failed_code", f"호출은 되었지만 table_id/item_code 또는 요청 파라미터가 올바르지 않을 수 있습니다. status_code={response.status_code}")

    try:
        payload = response.json()
    except ValueError:
        return failure_result("failed_parse", "JSON 파싱에 실패했습니다. 응답 포맷을 다시 확인해야 합니다.")

    sample_keys = list(payload[0].keys()) if isinstance(payload, list) and payload else []
    sample_rows = payload[:5] if isinstance(payload, list) else []
    rows, normalized = normalize_payload(indicator_id, payload)

    if debug_response:
        print(f"[debug] {indicator_id} {label} rows={rows}")
        print(f"[debug] {indicator_id} {label} sample_keys={sample_keys}")
        print(f"[debug] {indicator_id} {label} sample_rows={json.dumps(sample_rows, ensure_ascii=False)}")
        print(f"[debug] {indicator_id} {label} field_check=PRD_DE={'PRD_DE' in sample_keys}, DT={'DT' in sample_keys}, C1_NM={'C1_NM' in sample_keys}")

    return {
        "result_type": "ok",
        "status": "",
        "error_type": "",
        "message": "",
        "rows": rows,
        "normalized": normalized,
        "sample_keys": sample_keys,
        "sample_rows": sample_rows,
    }


def execute_raw_http_probe(probe: dict[str, Any], timeout: int) -> dict[str, Any]:
    try:
        response = requests.get(probe["url"], params=probe["params"], timeout=timeout)
    except requests.exceptions.Timeout:
        return {"ok": False, "message": "요청 시간이 초과되었습니다.", "rows": 0, "sample_keys": [], "sample_rows": []}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "message": "네트워크 연결 또는 서버 응답에 실패했습니다.", "rows": 0, "sample_keys": [], "sample_rows": []}
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "message": f"요청 중 예외가 발생했습니다: {exc}", "rows": 0, "sample_keys": [], "sample_rows": []}

    if response.status_code >= 400:
        return {
            "ok": False,
            "message": f"status_code={response.status_code}",
            "rows": 0,
            "sample_keys": [],
            "sample_rows": [],
        }

    try:
        payload = response.json()
    except ValueError:
        return {"ok": False, "message": "JSON 파싱 실패", "rows": 0, "sample_keys": [], "sample_rows": []}

    sample_keys = list(payload[0].keys()) if isinstance(payload, list) and payload else []
    sample_rows = payload[:3] if isinstance(payload, list) else []
    rows = len(payload) if isinstance(payload, list) else 0
    return {
        "ok": True,
        "message": "",
        "rows": rows,
        "sample_keys": sample_keys,
        "sample_rows": sample_rows,
    }


def normalize_payload(indicator_id: str, payload: Any) -> tuple[int, bool]:
    if not isinstance(payload, list) or not payload:
        return 0, False
    if indicator_id in ("completion_volume", "housing_permits"):
        converted = 0
        for item in payload:
            period = item.get("PRD_DE") or item.get("TIME") or item.get("prdDe")
            value = item.get("DT") or item.get("DATA_VALUE") or item.get("dt")
            region = item.get("C1_NM") or item.get("C1") or item.get("region")
            if period and value not in (None, "") and region:
                converted += 1
        return len(payload), converted > 0
    return len(payload), False


def failure_result(status: str, message: str) -> dict[str, Any]:
    return {"result_type": "failure", "status": status, "error_type": status, "message": message, "rows": 0, "normalized": False, "sample_keys": [], "sample_rows": []}


def build_override(definition, status: str, error_type: str, message: str, sample_period: str, sample_region: str, rows: int, debug_sample_keys: list[str], debug_sample_rows: list[dict]) -> dict[str, Any]:
    return {
        "verification_status": status,
        "verification_message": message,
        "last_verified_at": str(date.today()),
        "sample_period": sample_period,
        "sample_region": sample_region,
        "provider": definition.source_detail.provider,
        "source_name": definition.source_detail.source_name,
        "org_id": definition.source_detail.org_id,
        "table_id": definition.source_detail.table_id,
        "stat_code": definition.source_detail.stat_code,
        "item_code": definition.source_detail.item_code,
        "obj_l1": definition.source_detail.obj_l1,
        "obj_l2": definition.source_detail.obj_l2,
        "obj_l3": definition.source_detail.obj_l3,
        "error_type": error_type,
        "rows": rows,
        "debug_sample_keys": debug_sample_keys,
        "debug_sample_rows": debug_sample_rows,
    }


def fetch_meta_rows(indicator_id: str, timeout: int, source_override: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], Any]:
    definition = INDICATORS[indicator_id]
    source = KosisSource(timeout=timeout)
    org_id = (source_override or {}).get("org_id") or definition.source_detail.org_id or "101"
    tbl_id = (source_override or {}).get("table_id") or definition.source_detail.table_id
    payload = source.fetch_table_items(org_id=org_id, tbl_id=tbl_id)
    rows = flatten_item_meta_payload(payload)
    return rows, payload


def flatten_item_meta_payload(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    walk_item_meta(payload, rows)
    return rows


def walk_item_meta(node: Any, rows: list[dict[str, Any]]) -> None:
    if isinstance(node, list):
        for item in node:
            walk_item_meta(item, rows)
        return
    if not isinstance(node, dict):
        return

    obj_id = str(node.get("OBJ_ID") or node.get("objId") or "")
    obj_name = str(node.get("OBJ_NM") or node.get("objNm") or "")
    item_id = str(node.get("ITM_ID") or node.get("itmId") or "")
    item_name = str(node.get("ITM_NM") or node.get("itmNm") or "")
    up_item_id = str(node.get("UP_ITM_ID") or node.get("upItmId") or "")
    obj_order = str(node.get("OBJ_ID_SN") or node.get("objIdSn") or "")

    if obj_id or item_id or item_name or obj_order:
        rows.append(
            {
                "obj_id": obj_id,
                "obj_name": obj_name,
                "item_id": item_id,
                "item_name": item_name,
                "up_item_id": up_item_id,
                "obj_order": obj_order,
                "param_role": infer_param_role(obj_id=obj_id, obj_order=obj_order),
            }
        )

    for value in node.values():
        walk_item_meta(value, rows)


def infer_param_role(obj_id: str, obj_order: str) -> str:
    if obj_id == "ITEM":
        return "itmId candidate"
    if obj_id and obj_order == "1":
        return "objL1 candidate"
    if obj_id and obj_order == "2":
        return "objL2 candidate"
    if obj_id and obj_order == "3":
        return "objL3 candidate"
    return ""


def save_meta_payload(tbl_id: str, payload: Any) -> Path:
    WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
    path = WAREHOUSE_DIR / f"kosis_items_{tbl_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def pick_meta_candidate(rows: list[dict[str, Any]], param_role: str) -> str:
    for row in rows:
        if row.get("param_role") == param_role and row.get("item_id"):
            return str(row["item_id"])
    return ""


def run_probe_combinations(target_ids: list[str], timeout: int, candidate_id: str | None = None) -> int:
    overrides = load_verification_overrides()
    selected_candidate = find_candidate(candidate_id)
    for indicator_id in target_ids:
        if indicator_id not in ("completion_volume", "housing_permits"):
            print(f"[probe] indicator_id={indicator_id} is not configured for probe combinations yet.")
            continue
        source_override: dict[str, str] | None = None
        if selected_candidate and indicator_id == "completion_volume":
            if selected_candidate.get("provider") != "KOSIS":
                print(f"[probe] candidate_id={candidate_id} is not a KOSIS table and cannot be probed with KOSIS API.")
                continue
            source_override = {
                "org_id": str(selected_candidate.get("org_id") or ""),
                "table_id": str(selected_candidate.get("table_id") or ""),
            }
            print(f"[probe] candidate_id={candidate_id} org_id={source_override['org_id']} tbl_id={source_override['table_id']}")

        definition = INDICATORS[indicator_id]
        meta_rows, _ = fetch_meta_rows(indicator_id, timeout, source_override=source_override)
        itm_candidate = definition.source_detail.item_code or pick_meta_candidate(meta_rows, "itmId candidate") or "ALL"
        obj_l1_candidate = definition.source_detail.obj_l1 or pick_meta_candidate(meta_rows, "objL1 candidate") or "ALL"
        obj_l2_candidate = definition.source_detail.obj_l2 or pick_meta_candidate(meta_rows, "objL2 candidate") or "ALL"
        obj_l3_candidate = definition.source_detail.obj_l3 or pick_meta_candidate(meta_rows, "objL3 candidate") or "ALL"
        if selected_candidate and indicator_id == "completion_volume":
            itm_candidate = pick_meta_candidate(meta_rows, "itmId candidate") or "ALL"
            obj_l1_candidate = pick_meta_candidate(meta_rows, "objL1 candidate") or "ALL"
            obj_l2_candidate = pick_meta_candidate(meta_rows, "objL2 candidate") or "ALL"
            obj_l3_candidate = pick_meta_candidate(meta_rows, "objL3 candidate") or "ALL"
        base_params = build_kosis_params(indicator_id, months=24, source_override=source_override)
        combinations = [
            ("A", {"itmId": itm_candidate, "objL1": obj_l1_candidate, "objL2": obj_l2_candidate, "objL3": obj_l3_candidate}),
            ("B", {"itmId": itm_candidate, "objL1": "ALL", "objL2": obj_l2_candidate, "objL3": obj_l3_candidate}),
            ("C", {"itmId": "ALL", "objL1": obj_l1_candidate, "objL2": obj_l2_candidate, "objL3": obj_l3_candidate}),
            ("D", {"itmId": "ALL", "objL1": "ALL", "objL2": "ALL", "objL3": "ALL"}),
        ]

        candidate_params: dict[str, str] | None = None
        for label, combo in combinations:
            params = dict(base_params)
            params["itmId"] = combo["itmId"]
            params["objL1"] = combo["objL1"]
            params["objL2"] = combo["objL2"]
            params["objL3"] = combo["objL3"]
            probe = {
                "indicator_id": indicator_id,
                "url": KOSIS_BASE_URL,
                "params": params,
            }
            result = execute_raw_http_probe(probe, timeout)
            print(f"\n[probe {label}] indicator_id={indicator_id}")
            print(f"params={json.dumps(params, ensure_ascii=False)}")
            print(f"rows={result['rows']}")
            print(f"sample_keys={result['sample_keys']}")
            print(f"first_3_rows={json.dumps(result['sample_rows'], ensure_ascii=False)}")
            print(f"error_message={result['message']}")
            if candidate_params is None and result["rows"] > 0:
                candidate_params = {
                    "itmId": params["itmId"],
                    "objL1": params["objL1"],
                    "objL2": params["objL2"],
                    "objL3": params["objL3"],
                    "prdSe": params["prdSe"],
                    "newEstPrdCnt": params["newEstPrdCnt"],
                }

        if candidate_params:
            current = overrides.get(indicator_id, {})
            current["candidate_params"] = candidate_params
            overrides[indicator_id] = current
            print(f"\n[candidate] indicator_id={indicator_id} candidate_params={json.dumps(candidate_params, ensure_ascii=False)}")
        else:
            print(f"\n[candidate] indicator_id={indicator_id} no combination returned rows > 0")

    save_verification_overrides(overrides)
    return 0


def verify_indicator_fallback(indicator_id: str, timeout: int) -> dict[str, Any]:
    definition = INDICATORS[indicator_id]
    fallback = definition.fallback_source
    if fallback is None:
        return {
            "fallback_verification_status": "failed_schema",
            "fallback_verification_message": f"{indicator_id} fallback_source is not configured.",
            "fallback_last_verified_at": str(date.today()),
            "fallback_error_type": "failed_schema",
            "fallback_rows": 0,
        }

    source = MolitStatFileSource(indicator_ids=[indicator_id], timeout=timeout)
    source_latest_period = ""
    try:
        source_latest_period = source.fetch_latest_available_period(indicator_id)
    except Exception:
        source_latest_period = ""

    try:
        standardized_rows = source.fetch_indicator_rows(indicator_id)
    except SourceError as exc:
        message = str(exc)
        error_type = "failed_download" if "download" in message.lower() or "file not found" in message.lower() else "failed_parse"
        return {
            "fallback_verification_status": error_type,
            "fallback_verification_message": message,
            "fallback_last_verified_at": str(date.today()),
            "fallback_error_type": error_type,
            "fallback_rows": 0,
        }
    except Exception as exc:  # pragma: no cover
        return {
            "fallback_verification_status": "failed_parse",
            "fallback_verification_message": repr(exc),
            "fallback_last_verified_at": str(date.today()),
            "fallback_error_type": "failed_parse",
            "fallback_rows": 0,
        }

    standardized = pd.read_csv(processed_path_for(indicator_id))
    required = {"indicator_id", "date", "region", "value", "unit", "source"}
    if not required.issubset(set(standardized.columns)):
        return {
            "fallback_verification_status": "failed_schema",
            "fallback_verification_message": "Fallback standardized file is missing required columns.",
            "fallback_last_verified_at": str(date.today()),
            "fallback_error_type": "failed_schema",
            "fallback_rows": 0,
        }

    standardized["date"] = pd.to_datetime(standardized["date"])
    current_month = pd.Timestamp(date.today().replace(day=1))
    minimum_month = current_month - pd.DateOffset(months=24)
    recent = standardized[(standardized["date"] >= minimum_month) & (standardized["date"] <= current_month)]
    if recent.empty:
        return {
            "fallback_verification_status": "failed_empty",
            "fallback_verification_message": "Fallback file parsed successfully but no rows were found in the recent 24-month window.",
            "fallback_last_verified_at": str(date.today()),
            "fallback_error_type": "failed_empty",
            "fallback_rows": 0,
        }

    latest_period = recent["date"].max().strftime("%Y-%m-%d")
    message = f"Fallback file download and read_excel succeeded; standardized rows are available through {latest_period}."
    if source_latest_period:
        source_latest_date = pd.to_datetime(source_latest_period + "01", format="%Y%m%d", errors="coerce")
        if pd.notna(source_latest_date):
            source_latest_text = source_latest_date.strftime("%Y-%m-%d")
            message += f" Official source page advertises periods through {source_latest_text}."
            gap_months = (source_latest_date.year - recent["date"].max().year) * 12 + (source_latest_date.month - recent["date"].max().month)
            if gap_months > 0:
                message += f" Parsed data currently lags the official source by {gap_months} month(s)."
    warning_count = 0
    if indicator_id == "housing_permits":
        warning_count = int((standardized["value"] < 0).sum())
        if warning_count > 0:
            message += f" Warning: {warning_count} monthly rows are negative after cumulative-to-monthly conversion."
    return {
        "fallback_verification_status": "verified",
        "fallback_verification_message": message,
        "fallback_last_verified_at": str(date.today()),
        "fallback_error_type": "",
        "fallback_rows": int(len(recent)),
        "fallback_sample_period": f"{recent['date'].min().strftime('%Y-%m-%d')}~{latest_period}",
        "fallback_sample_region": "전국",
        "fallback_source_name": fallback.source_name,
        "fallback_provider": fallback.provider,
        "fallback_api_type": fallback.api_type,
        "fallback_h_rs_id": fallback.h_rs_id,
        "fallback_h_form_id": fallback.h_form_id,
        "fallback_processed_path": str(processed_path_for(indicator_id)),
        "fallback_source_series_code": f"hRsId={fallback.h_rs_id}&hFormId={fallback.h_form_id}",
        "fallback_source_latest_period": source_latest_period,
        "fallback_data_latest_period": latest_period,
        "fallback_warning_count": warning_count,
        "rows": int(len(standardized_rows)),
    }


def run_verify_fallback(target_ids: list[str], timeout: int) -> int:
    overrides = load_verification_overrides()
    results: list[dict[str, Any]] = []
    for indicator_id in target_ids:
        if indicator_id not in {"completion_volume", "housing_permits", "unsold_units"}:
            print(f"[fallback] indicator_id={indicator_id} does not have a configured fallback verifier.")
            continue
        result = verify_indicator_fallback(indicator_id=indicator_id, timeout=timeout)
        current = overrides.get(indicator_id, {})
        current.update(result)
        overrides[indicator_id] = current
        results.append(
            {
                "indicator_id": indicator_id,
                "status": result["fallback_verification_status"],
                "message": result["fallback_verification_message"],
                "sample_period": result.get("fallback_sample_period", ""),
                "sample_region": result.get("fallback_sample_region", ""),
                "rows": result.get("fallback_rows", 0),
            }
        )
    save_verification_overrides(overrides)
    print_results_table(results)
    return 0


def execute_rone_probe(candidate: dict[str, str], indicator_id: str, timeout: int) -> dict[str, Any]:
    source = RoneSource()
    try:
        rows = source.fetch_price_index_data(
            statbl_id=candidate["statbl_id"],
            service_code=candidate["service_code"],
            dtacycle_cd=candidate.get("dtacycle_cd", "MM"),
        )
        sample_keys = list(rows[0].keys()) if rows else []
        first_3_rows = rows[:3]
        standardized = source.standardize_rows(
            indicator_id=indicator_id,
            rows=rows,
            region_name=candidate.get("region_name", "전국"),
            region_cls_id=candidate.get("region_cls_id"),
            item_id=candidate.get("item_id"),
        )
    except requests.exceptions.Timeout:
        return {"service_code": candidate["service_code"], "statbl_id": candidate["statbl_id"], "rows": 0, "sample_keys": [], "first_3_rows": [], "message": "request timeout", "status": "failed_network"}
    except requests.exceptions.ConnectionError:
        return {"service_code": candidate["service_code"], "statbl_id": candidate["statbl_id"], "rows": 0, "sample_keys": [], "first_3_rows": [], "message": "network connection failed", "status": "failed_network"}
    except requests.exceptions.HTTPError as exc:
        code = getattr(exc.response, "status_code", 0)
        status = "failed_auth" if code in (401, 403) else "failed_code"
        return {"service_code": candidate["service_code"], "statbl_id": candidate["statbl_id"], "rows": 0, "sample_keys": [], "first_3_rows": [], "message": f"http error status_code={code}", "status": status}
    except ValueError:
        return {"service_code": candidate["service_code"], "statbl_id": candidate["statbl_id"], "rows": 0, "sample_keys": [], "first_3_rows": [], "message": "json parse failed", "status": "failed_parse"}
    except Exception as exc:
        return {"service_code": candidate["service_code"], "statbl_id": candidate["statbl_id"], "rows": 0, "sample_keys": [], "first_3_rows": [], "message": str(exc), "status": "failed_parse"}

    recent_rows = 0
    latest_period = ""
    if not standardized.empty:
        standardized_dates = pd.to_datetime(standardized["date"], errors="coerce")
        current_month = pd.Timestamp(date.today().replace(day=1))
        minimum_month = current_month - pd.DateOffset(months=24)
        recent_rows = int(((standardized_dates >= minimum_month) & (standardized_dates <= current_month)).sum())
        latest_period = standardized_dates.max().strftime("%Y-%m-%d") if standardized_dates.notna().any() else ""

    if rows and not standardized.empty and recent_rows > 0:
        return {
            "service_code": candidate["service_code"],
            "statbl_id": candidate["statbl_id"],
            "rows": len(rows),
            "recent_rows": recent_rows,
            "latest_period": latest_period,
            "sample_keys": sample_keys,
            "first_3_rows": first_3_rows,
            "message": "rows found and standardization is possible",
            "status": "verified",
            "standardized_rows": standardized.head(3).to_dict(orient="records"),
        }
    if rows:
        return {
            "service_code": candidate["service_code"],
            "statbl_id": candidate["statbl_id"],
            "rows": len(rows),
            "recent_rows": recent_rows,
            "latest_period": latest_period,
            "sample_keys": sample_keys,
            "first_3_rows": first_3_rows,
            "message": "rows found but recent 24-month standardized rows are absent or incomplete",
            "status": "pending_condition_check",
            "standardized_rows": [],
        }
    return {
        "service_code": candidate["service_code"],
        "statbl_id": candidate["statbl_id"],
        "rows": 0,
        "recent_rows": 0,
        "latest_period": "",
        "sample_keys": [],
        "first_3_rows": [],
        "message": "no rows returned from probe",
        "status": "failed_empty",
        "standardized_rows": [],
    }


def verify_indicator_rone(indicator_id: str, timeout: int) -> dict[str, Any]:
    overrides = load_verification_overrides()
    definition = INDICATORS[indicator_id]
    source = RoneSource()
    candidate_params = overrides.get(indicator_id, {}).get("candidate_params", {})
    candidates = source.build_service_code_candidates(indicator_id=indicator_id, candidate_params=candidate_params)
    if not RONE_API_KEY:
        first = candidates[0]
        probe_text = source.build_probe_text(
            statbl_id=first["statbl_id"],
            service_code=first["service_code"],
            dtacycle_cd=first["dtacycle_cd"],
        )
        return {
            "verification_status": "failed_auth",
            "verification_message": f"RONE_API_KEY is missing. Probe template: {probe_text}",
            "last_verified_at": str(date.today()),
            "error_type": "failed_auth",
            "rows": 0,
            "sample_period": "최근 24개월",
            "sample_region": "전국",
            "debug_sample_keys": [],
            "debug_sample_rows": [],
            "source_name": definition.source_detail.source_name,
            "provider": definition.source_detail.provider,
            "api_type": definition.source_detail.api_type,
            "stat_code": definition.source_detail.stat_code,
            "probe_results": [],
        }

    probe_results = [execute_rone_probe(candidate, indicator_id=indicator_id, timeout=timeout) for candidate in candidates]
    verified_result = next((result for result in probe_results if result["status"] == "verified"), None)
    if verified_result:
        return {
            "verification_status": "verified",
            "verification_message": (
                f"R-ONE probe verified with service_code={verified_result['service_code']} statbl_id={verified_result['statbl_id']}; "
                f"recent_rows={verified_result.get('recent_rows', 0)} latest_period={verified_result.get('latest_period', '')}"
            ),
            "last_verified_at": str(date.today()),
            "error_type": "",
            "rows": int(verified_result["rows"]),
            "sample_period": f"최근 24개월~{verified_result.get('latest_period', '')}",
            "sample_region": "전국",
            "debug_sample_keys": verified_result.get("sample_keys", []),
            "debug_sample_rows": verified_result.get("first_3_rows", []),
            "source_name": definition.source_detail.source_name,
            "provider": definition.source_detail.provider,
            "api_type": definition.source_detail.api_type,
            "stat_code": verified_result["service_code"],
            "probe_results": probe_results,
            "candidate_params": {
                "service_code": verified_result["service_code"],
                "statbl_id": verified_result["statbl_id"],
                "region_name": "전국",
                "region_cls_id": "500001",
                "item_id": "100001",
                "dtacycle_cd": "MM",
            },
        }

    last = probe_results[-1] if probe_results else {"status": "failed_code", "message": "no service code candidate configured"}
    return {
        "verification_status": last["status"],
        "verification_message": last["message"],
        "last_verified_at": str(date.today()),
        "error_type": last["status"] if str(last["status"]).startswith("failed") else "",
        "rows": int(last.get("rows", 0)),
        "sample_period": "최근 24개월",
        "sample_region": "전국",
        "debug_sample_keys": last.get("sample_keys", []),
        "debug_sample_rows": last.get("first_3_rows", []),
        "source_name": definition.source_detail.source_name,
        "provider": definition.source_detail.provider,
        "api_type": definition.source_detail.api_type,
        "stat_code": definition.source_detail.stat_code,
        "probe_results": probe_results,
    }


def print_rone_probe_results(indicator_id: str, probe_results: list[dict[str, Any]]) -> None:
    if not probe_results:
        return
    print(f"\n[rone-probe] indicator_id={indicator_id}")
    for result in probe_results:
        print(
            f"service_code={result.get('service_code','')} | "
            f"statbl_id={result.get('statbl_id','')} | "
            f"indicator_id={indicator_id} | rows={result.get('rows',0)} | "
            f"message={result.get('message','')}"
        )
        print(f"sample_keys={json.dumps(result.get('sample_keys', []), ensure_ascii=False)}")
        print(f"first_3_rows={json.dumps(result.get('first_3_rows', []), ensure_ascii=False)}")


def run_verify_rone(target_ids: list[str], timeout: int) -> int:
    overrides = load_verification_overrides()
    save_price_index_candidates()
    results: list[dict[str, Any]] = []
    for indicator_id in target_ids:
        if indicator_id not in {"sale_price_index", "jeonse_price_index"}:
            print(f"[R-ONE] indicator_id={indicator_id} is not a configured price indicator.")
            continue
        result = verify_indicator_rone(indicator_id=indicator_id, timeout=timeout)
        current = overrides.get(indicator_id, {})
        current.update(result)
        overrides[indicator_id] = current
        print_rone_probe_results(indicator_id=indicator_id, probe_results=result.get("probe_results", []))
        results.append(
            {
                "indicator_id": indicator_id,
                "status": result["verification_status"],
                "message": result["verification_message"],
                "sample_period": result.get("sample_period", ""),
                "sample_region": result.get("sample_region", ""),
                "rows": result.get("rows", 0),
            }
        )
    save_verification_overrides(overrides)
    print_results_table(results)
    return 0


def print_results_table(results: list[dict[str, Any]]) -> None:
    columns = ["indicator_id", "status", "message", "sample_period", "sample_region", "rows"]
    widths = {column: len(column) for column in columns}
    for row in results:
        for column in columns:
            widths[column] = min(max(widths[column], len(str(row[column]))), 100)
    header = " | ".join(column.ljust(widths[column]) for column in columns)
    divider = "-+-".join("-" * widths[column] for column in columns)
    print(header)
    print(divider)
    for row in results:
        print(" | ".join(str(row[column])[: widths[column]].ljust(widths[column]) for column in columns))


def print_meta_table(rows: list[dict[str, Any]]) -> None:
    columns = ["obj_id", "obj_name", "item_id", "item_name", "up_item_id", "obj_order", "param_role"]
    widths = {column: len(column) for column in columns}
    for row in rows:
        for column in columns:
            widths[column] = min(max(widths[column], len(str(row.get(column, "")))), 80)
    header = " | ".join(column.ljust(widths[column]) for column in columns)
    divider = "-+-".join("-" * widths[column] for column in columns)
    print(header)
    print(divider)
    for row in rows:
        print(" | ".join(str(row.get(column, ""))[: widths[column]].ljust(widths[column]) for column in columns))


def run_inspect_meta(target_ids: list[str], timeout: int, candidate_id: str | None = None) -> int:
    selected_candidate = find_candidate(candidate_id)
    for indicator_id in target_ids:
        definition = INDICATORS[indicator_id]
        source_override: dict[str, str] | None = None
        org_id = definition.source_detail.org_id
        tbl_id = definition.source_detail.table_id
        if selected_candidate and indicator_id == "completion_volume":
            if selected_candidate.get("provider") != "KOSIS":
                print(f"[meta] candidate_id={candidate_id} is not a KOSIS table and cannot be inspected with KOSIS meta API.")
                continue
            source_override = {
                "org_id": str(selected_candidate.get("org_id") or ""),
                "table_id": str(selected_candidate.get("table_id") or ""),
            }
            org_id = source_override["org_id"]
            tbl_id = source_override["table_id"]
            print(f"[meta] candidate_id={candidate_id}")
        rows, payload = fetch_meta_rows(indicator_id, timeout, source_override=source_override)
        path = save_meta_payload(tbl_id, payload)
        print(f"\n[meta] indicator_id={indicator_id} org_id={org_id} tbl_id={tbl_id}")
        print(f"[meta] saved_json={path}")
        print_meta_table(rows)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify supply/price source metadata and sample connectivity")
    parser.add_argument("--live", action="store_true", help="Run real HTTP verification when API details are available")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds")
    parser.add_argument("--debug-response", action="store_true", help="Print raw sample response information for debugging")
    parser.add_argument("--inspect-meta", action="store_true", help="Inspect KOSIS table metadata and save raw JSON")
    parser.add_argument("--probe-combinations", action="store_true", help="Probe multiple itmId/objL1 combinations for KOSIS indicators")
    parser.add_argument("--verify-fallback", action="store_true", help="Verify file-download fallback sources")
    parser.add_argument("--verify-rone", action="store_true", help="Verify R-ONE price indicator source candidates")
    parser.add_argument("--candidate-id", help="Use a candidate from completion_volume_candidates.json for completion_volume meta/probe")
    parser.add_argument("--indicator", action="append", choices=VERIFICATION_TARGET_INDICATOR_IDS, help="Verify only selected indicator ids")
    args = parser.parse_args()

    target_ids = args.indicator or VERIFICATION_TARGET_INDICATOR_IDS
    if args.inspect_meta:
        return run_inspect_meta(target_ids=target_ids, timeout=args.timeout, candidate_id=args.candidate_id)
    if args.probe_combinations:
        return run_probe_combinations(target_ids=target_ids, timeout=args.timeout, candidate_id=args.candidate_id)
    if args.verify_fallback:
        return run_verify_fallback(target_ids=target_ids, timeout=args.timeout)
    if args.verify_rone:
        return run_verify_rone(target_ids=target_ids, timeout=args.timeout)

    dry_run = not args.live or not KOSIS_API_KEY
    overrides = load_verification_overrides()
    results: list[dict[str, Any]] = []

    if args.live and not KOSIS_API_KEY:
        print("KOSIS_API_KEY가 없어 dry-run으로 전환합니다.")

    print("verified 기준:")
    for idx, criterion in enumerate(VERIFIED_CRITERIA, start=1):
        print(f"{idx}. {criterion}")
    print()

    for indicator_id in target_ids:
        source_override = None
        selected_candidate = find_candidate(args.candidate_id) if args.candidate_id and indicator_id == "completion_volume" else None
        if selected_candidate and selected_candidate.get("provider") == "KOSIS":
            source_override = {
                "org_id": str(selected_candidate.get("org_id") or ""),
                "table_id": str(selected_candidate.get("table_id") or ""),
            }
        result = verify_indicator(
            indicator_id=indicator_id,
            dry_run=dry_run,
            timeout=args.timeout,
            debug_response=args.debug_response,
            source_override=source_override,
        )
        overrides[indicator_id] = result
        results.append(
            {
                "indicator_id": indicator_id,
                "status": result["verification_status"],
                "message": result["verification_message"],
                "sample_period": result["sample_period"],
                "sample_region": result["sample_region"],
                "rows": result["rows"],
            }
        )

    save_verification_overrides(overrides)
    print_results_table(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
