"""Difficult-table subsystem.

Handles financial/numerical table semantics: raw-value preservation, Decimal
parsing with currency/units/negative forms, merged-header normalization and
structural + numerical validation with explicit flags. Never mutates raw values.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ingestion.canonical import DocumentTable, TableCell, TableRow

_CURRENCY_SYMBOLS = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP", "\u00a5": "JPY", "\u20b9": "INR"}
_CURRENCY_CODES = {"USD", "EUR", "GBP", "JPY", "INR", "CHF", "CAD", "AUD", "CNY"}
_THRESHOLDS = {"K": Decimal("1e3"), "M": Decimal("1e6"), "B": Decimal("1e9"), "T": Decimal("1e12")}


def parse_numeric(raw: str) -> dict[str, Any] | None:
    """Parse a raw cell string into numeric semantics; returns None for non-numeric.

    Understands: 1,234.56 / (1,234.56) / $1.2M / 12.3% / USD 500 / -42 / 1 234,56 (eu).
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.lower() in {"n/a", "na", "-", "--", "null", "none"}:
        return None

    result: dict[str, Any] = {"data_type": "number", "currency": None, "unit": None, "is_negative": False}

    m = re.match(r"^\((.*)\)$", text)
    if m:  # parentheses = negative in financial tables
        result["is_negative"] = True
        text = m.group(1).strip()

    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in text:
            result["currency"] = code
            text = text.replace(sym, "").strip()
            result["data_type"] = "currency"
            break

    m = re.match(r"^(USD|EUR|GBP|JPY|INR|CHF|CAD|AUD|CNY)\s*(.*)$", text)
    if m:
        result["currency"] = m.group(1)
        result["data_type"] = "currency"
        text = m.group(2).strip()

    percent = False
    if text.endswith("%"):
        percent = True
        text = text[:-1].strip()

    threshold = None
    m = re.search(r"([KMBT])$", text)
    if m and re.match(r"^[\d.,\s\-]+$", text[:-1].strip()):
        threshold = _THRESHOLDS[m.group(1)]
        text = text[:-1].strip()

    # European format: 1.234,56 -> 1234.56
    if re.match(r"^\d{1,3}(\.\d{3})+,\d+$", text):
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    text = text.replace(" ", "").replace("\u00a0", "")

    if text.startswith("-"):
        result["is_negative"] = not result["is_negative"]
        text = text[1:]

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if threshold is not None:
        value = value * threshold
    if result["is_negative"]:
        value = -value

    if percent:
        result["data_type"] = "percent"
        result["normalized"] = f"{value}%"
    else:
        result["normalized"] = str(value.normalize()) if value == value.to_integral_value() else str(value)
    result["decimal_value"] = str(value)
    return result


def normalize_headers(raw_headers: list[str]) -> list[str]:
    """Flatten merged/multi-line headers into stable, unique column names."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for h in raw_headers:
        name = re.sub(r"\s+", " ", (h or "").strip())
        if not name:
            name = "column"
        base = name
        count = seen.get(base, 0)
        seen[base] = count + 1
        if count:
            name = f"{base} ({count + 1})"
        out.append(name)
    return out


def validate_table(table: DocumentTable) -> list[str]:
    """Structural + numerical validation; returns flags (never throws, never fixes)."""
    flags: list[str] = []
    body_rows = [r for r in table.rows if not r.is_header]
    if not body_rows:
        flags.append("empty_table")
        return flags

    widths = {len(r.cells) for r in table.rows}
    if len(widths) > 1:
        flags.append(f"ragged_rows: column counts {sorted(widths)}")

    # Detect header/body column mismatch
    header_rows = [r for r in table.rows if r.is_header]
    if header_rows and len(header_rows[0].cells) not in widths:
        flags.append("header_body_width_mismatch")

    # Numeric consistency: does any numeric column mix parsed/unparsed values?
    ncols = table.n_cols
    for col in range(ncols):
        values = [r.cells[col] for r in body_rows if col < len(r.cells)]
        numeric = [c for c in values if c.decimal_value is not None]
        if numeric and len(numeric) < len(values) * 0.5:
            flags.append(f"mixed_numeric_column:{col}")
        currencies = {c.currency for c in numeric if c.currency}
        if len(currencies) > 1:
            flags.append(f"mixed_currencies:{col}:{sorted(currencies)}")

    # Totals-row sanity: a row labelled total whose value disagrees with column sum.
    for idx, row in enumerate(body_rows):
        first = (row.cells[0].raw_value or "").lower() if row.cells else ""
        if re.match(r"^(total|grand total|net total)\b", first):
            flags.append(f"totals_row:{idx}")
            for col in range(1, ncols):
                cells = [r.cells[col] for r in body_rows if col < len(r.cells)]
                total_cell = row.cells[col] if col < len(row.cells) else None
                parts = [c for c in cells if c.decimal_value is not None and c is not total_cell]
                if total_cell and total_cell.decimal_value is not None and parts:
                    try:
                        expected = sum((Decimal(c.decimal_value) for c in parts), Decimal(0))
                        actual = Decimal(total_cell.decimal_value)
                        if abs(expected - actual) > Decimal("0.01"):
                            flags.append(f"totals_mismatch:{idx},{col}:expected={expected},actual={actual}")
                    except InvalidOperation:
                        flags.append(f"totals_check_invalid:{idx},{col}")
    return flags


def build_cell(raw: str, row_index: int, col_index: int, provenance=None) -> TableCell:
    cell = TableCell(raw_value=str(raw), row_index=row_index, col_index=col_index, provenance=provenance)
    parsed = parse_numeric(str(raw))
    if parsed is not None:
        cell.normalized_value = parsed["normalized"]
        cell.data_type = parsed["data_type"]
        cell.currency = parsed["currency"]
        cell.is_negative = parsed["is_negative"]
        cell.decimal_value = parsed["decimal_value"]
    elif not str(raw).strip():
        cell.data_type = "empty"
    return cell


def table_to_markdown(table: DocumentTable, max_rows: int | None = None) -> str:
    """Serialize a table for RAG context with header + provenance annotation."""
    lines: list[str] = []
    if table.title:
        lines.append(f"Table: {table.title}")
    src = table.sheet or (f"page {table.pages[0]}" if table.pages else "")
    if src:
        lines.append(f"Source: {src}")
    if table.headers:
        lines.append("| " + " | ".join(table.headers) + " |")
        lines.append("|" + "---|" * len(table.headers))
    body = [r for r in table.rows if not r.is_header]
    for row in (body[:max_rows] if max_rows else body):
        lines.append("| " + " | ".join(c.raw_value for c in row.cells) + " |")
    if max_rows and len(body) > max_rows:
        lines.append(f"... ({len(body) - max_rows} more rows)")
    for note in table.footnotes:
        lines.append(f"Footnote: {note}")
    return "\n".join(lines)
