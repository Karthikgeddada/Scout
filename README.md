# SCOUT — AI-Powered Document Intelligence Platform

SCOUT ingests documents (PDF, DOCX, XLSX, CSV, PPTX, TXT, MD), parses them into a
rich canonical representation, indexes them for hybrid lexical + semantic retrieval, and
answers questions about them with **verifiable, traceable citations**. Spreadsheets become
queryable through a strict, LLM-proposed but server-vetted analytics plan executor — no
arbitrary SQL ever runs.

## Highlights

* **Multi-format ingestion** — PyMuPDF (PDF), python-docx, openpyxl/CSV with delimiter
  detection and financial cell parsing ($1.2M, (45,000), 12.3%, European decimal formats),
  python-pptx, structure-aware Markdown/TXT.
* **Hybrid retrieval** — Python term scoring + Gemini `text-embedding-004` embeddings
  compared with numpy cosine, fused with Reciprocal Rank Fusion, then parent/context
  expansion so evidence blocks carry the sections and tables needed for grounded answers.
* **Free-tier AI** — no torch, no transformers, no local model downloads: chat, extraction,
  and embeddings all run on the **free Gemini API tier** (`google-genai`).
* **Citation enforcement** — answers must cite evidence block IDs; a validator verifies each
  citation against the retrieved evidence (quote/page/sheet provenance) and marks rejections.
* **Grounded analytics** — natural-language questions over spreadsheet data are answered by an
  LLM-proposed **restricted plan** (filter / group / aggregate / sort / limit whitelist only),
  validated server-side and executed with `Decimal` arithmetic — deterministic, provenance-tracked,
  never hallucinated.
* **Prompt-injection hardening** — retrieved document text is demarcated and neutralized before
  it reaches the model; instructions found inside document content are stripped.
* **Simple async pipeline** — FastAPI BackgroundTasks: no broker, no worker process, no
  Docker. Idempotent by (document_id, version); failed jobs are reprocessable from the UI.
* **Multi-tenancy** — Supabase JWT auth; every query and row is workspace-scoped in the ORM
  (no cross-workspace access by construction).
* **Streaming chat** — SSE token streaming with per-message citations.
* **Reports** — PDF/DOCX generation (reportlab / python-docx) stored in Supabase Storage.

## Repository layout

```
Scout/
├── backend/
│   ├── app/
│   │   ├── core/        # config, errors, logging, security (JWT)
│   │   ├── db/          # SQLAlchemy session/engine (SQLite default), models
│   │   ├── ingestion/   # format detection, canonical model, in-process tasks
│   │   ├── rag/         # chunking, hybrid fusion, citations, context, prompt guard
│   │   ├── analytics/   # plan schema, whitelister, restricted executor, provenance
│   │   ├── ai/          # Gemini client, Gemini embeddings, optional reranker
│   │   ├── parsers/     # pdf (pymupdf), docx, xlsx/csv, pptx, md/txt
│   │   ├── api/         # dependencies + aggregated FastAPI router
│   │   ├── schemas_*.py # Pydantic request/response models
│   │   └── services/    # storage, documents, chat, intelligence, analytics, reports
│   └── tests/           # unit tests (ingestion, chunking, RAG, analytics, parsers)
├── frontend/            # React + Vite + TypeScript + Tailwind SPA
└── docs/                # architecture, API, operations guides
```

## Quick start (local, no Docker needed)

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[ingestion,dev]"
cp ../.env.example ../.env            # fill in Supabase + Gemini credentials
uvicorn app.main:app --reload         # SQLite (scout.db) created automatically

# Frontend
cd frontend
npm install && npm run dev            # proxies /v1 to localhost:8000
```

* Frontend: http://localhost:5173
* API: http://localhost:8000 (docs at `/docs`)

Tables are created automatically on startup (`init_db`) — there are no migrations to run.

## The answer pipeline

```
upload → storage (signed URL) → ingest task (FastAPI BackgroundTask)
  → format detect → parse → canonical document
  → persist (elements/sections/tables/rows/cells/version)
  → chunk → embed (Gemini text-embedding-004)
question → query router (Q&A | summarize | extract | compare | analytics)
  → hybrid retrieve (lexical + cosine) → fuse (RRF) → context expansion
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

The backend deploys as **one native Python web service** on Render — no Docker, no
containers: SQLite on the service disk, FastAPI BackgroundTasks for ingestion, and
uvicorn on Render's `PORT`. The frontend deploys to **Vercel**.

### 1) Supabase (free) — auth + storage only, NO database needed

The database is **built in**: SQLite (`scout.db`) is created automatically on first boot.
You do NOT need Supabase's database at all.

From a free Supabase project you still need (for auth + file storage):

* The **service role key** (Settings → API).
* Your JWKS URL: `https://<PROJECT_REF>.supabase.co/auth/v1/.well-known/jwks.json`.
* A private **storage bucket** named `documents` (Storage → New bucket, keep it private).

### 2) Backend → Render (free plan)

1. Push this repo to GitHub, then in Render: **New → Blueprint** and select the repo
   (it reads `render.yaml`), or create a Web Service manually:
   * **Runtime:** Python · **Root directory:** `backend` · **Build command:**
     `pip install -e ".[ingestion]"` · **Start command:**
     `uvicorn app.main:app --host 0.0.0.0 --port $PORT` · **Health check path:** `/health`
     · **Plan:** Free
2. Set the environment variables (the blueprint prompts for them):
   * `SCOUT_SUPABASE_SERVICE_ROLE_KEY`
   * `SCOUT_SUPABASE_JWKS_URL`
   * `SCOUT_GEMINI_API_KEY` — free key from Google AI Studio
   * `SCOUT_CORS_ALLOW_ORIGINS` — your Vercel URL (comma-separated if several)
   * (`SCOUT_DATABASE_URL` is optional — leave unset for SQLite)
3. Deploy. Tables are created automatically on first start; ingestion runs in-process.

### 3) Frontend → Vercel (Hobby plan)

1. **Add New → Project** → import the same GitHub repo.
2. **Root Directory:** `frontend` (framework auto-detected: Vite; build command
   `tsc -b && vite build`).
3. Environment variable: `VITE_API_URL` = your Render origin, e.g.
   `https://scout-api.onrender.com` — bare origin, **no path, no trailing slash**.
4. Deploy. `vercel.json` handles the SPA rewrite for client-side routes.

### Free-tier notes

* ⚠️ **SQLite persistence**: Render free services sleep after ~15 min idle and have an
  **ephemeral disk**. `scout.db` — uploaded documents and their index — is **lost** on
  every sleep/wake, redeploy, or restart and must be re-uploaded. For durable data,
  either set `SCOUT_DATABASE_URL` to an external Postgres (e.g. Supabase, free) or
  upgrade to a paid plan with a persistent disk.
* The first request after sleep takes ~30–60 s to wake.
* All AI features run on the free Gemini API tier — no model downloads, no GPU, no
  heavy Python packages.

## Documentation

* [docs/architecture.md](docs/architecture.md) — system design and data flow
* [docs/api.md](docs/api.md) — REST + SSE API reference
* [docs/operations.md](docs/operations.md) — deployment, configuration, operations runbook
