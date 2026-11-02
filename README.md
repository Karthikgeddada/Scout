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

## Documentation

* [docs/architecture.md](docs/architecture.md) — system design and data flow
* [docs/api.md](docs/api.md) — REST + SSE API reference
* [docs/operations.md](docs/operations.md) — deployment, configuration, operations runbook
