"""Central application settings.

All model selection and tuning parameters are configuration-driven; nothing is
hardcoded at call sites (see .env.example for every supported variable).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCOUT_", env_file=".env", extra="ignore")

    # Runtime
    environment: str = "development"  # development | staging | production
    api_prefix: str = "/v1"
    # Comma-separated origins (Render sets SCOUT_CORS_ALLOW_ORIGINS to the Vercel URL)
    cors_allow_origins: str = "http://localhost:5173"
    # Enable /docs Swagger UI (disable in sensitive prod environments if desired)
    api_docs_enabled: bool = True

    # Supabase (service-role key is backend-only and must never reach a client)
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    storage_bucket: str = "documents"

    # Database — SQLite by default (zero-config; file lives next to the app).
    # Any SQLAlchemy URL works, e.g. a Postgres URL if you want one.
    database_url: str = "sqlite:///./scout.db"

    # Gemini (server-side only)
    gemini_api_key: str = ""
    gemini_model: str = ""
    gemini_max_output_tokens: int = 8192

    # Embeddings via the free Gemini API tier (no local models, no torch).
    embedding_model_id: str = "gemini-embedding-001"
    embedding_dim: int = 768
    embedding_batch_size: int = 64

    # Local reranker is disabled by default in the light edition; retrieval
    # falls back to the fused (RRF) ordering, which is already strong.
    reranker_model_id: str = ""
    reranker_device: str = "cpu"
    reranker_enabled: bool = False

    # Retrieval
    rrf_k: int = 60
    lexical_candidates: int = 50
    dense_candidates: int = 50
    rerank_candidates: int = 40
    final_context_chunks: int = 12
    context_token_budget: int = 12000

    # Chunking (initial defaults; the chunker applies special rules to tables/code)
    chunk_target_tokens: int = 550
    chunk_overlap_tokens: int = 80
    chunk_min_tokens: int = 80

    # Uploads
    upload_max_bytes: int = 100 * 1024 * 1024

    # Rate limiting
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    # Jobs
    job_max_retries: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
