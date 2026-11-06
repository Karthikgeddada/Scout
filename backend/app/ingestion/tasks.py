"""In-process ingestion pipeline (replaces the Celery/RabbitMQ worker).

Runs as a FastAPI background task on the same service: claim job -> download
-> detect -> parse -> persist canonical -> chunk -> embed -> ready. Idempotent
by (document_id, version); failures mark the job (and document) failed so the
user can reprocess from the UI.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.observability import get_logger
from app.db.models_documents import (
    Document,
    DocumentElement,
    DocumentSection,
    DocumentTable,
    DocumentTableRow,
    DocumentTableCell,
    DocumentVersion,
)
from app.db.models_jobs import ProcessingJob
from app.db.models_retrieval import (
    DocumentChunk,
    DocumentEmbedding,
    RetrievalCandidate,
)
from app.db.session import session_scope
from app.ingestion.canonical import CanonicalDocument
from app.parsers.pipeline import parse_document
from app.parsers.spreadsheets import table_payloads
from app.rag.chunking import Chunker
from app.services.storage import SupabaseStorage

log = get_logger("ingest.tasks")


def run_ingestion(job_id: str, document_id: str, workspace_id: str, version: int) -> dict:
    """Process one document version end-to-end. Raises on failure after the
    job/document rows are marked failed (the caller logs the exception)."""
    storage = SupabaseStorage()
    with session_scope() as session:
        try:
            job = _claim(session, job_id)
            if job is None:
                return {"skipped": True}

            doc = session.get(Document, uuid.UUID(document_id))
            if doc is None:
                raise RuntimeError("document row missing")
            _progress(session, job, 10, "download")

            data = storage.download_bytes(doc.storage_path)
            _progress(session, job, 15, "detect")

            canonical = parse_document(
                data,
                document_id=document_id,
                filename=doc.source_filename,
                declared_format=doc.fmt if doc.fmt != "unknown" else None,
            )
            _progress(session, job, 30, "parse")

            _wipe_version(session, document_id)
            _persist_canonical(session, document_id, version, canonical)
            _progress(session, job, 50, "persist")

            chunks = Chunker().chunk_document(canonical)
            chunk_models = _persist_chunks(session, workspace_id, document_id, chunks)
            _progress(session, job, 65, "chunk")

            embedded = _embed_and_store(session, workspace_id, document_id, chunk_models, chunks)
            _progress(session, job, 85, "embed")

            doc.status = "ready"
            doc.fmt = canonical.fmt
            doc.mime = canonical.mime
            doc.version = version
            job.status = "succeeded"
            job.progress = 100
            job.stage = "finalize"
            job.finished_at = datetime.now(timezone.utc)
            job.result = _result(canonical, chunks, embedded)
            session.commit()
            log.info("ingest_done", document_id=document_id,
                     chunks=len(chunks), embedded=embedded)
            return {"document_id": document_id, "chunks": len(chunks), "embedded": embedded}
        except Exception as exc:  # noqa: BLE001
            log.error("ingest_failed", document_id=document_id, error=str(exc))
            _fail(session, job_id, document_id, exc)
            raise


def _claim(session: Session, job_id: str) -> ProcessingJob | None:
    job = session.get(ProcessingJob, uuid.UUID(job_id))
    if job is None or job.status not in {"queued", "failed"}:
        return None
    job.attempts = (job.attempts or 0) + 1
    if job.attempts > job.max_attempts:
        job.status = "dead"
        session.commit()
        return None
    job.status = "running"
    job.started_at = job.started_at or datetime.now(timezone.utc)
    session.commit()
    return job


def _progress(session: Session, job: ProcessingJob, pct: int, stage: str) -> None:
    job.progress = pct
    job.stage = stage
    session.commit()


def _wipe_version(session: Session, document_id: str) -> None:
    """Remove all stored artifacts of previous versions (re-ingest is a reset)."""
    did = uuid.UUID(document_id)
    chunk_ids = select(DocumentChunk.id).where(DocumentChunk.document_id == did)
    row_ids = select(DocumentTableRow.id).where(DocumentTableRow.document_id == did)
    # children first (SQLite FKs are enforced per-connection)
    session.query(DocumentTableCell).filter(
        DocumentTableCell.row_db_id.in_(row_ids)
    ).delete(synchronize_session=False)
    for model in (DocumentTableRow, DocumentTable, DocumentElement, DocumentSection):
        session.query(model).filter(model.document_id == did).delete(synchronize_session=False)
    session.query(RetrievalCandidate).filter(
        RetrievalCandidate.chunk_db_id.in_(chunk_ids)
    ).delete(synchronize_session=False)
    session.query(DocumentEmbedding).filter(DocumentEmbedding.document_id == did).delete(synchronize_session=False)
    session.query(DocumentChunk).filter(DocumentChunk.document_id == did).delete(synchronize_session=False)
    session.query(DocumentVersion).filter(DocumentVersion.document_id == did).delete(synchronize_session=False)
    session.commit()


def _persist_canonical(session: Session, document_id: str, version: int, doc: CanonicalDocument) -> None:
    did = uuid.UUID(document_id)
    enriched = doc.to_dict()
    enriched["tables"] = table_payloads(doc)
    session.add(DocumentVersion(
        document_id=did,
        version=version,
        canonical=enriched,
        quality=doc.quality.to_dict(),
        parser_versions=doc.quality.parser_versions,
    ))
    for s in doc.sections:
        session.add(DocumentSection(
            document_id=did, section_id=s.section_id,
            title=(s.title or "")[:500], level=s.level, parent_section_id=s.parent_id,
        ))
    for e in doc.elements:
        prov = e.provenance
        session.add(DocumentElement(
            document_id=did, section_id=e.section_id, element_id=e.element_id,
            kind=e.kind, text=e.text, level=e.level,
            page=prov.page if prov else None,
            sheet=prov.sheet if prov else None,
            locator=prov.locator if prov else None,
        ))
    for t in doc.tables:
        tmodel = DocumentTable(
            document_id=did, table_id=t.table_id, title=t.title, sheet=t.sheet,
            pages=t.pages or [], headers=t.headers or [],
            n_rows=t.n_rows, n_cols=t.n_cols,
            footnotes=t.footnotes or [], validation_flags=t.validation_flags or [],
        )
        session.add(tmodel)
        session.flush()
        for r in t.rows:
            rmodel = DocumentTableRow(
                document_id=did, table_db_id=tmodel.id,
                row_index=r.cells[0].row_index if r.cells else 0,
                is_header=r.is_header,
                page=r.provenance.page if r.provenance else None,
            )
            session.add(rmodel)
            session.flush()
            for c in r.cells:
                session.add(DocumentTableCell(
                    row_db_id=rmodel.id, row_index=c.row_index, col_index=c.col_index,
                    raw_value=c.raw_value, normalized_value=c.normalized_value,
                    data_type=c.data_type, unit=c.unit, currency=c.currency,
                    is_negative=c.is_negative, decimal_value=c.decimal_value,
                ))
    session.commit()


def _persist_chunks(session: Session, workspace_id: str, document_id: str, chunks) -> list[DocumentChunk]:
    did = uuid.UUID(document_id)
    wid = uuid.UUID(workspace_id)
    out: list[DocumentChunk] = []
    for index, ch in enumerate(chunks):
        pages = [p.get("page") for p in ch.provenance if p.get("page")]
        sheet = next((p.get("sheet") for p in ch.provenance if p.get("sheet")), None)
        metadata = ch.metadata or {}
        model = DocumentChunk(
            chunk_id=ch.chunk_id, document_id=did, workspace_id=wid, version=1,
            seq=index, kind=ch.kind, text=ch.text, token_count=ch.token_count,
            parent_section_id=ch.parent_section_id,
            section_title=metadata.get("section_title"),
            pages=pages, sheet=sheet,
            locator=(ch.provenance[0].get("locator") if ch.provenance else None),
            table_id=metadata.get("table_id"),
            content_hash=ch.content_hash,
            metadata_={k: v for k, v in metadata.items() if k != "headers"},
        )
        session.add(model)
        out.append(model)
    session.commit()
    return out


def _embed_and_store(session, workspace_id, document_id, chunk_models, chunks) -> int:
    from app.ai.embeddings import EmbeddingService  # lazy: needs the API key

    try:
        embedder = EmbeddingService()
    except Exception as exc:  # noqa: BLE001
        log.warning("embedder_unavailable", document_id=document_id, error=str(exc))
        raise
    vectors = embedder.embed_batch([ch.text for ch in chunks])
    did = uuid.UUID(document_id)
    wid = uuid.UUID(workspace_id)
    for model, vec in zip(chunk_models, vectors):
        session.add(DocumentEmbedding(
            chunk_db_id=model.id, document_id=did, workspace_id=wid,
            model_id=embedder.model_id, model_version=embedder.model_version,
            dim=embedder.dim, embedding=vec,
        ))
    session.commit()
    return len(vectors)


def _result(canonical: CanonicalDocument, chunks, embedded: int) -> dict:
    return {
        "elements": len(canonical.elements),
        "tables": len(canonical.tables),
        "sections": len(canonical.sections),
        "chunks": len(chunks),
        "embedded": embedded,
        "quality": {
            "text_coverage": canonical.quality.text_coverage,
            "flags": canonical.quality.validation_flags,
        },
    }


def _fail(session: Session, job_id: str, document_id: str, exc: Exception) -> None:
    job = session.get(ProcessingJob, uuid.UUID(job_id))
    if job is None:
        return
    job.status = "dead" if (job.attempts or 0) >= job.max_attempts else "failed"
    job.error = str(exc)[:2000]
    doc = session.get(Document, uuid.UUID(document_id))
    if doc is not None:
        doc.status = "failed"  # user can reprocess from the UI
    if job.status == "dead":
        job.finished_at = datetime.now(timezone.utc)
    session.commit()
