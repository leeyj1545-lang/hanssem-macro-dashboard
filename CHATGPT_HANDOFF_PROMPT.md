# ChatGPT Handoff Prompt

아래 내용을 새 대화의 첫 메시지로 붙여넣으면, ChatGPT가 이 프로젝트를 빠르게 이해하고 이어서 피드백하기 쉽다.

```text
나는 한샘의 탑라인에 영향을 주는 부동산 거시 지표 ETL + 대시보드 프로젝트를 진행 중이야.

프로젝트 목표:
- 공식 API에서 거시 지표를 수집
- Python으로 ETL
- DB 적재
- 대시보드 시각화

현재 상태:
- ETL/DB/대시보드 기본 골격은 구현됨
- 데모 데이터로 실행 확인됨
- 실제 API 연결은 일부 시리즈 코드 검증이 더 필요함

중요 파일:
- README.md
- PROJECT_CONTEXT.md
- STATUS.md
- src/hanssem_macro_dashboard/config.py
- src/hanssem_macro_dashboard/pipeline.py
- src/hanssem_macro_dashboard/dashboard.py

이번에 받고 싶은 도움:
- 현재 구조 리뷰
- 개선 우선순위 제안
- 공급 지표 API/시리즈 설계 검토
- 대시보드 지표 구성 개선

응답 방식:
- 먼저 전체 구조를 짧게 요약
- 그다음 가장 중요한 문제점/리스크를 우선순위로 정리
- 바로 실행 가능한 다음 작업 3개를 제안
```
