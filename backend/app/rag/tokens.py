"""Token estimation without external tokenizer dependencies.

A deterministic approximation (4 chars/token) keeps chunking testable everywhere;
the service layer can substitute an exact tokenizer by injecting `counter`.
"""

from __future__ import annotations

from typing import Callable


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


DefaultCounter: Callable[[str], int] = estimate_tokens
