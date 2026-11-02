"""Deterministic query router.

Classifies a request into a task type and (when useful) decomposes it into
sub-retrieval tasks. Rules-based and testable — no agent framework needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class TaskType(str, Enum):
    SEMANTIC_SEARCH = "semantic_search"
    EXACT_PHRASE = "exact_phrase"
    SECTION_SUMMARY = "section_summary"
    MULTI_DOC = "multi_doc"
    NUMERIC_ANALYTICS = "numeric_analytics"
    COMPARISON = "comparison"
    EXTRACTION = "extraction"
    REPORT = "report"


_ANALYTICS_RE = re.compile(
    r"\b(total|sum|add up|average|mean|avg|minimum|maximum|how much|how many|"
    r"percentage|percent change|pct|growth|decline|increase|decrease|"
    r"year[- ]over[- ]year|yoy|month[- ]over[- ]month|revenue|net income|ebitda)\b",
    re.IGNORECASE,
)
_COMPARISON_RE = re.compile(
    r"\b(compar\w*|differen\w*|versus|\bvs\.?|between .+ and .+|changed)\b", re.IGNORECASE
)
_SUMMARY_RE = re.compile(r"\b(summar\w+|tl;?dr|overview|key points|executive summary)\b", re.IGNORECASE)
_EXTRACT_RE = re.compile(
    r"\b(extract|list all (the )?(names|dates|amounts|organizations|parties|"
    r"deadlines|metrics)|find all (the )?(names|dates|amounts|organizations))\b",
    re.IGNORECASE,
)
_REPORT_RE = re.compile(r"\b(report|draft|memo|write up)\b", re.IGNORECASE)
_QUOTED_RE = re.compile(r'"([^"]{3,})"')


@dataclass
class QueryPlan:
    primary: TaskType
    sub_queries: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    requires_analytics: bool = False
    requires_rerank: bool = True
    exact_phrase: str | None = None
    notes: list[str] = field(default_factory=list)


class QueryRouter:
    """Deterministic rules evaluated in priority order."""

    def plan(self, query: str, *, document_ids: list[str] | None = None) -> QueryPlan:
        query = (query or "").strip()
        document_ids = list(document_ids or [])
        notes: list[str] = []

        quoted = _QUOTED_RE.search(query)
        if quoted:
            return QueryPlan(
                primary=TaskType.EXACT_PHRASE,
                document_ids=document_ids,
                exact_phrase=quoted.group(1),
                notes=["quoted phrase detected; lexical-first retrieval"],
            )

        if _REPORT_RE.search(query):
            return QueryPlan(primary=TaskType.REPORT, document_ids=document_ids, notes=notes)

        if _COMPARISON_RE.search(query) and len(document_ids) >= 2:
            return QueryPlan(
                primary=TaskType.COMPARISON,
                document_ids=document_ids,
                sub_queries=[f"{doc}: {query}" for doc in document_ids],
                notes=["per-document sub-retrieval for comparison"],
            )

        if _ANALYTICS_RE.search(query):
            # Deterministic computation wins over generated prose for numbers.
            return QueryPlan(
                primary=TaskType.NUMERIC_ANALYTICS,
                document_ids=document_ids,
                requires_analytics=True,
                notes=["aggregate/numeric intent detected"],
            )

        if _SUMMARY_RE.search(query):
            primary = TaskType.MULTI_DOC if len(document_ids) > 1 else TaskType.SECTION_SUMMARY
            return QueryPlan(primary=primary, document_ids=document_ids, requires_rerank=False, notes=notes)

        if _EXTRACT_RE.search(query):
            return QueryPlan(primary=TaskType.EXTRACTION, document_ids=document_ids, notes=notes)

        if len(document_ids) > 1:
            notes.append("multiple documents selected without comparison intent; multi-doc retrieval")
            return QueryPlan(primary=TaskType.MULTI_DOC, document_ids=document_ids, notes=notes)

        return QueryPlan(primary=TaskType.SEMANTIC_SEARCH, document_ids=document_ids, notes=notes)
