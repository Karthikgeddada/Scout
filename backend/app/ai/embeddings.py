"""Local embedding service (Qwen3-Embedding family).

Batches texts, selects device (cuda -> cpu fallback), validates dimension,
tracks model version and retries transient failures. Not exposed to clients.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.core.observability import get_logger

log = get_logger("embeddings")


class EmbeddingService:
    def __init__(self) -> None:
        s = get_settings()
        if not s.embedding_model_id:
            raise UpstreamError("SCOUT_EMBEDDING_MODEL_ID not configured")
        self.model_id = s.embedding_model_id
        self.dim = s.embedding_dim
        self.device = self._resolve_device(s.embedding_device)
        self.batch_size = s.embedding_batch_size
        self._model = None
        self._tokenizer = None
        self._version: str | None = None

    @staticmethod
    def _resolve_device(pref: str) -> str:
        if pref != "auto":
            return pref
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _load(self):
        if self._model is None:
            try:
                from transformers import AutoModel, AutoTokenizer
            except ImportError as exc:  # pragma: no cover
                raise UpstreamError("transformers not installed") from exc
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_id, trust_remote_code=True, padding_side="left"
            )
            self._model = AutoModel.from_pretrained(self.model_id, trust_remote_code=True)
            self._model.to(self.device).eval()
            self._version = getattr(self._model.config, "_name_or_path", self.model_id)
        return self._model, self._tokenizer

    @property
    def model_version(self) -> str | None:
        return self._version

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Last-token-pooling embeddings (Qwen3 recipe), validated dimensions."""
        import torch

        model, tokenizer = self._load()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            for attempt in range(3):
                try:
                    encoded = tokenizer(
                        batch, padding=True, truncation=True, max_length=8192, return_tensors="pt"
                    ).to(self.device)
                    with torch.no_grad():
                        out = model(**encoded).last_hidden_state
                    mask = encoded["attention_mask"]
                    last_idx = mask.sum(dim=1) - 1
                    pooled = out[torch.arange(out.size(0)), last_idx]
                    pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
                    vectors.extend(pooled.cpu().tolist())
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
