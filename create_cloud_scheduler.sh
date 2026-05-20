#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-cellular-client-310600}"
REGION="${REGION:-us-central1}"
JOB_NAME="${JOB_NAME:-hanssem-macro-etl}"
SCHEDULER_NAME="${SCHEDULER_NAME:-hanssem-macro-etl-daily}"
CRON_SCHEDULE="${CRON_SCHEDULE:-0 9 * * *}"
TIME_ZONE="${TIME_ZONE:-Asia/Seoul}"

gcloud config set project "${PROJECT_ID}"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

gcloud scheduler jobs delete "${SCHEDULER_NAME}" --location "${REGION}" --quiet || true

gcloud scheduler jobs create http "${SCHEDULER_NAME}" \
  --location "${REGION}" \
  --schedule "${CRON_SCHEDULE}" \
  --time-zone "${TIME_ZONE}" \
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run" \
  --http-method POST \
  --oauth-service-account-email "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "Cloud Scheduler created: ${SCHEDULER_NAME}"
