from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
DB_PATH = WAREHOUSE_DIR / "hanssem_macro.db"
VERIFICATION_STATE_PATH = WAREHOUSE_DIR / "source_verification.json"

VERIFICATION_STATUSES = (
    "pending",
    "pending_condition_check",
    "verified",
    "failed_auth",
    "failed_code",
    "failed_empty",
    "failed_parse",
    "failed_network",
    "demo_only",
)
DEFAULT_REGIONS = ["전국", "수도권", "서울", "경기", "인천", "부산", "대구", "대전", "광주"]


@dataclass(frozen=True)
class SourceDetail:
    source_name: str
    provider: str
    api_type: str
    org_id: str = ""
    table_id: str = ""
    h_rs_id: str = ""
    h_form_id: str = ""
    stat_code: str = ""
    item_code: str = ""
    obj_l1: str = ""
    obj_l2: str = ""
    obj_l3: str = ""
    region_level: str = "전국"
    frequency: str = "M"
    verification_status: str = "pending"
    verification_message: str = ""
    last_verified_at: str = ""
    sample_period: str = ""
    sample_region: str = ""
    error_type: str = ""
    rows: int = 0
    debug_sample_keys: list[str] = field(default_factory=list)
    debug_sample_rows: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class IndicatorDefinition:
    indicator_id: str
    name_kr: str
    category: str
    source: str
    frequency: str
    unit: str
    region: str
    direction: str
    hanssem_logic: str
    source_series_code: str
    source_detail: SourceDetail
    fallback_source: SourceDetail | None = None
    region_code: str = "KR"
    regions: list[str] = field(default_factory=lambda: DEFAULT_REGIONS.copy())
    notes: str = ""
    active: bool = True


def _detail(**kwargs: str) -> SourceDetail:
    return SourceDetail(**kwargs)


INDICATORS = {
    "base_rate": IndicatorDefinition(
        indicator_id="base_rate",
        name_kr="기준금리",
        category="금융",
        source="ECOS",
        frequency="M",
        unit="%",
        region="전국",
        direction="negative",
        hanssem_logic="금리 상승 시 주택 구매 및 리모델링 의사결정 부담이 커집니다.",
        source_series_code="722Y001/0101000",
        source_detail=_detail(
            source_name="한국은행 ECOS 기준금리",
            provider="한국은행",
            api_type="Open API JSON",
            stat_code="722Y001",
            item_code="0101000",
            region_level="전국",
            frequency="M",
            verification_status="verified",
            verification_message="시리즈 코드 매핑 완료",
            last_verified_at=str(date.today()),
            sample_period="2026-03",
            sample_region="전국",
        ),
        notes="대표 금융 부담 지표",
    ),
    "mortgage_rate": IndicatorDefinition(
        indicator_id="mortgage_rate",
        name_kr="주택담보대출 금리",
        category="금융",
        source="ECOS",
        frequency="M",
        unit="%",
        region="전국",
        direction="negative",
        hanssem_logic="주담대 금리 상승은 주택 구매와 인테리어 투자 의사결정을 지연시킬 수 있습니다.",
        source_series_code="121Y006/BECBLA01",
        source_detail=_detail(
            source_name="한국은행 ECOS 예금은행 주택담보대출 금리",
            provider="한국은행",
            api_type="Open API JSON",
            stat_code="121Y006",
            item_code="BECBLA01",
            region_level="전국",
            frequency="M",
            verification_status="verified",
            verification_message="시리즈 코드 매핑 완료",
            last_verified_at=str(date.today()),
            sample_period="2026-03",
            sample_region="전국",
        ),
        notes="대표 금융 부담 지표",
    ),
    "consumer_sentiment": IndicatorDefinition(
        indicator_id="consumer_sentiment",
        name_kr="소비자심리지수",
        category="수요",
        source="ECOS",
        frequency="M",
        unit="지수",
        region="전국",
        direction="positive",
        hanssem_logic="소비심리 개선은 고관여 소비와 리모델링 집행 확률을 높일 수 있습니다.",
        source_series_code="511Y002/FME",
        source_detail=_detail(
            source_name="한국은행 ECOS 소비자심리지수",
            provider="한국은행",
            api_type="Open API JSON",
            stat_code="511Y002",
            item_code="FME",
            region_level="전국",
            frequency="M",
            verification_status="verified",
            verification_message="시리즈 코드 매핑 완료",
            last_verified_at=str(date.today()),
            sample_period="2026-03",
            sample_region="전국",
        ),
        notes="주택구입심리 API 확정 전 대용 지표",
    ),
    "household_loan_balance": IndicatorDefinition(
        indicator_id="household_loan_balance",
        name_kr="가계대출 잔액",
        category="수요",
        source="ECOS",
        frequency="M",
        unit="십억원",
        region="전국",
        direction="positive",
        hanssem_logic="실수요 대출 흐름 회복은 주택 관련 소비의 자금조달 여건 개선 신호가 될 수 있습니다.",
        source_series_code="101Y004/BBHA00",
        source_detail=_detail(
            source_name="한국은행 ECOS 가계대출 잔액",
            provider="한국은행",
            api_type="Open API JSON",
            stat_code="101Y004",
            item_code="BBHA00",
            region_level="전국",
            frequency="M",
            verification_status="verified",
            verification_message="시리즈 코드 매핑 완료",
            last_verified_at=str(date.today()),
            sample_period="2026-03",
            sample_region="전국",
        ),
        notes="주담대 잔액 대용 지표",
    ),
    "apt_trade_volume": IndicatorDefinition(
        indicator_id="apt_trade_volume",
        name_kr="아파트 매매거래량",
        category="거래",
        source="MOLIT",
        frequency="M",
        unit="건",
        region="주요 지역",
        direction="positive",
        hanssem_logic="매매거래 증가는 이사와 입주, 리모델링 수요 발생 가능성을 높입니다.",
        source_series_code="RTMS_AptTrade",
        source_detail=_detail(
            source_name="국토교통부 아파트 매매 실거래가",
            provider="국토교통부",
            api_type="REST XML",
            table_id="RTMSDataSvcAptTrade",
            item_code="LAWD_CD+DEAL_YMD",
            region_level="시군구",
            frequency="M",
            verification_status="verified",
            verification_message="샘플 호출 방식 검증 완료",
            last_verified_at=str(date.today()),
            sample_period="2026-03",
            sample_region="서울 종로구",
        ),
        region_code="KR_MAJOR",
    ),
    "apt_rent_volume": IndicatorDefinition(
        indicator_id="apt_rent_volume",
        name_kr="아파트 전월세거래량",
        category="거래",
        source="MOLIT",
        frequency="M",
        unit="건",
        region="주요 지역",
        direction="positive",
        hanssem_logic="전월세 이동은 부분 수리와 가구 교체 수요로 이어질 수 있습니다.",
        source_series_code="RTMS_AptRent",
        source_detail=_detail(
            source_name="국토교통부 아파트 전월세 실거래가",
            provider="국토교통부",
            api_type="REST XML",
            table_id="RTMSDataSvcAptRent",
            item_code="LAWD_CD+DEAL_YMD",
            region_level="시군구",
            frequency="M",
            verification_status="verified",
            verification_message="샘플 호출 방식 검증 완료",
            last_verified_at=str(date.today()),
            sample_period="2026-03",
            sample_region="서울 강남구",
        ),
        region_code="KR_MAJOR",
    ),
    "sale_price_index": IndicatorDefinition(
        indicator_id="sale_price_index",
        name_kr="매매가격지수",
        category="가격",
        source="R-ONE",
        frequency="M",
        unit="지수",
        region="전국",
        direction="positive",
        hanssem_logic="주택가격 상승은 자산효과를 통해 인테리어 투자 여력을 높일 수 있습니다.",
        source_series_code="sale_price_index_placeholder",
        source_detail=_detail(
            source_name="한국부동산원 전국주택가격동향조사 매매가격지수",
            provider="R-ONE",
            api_type="Open API REST",
            stat_code="SttsApiTblData.do",
            table_id="A_2024_00178",
            item_code="지수",
            region_level="전국/시도",
            frequency="M",
            verification_status="pending",
            verification_message="R-ONE 인증키와 통계코드 매핑 확인 필요",
            sample_period="최근 3개월",
            sample_region="전국",
        ),
        fallback_source=_detail(
            source_name="한국부동산원 전국주택가격동향조사 월간 공표 엑셀",
            provider="R-ONE",
            api_type="file_download",
            stat_code="전국주택가격동향조사 월간 공표자료",
            item_code="매매가격지수",
            region_level="전국/시도",
            frequency="M",
            verification_status="pending",
            verification_message="공개자료실 엑셀 다운로드 경로 확인 필요",
            sample_period="최근 24개월",
            sample_region="전국",
        ),
        notes="가격 지표 1차 연결 대상",
        active=False,
    ),
    "jeonse_price_index": IndicatorDefinition(
        indicator_id="jeonse_price_index",
        name_kr="전세가격지수",
        category="가격",
        source="R-ONE",
        frequency="M",
        unit="지수",
        region="전국",
        direction="positive",
        hanssem_logic="전세가격 강세는 이동 수요와 주거 관련 소비 압력을 함께 보여줄 수 있습니다.",
        source_series_code="jeonse_price_index_placeholder",
        source_detail=_detail(
            source_name="한국부동산원 전국주택가격동향조사 전세가격지수",
            provider="R-ONE",
            api_type="Open API REST",
            stat_code="SttsApiTblData.do",
            table_id="A_2024_00182",
            item_code="지수",
            region_level="전국/시도",
            frequency="M",
            verification_status="pending",
            verification_message="R-ONE 인증키와 통계코드 매핑 확인 필요",
            sample_period="최근 3개월",
            sample_region="전국",
        ),
        fallback_source=_detail(
            source_name="한국부동산원 전국주택가격동향조사 월간 공표 엑셀",
            provider="R-ONE",
            api_type="file_download",
            stat_code="전국주택가격동향조사 월간 공표자료",
            item_code="전세가격지수",
            region_level="전국/시도",
            frequency="M",
            verification_status="pending",
            verification_message="공개자료실 엑셀 다운로드 경로 확인 필요",
            sample_period="최근 24개월",
            sample_region="전국",
        ),
        notes="가격 지표 1차 연결 대상",
        active=False,
    ),
    "completion_volume": IndicatorDefinition(
        indicator_id="completion_volume",
        name_kr="준공 물량",
        category="공급",
        source="KOSIS",
        frequency="M",
        unit="호",
        region="전국",
        direction="positive",
        hanssem_logic="준공 증가는 실제 입주와 인테리어 시공 기회 확대로 이어질 수 있습니다.",
        source_series_code="completion_placeholder",
        source_detail=_detail(
            source_name="KOSIS 주택건설실적통계 준공실적",
            provider="KOSIS",
            api_type="Open API JSON/XML",
            org_id="116",
            table_id="DT_MLTM_5374",
            item_code="13103766974T1",
            obj_l1="13102766974A.0001",
            obj_l2="13102766974B.0001",
            region_level="전국/시도",
            frequency="M",
            verification_status="pending",
            verification_message="org_id=116 기준 itmId/objL1/objL2 후보 반영 후 조합 검증 필요",
            sample_period="최근 3개월",
            sample_region="전국",
        ),
        fallback_source=_detail(
            source_name="국토교통 통계누리 주택건설실적통계(준공)",
            provider="MOLIT_STAT",
            api_type="file_download",
            h_rs_id="468",
            h_form_id="5372",
            region_level="전국/시도",
            frequency="M",
            verification_status="pending",
            verification_message="통계누리 파일 다운로드 fallback 검증 필요",
            sample_period="최근 24개월",
            sample_region="전국",
        ),
        notes="공급 우선 지표 1차 연결 대상",
        active=False,
    ),
    "housing_permits": IndicatorDefinition(
        indicator_id="housing_permits",
        name_kr="주택 인허가",
        category="공급",
        source="KOSIS",
        frequency="M",
        unit="호",
        region="전국",
        direction="positive",
        hanssem_logic="인허가 증가는 중장기 공급 방향과 향후 시공 시장 기회 신호입니다.",
        source_series_code="housing_permits_placeholder",
        source_detail=_detail(
            source_name="KOSIS 주택건설실적통계 인허가실적",
            provider="KOSIS",
            api_type="Open API JSON/XML",
            org_id="101",
            table_id="DT_1YL7501E",
            item_code="13103871094T1",
            obj_l1="13102871094A.0002",
            region_level="전국/시도",
            frequency="M",
            verification_status="pending",
            verification_message="KOSIS 필드 매핑 검증 필요",
            sample_period="최근 3개월",
            sample_region="전국",
        ),
        fallback_source=_detail(
            source_name="국토교통 통계누리 주택건설실적통계(인허가)",
            provider="MOLIT_STAT",
            api_type="file_download",
            h_rs_id="31",
            h_form_id="1946",
            region_level="전국/시도",
            frequency="M",
            verification_status="pending",
            verification_message="인허가 통계누리 파일 다운로드 fallback 검증 필요",
            sample_period="최근 24개월",
            sample_region="전국",
        ),
        notes="공급 우선 지표 1차 연결 대상",
        active=False,
    ),
    "unsold_units": IndicatorDefinition(
        indicator_id="unsold_units",
        name_kr="미분양 물량",
        category="리스크",
        source="MOLIT_STAT",
        frequency="M",
        unit="호",
        region="전국",
        direction="negative",
        hanssem_logic="미분양 증가는 지역 수요 둔화와 신규 주거 투자 심리 악화 가능성을 시사합니다.",
        source_series_code="unsold_units_placeholder",
        source_detail=_detail(
            source_name="국토교통 통계누리 미분양주택현황보고",
            provider="국토교통부 통계누리",
            api_type="공식 통계 연계 예정",
            table_id="pending",
            item_code="미분양주택현황",
            region_level="전국/시군구",
            frequency="M",
            verification_status="pending",
            verification_message="공식 호출 경로 또는 다운로드 방식 검증 필요",
            sample_period="최근 3개월",
            sample_region="전국",
        ),
        fallback_source=_detail(
            source_name="국토교통 통계누리 미분양주택현황보고",
            provider="MOLIT_STAT",
            api_type="file_download",
            h_rs_id="32",
            h_form_id="2082",
            region_level="전국/시도",
            frequency="M",
            verification_status="pending",
            verification_message="미분양 통계누리 파일 다운로드 fallback 검증 필요",
            sample_period="최근 24개월",
            sample_region="전국",
        ),
        notes="공급 우선 지표 1차 연결 대상",
        active=False,
    ),
}

ECOS_INDICATOR_IDS = [
    "base_rate",
    "mortgage_rate",
    "consumer_sentiment",
    "household_loan_balance",
]

MOLIT_INDICATOR_IDS = [
    "apt_trade_volume",
    "apt_rent_volume",
]

SUPPLY_PRIORITY_INDICATOR_IDS = [
    "completion_volume",
    "housing_permits",
    "unsold_units",
]

PRICE_PRIORITY_INDICATOR_IDS = [
    "sale_price_index",
    "jeonse_price_index",
]

VERIFICATION_TARGET_INDICATOR_IDS = SUPPLY_PRIORITY_INDICATOR_IDS + PRICE_PRIORITY_INDICATOR_IDS

EXECUTIVE_CARDS = [
    ("수요", "시장 수요 온도"),
    ("거래", "거래 활성도"),
    ("금융", "금융 부담"),
    ("공급", "공급 파이프라인"),
    ("topline", "한샘 탑라인 영향 요약"),
]


def indicator_metadata_rows() -> list[dict]:
    rows: list[dict] = []
    for definition in INDICATORS.values():
        row = asdict(definition)
        row.update(row.pop("source_detail"))
        fallback = row.pop("fallback_source", None)
        if isinstance(fallback, dict):
            row.update({f"fallback_{key}": value for key, value in fallback.items()})
        rows.append(row)
    return rows


def load_verification_overrides() -> dict[str, dict]:
    if not VERIFICATION_STATE_PATH.exists():
        return {}
    try:
        return json.loads(VERIFICATION_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_verification_overrides(overrides: dict[str, dict]) -> None:
    VERIFICATION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    VERIFICATION_STATE_PATH.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


ECOS_API_KEY = get_env("ECOS_API_KEY")
DATA_GO_KR_API_KEY = get_env("DATA_GO_KR_API_KEY")
KOSIS_API_KEY = get_env("KOSIS_API_KEY")
RONE_API_KEY = get_env("RONE_API_KEY")
BIGQUERY_PROJECT_ID = get_env("BIGQUERY_PROJECT_ID") or get_env("BQ_PROJECT_ID")
BIGQUERY_DATASET = get_env("BIGQUERY_DATASET") or get_env("BQ_DATASET", "hanssem_macro")
BIGQUERY_LOCATION = get_env("BIGQUERY_LOCATION") or get_env("BQ_LOCATION", "asia-northeast3")

ECOS_BASE_URL = get_env("ECOS_BASE_URL", "https://ecos.bok.or.kr/api")
MOLIT_APT_TRADE_URL = get_env(
    "MOLIT_APT_TRADE_URL",
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
)
MOLIT_APT_RENT_URL = get_env(
    "MOLIT_APT_RENT_URL",
    "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
)
KOSIS_BASE_URL = get_env("KOSIS_BASE_URL", "https://kosis.kr/openapi/statisticsData.do")
RONE_BASE_URL = get_env("RONE_BASE_URL", "https://www.reb.or.kr/r-one/openapi")
