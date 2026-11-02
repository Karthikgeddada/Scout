"""Worker tasks: idempotent document ingestion + transactional outbox dispatch.

Ingestion pipeline (claim job -> download -> detect -> parse -> persist -> chunk
-> embed -> ready) is crash-safe: the job row is claimed atomically, the task is
idempotent by (document_id, version), and failures retry with capped attempts
before dead-lettering."""

from __future__ import annotations

from sqlalchemy import text

from app.core.config import get_settings
from app.core.observability import get_logger
from app.db.session import session_scope
from app.ingestion.canonical import CanonicalDocument
from app.parsers.pipeline import parse_document
from app.parsers.spreadsheets import table_payloads
from app.rag.chunking import Chunker
from app.services.storage import SupabaseStorage
from workers.celery_app import app

log = get_logger("worker.tasks")

STAGES = [
    ("download", 10), ("detect", 15), ("parse", 30), ("persist", 50),
    ("chunk", 65), ("embed", 85), ("finalize", 100),
]


def _claim_job(session, job_id: str) -> bool:
    """Atomically claim the job (queued -> running). False if another worker won."""
    result = session.execute(
        text(
            "UPDATE processing_jobs SET status = 'running', attempts = attempts + 1, "
            "started_at = coalesce(started_at, now()) "
            "WHERE id = CAST(:id AS uuid) AND status IN ('queued', 'failed') "
            "RETURNING max_attempts, attempts"
        ),
        {"id": str(job_id)},
    ).fetchone()
    if result is None:
        return False
    if result[1] > result[0]:
        session.execute(
            text("UPDATE processing_jobs SET status = 'dead', finished_at = now() WHERE id = CAST(:id AS uuid)"),
            {"id": str(job_id)},
        )
        session.commit()
        return False
    return True


def _progress(session, job_id: str, pct: int, stage: str) -> None:
    session.execute(
        text("UPDATE processing_jobs SET progress = :p, stage = :s WHERE id = CAST(:id AS uuid)"),
        {"p": pct, "s": stage, "id": str(job_id)},
    )
    session.commit()


def _fail(session, job_id: str, document_id: str, exc: Exception) -> None:
    status = "failed"
    attempts = session.execute(
        text("SELECT attempts, max_attempts FROM processing_jobs WHERE id = CAST(:id AS uuid)"),
        {"id": str(job_id)},
    ).fetchone()
    if attempts and attempts[0] >= attempts[1]:
        status = "dead"
    session.execute(
        text(
            "UPDATE processing_jobs SET status = :st, error = :err, finished_at = CASE "
            "WHEN :st = 'dead' THEN now() ELSE finished_at END "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"st": status, "err": str(exc)[:2000], "id": str(job_id)},
    )
    session.execute(
        text("UPDATE documents SET status = CASE WHEN :st = 'dead' THEN 'failed' ELSE 'queued' END "
            "WHERE id = CAST(:did AS uuid)"),
        {"st": status, "did": str(document_id)},
    )
    session.commit()


@app.task(name="scout.ingest_document", bind=True, max_retries=3, autoretry_for=(Exception,),
          retry_backoff=True, retry_backoff_max=600, retry_jitter=True)
def ingest_document(self, job_id: str, document_id: str, workspace_id: str, version: int) -> dict:
    storage = SupabaseStorage()
    with session_scope() as session:
        try:
            if not _claim_job(session, job_id):
                return {"skipped": True, "reason": "job not claimable"}
            _progress(session, job_id, 10, "download")

            row = session.execute(
                text("SELECT storage_path, source_filename, fmt FROM documents WHERE id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            ).fetchone()
            if row is None:
                raise RuntimeError("document row missing")
            storage_path, filename, declared_fmt = row

            data = storage.download_bytes(storage_path)
            _progress(session, job_id, 15, "detect")

            canonical = parse_document(data, document_id=str(document_id),
                                        filename=filename, declared_format=declared_fmt)
            _progress(session, job_id, 30, "parse")

            # Version-scoped wipe: idempotency for reprocessing
            session.execute(
                text("DELETE FROM document_embeddings WHERE document_id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            )
            session.execute(
                text("DELETE FROM document_chunks WHERE document_id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            )
            session.execute(
                text("DELETE FROM document_elements WHERE document_id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            )
            session.execute(
                text("DELETE FROM document_sections WHERE document_id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            )
            session.execute(
                text("DELETE FROM document_tables WHERE document_id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            )
            session.execute(
                text("DELETE FROM document_versions WHERE document_id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            )

            _persist_canonical(session, str(workspace_id), str(document_id), version, canonical)
            _progress(session, job_id, 50, "persist")

            chunks = Chunker().chunk_document(canonical)
            chunk_ids = _persist_chunks(session, str(workspace_id), str(document_id), chunks)
            _progress(session, job_id, 65, "chunk")

            embedded = _embed_and_store(session, str(workspace_id), str(document_id), chunk_ids, chunks)
            _progress(session, job_id, 85, "embed")

            session.execute(
                text(
                    "UPDATE documents SET status = 'ready', fmt = :fmt, mime = :mime, "
                    "version = :v WHERE id = CAST(:id AS uuid)"
                ),
                {"fmt": canonical.fmt, "mime": canonical.mime, "v": version, "id": str(document_id)},
            )
            session.execute(
                text(
                    "UPDATE processing_jobs SET status = 'succeeded', progress = 100, "
                    "stage = 'finalize', finished_at = now(), "
                    "result = CAST(:res AS jsonb) WHERE id = CAST(:id AS uuid)"
                ),
                {"res": _result_json(canonical, chunks, embedded), "id": str(job_id)},
            )
            session.commit()
            _progress(session, job_id, 100, "finalize")
            log.info("ingest_done", document_id=document_id, chunks=len(chunks), embedded=embedded)
            return {"document_id": str(document_id), "chunks": len(chunks), "embedded": embedded}
        except Exception as exc:  # noqa: BLE001
            log.error("ingest_failed", document_id=document_id, error=str(exc))
            _fail(session, job_id, document_id, exc)
            raise


def _persist_canonical(session, workspace_id: str, document_id: str, version: int, doc: CanonicalDocument) -> None:
    import json

    enriched = doc.to_dict()
    enriched["tables"] = table_payloads(doc)  # analytics-ready records + column types
    session.execute(
        text(
            "INSERT INTO document_versions (id, document_id, version, canonical, quality, parser_versions) "
            "VALUES (gen_random_uuid(), CAST(:d AS uuid), :v, CAST(:canon AS jsonb), "
            "CAST(:qual AS jsonb), CAST(:pv AS jsonb))"
        ),
        {"d": document_id, "v": version, "canon": json.dumps(enriched),
         "qual": json.dumps(doc.quality.to_dict() if hasattr(doc.quality, "to_dict") else {}),
         "pv": json.dumps(doc.quality.parser_versions)},
    )
    for s in doc.sections:
        session.execute(
            text(
                "INSERT INTO document_sections (id, document_id, section_id, title, level, parent_section_id) "
                "VALUES (gen_random_uuid(), CAST(:d AS uuid), :sid, :title, :lvl, :parent)"
            ),
            {"d": document_id, "sid": s.section_id, "title": s.title[:500], "lvl": s.level, "parent": s.parent_id},
        )
    for e in doc.elements:
        prov = e.provenance
        session.execute(
            text(
                "INSERT INTO document_elements (id, document_id, section_id, element_id, kind, text, "
                "level, page, sheet, locator, metadata) VALUES (gen_random_uuid(), CAST(:d AS uuid), "
                ":sec, :eid, :kind, :text, :lvl, :page, :sheet, :loc, CAST(:meta AS jsonb))"
            ),
            {"d": document_id, "sec": e.section_id, "eid": e.element_id, "kind": e.kind,
             "text": e.text, "lvl": e.level, "page": prov.page if prov else None,
             "sheet": prov.sheet if prov else None, "loc": prov.locator if prov else None,
             "meta": "{}"},
        )
    for t in doc.tables:
        session.execute(
            text(
                "INSERT INTO document_tables (id, document_id, table_id, title, sheet, pages, headers, "
                "n_rows, n_cols, footnotes, validation_flags) VALUES (gen_random_uuid(), CAST(:d AS uuid), "
                ":tid, :title, :sheet, CAST(:pages AS jsonb), CAST(:headers AS jsonb), :nr, :nc, "
                "CAST(:fn AS jsonb), CAST(:vf AS jsonb))"
            ),
            {"d": document_id, "tid": t.table_id, "title": t.title, "sheet": t.sheet,
             "pages": _json(t.pages), "headers": _json(t.headers), "nr": t.n_rows, "nc": t.n_cols,
             "fn": _json(t.footnotes), "vf": _json(t.validation_flags)},
        )
        for r in t.rows:
            session.execute(
                text(
                    "INSERT INTO document_table_rows (id, document_id, table_db_id, row_index, "
                    "is_header, page) SELECT gen_random_uuid(), CAST(:d AS uuid), id, :ri, :hdr, :pg "
                    "FROM document_tables WHERE document_id = CAST(:d AS uuid) AND table_id = :tid"
                ),
                {"d": document_id, "ri": r.cells[0].row_index if r.cells else 0, "hdr": r.is_header,
                 "pg": (r.provenance.page if r.provenance else None), "tid": t.table_id},
            )
            for c in r.cells:
                session.execute(
                    text(
                        "INSERT INTO document_table_cells (id, row_db_id, row_index, col_index, raw_value, "
                        "normalized_value, data_type, unit, currency, is_negative, decimal_value) "
                        "SELECT gen_random_uuid(), r.id, :ri, :ci, :raw, :norm, :dt, :unit, :cur, :neg, :dec "
                        "FROM document_table_rows r JOIN document_tables t ON r.table_db_id = t.id "
                        "WHERE t.document_id = CAST(:d AS uuid) AND t.table_id = :tid AND r.row_index = :ri"
                    ),
                    {"d": document_id, "tid": t.table_id, "ri": c.row_index, "ci": c.col_index,
                     "raw": c.raw_value, "norm": c.normalized_value, "dt": c.data_type,
                     "unit": c.unit, "cur": c.currency, "neg": c.is_negative, "dec": c.decimal_value},
                )


def _persist_chunks(session, workspace_id: str, document_id: str, chunks) -> list[str]:
    ids: list[str] = []
    for ch in chunks:
        pages = [p.get("page") for p in ch.provenance if p.get("page")]
        sheet = next((p.get("sheet") for p in ch.provenance if p.get("sheet")), None)
        session.execute(
            text(
                "INSERT INTO document_chunks (id, chunk_id, document_id, workspace_id, version, kind, text, "
                "token_count, parent_section_id, section_title, pages, sheet, locator, table_id, "
                "content_hash, metadata) VALUES (gen_random_uuid(), :cid, CAST(:d AS uuid), "
                "CAST(:w AS uuid), 1, :kind, :text, :tokens, :parent, :title, CAST(:pages AS jsonb), "
                ":sheet, :loc, :table_id, :hash, CAST(:meta AS jsonb))"
            ),
            {"cid": ch.chunk_id, "d": document_id, "w": workspace_id, "kind": ch.kind,
             "text": ch.text, "tokens": ch.token_count, "parent": ch.parent_section_id,
             "title": (ch.metadata or {}).get("section_title"), "pages": _json(pages),
             "sheet": sheet, "loc": (ch.provenance[0].get("locator") if ch.provenance else None),
             "table_id": (ch.metadata or {}).get("table_id"), "hash": ch.content_hash,
             "meta": _json({k: v for k, v in (ch.metadata or {}).items() if k != "headers"})},
        )
        ids.append(ch.chunk_id)
    session.commit()
    return ids


def _embed_and_store(session, workspace_id: str, document_id: str, chunk_ids: list[str], chunks) -> int:
    from app.ai.embeddings import EmbeddingService

    try:
        embedder = EmbeddingService()
    except Exception:  # noqa: BLE001
        log.warning("embedder_unavailable", document_id=document_id)
        return 0
    texts = [ch.text for ch in chunks]
    vectors = embedder.embed_batch(texts)
    for ch, vec in zip(chunks, vectors):
        qv = "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
        session.execute(
            text(
                "INSERT INTO document_embeddings (id, chunk_db_id, document_id, workspace_id, model_id, "
                "model_version, dim, embedding) SELECT gen_random_uuid(), id, CAST(:d AS uuid), "
                "CAST(:w AS uuid), :model, :mv, :dim, CAST(:qv AS vector) "
                "FROM document_chunks WHERE document_id = CAST(:d AS uuid) AND chunk_id = :cid"
            ),
            {"d": document_id, "w": workspace_id, "model": embedder.model_id,
             "mv": embedder.model_version, "dim": embedder.dim, "qv": qv, "cid": ch.chunk_id},
        )
    session.commit()
    return len(vectors)


def _result_json(canonical, chunks, embedded: int) -> str:
    import json

    return json.dumps({
        "elements": len(canonical.elements), "tables": len(canonical.tables),
        "sections": len(canonical.sections), "chunks": len(chunks), "embedded": embedded,
        "quality": {"text_coverage": canonical.quality.text_coverage,
                    "flags": canonical.quality.validation_flags},
    })


def _json(value) -> str:
    import json

    return json.dumps(value)


@app.task(name="scout.dispatch_outbox")
def dispatch_outbox() -> dict:
    """Transactional outbox -> RabbitMQ with publisher confirms."""
    import pika
    from urllib.parse import unquote, urlparse

    settings = get_settings()
    with session_scope() as session:
        pending = session.execute(
            text("SELECT id, topic, payload FROM outbox_events "
                 "WHERE dispatched = false AND attempts < 10 ORDER BY created_at LIMIT 100")
        ).fetchall()
        if not pending:
            return {"dispatched": 0}

        u = urlparse(settings.rabbitmq_url)
        creds = pika.PlainCredentials(u.username or "guest", unquote(u.password or "guest"))
        conn = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=u.hostname or "localhost", port=u.port or 5672,
                virtual_host=unquote((u.path or "/").lstrip("/")) or "/",
                credentials=creds,
            )
        )
        try:
            ch = conn.channel()
            ch.confirm_delivery()
            for event_id, topic, payload in pending:
                body = _json({"topic": topic, "payload": payload})
                delivered = False
                try:
                    delivered = ch.basic_publish(
                        exchange="scout", routing_key="ingestion", body=body,
                        properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("outbox_publish_failed", error=str(exc))
                if delivered:
                    session.execute(
                        text("UPDATE outbox_events SET dispatched = true, dispatched_at = now() "
                             "WHERE id = CAST(:id AS uuid)"),
                        {"id": str(event_id)},
                    )
                else:
                    session.execute(
                        text("UPDATE outbox_events SET attempts = attempts + 1 WHERE id = CAST(:id AS uuid)"),
                        {"id": str(event_id)},
                    )
            session.commit()
            return {"dispatched": len(pending)}
        finally:
            conn.close()


@app.task(name="scout.reap_stuck_jobs")
def reap_stuck_jobs() -> dict:
    """Requeue jobs stuck in running (worker crash) beyond the visibility timeout."""
    with session_scope() as session:
        rows = session.execute(
            text(
                "UPDATE processing_jobs SET status = 'failed', error = 'worker crash suspected' "
                "WHERE status = 'running' AND updated_at < now() - interval '30 minutes' "
                "RETURNING id"
            )
        ).fetchall()
        session.commit()
        for (job_id,) in rows:
            log.warning("job_requeued", job_id=str(job_id))
    return {"requeued": len(rows)}
