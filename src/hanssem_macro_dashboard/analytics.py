from __future__ import annotations

import math

import pandas as pd

from hanssem_macro_dashboard.config import EXECUTIVE_CARDS, INDICATORS, indicator_metadata_rows, load_verification_overrides

HMI_COMPONENT_WEIGHTS = {
    "sale_price_index": 0.4,
    "completion_volume": 0.3,
    "unsold_units": 0.3,
}


def enrich_with_metadata(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    metadata = metadata_frame()
    enriched = df.merge(metadata, on="indicator_code", how="left")
    enriched["display_name"] = enriched["name_kr"].fillna(enriched["indicator_name"])
    enriched["category"] = enriched["category"].fillna(enriched["bucket"])
    return enriched


def compute_changes(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    ordered = df.sort_values(["indicator_code", "region_code", "observation_date"]).copy()
    group_cols = ["indicator_code", "region_code"]
    ordered["prev_value"] = ordered.groupby(group_cols)["value"].shift(1)
    ordered["prev_year_value"] = ordered.groupby(group_cols)["value"].shift(12)
    ordered["mom_pct"] = ((ordered["value"] / ordered["prev_value"]) - 1.0) * 100.0
    ordered["yoy_pct"] = ((ordered["value"] / ordered["prev_year_value"]) - 1.0) * 100.0
    ordered.loc[ordered["prev_value"].eq(0), "mom_pct"] = pd.NA
    ordered.loc[ordered["prev_year_value"].eq(0), "yoy_pct"] = pd.NA
    ordered["signal_sign"] = ordered["direction"].map({"positive": 1.0, "negative": -1.0, "neutral": 0.0}).fillna(0.0)
    ordered["favorable_mom"] = ordered["mom_pct"] * ordered["signal_sign"]
    ordered["favorable_yoy"] = ordered["yoy_pct"] * ordered["signal_sign"]
    return ordered


def latest_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return (
        df.sort_values("observation_date")
        .groupby(["indicator_code", "region_code"], as_index=False)
        .tail(1)
        .sort_values(["category", "indicator_code"])
    )


def build_category_scores(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    rows = []
    for (category, observation_date), group in df.groupby(["category", "observation_date"]):
        favorable_values = pd.concat([group["favorable_mom"], group["favorable_yoy"]]).dropna()
        signal = favorable_values.mean() if not favorable_values.empty else 0.0
        current_score = _score_from_signal(signal)
        rows.append(
            {
                "category": category,
                "observation_date": observation_date,
                "current_score": current_score,
            }
        )

    scores = pd.DataFrame(rows).sort_values(["category", "observation_date"])
    if scores.empty:
        return scores
    scores["prev_score"] = scores.groupby("category")["current_score"].shift(1)
    scores["prev_year_score"] = scores.groupby("category")["current_score"].shift(12)
    scores["mom_delta"] = scores["current_score"] - scores["prev_score"]
    scores["yoy_delta"] = scores["current_score"] - scores["prev_year_score"]
    return scores


def executive_card_snapshot(scores: pd.DataFrame) -> pd.DataFrame:
    if scores.empty:
        return pd.DataFrame(columns=["card_id", "title", "current_score", "mom_delta", "yoy_delta"])

    latest_by_category = (
        scores.sort_values("observation_date").groupby("category", as_index=False).tail(1)[
            ["category", "current_score", "mom_delta", "yoy_delta"]
        ]
    )
    card_rows = []
    top_line_source = latest_by_category[latest_by_category["category"].isin(["수요", "거래", "가격", "공급", "리스크", "금융"])]
    topline_score = top_line_source["current_score"].mean() if not top_line_source.empty else 50.0
    topline_mom = top_line_source["mom_delta"].mean() if not top_line_source.empty else 0.0
    topline_yoy = top_line_source["yoy_delta"].mean() if not top_line_source.empty else 0.0

    for card_id, title in EXECUTIVE_CARDS:
        if card_id == "topline":
            card_rows.append(
                {
                    "card_id": card_id,
                    "title": title,
                    "current_score": topline_score,
                    "mom_delta": topline_mom,
                    "yoy_delta": topline_yoy,
                }
            )
            continue

        match = latest_by_category[latest_by_category["category"] == card_id]
        if match.empty:
            card_rows.append(
                {"card_id": card_id, "title": title, "current_score": 50.0, "mom_delta": 0.0, "yoy_delta": 0.0}
            )
            continue

        row = match.iloc[0]
        card_rows.append(
            {
                "card_id": card_id,
                "title": title,
                "current_score": row["current_score"],
                "mom_delta": row["mom_delta"],
                "yoy_delta": row["yoy_delta"],
            }
        )
    return pd.DataFrame(card_rows)


def build_interpretation(snapshot: pd.DataFrame) -> str:
    latest = {row["indicator_code"]: row for _, row in snapshot.iterrows()}
    messages: list[str] = []

    mortgage = latest.get("mortgage_rate")
    trade = latest.get("apt_trade_volume")
    rent = latest.get("apt_rent_volume")
    sentiment = latest.get("consumer_sentiment")
    completions = latest.get("completion_volume")
    unsold = latest.get("unsold_units")

    if mortgage is not None and trade is not None:
        mortgage_yoy = _safe_number(mortgage.get("yoy_pct"))
        trade_yoy = _safe_number(trade.get("yoy_pct"))
        if mortgage_yoy > 0 and trade_yoy < 0:
            messages.append("주택 구매 부담 증가와 거래 둔화가 동시 발생해 단기 리모델링 수요에는 부정적입니다.")
        elif mortgage_yoy < 0 and trade_yoy > 0:
            messages.append("금융 부담 완화와 거래 회복이 함께 나타나 한샘 탑라인에는 우호적입니다.")

    if sentiment is not None:
        sentiment_yoy = _safe_number(sentiment.get("yoy_pct"))
        if sentiment_yoy > 3:
            messages.append("소비심리 회복은 고관여 인테리어 집행 확률을 높이는 신호입니다.")
        elif sentiment_yoy < -3:
            messages.append("소비심리 둔화는 고가 가구와 리모델링 집행 지연 리스크를 키웁니다.")

    if rent is not None and _safe_number(rent.get("yoy_pct")) > 0:
        messages.append("전월세 이동 확대는 부분 수리와 가구 교체 수요 유지에 도움을 줄 수 있습니다.")

    if completions is not None and _safe_number(completions.get("yoy_pct")) > 0:
        messages.append("준공 물량 증가는 입주 기반 인테리어 수요 확대 가능성을 높입니다.")

    if unsold is not None and _safe_number(unsold.get("yoy_pct")) > 0:
        messages.append("미분양 증가는 지역별 수요 냉각 신호이므로 공급 전략과 지역 믹스를 보수적으로 볼 필요가 있습니다.")

    if not messages:
        messages.append("현재 지표 조합만으로는 방향성이 혼재되어 있어 거래와 금융 지표를 함께 보며 해석하는 것이 안전합니다.")
    return " ".join(messages)


def get_latest_metrics(snapshot: pd.DataFrame) -> dict[str, float | None]:
    latest = {row["indicator_code"]: row for _, row in snapshot.iterrows()}
    return {
        "price_yoy": _metric_value(latest, "sale_price_index", "yoy_pct"),
        "completion_yoy": _metric_value(latest, "completion_volume", "yoy_pct"),
        "unsold_yoy": _metric_value(latest, "unsold_units", "yoy_pct"),
        "jeonse_yoy": _metric_value(latest, "jeonse_price_index", "yoy_pct"),
    }


def normalize_score(value: float | None, positive: bool = True) -> float:
    if value is None or pd.isna(value):
        return 0.0
    score = float(value) / 10.0
    if not positive:
        score = -score
    return max(min(score, 2.0), -2.0)


def calculate_hanssem_macro_score(metrics: dict[str, float | None]) -> float:
    price_score = normalize_score(metrics.get("price_yoy"), positive=True)
    completion_score = normalize_score(metrics.get("completion_yoy"), positive=True)
    unsold_score = normalize_score(metrics.get("unsold_yoy"), positive=False)
    hmi = (
        price_score * HMI_COMPONENT_WEIGHTS["sale_price_index"]
        + completion_score * HMI_COMPONENT_WEIGHTS["completion_volume"]
        + unsold_score * HMI_COMPONENT_WEIGHTS["unsold_units"]
    )
    return round(hmi, 2)


def classify_signal(hmi: float) -> str:
    if hmi >= 1.0:
        return "strong_positive"
    if hmi >= 0.3:
        return "positive"
    if hmi > -0.3:
        return "neutral"
    if hmi > -1.0:
        return "negative"
    return "strong_negative"


def signal_label(signal: str) -> str:
    labels = {
        "strong_positive": "강한 우호",
        "positive": "우호",
        "neutral": "중립",
        "negative": "비우호",
        "strong_negative": "강한 비우호",
    }
    return labels.get(signal, "중립")


def market_phase(signal: str) -> str:
    phases = {
        "strong_positive": "회복 가속",
        "positive": "회복",
        "neutral": "혼조",
        "negative": "둔화",
        "strong_negative": "침체",
    }
    return phases.get(signal, "혼조")


def generate_insight(metrics: dict[str, float | None]) -> str:
    price = _coalesce_metric(metrics.get("price_yoy"))
    completion = _coalesce_metric(metrics.get("completion_yoy"))
    unsold = _coalesce_metric(metrics.get("unsold_yoy"))
    jeonse = _coalesce_metric(metrics.get("jeonse_yoy"))

    if price > 0 and completion > 0 and unsold < 0:
        return "실수요 기반 시장 회복으로 해석되며, 입주와 인테리어 수요가 함께 살아나는 강한 매출 기회 구간입니다."
    if price > 0 and unsold > 0:
        return "가격은 오르지만 미분양이 누적되고 있어 체감 수요는 제한적입니다. 선택적 지역 공략이 필요한 구간입니다."
    if price < 0 and unsold > 0:
        return "가격 약세와 재고 누적이 동반되어 단기 매출 하방 리스크가 큰 구간입니다. 보수적 영업 운영이 적합합니다."
    if completion > 0 and unsold < 0 and jeonse > 0:
        return "입주 물량 확대와 미분양 해소, 전세 강세가 함께 나타나 주거 이동 수요 기반의 우호적 환경으로 볼 수 있습니다."
    return "가격, 공급, 재고 신호가 엇갈리고 있어 방향성은 혼조입니다. 지역별·상품별 선택과 집중이 필요한 구간입니다."


def build_hanssem_macro_insight(snapshot: pd.DataFrame) -> dict[str, object]:
    metrics = get_latest_metrics(snapshot)
    score = calculate_hanssem_macro_score(metrics)
    signal = classify_signal(score)
    return {
        "score": score,
        "signal": signal,
        "signal_label": signal_label(signal),
        "market_phase": market_phase(signal),
        "insight": generate_insight(metrics),
        "metrics": metrics,
    }


def metadata_frame() -> pd.DataFrame:
    metadata = pd.DataFrame(indicator_metadata_rows())
    if metadata.empty:
        return metadata
    overrides = load_verification_overrides()
    for indicator_id, override in overrides.items():
        mask = metadata["indicator_id"] == indicator_id
        for field_name, field_value in override.items():
            if field_name in metadata.columns:
                indices = metadata.index[mask]
                if len(indices) == 0:
                    continue
                metadata[field_name] = metadata[field_name].astype(object)
                for idx in indices:
                    metadata.at[idx, field_name] = field_value

    metadata = metadata.rename(columns={"indicator_id": "indicator_code"})
    return metadata[
        [
            "indicator_code",
            "name_kr",
            "category",
            "region",
            "direction",
            "hanssem_logic",
            "verification_status",
            "verification_message",
            "last_verified_at",
            "sample_period",
            "sample_region",
            "error_type",
            "rows",
            "source_name",
            "provider",
            "api_type",
            "table_id",
            "h_rs_id",
            "h_form_id",
            "stat_code",
            "item_code",
            "region_level",
            "fallback_source_name",
            "fallback_provider",
            "fallback_api_type",
            "fallback_h_rs_id",
            "fallback_h_form_id",
            "fallback_verification_status",
            "fallback_verification_message",
            "fallback_last_verified_at",
            "fallback_sample_period",
            "fallback_sample_region",
            "fallback_error_type",
            "fallback_rows",
            "active",
            "notes",
        ]
    ]


def source_status_frame() -> pd.DataFrame:
    metadata = metadata_frame().rename(columns={"indicator_code": "indicator_id"})
    if metadata.empty:
        return metadata
    return metadata.rename(
        columns={
            "name_kr": "지표명",
            "category": "카테고리",
            "provider": "제공기관",
            "source_name": "데이터 소스",
            "api_type": "API 유형",
            "table_id": "table_id",
            "h_rs_id": "hRsId",
            "h_form_id": "hFormId",
            "stat_code": "stat_code",
            "item_code": "item_code",
            "region_level": "지역 수준",
            "verification_status": "검증 상태",
            "verification_message": "검증 메시지",
            "last_verified_at": "마지막 검증일",
            "sample_period": "샘플 기간",
            "sample_region": "샘플 지역",
            "error_type": "오류 유형",
            "rows": "rows",
            "fallback_source_name": "fallback_source",
            "fallback_provider": "fallback_provider",
            "fallback_api_type": "fallback_api_type",
            "fallback_h_rs_id": "fallback_hRsId",
            "fallback_h_form_id": "fallback_hFormId",
            "fallback_verification_status": "fallback_status",
            "fallback_verification_message": "fallback_message",
            "fallback_last_verified_at": "fallback_last_verified_at",
            "fallback_sample_period": "fallback_sample_period",
            "fallback_sample_region": "fallback_sample_region",
            "fallback_error_type": "fallback_error_type",
            "fallback_rows": "fallback_rows",
            "active": "활성화",
            "notes": "비고",
        }
    )


def _score_from_signal(signal: float) -> float:
    bounded = math.tanh(signal / 15.0)
    return round(50.0 + bounded * 35.0, 1)


def _safe_number(value: object) -> float:
    if pd.isna(value):
        return 0.0
    return float(value)


def _metric_value(latest: dict[str, pd.Series], indicator_code: str, column: str) -> float | None:
    row = latest.get(indicator_code)
    if row is None:
        return None
    value = row.get(column)
    if pd.isna(value):
        return None
    return float(value)


def _coalesce_metric(value: float | None) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return float(value)
