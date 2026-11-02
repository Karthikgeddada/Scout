"""Hybrid retrieval: PostgreSQL FTS (lexical) + pgvector (dense) + RRF + rerank.

Every search is workspace-scoped and document-filtered; unscoped vector search is
impossible by construction. Runs and candidates are persisted for evaluation.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.embeddings import EmbeddingService
from app.ai.reranker import RerankerService
from app.core.config import get_settings
from app.core.observability import get_logger
from app.rag.context import ScoredChunk
from app.rag.fusion import reciprocal_rank_fusion

log = get_logger("retrieval")

_LEXICAL_SQL = """
SELECT c.id, c.chunk_id, c.document_id, c.text, c.kind, c.pages, c.sheet,
       c.locator, c.section_title, c.parent_section_id, c.token_count,
       ts_rank(c.tsv, plainto_tsquery('english', :q)) AS rank
FROM document_chunks c
WHERE c.workspace_id = :ws AND c.tsv @@ plainto_tsquery('english', :q)
  AND (:docs IS NULL OR c.document_id = ANY(CAST(:docs AS uuid[])))
ORDER BY rank DESC
LIMIT :limit
"""

# Exact-phrase variant: match the raw phrase inside chunk text.
_EXACT_SQL = """
SELECT c.id, c.chunk_id, c.document_id, c.text, c.kind, c.pages, c.sheet,
       c.locator, c.section_title, c.parent_section_id, c.token_count, 1.0 AS rank
FROM document_chunks c
WHERE c.workspace_id = :ws AND c.text ILIKE :phrase
  AND (:docs IS NULL OR c.document_id = ANY(CAST(:docs AS uuid[])))
LIMIT :limit
"""

_DENSE_SQL = """
SELECT c.id, c.chunk_id, c.document_id, c.text, c.kind, c.pages, c.sheet,
       c.locator, c.section_title, c.parent_section_id, c.token_count,
       1 - (e.embedding <=> CAST(:qv AS vector)) AS rank
FROM document_embeddings e
JOIN document_chunks c ON c.id = e.chunk_db_id
WHERE e.workspace_id = :ws
  AND (:docs IS NULL OR c.document_id = ANY(CAST(:docs AS uuid[])))
ORDER BY e.embedding <=> CAST(:qv AS vector)
LIMIT :limit
"""


class HybridRetriever:
    def __init__(self, embedder: EmbeddingService | None = None, reranker: RerankerService | None = None) -> None:
        self.settings = get_settings()
        self.embedder = embedder
        self.reranker = reranker or RerankerService()

    def _rows(self, session: Session, sql: str, params: dict) -> list[dict]:
        result = session.execute(text(sql), params)
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]

    def lexical(self, session: Session, query: str, workspace_id: str, document_ids: list[str] | None, limit: int) -> list[dict]:
        rows = self._rows(
            session, _LEXICAL_SQL,
            {"q": query, "ws": workspace_id, "docs": document_ids or None, "limit": limit},
        )
        return rows

    def exact_phrase(self, session: Session, phrase: str, workspace_id: str, document_ids: list[str] | None, limit: int) -> list[dict]:
        return self._rows(
            session, _EXACT_SQL,
            {"phrase": f"%{phrase}%", "ws": workspace_id, "docs": document_ids or None, "limit": limit},
        )

    def dense(self, session: Session, query: str, workspace_id: str, document_ids: list[str] | None, limit: int) -> list[dict]:
        if self.embedder is None:
            return []
        qv = "[" + ",".join(f"{v:.6f}" for v in self.embedder.embed_query(query)) + "]"
        return self._rows(
            session, _DENSE_SQL,
            {"qv": qv, "ws": workspace_id, "docs": document_ids or None, "limit": limit},
        )

    def _parent_and_neighbours(self, session: Session, rows: list[dict]) -> None:
        """Attach parent-section text and adjacent-chunk text for context expansion."""
        for row in rows:
            if row.get("parent_section_id"):
                row["parent_text"] = session.scalar(
                    text(
                        "SELECT string_agg(text, '\n' ORDER BY created_at) FROM document_chunks "
                        "WHERE document_id = :d AND parent_section_id = :s AND token_count < 2000"
                    ),
                    {"d": row["document_id"], "s": row["parent_section_id"]},
                )
            row["neighbours"] = [
                r[0]
                for r in session.execute(
                    text(
                        "SELECT text FROM document_chunks WHERE document_id = :d "
                        "ORDER BY created_at OFFSET :o LIMIT 2"
                    ),
                    {"d": row["document_id"], "o": max(0, int(row["chunk_index"] or 0) - 1)},
                ).fetchall()
            ] if row.get("chunk_index") else []

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
            text("SELECT id::text, title FROM documents WHERE workspace_id = :ws"), {"ws": workspace_id}
        ).fetchall()
        return dict(rows)

    def _persist(self, session, run_id, query, lexical_rows, dense_rows, scored, reranked) -> None:
        session.execute(
            text(
                "UPDATE retrieval_runs SET lexical_count = :lx, dense_count = :dn, reranked = :rr "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"lx": len(lexical_rows), "dn": len(dense_rows), "rr": reranked, "id": run_id},
        )
        for rank, (cid, score) in enumerate(scored):
            session.execute(
                text(
                    "INSERT INTO retrieval_candidates (id, run_id, chunk_db_id, rank, source, score, selected) "
                    "SELECT gen_random_uuid(), CAST(:rid AS uuid), id, :rank, 'rerank', :score, true "
                    "FROM document_chunks WHERE chunk_id = :cid LIMIT 1"
                ),
                {"rid": run_id, "rank": rank, "score": score, "cid": cid},
            )
