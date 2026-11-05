# SCOUT — AI-Powered Document Intelligence Platform

SCOUT ingests documents (PDF, DOCX, XLSX, CSV, PPTX, images, TXT, MD), parses them into a
rich canonical representation, indexes them for hybrid lexical + semantic retrieval, and
answers questions about them with **verifiable, traceable citations**. Spreadsheets become
queryable through a strict, LLM-proposed but server-vetted analytics plan executor — no
arbitrary SQL ever runs.

## Highlights

* **Multi-format ingestion** — Docling (deep PDF layout) with PyMuPDF fallback, python-docx,
  openpyxl/CSV with delimiter detection and financial cell parsing ($1.2M, (45,000), 12.3%,
  European decimal formats), python-pptx, OCR path for images/scans, structure-aware
  Markdown/TXT.
* **Hybrid retrieval** — PostgreSQL FTS + pgvector cosine similarity fused with Reciprocal Rank
  Fusion, then cross-encoder reranking (Qwen3), then parent/context expansion so evidence
  blocks carry the sections and tables needed for grounded answers.
* **Citation enforcement** — answers must cite evidence block IDs; a validator verifies each
  citation against the retrieved evidence (quote/page/sheet provenance) and marks rejections.
* **Grounded analytics** — natural-language questions over spreadsheet data are answered by an
  LLM-proposed **restricted plan** (filter / group / aggregate / sort / limit whitelist only),
  validated server-side and executed with `Decimal` arithmetic — deterministic, provenance-tracked,
  never hallucinated.
* **Prompt-injection hardening** — retrieved document text is demarcated and neutralized before
  it reaches the model; instructions found inside document content are stripped.
* **Durable async pipeline** — Celery + RabbitMQ (durable queues, DLQ, publisher confirms),
  atomic job claiming, idempotent reprocessing, transactional outbox, stuck-job reaper.
* **Multi-tenancy** — Supabase JWT auth; every query and row is workspace-scoped with
  PostgreSQL Row-Level Security policies enforced at the database level.
* **Streaming chat** — SSE token streaming with per-message citations.
* **Reports** — PDF/DOCX generation (reportlab / python-docx) stored in Supabase Storage.

## Repository layout

```
Scout/
├── backend/
│   ├── app/
│   │   ├── core/        # config, errors, logging, security (JWT/RLS context)
│   │   ├── db/          # SQLAlchemy session/engine, models (22 tables)
│   │   ├── ingestion/   # format detection, canonical model, table semantics
│   │   ├── rag/         # chunking, hybrid fusion, citations, context, prompt guard
│   │   ├── analytics/   # plan schema, whitelister, restricted executor, provenance
│   │   ├── ai/          # Gemini client, Qwen3 embeddings, reranker
│   │   ├── parsers/     # pdf (docling/pymupdf), docx, xlsx/csv, pptx, md/txt
│   │   ├── api/         # dependencies + aggregated FastAPI router
│   │   ├── schemas_*.py # Pydantic request/response models
│   │   ├── services/    # storage, documents, chat, intelligence, analytics, reports
│   ├── alembic/         # 0001 migration: tables, pgvector, FTS triggers, RLS, outbox
│   ├── workers/         # Celery app + ingestion/outbox/reaper tasks
│   └── tests/           # 53 unit tests (ingestion, chunking, RAG, analytics, parsers)
├── frontend/            # React + Vite + TypeScript + Tailwind SPA
├── docker-compose.yml   # db (pgvector), rabbitmq, migrate, api, worker, beat, frontend
└── docs/                # architecture, API, operations guides
```

## Quick start (Docker)

```bash
cp .env.example .env   # fill in Supabase + Gemini credentials
docker compose up --build
```

* Frontend: http://localhost:5173
* API: http://localhost:8000 (docs at `/docs`)
* RabbitMQ management: http://localhost:15672

Migrations run automatically (the `migrate` service runs `alembic upgrade head` first).

## Local development (without Docker)

```bash
# Backend
cd backend
pip install -e ".[dev]"            # core + tests
pip install -e ".[ingestion,ai,supabase]"  # full stack
pytest                              # 53 tests
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install && npm run dev          # proxies /v1 to localhost:8000
```

## The answer pipeline

```
upload → storage (signed URL) → ingest task (Celery)
  → format detect → parse → canonical document
  → persist (elements/sections/tables/rows/cells/version)
  → chunk → embed (Qwen3) → index (FTS + pgvector)
question → query router (Q&A | summarize | extract | compare | analytics)
  → hybrid retrieve → fuse (RRF) → rerank → context expansion
  → prompt guard (injection neutralization) → Gemini (streaming)
  → citation validation → answer + citations
```

Analytics questions branch differently: retrieval surfaces the document's tables, the LLM
proposes a restricted JSON plan, the server whitelists it, executes it with Decimal math, and
returns deterministic rows plus a natural-language explanation.

## Testing

```bash
cd backend && pytest
```

The suite covers detection heuristics, canonical round-trips, financial cell parsing, chunking
invariants, fusion ordering, citation validation, context expansion, prompt-guard behavior,
plan validation/rejection, and executor edge cases — all runnable without external services.

## Cloud deployment (Render + Vercel, 100% free tier)

The backend deploys as **one** Render web service (PostgreSQL + pgvector, RabbitMQ,
Celery worker with embedded beat, and the FastAPI API — all in a single container, no
external database); the frontend deploys to **Vercel**.

### 1) Supabase (free) — auth + storage only, NO database needed

The database is **built in**: on Render, `start.sh` boots a PostgreSQL 15 + pgvector
instance inside the same container and migrates it automatically. You do NOT need
Supabase's database at all.

From a free Supabase project you still need (for auth + file storage):

* The **service role key** (Settings → API).
* Your JWKS URL: `https://<PROJECT_REF>.supabase.co/auth/v1/.well-known/jwks.json`.
* A private **storage bucket** named `documents` (Storage → New bucket, keep it private).

### 2) Backend → Render (free plan)

1. Push this repo to GitHub, then in Render: **New → Blueprint** and select the repo
   (it reads `render.yaml`), or create a Web Service manually:
   * **Runtime:** Docker · **Root directory:** `backend` · **Health check path:** `/health` · **Plan:** Free
2. Set the environment variables (the blueprint prompts for them):
   * `SCOUT_SUPABASE_SERVICE_ROLE_KEY`
   * `SCOUT_SUPABASE_JWKS_URL`
   * `SCOUT_GEMINI_API_KEY` — free key from Google AI Studio
   * `SCOUT_CORS_ALLOW_ORIGINS` — your Vercel URL (comma-separated if several)
   * (`SCOUT_DATABASE_URL` is optional — leave unset to use the built-in Postgres)
3. Deploy. `backend/start.sh` boots PostgreSQL (in-container), then RabbitMQ
   (in-container), runs `alembic upgrade head`, starts the Celery worker (embedded
   beat, memory-capped for the 512 MB free tier), then uvicorn on Render's `PORT`.
   The first deploy is slow (pip installs + apt packages); the health check has a
   120 s start period.

### 3) Frontend → Vercel (Hobby plan)

1. **Add New → Project** → import the same GitHub repo.
2. **Root Directory:** `frontend` (framework auto-detected: Vite; build command
   `tsc -b && vite build`).
3. Environment variable: `VITE_API_URL` = your Render origin, e.g.
   `https://scout-api.onrender.com` — bare origin, **no path, no trailing slash**.
4. Deploy. `vercel.json` handles the SPA rewrite for client-side routes.

### Free-tier notes

* ⚠️ **Built-in database persistence**: Render free services sleep after ~15 min idle and
  have an **ephemeral disk**. The in-container PostgreSQL (like the in-container RabbitMQ)
  is wiped on every sleep/wake, redeploy, or restart — uploaded documents and their index
  are **lost** and must be re-uploaded. For durable data, either set `SCOUT_DATABASE_URL`
  to an external Postgres (e.g. Supabase, free) or upgrade to a paid plan with a disk.
* The first request after sleep takes ~30–60 s to wake. Queues are re-provisioned by
  `start.sh`; documents can be reprocessed from the UI.
* The stack (Postgres + RabbitMQ + worker + API + ML models) is deliberately tuned for
  512 MB: worker `--concurrency=1 --max-tasks-per-child=8`.
* Embedding/reranker models download to a container-local HF cache on first use.

## Documentation

* [docs/architecture.md](docs/architecture.md) — system design and data flow
* [docs/api.md](docs/api.md) — REST + SSE API reference
* [docs/operations.md](docs/operations.md) — deployment, configuration, operations runbook
