#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-cellular-client-310600}"
DATASET="${DATASET:-Yunjae_Workspace}"
BQ_LOCATION="${BQ_LOCATION:-US}"
REGION="${REGION:-us-central1}"
REPOSITORY="${REPOSITORY:-hanssem-etl}"
IMAGE_NAME="${IMAGE_NAME:-hanssem-macro-dashboard}"
JOB_NAME="${JOB_NAME:-hanssem-macro-etl}"

if [[ -z "${KOSIS_API_KEY:-}" || -z "${RONE_API_KEY:-}" ]]; then
  echo "KOSIS_API_KEY and RONE_API_KEY must be set before deployment."
  exit 1
fi

gcloud config set project "${PROJECT_ID}"

gcloud artifacts repositories create "${REPOSITORY}" \
  --repository-format=docker \
  --location=us \
  --description="Hanssem macro ETL images" \
  || true

gcloud builds submit \
  --tag "us-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:latest"

gcloud run jobs delete "${JOB_NAME}" --region "${REGION}" --quiet || true

gcloud run jobs create "${JOB_NAME}" \
  --image "us-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:latest" \
  --region "${REGION}" \
  --tasks 1 \
  --max-retries 1 \
  --task-timeout 3600s \
  --set-env-vars "BQ_PROJECT_ID=${PROJECT_ID},BQ_DATASET=${DATASET},BQ_LOCATION=${BQ_LOCATION}" \
  --set-env-vars "KOSIS_API_KEY=${KOSIS_API_KEY},RONE_API_KEY=${RONE_API_KEY},ECOS_API_KEY=${ECOS_API_KEY:-},DATA_GO_KR_API_KEY=${DATA_GO_KR_API_KEY:-}"

echo "Cloud Run Job deployed: ${JOB_NAME}"
