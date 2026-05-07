# GCP Scheduling Guide

이 프로젝트의 BigQuery ETL을 자동으로 돌리기 위한 운영 기준은 다음과 같습니다.

```text
Cloud Run Job
-> python -m hanssem_macro_dashboard.run_bq_job
-> BigQuery staging / production 적재
-> Cloud Scheduler가 정기 실행
```

## 1. 권장 구조

- 실행기: Cloud Run Job
- 스케줄링: Cloud Scheduler
- 저장소: BigQuery
- 시각화: Tableau

Colab은 수동 검증용으로 남기고, 운영 자동화는 GCP에서 수행합니다.

## 2. 필요한 환경변수

Cloud Run Job에는 아래 환경변수가 필요합니다.

- `BQ_PROJECT_ID`
- `BQ_DATASET`
- `BQ_LOCATION`
- `KOSIS_API_KEY`
- `RONE_API_KEY`
- `ECOS_API_KEY` (optional)
- `DATA_GO_KR_API_KEY` (optional)

현재 권장 값:

```text
BQ_PROJECT_ID=cellular-client-310600
BQ_DATASET=Yunjae_Workspace
BQ_LOCATION=US
```

## 3. 컨테이너 이미지 빌드

Artifact Registry 리포지토리를 먼저 준비한 뒤 이미지를 빌드/푸시합니다.

예시:

```bash
gcloud config set project cellular-client-310600

gcloud artifacts repositories create hanssem-etl \
  --repository-format=docker \
  --location=us \
  --description="Hanssem macro ETL images"

gcloud builds submit \
  --tag us-docker.pkg.dev/cellular-client-310600/hanssem-etl/hanssem-macro-dashboard:latest
```

## 4. Cloud Run Job 생성

Cloud Run Job은 기존 컨테이너 이미지를 기준으로 생성합니다. 공식 문서 기준 `gcloud run jobs create JOB_NAME --image IMAGE_URL` 형식입니다.

예시:

```bash
gcloud run jobs create hanssem-macro-etl \
  --image us-docker.pkg.dev/cellular-client-310600/hanssem-etl/hanssem-macro-dashboard:latest \
  --region us-central1 \
  --tasks 1 \
  --max-retries 1 \
  --task-timeout 3600s \
  --set-env-vars BQ_PROJECT_ID=cellular-client-310600,BQ_DATASET=Yunjae_Workspace,BQ_LOCATION=US \
  --set-env-vars KOSIS_API_KEY=YOUR_KOSIS_API_KEY,RONE_API_KEY=YOUR_RONE_API_KEY \
  --set-env-vars ECOS_API_KEY=,DATA_GO_KR_API_KEY=
```

권장:

- API 키는 장기적으로 `--set-secrets`로 Secret Manager에 연결
- `ECOS_API_KEY`, `DATA_GO_KR_API_KEY`를 확보한 뒤 업데이트

## 5. Cloud Scheduler 생성

Cloud Scheduler는 Cloud Run Job 실행 API를 주기적으로 호출합니다. 공식 문서 기준 `gcloud scheduler jobs create http`를 사용하고, Google API 대상이므로 OAuth 서비스 계정을 씁니다.

예시:

```bash
PROJECT_ID=cellular-client-310600
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')

gcloud scheduler jobs create http hanssem-macro-etl-daily \
  --location us-central1 \
  --schedule "0 9 * * *" \
  --time-zone "Asia/Seoul" \
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/us-central1/jobs/hanssem-macro-etl:run" \
  --http-method POST \
  --oauth-service-account-email "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
```

이 예시는 매일 오전 9시(Asia/Seoul)에 ETL을 실행합니다.

## 6. 수동 테스트

Cloud Run Job을 먼저 수동으로 실행해보는 것이 좋습니다.

```bash
gcloud run jobs execute hanssem-macro-etl --region us-central1 --wait
```

Scheduler도 즉시 실행해볼 수 있습니다.

```bash
gcloud scheduler jobs run hanssem-macro-etl-daily --location us-central1
```

## 7. 성공 확인

BigQuery에서 아래 쿼리로 적재 상태를 확인합니다.

```sql
SELECT indicator_id, COUNT(*) AS cnt
FROM `cellular-client-310600.Yunjae_Workspace.macro_indicator_observations`
GROUP BY indicator_id
ORDER BY indicator_id;
```

```sql
SELECT run_id, indicator_id, stage_status, rows_loaded, latest_period
FROM `cellular-client-310600.Yunjae_Workspace.etl_run_history`
ORDER BY started_at DESC, indicator_id;
```

## 8. Tableau 연결 대상

- `vw_hanssem_macro_hmi`
- `vw_macro_sales_join`
- `vw_etl_status_summary`
- `vw_source_health`

## 참고 문서

- Cloud Run Job 생성: https://cloud.google.com/run/docs/create-jobs
- Cloud Run Job 스케줄 실행: https://cloud.google.com/run/docs/execute/jobs-on-schedule
- Cloud Scheduler HTTP job: https://docs.cloud.google.com/sdk/gcloud/reference/scheduler/jobs/create/http
