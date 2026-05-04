from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from hanssem_macro_dashboard.analytics import (
    build_hanssem_macro_insight,
    build_category_scores,
    build_interpretation,
    compute_changes,
    enrich_with_metadata,
    executive_card_snapshot,
    latest_snapshot,
    source_status_frame,
)
from hanssem_macro_dashboard.config import DB_PATH, PRICE_PRIORITY_INDICATOR_IDS, SUPPLY_PRIORITY_INDICATOR_IDS, VERIFICATION_STATUSES
from hanssem_macro_dashboard.db import initialize_database, load_etl_run_history, load_observations


st.set_page_config(page_title="한샘 탑라인 선행/동행 지표 대시보드", layout="wide")


@st.cache_data(show_spinner=False)
def get_data() -> pd.DataFrame:
    initialize_database(DB_PATH)
    df = load_observations(DB_PATH)
    if df.empty:
        return df
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    return compute_changes(enrich_with_metadata(df))


@st.cache_data(show_spinner=False)
def get_source_status() -> pd.DataFrame:
    return source_status_frame()


@st.cache_data(show_spinner=False)
def get_etl_history() -> pd.DataFrame:
    initialize_database(DB_PATH)
    return load_etl_run_history(DB_PATH)


def render_executive_cards(cards: pd.DataFrame) -> None:
    st.subheader("임원용 요약")
    cols = st.columns(5)
    for col, row in zip(cols, cards.itertuples(index=False)):
        col.markdown(
            f"""
            <div style="border:1px solid #d9dfd6;border-radius:18px;padding:18px 16px;background:linear-gradient(180deg,#faf7ef 0%,#ffffff 100%);min-height:180px;">
                <div style="font-size:13px;color:#56615a;margin-bottom:8px;">{row.title}</div>
                <div style="font-size:34px;font-weight:700;color:#1f2c24;line-height:1;">{row.current_score:.1f}</div>
                <div style="font-size:12px;color:#6e786f;margin-top:4px;">현재 점수</div>
                <div style="margin-top:16px;font-size:14px;color:#2d3c32;">전월 대비 <strong>{format_points(row.mom_delta)}</strong></div>
                <div style="margin-top:8px;font-size:14px;color:#2d3c32;">전년동월 대비 <strong>{format_points(row.yoy_delta)}</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_interpretation(summary_text: str) -> None:
    st.subheader("한샘 영향 해석")
    st.info(summary_text)


def render_hanssem_macro_panel(insight: dict[str, object]) -> None:
    st.subheader("한샘 영향 지수")
    score_col, signal_col, phase_col = st.columns(3)
    score_col.metric("HMI", f"{float(insight['score']):.2f}")
    signal_col.metric("신호 강도", str(insight["signal_label"]))
    phase_col.metric("시장 국면", str(insight["market_phase"]))

    metrics = insight.get("metrics", {})
    metric_cols = st.columns(3)
    metric_cols[0].metric("매매가격 YoY", format_pct(metrics.get("price_yoy")))
    metric_cols[1].metric("준공 YoY", format_pct(metrics.get("completion_yoy")))
    metric_cols[2].metric("미분양 YoY", format_pct(metrics.get("unsold_yoy")))
    st.info(str(insight["insight"]))


def render_priority_note(snapshot: pd.DataFrame) -> None:
    supply_names = snapshot[snapshot["indicator_code"].isin(SUPPLY_PRIORITY_INDICATOR_IDS)]["display_name"].tolist()
    price_names = snapshot[snapshot["indicator_code"].isin(PRICE_PRIORITY_INDICATOR_IDS)]["display_name"].tolist()
    st.caption(
        "공급 우선 지표: "
        + (", ".join(supply_names) if supply_names else "준공 물량, 주택 인허가, 미분양 물량")
        + " | 가격 연결 대상: "
        + (", ".join(price_names) if price_names else "매매가격지수, 전세가격지수")
    )


def render_category_trends(df: pd.DataFrame) -> None:
    st.subheader("카테고리 추이")
    categories = st.multiselect(
        "카테고리 선택",
        options=sorted(df["category"].dropna().unique().tolist()),
        default=["금융", "수요", "거래", "공급"] if "공급" in df["category"].unique() else sorted(df["category"].dropna().unique().tolist())[:4],
    )
    filtered = df[df["category"].isin(categories)].copy()
    fig = px.line(
        filtered,
        x="observation_date",
        y="value",
        color="display_name",
        facet_row="category",
        markers=True,
        title="지표별 추이",
    )
    fig.update_layout(height=max(500, 220 * max(1, len(categories))), legend_title_text="지표")
    st.plotly_chart(fig, use_container_width=True)


def render_indicator_table(snapshot: pd.DataFrame) -> None:
    st.subheader("지표 상세")
    table = snapshot[
        [
            "category",
            "display_name",
            "value",
            "unit",
            "mom_pct",
            "yoy_pct",
            "direction",
            "verification_status",
            "source_name",
            "hanssem_logic",
            "observation_date",
        ]
    ].copy()
    table = table.rename(
        columns={
            "category": "카테고리",
            "display_name": "지표",
            "value": "현재값",
            "unit": "단위",
            "mom_pct": "전월 대비 %",
            "yoy_pct": "전년동월 대비 %",
            "direction": "방향성",
            "verification_status": "검증 상태",
            "source_name": "데이터 소스",
            "hanssem_logic": "한샘 해석 로직",
            "observation_date": "기준월",
        }
    )
    st.dataframe(table.sort_values(["카테고리", "지표"]), use_container_width=True)


def render_source_status() -> None:
    st.subheader("데이터 소스 상태")
    status = get_source_status().copy()
    if status.empty:
        st.caption("등록된 지표 메타데이터가 없습니다.")
        return

    selected_status = st.multiselect(
        "검증 상태 필터",
        options=list(VERIFICATION_STATUSES),
        default=list(VERIFICATION_STATUSES),
    )
    filtered = status[status["검증 상태"].isin(selected_status)].copy()
    preferred_columns = [
        "indicator_id",
        "지표명",
        "카테고리",
        "검증 상태",
        "오류 유형",
        "rows",
        "검증 메시지",
        "샘플 기간",
        "샘플 지역",
        "제공기관",
        "데이터 소스",
        "fallback_provider",
        "fallback_source",
        "fallback_status",
        "fallback_error_type",
        "fallback_rows",
        "table_id",
        "hRsId",
        "hFormId",
        "stat_code",
        "item_code",
        "마지막 검증일",
        "활성화",
        "비고",
    ]
    filtered = filtered[[column for column in preferred_columns if column in filtered.columns]]
    st.dataframe(filtered.sort_values(["검증 상태", "카테고리", "indicator_id"]), use_container_width=True)


def render_etl_history() -> None:
    st.subheader("ETL 실행 이력")
    history = get_etl_history().copy()
    if history.empty:
        st.caption("저장된 ETL 실행 이력이 없습니다.")
        return

    latest_run = history["run_started_at"].max()
    st.caption(f"최근 실행일: {latest_run}")
    view = history[
        [
            "run_started_at",
            "indicator_id",
            "verification_status",
            "collect_status",
            "stage_status",
            "rows_loaded",
            "message",
        ]
    ].rename(
        columns={
            "run_started_at": "실행시각",
            "indicator_id": "지표",
            "verification_status": "검증 상태",
            "collect_status": "수집 상태",
            "stage_status": "적재 단계",
            "rows_loaded": "rows_loaded",
            "message": "메시지",
        }
    )
    st.dataframe(view, use_container_width=True)


def format_points(value: object) -> str:
    if pd.isna(value):
        return "-"
    return f"{float(value):+.1f}p"


def format_pct(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):+.1f}%"


def main() -> None:
    st.title("한샘 탑라인 선행/동행 지표 대시보드")
    st.caption("거시지표 변화가 한샘 매출 영향 방향과 하반기 전략 판단으로 이어지도록 설계한 임원용 프로토타입")

    df = get_data()
    if df.empty:
        st.warning("데이터가 없습니다. `python -m hanssem_macro_dashboard.pipeline demo` 또는 `run`을 먼저 실행해 주세요.")
        render_source_status()
        st.stop()

    scores = build_category_scores(df)
    cards = executive_card_snapshot(scores)
    snapshot = latest_snapshot(df)
    hanssem_insight = build_hanssem_macro_insight(snapshot)

    render_hanssem_macro_panel(hanssem_insight)
    render_executive_cards(cards)
    render_interpretation(build_interpretation(snapshot))
    render_priority_note(snapshot)
    render_category_trends(df)
    render_indicator_table(snapshot)
    render_source_status()
    render_etl_history()


if __name__ == "__main__":
    main()
