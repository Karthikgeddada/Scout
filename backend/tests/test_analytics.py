"""Tests for restricted query plans and the Decimal executor."""

from __future__ import annotations

from decimal import Decimal

from app.analytics.executor import execute_plan
from app.analytics.plan import DatasetSchema, PlanValidationError, plan_from_json


def _schema() -> DatasetSchema:
    return DatasetSchema(
        document_id="d1",
        sheet="Sales",
        table_id="tab1",
        columns=["year", "region", "product", "revenue", "units"],
        column_types={
            "year": "date",
            "region": "text",
            "product": "text",
            "revenue": "currency",
            "units": "number",
        },
        provenance={"row_offset": 1},
    )


RECORDS = [
    {"year": "2022", "region": "EU", "product": "Core", "revenue": Decimal("1000.00"), "units": 10},
    {"year": "2022", "region": "US", "product": "Core", "revenue": Decimal("2000.00"), "units": 20},
    {"year": "2023", "region": "EU", "product": "Core", "revenue": Decimal("1500.50"), "units": 12},
    {"year": "2023", "region": "US", "product": "Pro", "revenue": Decimal("3000.25"), "units": 25},
]


def test_plan_rejects_unknown_columns_and_ops():
    schema = _schema()
    for bad in [
        {"aggregations": [{"op": "sum", "column": "secret_column"}]},
        {"aggregations": [{"op": "drop_table", "column": "revenue"}]},
        {"filters": [{"op": "like", "column": "region", "value": "EU"}]},
        {"aggregations": [{"op": "sum", "column": "region"}]},  # numeric agg on text
        {"group_by": ["region"]},  # group without aggregation
        {"unknown_key": 1},
        {"date_bucket": "decade"},
    ]:
        try:
            plan_from_json(bad, schema)
            raise AssertionError(f"expected rejection for {bad}")
        except PlanValidationError:
            pass


def test_plan_rejects_sql_shapes():
    # A model proposing SQL text instead of a structured plan is rejected outright.
    try:
        plan_from_json({"query": "SELECT * FROM sales; DROP TABLE x"}, _schema())
        raise AssertionError("expected rejection")
    except PlanValidationError:
        pass


def test_valid_plan_roundtrip():
    plan = plan_from_json(
        {
            "filters": [{"op": "eq", "column": "region", "value": "EU"}],
            "aggregations": [{"op": "sum", "column": "revenue", "alias": "eu_revenue"}],
        },
        _schema(),
    )
    result = execute_plan(plan, RECORDS)
    assert result.rows[0]["eu_revenue"] == Decimal("2500.50")
    assert result.plan_summary["records_matched"] == 2
    assert result.provenance.sheet == "Sales"


def test_group_by_with_aggregation():
    plan = plan_from_json(
        {
            "group_by": ["region"],
            "aggregations": [
                {"op": "sum", "column": "revenue", "alias": "total"},
                {"op": "count", "alias": "n"},
            ],
            "sort_by": "total",
            "sort_dir": "desc",
        },
        _schema(),
    )
    result = execute_plan(plan, RECORDS)
    rows = {r["region"]: r for r in result.rows}
    assert rows["US"]["total"] == Decimal("5000.25")
    assert rows["US"]["n"] == 2
    assert rows["EU"]["total"] == Decimal("2500.50")


def test_date_bucket_yearly_with_pct_change():
    plan = plan_from_json(
        {
            "date_bucket": "year",
            "aggregations": [{"op": "sum", "column": "revenue", "alias": "total"}],
            "derived": ["pct_change", "yoy"],
        },
        _schema(),
    )
    result = execute_plan(plan, RECORDS)
    by_period = {r["period"]: r for r in result.rows}
    assert by_period["2022"]["total"] == Decimal("3000.00")
    assert by_period["2023"]["total"] == Decimal("4500.75")
    expected_pct = (Decimal("4500.75") - Decimal("3000.00")) / Decimal("3000.00") * 100
    assert by_period["2023"]["pct_change_total"] == expected_pct.quantize(Decimal("0.01"))
    assert by_period["2023"]["yoy_delta_total"] == Decimal("1500.75")


def test_filters_on_numbers():
    plan = plan_from_json(
        {
            "filters": [{"op": "ge", "column": "units", "value": 20}],
            "aggregations": [{"op": "count", "alias": "big_orders"}],
        },
        _schema(),
    )
    result = execute_plan(plan, RECORDS)
    assert result.rows[0]["big_orders"] == 2


def test_limit_and_sort():
    plan = plan_from_json(
        {
            "group_by": ["product"],
            "aggregations": [{"op": "sum", "column": "units", "alias": "u"}],
            "sort_by": "u",
            "sort_dir": "desc",
            "limit": 1,
        },
        _schema(),
    )
    result = execute_plan(plan, RECORDS)
    assert len(result.rows) == 1
    assert result.rows[0]["product"] == "Core"
