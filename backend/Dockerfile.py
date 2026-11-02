# SCOUT backend image - used by both the FastAPI API service and the Celery worker.
# NOTE: if this file is named Dockerfile.py after automated creation, rename to `Dockerfile`.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf_cache

# System libraries required by PyMuPDF, OpenCV/PaddleOCR, Camelot (ghostscript).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libmagic1 \
        ghostscript \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir ".[ingestion,ai,supabase,ocr]"

# Non-root runtime user; HF model cache directory for embedding/reranker weights.
RUN useradd --create-home --uid 10001 scout \
    && mkdir -p /opt/hf_cache \
    && chown -R scout:scout /opt/hf_cache /app
USER scout

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Default command is the API; the worker service overrides with the Celery command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
