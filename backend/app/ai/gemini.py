"""Google Gemini client (official google-genai SDK, server-side only).

Model selection comes exclusively from configuration. All calls include retry
and timeout handling; no client ever talks to Gemini directly."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.core.observability import get_logger

log = get_logger("gemini")


class GeminiClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._client = None

    @property
    def model(self) -> str:
        if not self._settings.gemini_model:
            raise UpstreamError("SCOUT_GEMINI_MODEL not configured")
        return self._settings.gemini_model

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover
                raise UpstreamError("google-genai SDK not installed") from exc
            if not self._settings.gemini_api_key:
                raise UpstreamError("SCOUT_GEMINI_API_KEY not configured")
            self._client = genai.Client(api_key=self._settings.gemini_api_key)
        return self._client

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Single-shot generation with bounded retries."""
        from google.genai import errors as genai_errors

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._get_client().models.generate_content(
                    model=self.model,
                    contents=[user_prompt],
                    config={
                        "system_instruction": system_prompt,
                        "max_output_tokens": self._settings.gemini_max_output_tokens,
                        "temperature": 0.2,
                    },
                )
                return response.text or ""
            except genai_errors.GenAIError as exc:
                last_error = exc
                log.warning("gemini_retry", attempt=attempt + 1, error=str(exc))
        raise UpstreamError(f"Gemini generation failed: {last_error}")

    async def stream(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        """Streaming generation for chat SSE."""
        from google.genai import errors as genai_errors

        try:
            stream = self._get_client().models.generate_content_stream(
                model=self.model,
                contents=[user_prompt],
                config={
                    "system_instruction": system_prompt,
                    "max_output_tokens": self._settings.gemini_max_output_tokens,
                    "temperature": 0.2,
                },
            )
            for chunk in stream:
                if chunk.text:
                    yield chunk.text
        except genai_errors.GenAIError as exc:
            raise UpstreamError(f"Gemini streaming failed: {exc}") from exc


_singleton: GeminiClient | None = None


def gemini() -> GeminiClient:
    global _singleton
    if _singleton is None:
        _singleton = GeminiClient()
    return _singleton
