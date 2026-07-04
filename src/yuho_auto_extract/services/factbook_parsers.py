from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from . import factbook_ai


def parse_document(path: Path, source: Dict[str, Any], cfg: Dict[str, Any], fetched_at: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        tables, warnings = _extract_pdf_tables(path)
    elif ext in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        tables, warnings = _extract_xlsx_tables(path)
    elif ext == ".csv":
        tables, warnings = _extract_csv_tables(path)
    else:
        return [], [f"unsupported factbook document ext={ext or '(none)'} path={path}"]

    rows: List[Dict[str, Any]] = []
    for table_index, table in enumerate(tables):
        text = _table_text(table)
        decision = factbook_ai.classify_table(text, source)
        if decision.get("accept") != "true":
            continue
        rows.extend(_parse_table(table, source, decision, path, table_index, fetched_at))
    if not rows and tables:
        warnings.append(f"no factbook values parsed from {path.name}")
    return rows, warnings


def _extract_pdf_tables(path: Path) -> Tuple[List[List[List[str]]], List[str]]:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return [], ["pdfplumber is required to parse PDF factbooks"]
    tables: List[List[List[str]]] = []
    warnings: List[str] = []
    with pdfplumber.open(path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            try:
                for table in page.extract_tables() or []:
                    cleaned = _clean_table(table)
                    if cleaned:
                        tables.append(cleaned)
            except Exception as exc:
                warnings.append(f"pdf page {page_index + 1} table extraction failed: {exc}")
    return tables, warnings


def _extract_xlsx_tables(path: Path) -> Tuple[List[List[List[str]]], List[str]]:
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return [], ["openpyxl is required to parse Excel factbooks"]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    tables: List[List[List[str]]] = []
    for ws in wb.worksheets:
        rows = [[_cell_text(cell) for cell in row] for row in ws.iter_rows()]
        for block in _split_non_empty_blocks(rows):
            if len(block) >= 2:
                tables.append(block)
    return tables, []


def _extract_csv_tables(path: Path) -> Tuple[List[List[List[str]]], List[str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [[_cell_text(cell) for cell in row] for row in csv.reader(f)]
    return [block for block in _split_non_empty_blocks(rows) if len(block) >= 2], []


def _parse_table(
    table: Sequence[Sequence[str]],
    source: Dict[str, Any],
    decision: Dict[str, str],
    path: Path,
    table_index: int,
    fetched_at: str,
) -> List[Dict[str, Any]]:
    header_index = _find_header_index(table)
    if header_index is None:
        return []
    header = list(table[header_index])
    value_columns = _value_columns(header)
    if not value_columns and len(header) >= 2:
        value_columns = [(len(header) - 1, str(source.get("period_label") or source.get("fiscal_year") or header[-1]))]
    unit = _infer_unit(_table_text(table), str(source.get("unit") or "億円"))
    rows: List[Dict[str, Any]] = []
    for raw_row in table[header_index + 1 :]:
        row = list(raw_row)
        if not row or _is_total_row(row):
            continue
        category = _first_category(row)
        if not category or _looks_like_header_label(category):
            continue
        for column_index, column_label in value_columns:
            value = _number(row[column_index] if column_index < len(row) else "")
            if value is None:
                continue
            fiscal_year, period_label = _infer_period(str(column_label), source)
            metric_id = _metric_id(decision, source)
            rows.append(
                {
                    "company_id": source.get("company_id", ""),
                    "company_name": source.get("company_name", ""),
                    "fiscal_year": fiscal_year,
                    "fiscal_year_end": "",
                    "period_type": source.get("period_type") or "annual",
                    "period_label": period_label,
                    "source_company_id": source.get("company_id", ""),
                    "source_doc_type": source.get("source_doc_type", ""),
                    "source_dataset_id": source.get("source_dataset_id", source.get("id", "")),
                    "source_metric_id": metric_id,
                    "category_type": decision.get("category_type") or source.get("category_type", "use"),
                    "scope": source.get("scope", ""),
                    "business_scope": source.get("business_scope", ""),
                    "use_category_raw": category,
                    "order_amount": value,
                    "unit": unit,
                    "amount_million_yen": _amount_million_yen(value, unit),
                    "source_url": source.get("url") or source.get("source_url") or source.get("source_page_url", ""),
                    "source_page": source.get("source_page_url", ""),
                    "source_table_title": source.get("source_table_title") or f"table_{table_index + 1}",
                    "source_quote": f"{category} {period_label} {value}{unit}",
                    "source_file": str(path),
                    "extraction_status": "parsed",
                    "fetched_at_utc": fetched_at,
                }
            )
    return rows


def _find_header_index(table: Sequence[Sequence[str]]) -> int | None:
    for index, row in enumerate(table[:12]):
        joined = " ".join(row)
        if any(token in joined for token in ("用途", "区分", "種別", "項目", "年度", "年", "月期")):
            return index
    return 0 if table else None


def _value_columns(header: Sequence[str]) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    for index, label in enumerate(header[1:], start=1):
        text = str(label or "")
        if re.search(r"20\d{2}|令和\d+|当期|前期|金額|受注|完工|完成", text):
            out.append((index, text or f"column_{index + 1}"))
    return out


def _infer_period(label: str, source: Dict[str, Any]) -> Tuple[str, str]:
    text = f"{label} {source.get('period_label', '')} {source.get('title', '')}"
    match = re.search(r"(20\d{2})年\s*(\d{1,2})月期", text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        fiscal_year = year - 1 if month <= 3 else year
        return str(fiscal_year), f"{year}年{month}月期"
    match = re.search(r"(20\d{2})年度", text)
    if match:
        return match.group(1), f"{match.group(1)}年度"
    match = re.search(r"(20\d{2})", text)
    if match:
        return match.group(1), match.group(1)
    fiscal_year = str(source.get("fiscal_year") or "")
    return fiscal_year, str(source.get("period_label") or fiscal_year or label)


def _metric_id(decision: Dict[str, str], source: Dict[str, Any]) -> str:
    metric_id = str(decision.get("metric_id") or source.get("source_metric_id") or "")
    if metric_id == "building_use_metrics":
        return "building_orders_by_use"
    return metric_id


def _infer_unit(text: str, default: str) -> str:
    compact = text.replace(" ", "")
    for unit in ("百万円", "億円", "千円", "万円", "円"):
        if unit in compact:
            return unit
    return default


def _amount_million_yen(value: Any, unit: str) -> Any:
    number = _number(value)
    if number is None:
        return ""
    if "億" in unit:
        return round(number * 100, 6)
    if "百万円" in unit:
        return number
    if "千円" in unit:
        return round(number / 1000, 6)
    if "万円" in unit:
        return round(number / 100, 6)
    if "円" in unit:
        return round(number / 1_000_000, 6)
    return ""


def _number(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"-", "－", "ー"}:
        return None
    negative = text.startswith("△") or (text.startswith("(") and text.endswith(")"))
    cleaned = re.sub(r"[^0-9.\\-]", "", text)
    if cleaned in {"", "-", "."}:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -abs(number) if negative else number


def _clean_table(table: Iterable[Iterable[Any]]) -> List[List[str]]:
    rows = [[_cell_text(cell) for cell in row] for row in table]
    return [row for row in rows if any(cell for cell in row)]


def _split_non_empty_blocks(rows: Sequence[Sequence[str]]) -> List[List[List[str]]]:
    blocks: List[List[List[str]]] = []
    current: List[List[str]] = []
    for raw_row in rows:
        row = list(raw_row)
        if any(cell for cell in row):
            current.append(row)
            continue
        if current:
            blocks.append(_trim_block(current))
            current = []
    if current:
        blocks.append(_trim_block(current))
    return [block for block in blocks if block]


def _trim_block(block: Sequence[Sequence[str]]) -> List[List[str]]:
    max_len = max((len(row) for row in block), default=0)
    padded = [list(row) + [""] * (max_len - len(row)) for row in block]
    non_empty_cols = [index for index in range(max_len) if any(row[index] for row in padded)]
    return [[row[index] for index in non_empty_cols] for row in padded]


def _table_text(table: Sequence[Sequence[str]]) -> str:
    return " ".join(cell for row in table for cell in row if cell)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\n", " ").split())


def _first_category(row: Sequence[str]) -> str:
    for cell in row[:2]:
        text = str(cell or "").strip()
        if text and _number(text) is None:
            return text
    return ""


def _looks_like_header_label(text: str) -> bool:
    return text in {"用途", "区分", "種別", "項目", "合計"}


def _is_total_row(row: Sequence[str]) -> bool:
    first = str(row[0] if row else "")
    return first in {"合計", "計", "総計"}
