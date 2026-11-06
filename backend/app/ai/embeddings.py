"""Embedding service via the free Gemini API tier (text-embedding-004).

No local models and no torch: text batches are embedded through the Gemini
API, dimensions are validated against configuration, transient failures are
retried. Runs server-side only; the key never reaches a client.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.core.observability import get_logger

log = get_logger("embeddings")


class EmbeddingService:
    def __init__(self) -> None:
        s = get_settings()
        if not s.gemini_api_key:
            raise UpstreamError("SCOUT_GEMINI_API_KEY not configured")
        self.model_id = s.embedding_model_id or "text-embedding-004"
        self.dim = s.embedding_dim
        self.batch_size = s.embedding_batch_size
        self._version: str | None = self.model_id
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover
                raise UpstreamError("google-genai not installed") from exc
            self._client = genai.Client(api_key=get_settings().gemini_api_key)
        return self._client

    @property
    def model_version(self) -> str | None:
        return self._version

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Gemini embeddings, batched, retried, dimension-validated."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            for attempt in range(3):
                try:
                    response = self._get_client().models.embed_content(
                        model=self.model_id, contents=batch
                    )
                    embeddings = getattr(response, "embeddings", None) or []
                    if len(embeddings) != len(batch):
                        raise UpstreamError(
                            f"embedding count {len(embeddings)} != batch {len(batch)}"
                        )
                    vectors.extend([list(e.values) for e in embeddings])
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        raise UpstreamError(f"embedding failed: {exc}") from exc
                    log.warning("embed_retry", attempt=attempt + 1)
        for v in vectors:
            if len(v) != self.dim:
                raise UpstreamError(f"embedding dim {len(v)} != configured {self.dim}")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]
