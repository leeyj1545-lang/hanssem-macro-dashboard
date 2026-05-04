# Hanssem Macro Dashboard

한샘 탑라인에 큰 영향을 줄 수 있는 부동산 매크로 지표를 API로 수집하고, SQLite에 적재한 뒤, Streamlit 대시보드로 보여주는 예제 프로젝트입니다.

## 포함 범위

- 수요: 기준금리, 주택담보대출 금리, 가계대출 잔액, 소비심리지표
- 공급: 인허가/착공/준공/미분양용 KOSIS 확장 포인트
- 거래량/가격: 아파트 매매 거래량, 전월세 거래량

## 아키텍처

1. `sources/`
   공식 API별 클라이언트
2. `pipeline.py`
   Extract -> Transform -> Load 실행
3. `db.py`
   SQLite 스키마/업서트
4. `dashboard.py`
   지표 카드, 추이 차트, 카테고리별 비교
5. `demo.py`
   API 키가 없어도 UI를 확인할 수 있는 데모 데이터 생성

## 빠른 시작

```powershell
Copy-Item .env.example .env
```

필수 패키지 설치 후:

```powershell
C:\Users\20151545\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pip install -e .
```

데모 데이터 적재:

```powershell
C:\Users\20151545\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m hanssem_macro_dashboard.pipeline demo
```

실제 API 적재:

```powershell
C:\Users\20151545\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m hanssem_macro_dashboard.pipeline run
```

대시보드 실행:

```powershell
C:\Users\20151545\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m streamlit run src/hanssem_macro_dashboard/dashboard.py
```

## 기본 DB

- 파일: `data/warehouse/hanssem_macro.db`
- 핵심 테이블
  - `indicator_observations`
  - `etl_runs`

## 지표 설계

| bucket | indicator_code | meaning |
| --- | --- | --- |
| demand | `base_rate` | 기준금리 |
| demand | `mortgage_rate` | 주택담보대출 금리 |
| demand | `household_loan_balance` | 가계대출 잔액 |
| demand | `consumer_sentiment` | 주택/소비 심리 대용 지표 |
| transaction_price | `apt_trade_volume` | 아파트 매매 거래량 |
| transaction_price | `apt_rent_volume` | 아파트 전월세 거래량 |

공급 관련 지표는 KOSIS 표 식별자만 확정하면 `KosisSource.fetch_series()` 호출만 추가해 바로 늘릴 수 있게 구성했습니다.

## 공식 데이터 소스

- 한국은행 ECOS Open API: <https://ecos.bok.or.kr/api/#/>
- 한국은행 공공데이터 안내: <https://www.data.go.kr/en/data/15059635/openapi.do>
- 국토교통부 아파트 매매 실거래가 API: <https://www.data.go.kr/data/15126469/openapi.do>
- 국토교통부 아파트 전월세 실거래가 API: <https://www.data.go.kr/data/15126474/openapi.do>
- KOSIS Open API 안내: <https://kosis.kr/openapi/index/index.jsp?serviceCD=2>

## 현재 가정

- 매매/전월세 거래량은 원천 API의 건별 응답을 월 단위로 집계합니다.
- 한샘 탑라인과 직접 연결되는 전국 지표를 기본값으로 두고, 지역 확장은 `region_code` 컬럼으로 대응합니다.
- 일부 ECOS/KOSIS 세부 통계코드는 기관 정책에 따라 바뀔 수 있으므로 `config.py`의 코드 매핑을 수정 가능하게 열어두었습니다.
