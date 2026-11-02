"""Local reranker (Qwen3-Reranker family) with graceful fallback.

If local inference is unavailable, reranking degrades to the fused ordering.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.core.observability import get_logger

log = get_logger("reranker")


class RerankerService:
    def __init__(self) -> None:
        s = get_settings()
        self.enabled = s.reranker_enabled
        self.model_id = s.reranker_model_id
        self.device = s.reranker_device if s.reranker_device != "auto" else "cpu"
        if s.reranker_device == "auto":
            try:
                import torch

                self.device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                pass
        self._model = None
        self._tokenizer = None

    def _load(self):
        if not self.model_id:
            raise UpstreamError("SCOUT_RERANKER_MODEL_ID not configured")
        if self._model is None:
            try:
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
            except ImportError as exc:  # pragma: no cover
                raise UpstreamError("transformers not installed") from exc
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.model_id, trust_remote_code=True, num_labels=1
            ).to(self.device).eval()
        return self._model, self._tokenizer

    def rerank(self, query: str, candidates: list[tuple[str, float, str]]) -> list[tuple[str, float, str]] | None:
        """candidates: (id, fused_score, text). Returns re-scored list or None to
        signal "use fused order instead" when local inference is unavailable."""
        if not self.enabled or not candidates:
            return None
        try:
            import torch

            model, tokenizer = self._load()
            pairs = [[query, text] for _, _, text in candidates]
            scores: list[float] = []
            for start in range(0, len(pairs), 16):
                encoded = tokenizer(
                    pairs[start : start + 16],
                    padding=True,
                    truncation=True,
                    max_length=2048,
                    return_tensors="pt",
                ).to(self.device)
                with torch.no_grad():
                    logits = model(**encoded).logits.view(-1).float().cpu()
                scores.extend(torch.sigmoid(logits).tolist())
            ranked = sorted(zip(candidates, scores), key=lambda cs: -cs[1])
            return [(cid, float(score), text) for (cid, _, text), score in ranked]
        except Exception as exc:  # noqa: BLE001
            log.warning("rerank_unavailable", error=str(exc))
            return None
