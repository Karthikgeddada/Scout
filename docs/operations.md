# SCOUT Operations

## Topology

| Service | Role |
| --- | --- |
| `frontend` | nginx serving the built React SPA; proxies `/v1` to `api` (SSE-safe) |
| `api` | FastAPI — REST + SSE, auth, orchestration |
| `worker` | Celery worker — ingestion pipeline (parse → persist → chunk → embed) |
| `beat` | Celery beat — outbox dispatch (10s), stuck-job reaper (5 min) |
| `migrate` | one-shot `alembic upgrade head` |
| `db` | PostgreSQL 16 + pgvector (local dev parity for Supabase) |
| `rabbitmq` | AMQP broker (durable queues + DLQ, management UI on 15672) |

## Configuration

All settings are environment-driven (`app/core/config.py`, pydantic-settings, `SCOUT_` prefix).
Key groups:

* **Database**: `SCOUT_DATABASE_URL` (Supabase pooler in production).
* **RabbitMQ**: `SCOUT_RABBITMQ_URL` (single AMQP URL; vhost URL-encoded, e.g. `%2Fscout`).
* **Supabase**: `SCOUT_SUPABASE_URL`, `SCOUT_SUPABASE_SERVICE_ROLE_KEY` (storage + admin),
  `SCOUT_SUPABASE_JWKS_URL` (JWKS for token verification).
* **Gemini**: `SCOUT_GEMINI_API_KEY`, `SCOUT_GEMINI_MODEL`.
* **Embeddings**: `SCOUT_EMBEDDING_MODEL` (Qwen3), `SCOUT_EMBEDDING_DIM`, `SCOUT_EMBEDDING_DEVICE`.
* **Limits**: max upload size, chunk targets, retrieval K values, rate limits.

See `.env.example` for the full template.

## Deployment (Docker)

```bash
cp .env.example .env
# fill: DATABASE_URL, RABBITMQ_*, SUPABASE_*, GEMINI_API_KEY
docker compose up --build -d
```

Order is enforced by health checks + `depends_on`: db/rabbitmq healthy → migrate completes →
api/worker/beat start. The frontend listens on :5173 (mapped to nginx :80).

Production notes:

* Point `SCOUT_DATABASE_URL` at the Supabase connection pooler; run migrations once per deploy
  (the `migrate` service pattern).
* The worker image downloads Qwen3 embedding/reranker weights on first boot; mount a persistent
  `hf_cache` volume (already configured) or pre-bake weights for faster cold starts.
* Scale workers horizontally: `docker compose up --scale worker=4`. Job claiming is atomic, so
  duplicates are safe; beat must run exactly once.
* Put the frontend behind TLS and keep the JWT in cookies or the Supabase SDK session — the
  localStorage token is a development convenience only.

## Database

```bash
# apply / generate migrations
docker compose run --rm migrate alembic upgrade head
docker compose run --rm migrate alembic revision -m "change"

# psql shell
docker compose exec db psql -U scout -d scout
```

Useful checks:

```sql
-- job backlog
SELECT status, count(*) FROM processing_jobs GROUP BY 1;
-- failed documents with errors
SELECT id, error FROM processing_jobs WHERE status = 'dead' ORDER BY updated_at DESC LIMIT 20;
-- outbox lag
SELECT count(*) FILTER (WHERE NOT dispatched) AS pending FROM outbox_events;
```

## RabbitMQ

* Management UI: `http://localhost:15672` (compose credentials).
* Queues: `ingestion` (durable, acks-late) and `ingestion.dlq` (dead-letter target).
* Poison messages: inspect `ingestion.dlq`; the job row's `error` column carries the last failure.

## Failure playbook

| Symptom | Check | Remedy |
| --- | --- | --- |
| Document stuck `processing` | job row `stage`, worker logs | reaper requeues after 30 min; `POST /documents/{id}/reprocess` to force |
| Document `failed` | `processing_jobs.error` | fix cause, reprocess |
| Answers lack citations | embedding rows for document | if embeddings are 0, embedder was unavailable — reprocess after model service is up |
| Outbox backlog | outbox pending count | beat running? RabbitMQ reachable? attempts < 10? |
| Analytics rejected plan | API response `code` | plan hit whitelister; check document has parseable tables (CSV/XLSX) |

## Observability

* Structured JSON logs with `request_id` on every API response and log line.
* `GET /health` — liveness (used by Docker healthcheck).
* Usage metering per workspace (`usage_events`) surfaces in Settings → Usage.

## Testing

```bash
cd backend && pytest
```

53 unit tests; binary-parser paths (PDF/DOCX/XLSX) additionally covered by integration runs in
CI where those libraries are installed.
