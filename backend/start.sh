#!/usr/bin/env bash
# SCOUT all-in-one entrypoint for single-service deployments (e.g. Render).
# Runs the entire backend stack in ONE container:
#   1. RabbitMQ broker (in-container, provisioned with scout/scout + vhost /scout)
#   2. Alembic migrations (idempotent)
#   3. Celery worker with embedded beat (-B)
#   4. uvicorn API (foreground)
# docker-compose overrides this command for multi-service local development.
set -euo pipefail

# --- 1) Broker ------------------------------------------------------------
service rabbitmq-server start
rabbitmqctl await_startup --timeout 60 2>/dev/null || sleep 10
rabbitmqctl add_vhost scout 2>/dev/null || true
rabbitmqctl add_user scout scout 2>/dev/null || true
rabbitmqctl set_permissions -p scout scout ".*" ".*" ".*"

# --- 2) Migrations (idempotent) -------------------------------------------
alembic upgrade head

# --- 3) Celery worker + embedded beat --------------------------------------
# max-tasks-per-child recycles the worker periodically to cap memory growth
# on small (free-tier) instances.
celery -A workers.celery_app:app worker -B --loglevel=INFO --concurrency=1 --max-tasks-per-child=8 &

# --- 4) API (foreground) ---------------------------------------------------
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
