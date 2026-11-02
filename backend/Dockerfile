# SCOUT all-in-one image: API + Celery worker (embedded beat) + RabbitMQ broker
# in ONE container — used for single-service cloud deploys (e.g. Render).
# docker-compose overrides the start command per service for local development.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf_cache

# System libraries required by PyMuPDF, OpenCV/PaddleOCR, Camelot (ghostscript),
# plus rabbitmq-server for the in-container broker (runs as root in start.sh).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libmagic1 \
        ghostscript \
        rabbitmq-server \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir ".[ingestion,ai,supabase,ocr]" \
    && mkdir -p /opt/hf_cache \
    && chmod +x start.sh

EXPOSE 8000
# Long start period: broker boot + migrations + HF model cache warm-up happen first.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["./start.sh"]
