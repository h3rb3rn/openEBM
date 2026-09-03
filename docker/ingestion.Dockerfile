FROM python:3.12-slim

LABEL maintainer="EBM Analyzer" \
      description="EBM Catalog Ingestion Pipeline"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/ingestion/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ingestion ./src/ingestion

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.ingestion.fetch_ebm"]
