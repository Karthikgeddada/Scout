"""Test bootstrap: makes `app.*` importable without installing the package.

Pure-stdlib modules (ingestion, rag, analytics, eval) run anywhere; modules with
heavy imports (fastapi, sqlalchemy, supabase) are exercised only where those
dependencies exist (CI/Docker).
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
