FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
COPY sql ./sql

RUN mkdir -p /app/data/raw /app/data/processed /app/data/warehouse /app/logs
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "-m", "hanssem_macro_dashboard.run_bq_job"]
