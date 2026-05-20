# GCP Scheduling Guide

이 프로젝트의 BigQuery ETL을 자동 최신화하려면 아래 구조로 운영하면 됩니다.

```text
Cloud Scheduler
-> Cloud Run Job 실행
-> python -m hanssem_macro_dashboard.run_bq_job
-> BigQuery staging / production 적재
```

## 1. 현재 구조

- 실행기: `Cloud Run Job`
- 스케줄러: `Cloud Scheduler`
- 대상 프로젝트: `cellular-client-310600`
- 대상 데이터셋: `Yunjae_Workspace`
- 실행 리전:
  - BigQuery: `US`
  - Cloud Run / Scheduler: `us-central1`

Colab은 수동 검증용으로 남기고, 운영 자동화는 GCP에서 돌리는 구조를 권장합니다.

## 2. 필요한 환경변수

Cloud Run Job에 아래 환경변수가 필요합니다.

- `BQ_PROJECT_ID`
- `BQ_DATASET`
- `BQ_LOCATION`
- `KOSIS_API_KEY`
- `RONE_API_KEY`
- `ECOS_API_KEY` (optional)
- `DATA_GO_KR_API_KEY` (optional)

현재 권장값:

```text
BQ_PROJECT_ID=cellular-client-310600
BQ_DATASET=Yunjae_Workspace
BQ_LOCATION=US
```

## 3. 진입점

Cloud Run Job은 아래 모듈을 실행합니다.

```text
python -m hanssem_macro_dashboard.run_bq_job
```

이 모듈은:

1. BigQuery 데이터마트 초기화
2. `pipeline run-bq` 실행

을 순서대로 수행합니다.

## 4. Cloud Shell에서 처음 1회 해야 할 일

### 4-1. 프로젝트 선택

```bash
gcloud config set project cellular-client-310600
```

### 4-2. GitHub 코드 받기

```bash
git clone https://github.com/leeyj1545-lang/hanssem-macro-dashboard.git
cd hanssem-macro-dashboard
```

### 4-3. Artifact Registry 저장소 생성

```bash
gcloud artifacts repositories create hanssem-etl \
  --repository-format=docker \
  --location=us \
  --description="Hanssem macro ETL images"
```

이미 있으면 `already exists`가 나올 수 있고, 그 경우 그대로 다음 단계로 가면 됩니다.

### 4-4. 컨테이너 이미지 빌드

```bash
gcloud builds submit \
  --tag us-docker.pkg.dev/cellular-client-310600/hanssem-etl/hanssem-macro-dashboard:latest
```

## 5. Cloud Run Job 생성

### 5-1. 최소 버전

```bash
gcloud run jobs create hanssem-macro-etl \
  --image us-docker.pkg.dev/cellular-client-310600/hanssem-etl/hanssem-macro-dashboard:latest \
  --region us-central1 \
  --tasks 1 \
  --max-retries 1 \
  --task-timeout 3600s \
  --set-env-vars BQ_PROJECT_ID=cellular-client-310600,BQ_DATASET=Yunjae_Workspace,BQ_LOCATION=US \
  --set-env-vars KOSIS_API_KEY=YOUR_KOSIS_API_KEY,RONE_API_KEY=YOUR_RONE_API_KEY \
  --set-env-vars ECOS_API_KEY=YOUR_ECOS_API_KEY,DATA_GO_KR_API_KEY=YOUR_DATA_GO_KR_API_KEY
```

### 5-2. 추천

실제 운영에서는 API 키를 `--set-env-vars` 대신 `Secret Manager`로 넘기는 편이 더 안전합니다.
하지만 첫 세팅은 위 방식이 가장 단순합니다.

## 6. Job 수동 테스트

스케줄 걸기 전에 먼저 사람이 직접 한 번 실행합니다.

```bash
gcloud run jobs execute hanssem-macro-etl --region us-central1 --wait
```

성공하면 BigQuery에 새 `run_id`가 들어갑니다.

## 7. Cloud Scheduler 생성

### 7-1. 프로젝트 번호 확인

```bash
PROJECT_ID=cellular-client-310600
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
echo $PROJECT_NUMBER
```

### 7-2. 매일 오전 9시 실행 스케줄 생성

```bash
gcloud scheduler jobs create http hanssem-macro-etl-daily \
  --location us-central1 \
  --schedule "0 9 * * *" \
  --time-zone "Asia/Seoul" \
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/us-central1/jobs/hanssem-macro-etl:run" \
  --http-method POST \
  --oauth-service-account-email "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
```

의미:

- 매일
- 한국시간 오전 9시
- `hanssem-macro-etl` Cloud Run Job 실행

## 8. Scheduler 수동 테스트

```bash
gcloud scheduler jobs run hanssem-macro-etl-daily --location us-central1
```

## 9. 성공 확인

### 9-1. BigQuery 적재 상태

```sql
SELECT
  indicator_id,
  COUNT(*) AS cnt,
  MAX(date) AS latest_date
FROM `cellular-client-310600.Yunjae_Workspace.macro_indicator_observations`
GROUP BY indicator_id
ORDER BY indicator_id;
```

### 9-2. ETL 실행 이력

```sql
SELECT
  run_id,
  indicator_id,
  stage_status,
  rows_loaded,
  latest_period,
  started_at,
  finished_at
FROM `cellular-client-310600.Yunjae_Workspace.etl_run_history`
ORDER BY started_at DESC, indicator_id;
```

## 10. 권한 참고

실제로는 아래 권한이 필요할 수 있습니다.

- Cloud Build 관련 권한
- Artifact Registry Writer / Reader
- Cloud Run Admin
- Cloud Scheduler Admin
- Service Account User
- BigQuery Job User / Data Editor

조직 정책에 따라 별도 승인이나 관리자 지원이 필요할 수 있습니다.

## 11. 권장 운영 흐름

```text
코드 수정
-> GitHub push
-> Cloud Build로 새 이미지 빌드
-> Cloud Run Job은 latest 이미지 사용
-> Scheduler는 기존 Job을 계속 호출
```

즉, 스케줄은 한 번 만들고, 이후에는 이미지 재배포만 반복하면 됩니다.
