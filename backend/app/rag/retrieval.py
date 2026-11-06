"""Hybrid retrieval: lexical scoring + dense cosine + RRF + optional rerank.

SQLite-friendly: chunk text is scored in Python over a LIKE-prefiltered
candidate set; embeddings are JSON columns compared with numpy cosine. Every
search is workspace-scoped and document-filtered by construction. Runs and
candidates are persisted for evaluation.
"""

from __future__ import annotations

import math
import re
import uuid as uuid_mod

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai.embeddings import EmbeddingService
from app.ai.reranker import RerankerService
from app.core.config import get_settings
from app.core.observability import get_logger
from app.db.models_documents import Document
from app.db.models_retrieval import (
    DocumentChunk,
    DocumentEmbedding,
    RetrievalCandidate,
    RetrievalRun,
)
from app.rag.context import ScoredChunk
from app.rag.fusion import reciprocal_rank_fusion

log = get_logger("retrieval")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _uuid(value) -> uuid_mod.UUID:
    return value if isinstance(value, uuid_mod.UUID) else uuid_mod.UUID(str(value))


class HybridRetriever:
    def __init__(self, embedder: EmbeddingService | None = None, reranker: RerankerService | None = None) -> None:
        self.settings = get_settings()
        self.embedder = embedder
        self._embedder_ready = embedder is not None
        self.reranker = reranker or RerankerService()

    def _get_embedder(self):
        """Lazily construct the (Gemini-backed) embedder; None disables dense."""
        if not self._embedder_ready:
            try:
                self.embedder = EmbeddingService()
            except Exception as exc:  # noqa: BLE001
                log.warning("dense_disabled", error=str(exc))
                self.embedder = None
            self._embedder_ready = True
        return self.embedder

    def _scoped(self, session: Session, workspace_id: str, document_ids: list[str] | None):
        q = select(DocumentChunk).where(DocumentChunk.workspace_id == _uuid(workspace_id))
        if document_ids:
            q = q.where(DocumentChunk.document_id.in_([_uuid(d) for d in document_ids]))
        return q

    @staticmethod
    def _row(c: DocumentChunk) -> dict:
        return {
            "id": c.id,
            "chunk_id": c.chunk_id,
            "document_id": c.document_id,
            "text": c.text,
            "kind": c.kind,
            "pages": c.pages or [],
            "sheet": c.sheet,
            "locator": c.locator,
            "section_title": c.section_title,
            "parent_section_id": c.parent_section_id,
            "token_count": c.token_count,
            "seq": c.seq,
        }

    def lexical(self, session: Session, query: str, workspace_id: str, document_ids: list[str] | None, limit: int) -> list[dict]:
        """Term-overlap scoring over a LIKE-prefiltered candidate set."""
        query_terms = _terms(query)
        if not query_terms:
            return []
        like_ors = [DocumentChunk.text.ilike(f"%{t}%") for t in list(query_terms)[:6]]
        rows = [
            self._row(c)
            for c in session.scalars(
                self._scoped(session, workspace_id, document_ids).filter(or_(*like_ors))
            )
        ]
        scored: list[dict] = []
        for r in rows:
            text_terms = _terms(r["text"])
            overlap = query_terms & text_terms
            if not overlap:
                continue
            r["rank"] = len(overlap) / math.sqrt(max(1, len(text_terms)))
            scored.append(r)
        scored.sort(key=lambda r: (-r["rank"], r["seq"]))
        return scored[:limit]

    def exact_phrase(self, session: Session, phrase: str, workspace_id: str, document_ids: list[str] | None, limit: int) -> list[dict]:
        q = (
            self._scoped(session, workspace_id, document_ids)
            .filter(DocumentChunk.text.ilike(f"%{phrase}%"))
            .order_by(DocumentChunk.seq)
            .limit(limit)
        )
        rows = [self._row(c) for c in session.scalars(q)]
        for r in rows:
            r["rank"] = 1.0
        return rows

    def dense(self, session: Session, query: str, workspace_id: str, document_ids: list[str] | None, limit: int) -> list[dict]:
        if self._get_embedder() is None:
            return []
        qv = np.asarray(self.embedder.embed_query(query), dtype="float32")
        qnorm = float(np.linalg.norm(qv)) or 1e-9
        stmt = (
            select(DocumentChunk, DocumentEmbedding.embedding)
            .join(DocumentEmbedding, DocumentEmbedding.chunk_db_id == DocumentChunk.id)
            .where(DocumentChunk.workspace_id == _uuid(workspace_id))
        )
        if document_ids:
            stmt = stmt.where(DocumentChunk.document_id.in_([_uuid(d) for d in document_ids]))
        scored: list[tuple[dict, float]] = []
        for chunk, emb in session.execute(stmt).all():
            if not emb:
                continue
            v = np.asarray(emb, dtype="float32")
            if v.shape != qv.shape:
                continue  # stale model dimension — skip rather than crash
            denom = float(np.linalg.norm(v)) * qnorm
            sim = float(np.dot(v, qv) / denom) if denom else 0.0
            scored.append((self._row(chunk), sim))
        scored.sort(key=lambda rs: -rs[1])
        out = []
        for r, sim in scored[:limit]:
            r["rank"] = sim
            out.append(r)
        return out

    def _parent_and_neighbours(self, session: Session, rows: list[dict]) -> None:
        """Attach parent-section text and adjacent-chunk text for context expansion."""
        for row in rows:
            if row.get("parent_section_id"):
                texts = session.scalars(
                    select(DocumentChunk.text)
                    .where(
                        DocumentChunk.document_id == row["document_id"],
                        DocumentChunk.parent_section_id == row["parent_section_id"],
                        DocumentChunk.token_count < 2000,
                    )
                    .order_by(DocumentChunk.seq)
                ).all()
                row["parent_text"] = "\n".join(t for t in texts if t)
            if row.get("seq") is not None:
                nb = session.scalars(
                    select(DocumentChunk.text).where(
                        DocumentChunk.document_id == row["document_id"],
                        DocumentChunk.seq.in_([row["seq"] - 1, row["seq"] + 1]),
                    )
                ).all()
                row["neighbours"] = [t for t in nb if t]
            else:
                row["neighbours"] = []

    def retrieve(
        self,
        session: Session,
        query: str,
        workspace_id: str,
        document_ids: list[str] | None = None,
        *,
        exact_phrase: str | None = None,
        persist_run_id: str | None = None,
    ) -> list[ScoredChunk]:
        s = self.settings
        if exact_phrase:
            lexical_rows = self.exact_phrase(session, exact_phrase, workspace_id, document_ids, s.lexical_candidates)
            dense_rows = self.dense(session, exact_phrase, workspace_id, document_ids, s.dense_candidates)
        else:
            lexical_rows = self.lexical(session, query, workspace_id, document_ids, s.lexical_candidates)
            dense_rows = self.dense(session, query, workspace_id, document_ids, s.dense_candidates)

        by_id: dict[str, dict] = {}
        lexical_ranking: list[str] = []
        dense_ranking: list[str] = []
        for r in lexical_rows:
            key = str(r["chunk_id"])
            by_id.setdefault(key, r)
            if key not in lexical_ranking:
                lexical_ranking.append(key)
        for r in dense_rows:
            key = str(r["chunk_id"])
            by_id.setdefault(key, r)
            if key not in dense_ranking:
                dense_ranking.append(key)

        fused = reciprocal_rank_fusion([lexical_ranking, dense_ranking], k=s.rrf_k)
        top = fused[: s.rerank_candidates]

        reranked = None
        if exact_phrase is None:
            candidates = [(cid, score, by_id[cid]["text"]) for cid, score in top]
            reranked = self.reranker.rerank(query, candidates)

        final = reranked or top
        self._parent_and_neighbours(session, [by_id[cid] for cid, _ in final[: s.final_context_chunks]])

        scored: list[ScoredChunk] = []
        titles = self._document_titles(session, workspace_id)
        for cid, score in final[: s.final_context_chunks]:
            r = by_id[cid]
            scored.append(
                ScoredChunk(
                    chunk_id=cid,
                    document_id=str(r["document_id"]),
                    document_title=titles.get(str(r["document_id"]), str(r["document_id"])),
                    text=r["text"],
                    score=float(score),
                    kind=r.get("kind") or "text",
                    pages=r.get("pages") or [],
                    sheet=r.get("sheet"),
                    locator=r.get("locator"),
                    section_title=r.get("section_title"),
                    parent_text=r.get("parent_text"),
                    parent_section_id=r.get("parent_section_id"),
                    neighbours=r.get("neighbours") or [],
                )
            )

        if persist_run_id:
            self._persist(session, persist_run_id, query, lexical_rows, dense_rows, scored, reranked is not None)
        return scored

    def _document_titles(self, session: Session, workspace_id: str) -> dict[str, str]:
        rows = session.execute(
            select(Document.id, Document.title).where(Document.workspace_id == _uuid(workspace_id))
        ).all()
        return {str(i): t for i, t in rows}

    def _persist(self, session, run_id, query, lexical_rows, dense_rows, scored, reranked) -> None:
        run = session.get(RetrievalRun, _uuid(run_id))
        if run is None:
            return
        run.lexical_count = len(lexical_rows)
        run.dense_count = len(dense_rows)
        run.reranked = reranked
        for rank, (cid, score) in enumerate(scored):
            chunk = session.scalar(
                select(DocumentChunk)
                .where(DocumentChunk.workspace_id == run.workspace_id, DocumentChunk.chunk_id == cid)
                .limit(1)
            )
            if chunk is None:
                continue
            session.add(
                RetrievalCandidate(
                    run_id=run.id,
                    chunk_db_id=chunk.id,
                    rank=rank,
                    source="rerank",
                    score=float(score),
                    selected=True,
                )
            )
