# SCOUT REST + SSE API

Base URL: `/v1`. Auth: `Authorization: Bearer <Supabase JWT>`. All errors use the envelope
`{"error": {"code", "message", "request_id"}}`.

Interactive docs: `GET /docs` (OpenAPI).

## Workspaces & users

| Method | Path | Description |
| --- | --- | --- |
| GET | `/v1/workspaces` | List workspaces the caller belongs to |
| POST | `/v1/workspaces` | Create workspace `{name}` |
| GET | `/v1/users/me` | Current user (id, email, workspaces) |
| PATCH | `/v1/users/me` | Update profile `{display_name?, preferences?}` |
| GET | `/v1/workspaces/{workspace_id}/members` | List workspace members |
| GET | `/v1/home/recent?workspace_id=` | Recent documents + conversations digest |

## Documents

| Method | Path | Description |
| --- | --- | --- |
| GET | `/v1/documents?workspace_id=&q=&status=&page=&page_size=` | Search/list library |
| GET | `/v1/documents/{id}` | Document metadata |
| POST | `/v1/documents/upload-url` | Request signed upload URL `{workspace_id, filename, size_bytes, mime?, collection?}` → `{document_id, upload_url, storage_path, expires_in}` |
| POST | `/v1/documents/complete-upload` | Confirm storage upload `{document_id}` — creates processing job, queues ingestion |
| GET | `/v1/documents/{id}/status` | Job status `{status, stage, progress, error}` for polling |
| GET | `/v1/documents/{id}/content` | Canonical elements/sections/tables |
| POST | `/v1/documents/{id}/reprocess` | Re-run ingestion (idempotent) |
| DELETE | `/v1/documents/{id}` | Soft-delete document |
| GET | `/v1/jobs/{document_id}` | Processing jobs for a document |

Upload flow: request URL → `PUT` bytes to storage → confirm. The API never proxies file bytes.

## Conversations & chat

| Method | Path | Description |
| --- | --- | --- |
| GET | `/v1/conversations?workspace_id=` | List conversations |
| POST | `/v1/conversations` | Create `{workspace_id, mode?, document_ids?}` |
| GET | `/v1/conversations/{id}/messages` | Message history with citations |
| DELETE | `/v1/conversations/{id}` | Delete conversation |
| POST | `/v1/chat/stream` | **SSE** chat (see below) |

### SSE streaming — `POST /v1/chat/stream`

Request JSON: `{workspace_id, conversation_id?, document_ids?, query}`.

Response is `text/event-stream`:

```
event: delta
data: {"text": "partial answer…"}

event: done
data: {"message_id", "conversation_id", "task_type", "citations": [{"evidence_id", "document_id", "document_title", "page", "sheet", "quote", "valid", "rejection_reason"}]}

event: error
data: {"message": "human-readable error"}
```

Deltas stream in answer order; `done` carries validated citations. Each citation's `valid`
flag is the result of server-side verification against retrieved evidence.

## Intelligence (factuality-enforced)

| Method | Path | Body | Description |
| --- | --- | --- | --- |
| POST | `/v1/intelligence/ask` | `{workspace_id, query, document_ids?}` | Single-question grounded answer |
| POST | `/v1/intelligence/summarize` | `{workspace_id, document_ids, style}` | Multi-document summary (overview/brief/detailed) |
| POST | `/v1/intelligence/extract` | `{workspace_id, document_ids, fields[]}` | Structured field extraction to JSON |
| POST | `/v1/intelligence/compare` | `{workspace_id, document_ids, focus?}` | Multi-document comparison |

All return `{answer, task_type, citations[], evidence[], computed?}`.

## Analytics (deterministic)

| Method | Path | Body | Description |
| --- | --- | --- | --- |
| POST | `/v1/analytics/query` | `{workspace_id, document_id, query}` | NL → whitelisted plan → Decimal execution; response embeds an `explanation` |

`/query` returns `{document_id, sheet, rows[], provenance{sheet, source_rows}, plan_summary, explanation}`.

The model only ever *proposes* a plan (filter/group/aggregate/sort/limit); the server whitelists
it and executes deterministically — numbers in `rows` are never model output.

## Reports

| Method | Path | Description |
| --- | --- | --- |
| POST | `/v1/reports` | Create `{workspace_id, title, document_ids, brief, format: "pdf"\|"docx"}` — generates with citations, stores in Supabase Storage |
| GET | `/v1/reports?workspace_id=` | List reports |
| GET | `/v1/reports/{id}/download` | Signed download URL |

## Usage

| Method | Path | Description |
| --- | --- | --- |
| GET | `/v1/usage?workspace_id=` | Monthly usage by kind (questions, documents, reports) |

## Status codes

`400` validation · `401` missing/invalid JWT · `403` workspace access denied · `404` not found ·
`409` conflict (duplicate upload, version mismatch) · `413` file too large · `422` semantic
validation error · `429` rate limited · `500`/`503` server/storage conditions. All carry `request_id`.
