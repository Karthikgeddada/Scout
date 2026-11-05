#!/usr/bin/env bash
# SCOUT all-in-one entrypoint for single-service deployments (e.g. Render).
# Runs the entire backend stack in ONE container — fully self-contained, no
# external database required:
#   1. PostgreSQL 15 + pgvector (in-container, provisioned with scout/scout)
#   2. RabbitMQ broker (in-container, provisioned with scout/scout + vhost scout)
#   3. Alembic migrations (idempotent)
#   4. Celery worker with embedded beat (-B)
#   5. uvicorn API (foreground)
# docker-compose overrides this command for multi-service local development.
set -euo pipefail

# --- 1) In-container PostgreSQL (+ pgvector) --------------------------------
service postgresql start
for _ in $(seq 1 30); do
    pg_isready -q && break
    sleep 1
done

# Idempotent provisioning: role, database, vector extension.
# scout is SUPERUSER so alembic's CREATE EXTENSION always succeeds.
if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='scout'\"" | grep -q 1; then
    su postgres -c "psql -c \"CREATE USER scout WITH PASSWORD 'scout' SUPERUSER;\""
fi
if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='scout'\"" | grep -q 1; then
    su postgres -c "createdb -O scout scout"
fi
su postgres -c "psql -d scout -c 'CREATE EXTENSION IF NOT EXISTS vector;'"

# Default to the in-container database unless an external URL is provided.
export SCOUT_DATABASE_URL="${SCOUT_DATABASE_URL:-postgresql+psycopg://scout:scout@localhost:5432/scout}"

# --- 2) Broker ----------------------------------------------------------------
service rabbitmq-server start
rabbitmqctl await_startup --timeout 60 2>/dev/null || sleep 10
rabbitmqctl add_vhost scout 2>/dev/null || true
rabbitmqctl add_user scout scout 2>/dev/null || true
rabbitmqctl set_permissions -p scout scout ".*" ".*" ".*"

# --- 3) Migrations (idempotent) -------------------------------------------
alembic upgrade head

# --- 4) Celery worker + embedded beat --------------------------------------
# max-tasks-per-child recycles the worker periodically to cap memory growth
# on small (free-tier) instances.
celery -A workers.celery_app:app worker -B --loglevel=INFO --concurrency=1 --max-tasks-per-child=8 &

# --- 5) API (foreground) ----------------------------------------------------
# Render injects PORT (default 10000); local/compose use 8000.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
