"""Analytics service: dataset construction from canonical tables, LLM plan
proposal with strict validation, deterministic execution, natural explanation.

The model may only PROPOSE a JSON plan; the server validates it against the
actual dataset schema (plan_from_json) and executes it in Decimal-safe Python.
There is no path from user or model input to arbitrary SQL."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gemini import gemini
from app.analytics.executor import execute_plan
from app.analytics.plan import DatasetSchema, PlanValidationError, plan_from_json
from app.core.errors import NotFoundError, UpstreamError
from app.core.observability import get_logger
from app.db.models_documents import DocumentVersion

log = get_logger("analytics")

PLAN_SYSTEM = """You convert analytics questions about a tabular dataset into a
JSON query plan. Respond with ONLY the JSON object, no prose. Allowed keys:
filters (list of {op, column, value, value2}; ops: eq,ne,gt,lt,ge,le,between,in,contains),
group_by (list of columns), date_bucket (year|quarter|month|week|day),
aggregations (list of {op, column, alias}; ops: sum,avg,min,max,count,count_distinct),
derived (list from: pct_change, yoy), sort_by, sort_dir (asc|desc), limit (1-1000).
Use ONLY the columns provided in the schema. Do not invent columns."""

EXPLAIN_SYSTEM = """You explain deterministic computation results over a document's
numeric tables. State the exact figures from the RESULT ROWS, mention the sheet
and source rows given in provenance, and never recompute or invent numbers."""


class AnalyticsService:
    def dataset_schema(self, session: Session, document_id) -> DatasetSchema:
        version = session.scalar(
            select(DocumentVersion).where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version.desc()).limit(1)
        )
        if version is None:
            raise NotFoundError("document has no processed tables yet")
        canonical = version.canonical
        tables = canonical.get("tables", [])
        if not tables:
            raise NotFoundError("document has no tables to analyze")
        table = max(tables, key=lambda t: (t.get("n_rows", 0) * t.get("n_cols", 0)))
        columns = [str(h) for h in table.get("headers", [])]
        types = {c: "text" for c in columns}
        for col, t in (table.get("column_types") or {}).items():
            if col in types and t in {"number", "currency", "date", "text"}:
                types[col] = t
        return DatasetSchema(
            document_id=str(document_id), sheet=table.get("sheet"), table_id=table.get("table_id"),
            columns=columns, column_types=types,
            provenance={"row_offset": table.get("row_offset", 0)},
        )

    def records(self, session: Session, document_id) -> list[dict]:
        schema = self.dataset_schema(session, document_id)
        version = session.scalar(
            select(DocumentVersion).where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version.desc()).limit(1)
        )
        canonical = version.canonical
        tables = canonical.get("tables", [])
        table = next(
            (t for t in tables if t.get("table_id") == schema.table_id),
            max(tables, key=lambda t: (t.get("n_rows", 0) * t.get("n_cols", 0))),
        )
        records: list[dict] = []
        for row in table.get("records", []):
            records.append({col: row.get(col) for col in schema.columns if col in row})
        return records

    async def run(self, session: Session, req) -> dict:
        schema = self.dataset_schema(session, req.document_id)
        rows = self.records(session, req.document_id)

        schema_desc = json.dumps({
            "columns": [
                {"name": c, "type": schema.column_types[c]} for c in schema.columns
            ],
            "sheet": schema.sheet,
        })
        user_prompt = f"SCHEMA: {schema_desc}\n\nQUESTION: {req.query}\n\nJSON PLAN:"

        plan = None
        error_feedback = ""
        for attempt in range(3):
            raw = await gemini().generate(PLAN_SYSTEM, user_prompt + error_feedback)
            try:
                payload = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
                plan = plan_from_json(payload, schema)
                break
            except (json.JSONDecodeError, PlanValidationError) as exc:
                error_feedback = f"\n\nYour previous plan was rejected: {exc}. Fix it."
                log.warning("plan_rejected", attempt=attempt + 1, error=str(exc))
        if plan is None:
            raise UpstreamError("model could not produce a valid plan")

        result = execute_plan(plan, rows)
        serialized = [
            {k: (str(v) if hasattr(v, "quantize") else v) for k, v in row.items()}
            for row in result.rows
        ]

        explanation = await gemini().generate(
            EXPLAIN_SYSTEM,
            f"QUESTION: {req.query}\n\nRESULT ROWS (exact):\n{json.dumps(serialized, default=str)}\n\n"
            f"PROVENANCE: sheet={result.provenance.sheet}, source_rows={result.provenance.rows[:50]}",
        )
        return {
            "document_id": str(req.document_id),
            "sheet": schema.sheet,
            "rows": serialized,
            "provenance": {
                "sheet": result.provenance.sheet,
                "source_rows": result.provenance.rows[:200],
            },
            "plan_summary": result.plan_summary,
            "explanation": explanation,
        }
