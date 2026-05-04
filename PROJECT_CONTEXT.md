# Project Context

## Goal

한샘의 탑라인에 큰 영향을 주는 선행/동행 지표를 공식 데이터 소스 API로 수집하고, DB에 ETL한 뒤, 경영진이 해석 가능한 대시보드로 시각화하는 프로젝트.

## Current Scope

- 금융
  - 기준금리
  - 주택담보대출 금리
- 수요
  - 소비자심리지수
  - 가계대출 잔액
- 거래
  - 매매거래량
  - 전월세거래량
- 가격
  - 매매가격지수
  - 전세가격지수
- 공급
  - 준공 물량
  - 주택 인허가
- 리스크
  - 미분양 물량

## Current Implementation

- Python 프로젝트 스캐폴딩 완료
- SQLite 적재 구조 완료
- 데모 데이터 적재 가능
- Streamlit 임원용 대시보드 기본 화면 구현
- API 소스 연결 초안 구현
  - ECOS
  - 국토교통부 실거래 API
  - KOSIS 확장용 로더
- 전월 대비 / 전년동월 대비 계산 로직 구현
- 룰 기반 한샘 탑라인 해석 카드 구현

## Important Files

- [README.md](C:/Users/20151545/Documents/Codex/2026-04-30-api-db-etl/README.md)
- [src/hanssem_macro_dashboard/config.py](C:/Users/20151545/Documents/Codex/2026-04-30-api-db-etl/src/hanssem_macro_dashboard/config.py)
- [src/hanssem_macro_dashboard/pipeline.py](C:/Users/20151545/Documents/Codex/2026-04-30-api-db-etl/src/hanssem_macro_dashboard/pipeline.py)
- [src/hanssem_macro_dashboard/dashboard.py](C:/Users/20151545/Documents/Codex/2026-04-30-api-db-etl/src/hanssem_macro_dashboard/dashboard.py)
- [src/hanssem_macro_dashboard/sources/ecos.py](C:/Users/20151545/Documents/Codex/2026-04-30-api-db-etl/src/hanssem_macro_dashboard/sources/ecos.py)
- [src/hanssem_macro_dashboard/sources/molit.py](C:/Users/20151545/Documents/Codex/2026-04-30-api-db-etl/src/hanssem_macro_dashboard/sources/molit.py)
- [src/hanssem_macro_dashboard/sources/kosis.py](C:/Users/20151545/Documents/Codex/2026-04-30-api-db-etl/src/hanssem_macro_dashboard/sources/kosis.py)

## Open Decisions

- 공급 관련 지표를 어떤 공식 API/KOSIS 테이블로 확정할지
- 전국 기준으로 볼지, 수도권/광역시 중심으로 볼지
- 해석 카드 규칙을 더 정교하게 고도화할지
- 실제 운영 DB를 SQLite로 유지할지 Postgres로 옮길지

## Constraints

- 공식 데이터 소스 중심으로 구성
- API 키가 없는 상태에서도 데모 데이터로 화면 확인 가능해야 함
- ChatGPT가 다음 대화에서 바로 이해할 수 있도록 상태 문서 유지

## Next Best Tasks

1. 공급 우선 지표 3개용 KOSIS/공공데이터 API 표준 코드 확정
2. 각 지표의 실제 시계열 코드 검증
3. 가격 지표 공식 소스 연결
4. 해석 카드 규칙 정교화
5. 운영용 스케줄러 또는 배치 방식 결정
