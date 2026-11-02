"""Deterministic plan execution over dataset records.

Records are list[dict] with Decimal-safe values for number/currency columns (the
XLSX/CSV ingestion layer provides them). All arithmetic uses Decimal. Every result
carries source provenance (sheet + cell ranges / table rows) so answers cite cells,
not vibes. Polars/DuckDB adapters can reuse QueryPlan for large sheets; the semantics
implemented here are the reference implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.analytics.plan import QueryPlan


class ExecutorError(ValueError):
    pass


def _to_number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return Decimal(text)
    except Exception:
        return None


def _matches(flt, row: dict[str, Any], types: dict[str, str]) -> bool:
    raw = row.get(flt.column)
    if flt.op == "contains":
        return str(raw or "").lower().find(str(flt.value).lower()) >= 0
    if flt.op == "in":
        return raw in (flt.value or [])
    if flt.column in types and types[flt.column] in {"number", "currency"}:
        left, right = _to_number(raw), _to_number(flt.value)
        right2 = _to_number(flt.value2) if flt.value2 is not None else None
    else:
        left, right, right2 = raw, flt.value, flt.value2
    if left is None or right is None:
        return False
    return {
        "eq": lambda: left == right,
        "ne": lambda: left != right,
        "gt": lambda: left > right,
        "lt": lambda: left < right,
        "ge": lambda: left >= right,
        "le": lambda: left <= right,
        "between": lambda: (left >= min(right, right2)) and (left <= max(right, right2)),
    }[flt.op]()


def _bucket(value: Any, bucket: str) -> str | None:
    if isinstance(value, (date, datetime)):
        if bucket == "year":
            return str(value.year)
        if bucket == "quarter":
            return f"{value.year}-Q{(value.month - 1) // 3 + 1}"
        if bucket == "month":
            return f"{value.year}-{value.month:02d}"
        if bucket == "week":
            return f"{value.isocalendar().year}-W{value.isocalendar().week:02d}"
        if bucket == "day":
            return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        # YYYY(-MM(-DD))? prefix bucketing
        return value[: {"year": 4, "month": 7, "day": 10}.get(bucket, 7)]
    return None


@dataclass
class Provenance:
    sheet: str | None
    rows: list[Any] = field(default_factory=list)  # row indices or cell ranges


@dataclass
class ExecutionResult:
    rows: list[dict[str, Any]]
    provenance: Provenance
    plan_summary: dict[str, Any]


def execute_plan(plan: QueryPlan, records: list[dict[str, Any]]) -> ExecutionResult:
    types = plan.dataset.column_types
    source_rows = list(range(len(records)))

    # 1. filters
    filtered_idx = [
        i for i, row in enumerate(records) if all(_matches(f, row, types) for f in plan.filters)
    ]

    def _agg(op: str, values: list[Any]) -> Decimal | int:
        nums = [n for n in (_to_number(v) for v in values) if n is not None]
        if op in {"sum", "avg", "min", "max"} and not nums:
            raise ExecutorError(f"{op} over non-numeric or empty values")
        if op == "sum":
            return sum(nums, Decimal(0))
        if op == "avg":
            return (sum(nums, Decimal(0)) / Decimal(len(nums))).quantize(Decimal("0.0001"))
        if op == "min":
            return min(nums)
        if op == "max":
            return max(nums)
        if op == "count":
            return len(values)
        if op == "count_distinct":
            return len({str(v) for v in values})
        raise ExecutorError(f"unsupported op {op}")

    # 2. grouping / aggregation
    date_cols = [c for c, t in types.items() if t == "date"]
    grouped = bool(plan.group_by) or plan.date_bucket is not None
    if plan.date_bucket and not date_cols:
        raise ExecutorError("date_bucket requested but dataset has no date column")

    if not grouped:
        row: dict[str, Any] = {}
        for a in plan.aggregations:
            values = [records[i].get(a.column) for i in filtered_idx] if a.column else [None] * len(filtered_idx)
            row[a.alias or f"{a.op}_{a.column}"] = _agg(a.op, values)
        result_rows = [row] if (row or not plan.aggregations) else []
    else:
        groups: dict[tuple, list[int]] = {}
        for i in filtered_idx:
            rec = records[i]
            key = tuple(str(rec.get(c)) for c in plan.group_by)
            if plan.date_bucket:
                key = key + ((_bucket(rec.get(date_cols[0]), plan.date_bucket) or "unknown"),)
            groups.setdefault(key, []).append(i)
        result_rows = []
        for key, idxs in groups.items():
            out: dict[str, Any] = {}
            if plan.group_by:
                out.update(dict(zip(plan.group_by, key[: len(plan.group_by)])))
            if plan.date_bucket:
                out["period"] = key[-1]
            for a in plan.aggregations:
                values = [records[i].get(a.column) for i in idxs] if a.column else [None] * len(idxs)
                out[a.alias or f"{a.op}_{a.column}"] = _agg(a.op, values)
            result_rows.append(out)

    # 3. derived metrics (pct_change, yoy) — computed on the first aggregation
    if plan.derived and result_rows:
        agg_key = next(
            (a.alias or f"{a.op}_{a.column}" for a in plan.aggregations), None
        )
        if agg_key:
            ordered = result_rows  # grouped order assumed chronological
            for d in plan.derived:
                if d == "pct_change":
                    for prev, curr in zip(ordered, ordered[1:]):
                        prev_v, curr_v = _to_number(prev.get(agg_key)), _to_number(curr.get(agg_key))
                        if prev_v not in (None, 0) and curr_v is not None:
                            curr[f"pct_change_{agg_key}"] = (
                                ((curr_v - prev_v) / abs(prev_v)) * Decimal(100)
                            ).quantize(Decimal("0.01"))
                if d == "yoy":
                    for prev, curr in zip(ordered, ordered[1:]):
                        prev_v, curr_v = _to_number(prev.get(agg_key)), _to_number(curr.get(agg_key))
                        if prev_v is not None and curr_v is not None:
                            curr[f"yoy_delta_{agg_key}"] = curr_v - prev_v

    # 4. sort + limit
    if plan.sort_by:
        result_rows.sort(
            key=lambda r: (r.get(plan.sort_by) is None, r.get(plan.sort_by)),
            reverse=(plan.sort_dir == "desc"),
        )
    if plan.limit is not None:
        result_rows = result_rows[: plan.limit]

    # 5. provenance: sheet + the source row set that survived filtering
    prov_rows = [
        plan.dataset.provenance.get("row_offset", 0) + i + 1 for i in filtered_idx  # 1-based
    ]
    return ExecutionResult(
        rows=result_rows,
        provenance=Provenance(sheet=plan.dataset.sheet, rows=prov_rows[:1000]),
        plan_summary={
            "filters_applied": len(plan.filters),
            "records_scanned": len(records),
            "records_matched": len(filtered_idx),
            "aggregations": [a.alias or f"{a.op}_{a.column}" for a in plan.aggregations],
            "derived": plan.derived,
        },
    )
