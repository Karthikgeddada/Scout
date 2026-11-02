"""Restricted analytics query plans.

The LLM may PROPOSE a plan as JSON; the server validates it against a strict
whitelist (columns, operators, aggregations) derived from the actual dataset
schema. There is no path from a user/model to arbitrary SQL — ever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FILTER_OPS = {"eq", "ne", "gt", "lt", "ge", "le", "between", "in", "contains"}
AGG_OPS = {"sum", "avg", "min", "max", "count", "count_distinct"}
DERIVED_OPS = {"pct_change", "yoy"}
SORT_DIRS = {"asc", "desc"}
DATE_BUCKETS = {"year", "quarter", "month", "week", "day"}


class PlanValidationError(ValueError):
    pass


@dataclass
class DatasetSchema:
    document_id: str
    sheet: str | None
    table_id: str | None
    columns: list[str]
    column_types: dict[str, str]  # column -> text|number|currency|date
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class Filter:
    op: str
    column: str
    value: Any = None
    value2: Any = None  # for between


@dataclass
class Aggregation:
    op: str
    column: str | None = None  # None valid for count
    alias: str | None = None


@dataclass
class QueryPlan:
    dataset: DatasetSchema
    filters: list[Filter] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    date_bucket: str | None = None
    aggregations: list[Aggregation] = field(default_factory=list)
    derived: list[str] = field(default_factory=list)  # subset of DERIVED_OPS
    sort_by: str | None = None
    sort_dir: str = "desc"
    limit: int | None = None

    def validate(self) -> None:
        if self.limit is not None and not (1 <= self.limit <= 1000):
            raise PlanValidationError(f"limit out of range: {self.limit}")
        if self.sort_dir not in SORT_DIRS:
            raise PlanValidationError(f"bad sort_dir: {self.sort_dir}")
        if self.date_bucket and self.date_bucket not in DATE_BUCKETS:
            raise PlanValidationError(f"bad date_bucket: {self.date_bucket}")
        for d in self.derived:
            if d not in DERIVED_OPS:
                raise PlanValidationError(f"bad derived op: {d}")


def plan_from_json(payload: dict[str, Any], schema: DatasetSchema) -> QueryPlan:
    """Validate an LLM-proposed plan against the dataset schema. Strict rejection."""
    if not isinstance(payload, dict):
        raise PlanValidationError("plan must be a JSON object")
    allowed_keys = {
        "filters", "group_by", "date_bucket", "aggregations", "derived", "sort_by", "sort_dir", "limit"
    }
    unknown = set(payload) - allowed_keys
    if unknown:
        raise PlanValidationError(f"unknown plan keys: {sorted(unknown)}")

    def _col(name: Any, what: str) -> str:
        if not isinstance(name, str) or name not in schema.columns:
            raise PlanValidationError(f"{what} column not in dataset: {name!r}")
        return name

    filters: list[Filter] = []
    for f in payload.get("filters", []):
        if not isinstance(f, dict) or set(f) - {"op", "column", "value", "value2"}:
            raise PlanValidationError(f"bad filter: {f!r}")
        if f.get("op") not in FILTER_OPS:
            raise PlanValidationError(f"bad filter op: {f.get('op')!r}")
        col = _col(f.get("column"), "filter")
        if f["op"] in {"gt", "lt", "ge", "le", "between"} and schema.column_types.get(col) not in {
            "number", "currency", "date"
        }:
            raise PlanValidationError(f"numeric comparison on non-numeric column {col}")
        filters.append(Filter(op=f["op"], column=col, value=f.get("value"), value2=f.get("value2")))

    group_by = [_col(c, "group_by") for c in payload.get("group_by", [])]
    date_bucket = payload.get("date_bucket")
    if date_bucket is not None:
        date_cols = [c for c, t in schema.column_types.items() if t == "date"]
        if not date_cols:
            raise PlanValidationError("date_bucket requested but dataset has no date column")

    aggregations: list[Aggregation] = []
    for a in payload.get("aggregations", []):
        if not isinstance(a, dict) or set(a) - {"op", "column", "alias"}:
            raise PlanValidationError(f"bad aggregation: {a!r}")
        if a.get("op") not in AGG_OPS:
            raise PlanValidationError(f"bad aggregation op: {a.get('op')!r}")
        if a["op"] == "count" and not a.get("column"):
            col = None
        else:
            col = _col(a.get("column"), "aggregation")
            if a["op"] not in {"count", "count_distinct"} and schema.column_types.get(col) not in {
                "number", "currency"
            }:
                raise PlanValidationError(f"numeric aggregation on non-numeric column {col}")
        aggregations.append(Aggregation(op=a["op"], column=col, alias=a.get("alias")))
    if group_by and not aggregations:
        raise PlanValidationError("group_by without aggregation")

    derived = list(payload.get("derived", []))
    sort_by = payload.get("sort_by")
    if sort_by is not None:
        valid_sort = set(schema.columns) | {
            (a.alias or f"{a.op}_{a.column}") for a in aggregations
        }
        if sort_by not in valid_sort:
            raise PlanValidationError(f"sort_by not selectable: {sort_by!r}")

    plan = QueryPlan(
        dataset=schema,
        filters=filters,
        group_by=group_by,
        date_bucket=date_bucket,
        aggregations=aggregations,
        derived=derived,
        sort_by=sort_by,
        sort_dir=payload.get("sort_dir", "desc"),
        limit=payload.get("limit"),
    )
    plan.validate()
    return plan
