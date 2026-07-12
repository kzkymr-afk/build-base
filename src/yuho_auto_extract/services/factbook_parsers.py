from __future__ import annotations

import csv
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..ai_runner import AiCallResult, AiRunner
from . import factbook_ai


def parse_document(
    path: Path,
    source: Dict[str, Any],
    cfg: Dict[str, Any],
    fetched_at: str,
    *,
    ai_runner: Optional[AiRunner] = None,
    ai_config: Optional[Dict[str, Any]] = None,
    ai_call_results: Optional[List[AiCallResult]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        if _is_kajima_factbook(source, path):
            return _parse_kajima_factbook_pdf(path, source, fetched_at)
        if _is_kajima_q_order(source):
            rows, warnings = _parse_kajima_q_order_pdf(path, source, fetched_at)
            return rows, warnings
        if _is_obayashi_results_reference(source):
            rows, warnings = _parse_obayashi_results_reference_pdf(path, source, fetched_at)
            return rows, warnings
        if _is_taisei_databook(source):
            return [], [f"skip Taisei databook PDF; Excel zip is the structured source {path.name}"]
        _suppress_pdfminer_warnings()
        tables, warnings = _extract_pdf_tables(path)
    elif ext in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        tables, warnings = _extract_xlsx_tables(path)
    elif ext == ".xls":
        tables, warnings = _extract_xls_tables(path)
    elif ext == ".csv":
        tables, warnings = _extract_csv_tables(path)
    elif ext == ".zip":
        if _is_taisei_databook(source):
            rows, warnings = _parse_taisei_databook_zip(path, source, fetched_at)
            return rows, warnings
        tables, warnings = _extract_zip_tables(path)
    else:
        return [], [f"unsupported factbook document ext={ext or '(none)'} path={path}"]

    rows: List[Dict[str, Any]] = []
    ai_cfg = ai_config or {}
    tier_cfg = ai_cfg.get("tier_config") or {}
    for table_index, table in enumerate(tables):
        text = _table_text(table)
        decision = factbook_ai.classify_table(
            text,
            source,
            runner=ai_runner,
            model=str(tier_cfg.get("model") or ""),
            tier=str(ai_cfg.get("tier") or "bulk"),
            timeout_seconds=ai_cfg.get("timeout_seconds"),
            input_ref=f"{source.get('source_dataset_id') or source.get('id') or 'factbook'}:{path.name}:table_{table_index + 1}",
            call_results=ai_call_results,
        )
        if decision.get("accept") != "true":
            continue
        rows.extend(_parse_table(table, source, decision, path, table_index, fetched_at))
    if not rows and tables:
        warnings.append(f"no factbook values parsed from {path.name}")
    return rows, warnings


def _is_kajima_q_order(source: Dict[str, Any]) -> bool:
    return str(source.get("company_id") or "") == "KAJIMA" and str(source.get("source_doc_type") or "") == "q_order"


def _is_kajima_factbook(source: Dict[str, Any], path: Path) -> bool:
    return (
        str(source.get("company_id") or "") == "KAJIMA"
        and str(source.get("source_doc_type") or "") == "factbook"
        and path.name.lower().startswith("factbook")
    )


def _is_obayashi_results_reference(source: Dict[str, Any]) -> bool:
    return (
        str(source.get("company_id") or "") == "OBAYASHI"
        and str(source.get("source_dataset_id") or "") == "obayashi_results_reference"
    )


def _is_taisei_databook(source: Dict[str, Any]) -> bool:
    return str(source.get("company_id") or "") == "TAISEI" and str(source.get("source_dataset_id") or "") == "taisei_databook"


def _suppress_pdfminer_warnings() -> None:
    for name in ("pdfminer", "pdfminer.pdfinterp"):
        logging.getLogger(name).setLevel(logging.ERROR)


def _parse_kajima_q_order_pdf(path: Path, source: Dict[str, Any], fetched_at: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return [], ["pdfplumber is required to parse Kajima q_order PDFs"]
    warnings: List[str] = []
    texts: List[str] = []
    _suppress_pdfminer_warnings()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    rows = _parse_kajima_q_order_texts(texts, source, path, fetched_at)
    if not rows:
        warnings.append(f"no Kajima q_order annual cumulative rows parsed from {path.name}")
    return rows, warnings


def _parse_kajima_factbook_pdf(path: Path, source: Dict[str, Any], fetched_at: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return [], ["pdfplumber is required to parse Kajima factbook PDFs"]
    _suppress_pdfminer_warnings()
    with pdfplumber.open(path) as pdf:
        texts = [page.extract_text(x_tolerance=2, y_tolerance=2) or "" for page in pdf.pages]
    rows = _parse_kajima_factbook_texts(texts, source, path, fetched_at)
    warnings: List[str] = []
    if not rows:
        warnings.append(f"no Kajima building order use rows parsed from {path.name}")
    return rows, warnings


def _parse_kajima_factbook_texts(
    texts: Sequence[str],
    source: Dict[str, Any],
    path: Path,
    fetched_at: str,
) -> List[Dict[str, Any]]:
    categories = [
        ("事務所・庁舎", "office"),
        ("宿泊施設", "lodging"),
        ("店舗", "commercial"),
        ("工場・発電所", "factory"),
        ("倉庫・流通施設", "logistics"),
        ("住宅", "housing"),
        ("教育・研究・文化施設", "education_research"),
        ("医療・福祉施設", "medical_welfare"),
        ("その他", "other_use"),
    ]
    rows: List[Dict[str, Any]] = []
    for text in texts:
        if "【工種別受注高（建設事業）】" not in text:
            continue
        years = _kajima_factbook_years(text)
        if not years:
            continue
        in_order_section = False
        in_building_section = False
        for line in text.splitlines():
            compact = re.sub(r"\s+", "", line)
            if "【工種別受注高（建設事業）】" in compact:
                in_order_section = True
                continue
            if in_order_section and compact.startswith("【"):
                break
            if not in_order_section:
                continue
            if compact.startswith("建築工事"):
                in_building_section = True
                continue
            if not in_building_section:
                continue
            category = next(((raw, normalized) for raw, normalized in categories if compact.startswith(raw)), None)
            if category is None:
                continue
            raw_category, normalized_category = category
            amounts = [_number(token) for token in re.findall(r"-?\d[\d,]*(?:\.\d+)?|[-－]", line)]
            numeric_amounts = [amount for amount in amounts if amount is not None]
            if len(numeric_amounts) != len(years):
                continue
            for fiscal_year, amount in zip(years, numeric_amounts):
                rows.append(
                    {
                        "company_id": source.get("company_id", ""),
                        "company_name": source.get("company_name", ""),
                        "fiscal_year": str(fiscal_year),
                        "fiscal_year_end": f"{fiscal_year + 1}-03-31",
                        "period_type": "annual",
                        "period_label": f"{fiscal_year + 1}年3月期",
                        "source_company_id": source.get("company_id", ""),
                        "source_doc_type": source.get("source_doc_type", ""),
                        "source_dataset_id": source.get("source_dataset_id", source.get("id", "")),
                        "source_metric_id": "building_orders_by_use",
                        "category_type": "use",
                        "scope": source.get("scope") or "standalone",
                        "business_scope": source.get("business_scope") or "building_orders",
                        "use_category_raw": raw_category,
                        "use_category_normalized": normalized_category,
                        "order_amount": amount,
                        "unit": "百万円",
                        "amount_million_yen": amount,
                        "source_url": source.get("url") or source.get("source_url") or source.get("source_page_url", ""),
                        "source_page": source.get("source_page_url", ""),
                        "source_table_title": "受注高（単体） 工種別受注高（建設事業） 建築工事",
                        "source_quote": f"{raw_category} {fiscal_year + 1}年3月期 {amount:g}百万円",
                        "source_file": str(path),
                        "extraction_status": "parsed",
                        "fetched_at_utc": fetched_at,
                    }
                )
        if rows:
            break
    return rows


def _kajima_factbook_years(text: str) -> List[int]:
    for line in text.splitlines():
        if "会計年度" not in line:
            continue
        years = [int(year) - 1 for year in re.findall(r"(20\d{2})\.3", line)]
        if years:
            return years
    return []


def _parse_taisei_databook_zip(path: Path, source: Dict[str, Any], fetched_at: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    try:
        import xlrd  # type: ignore
    except ImportError:
        return [], ["xlrd is required to parse Taisei databook XLS"]
    warnings: List[str] = []
    rows: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(path) as zf:
            xls_members = [info for info in zf.infolist() if Path(info.filename).suffix.lower() == ".xls"]
            if not xls_members:
                return [], [f"no XLS file found in {path.name}"]
            xls_path = tmp_path / "taisei_databook.xls"
            xls_path.write_bytes(zf.read(xls_members[0]))
        wb = xlrd.open_workbook(str(xls_path))
        if "9-3.個別受注高_用途別" not in wb.sheet_names():
            return [], ["Taisei databook sheet 9-3.個別受注高_用途別 not found"]
        sheet = wb.sheet_by_name("9-3.個別受注高_用途別")
        rows = _parse_taisei_use_order_sheet(sheet, source, path, fetched_at)
    if not rows:
        warnings.append(f"no Taisei building order use rows parsed from {path.name}")
    return rows, warnings


def _parse_taisei_use_order_sheet(sheet: Any, source: Dict[str, Any], path: Path, fetched_at: str) -> List[Dict[str, Any]]:
    header_row = 4
    category_rows = {
        15: ("事務所・庁舎", "office"),
        16: ("宿泊施設", "lodging"),
        17: ("店舗", "commercial"),
        18: ("工場・発電所", "factory"),
        19: ("倉庫・流通施設", "logistics"),
        20: ("住宅（分譲マンション等）", "housing"),
        21: ("教育研究文化施設", "education_research"),
        22: ("医療・福祉施設", "medical_welfare"),
        23: ("娯楽施設", "other_use"),
        24: ("その他", "other_use"),
    }
    rows: List[Dict[str, Any]] = []
    for col_index in range(2, min(sheet.ncols, 12)):
        fiscal_year = _taisei_fiscal_year_from_header(sheet.cell_value(header_row, col_index))
        if fiscal_year is None:
            continue
        for row_index, (raw_category, normalized_category) in category_rows.items():
            if row_index >= sheet.nrows:
                continue
            amount = _number(sheet.cell_value(row_index, col_index))
            if amount is None:
                continue
            rows.append(
                {
                    "company_id": source.get("company_id", ""),
                    "company_name": source.get("company_name", ""),
                    "fiscal_year": str(fiscal_year),
                    "fiscal_year_end": f"{fiscal_year + 1}-03-31",
                    "period_type": "annual",
                    "period_label": f"{fiscal_year + 1}年3月期",
                    "source_company_id": source.get("company_id", ""),
                    "source_doc_type": source.get("source_doc_type", ""),
                    "source_dataset_id": source.get("source_dataset_id", source.get("id", "")),
                    "source_metric_id": "building_orders_by_use",
                    "category_type": "use",
                    "scope": source.get("scope") or "standalone",
                    "business_scope": source.get("business_scope") or "building_orders",
                    "use_category_raw": raw_category,
                    "use_category_normalized": normalized_category,
                    "order_amount": amount,
                    "unit": "百万円",
                    "amount_million_yen": _amount_million_yen(amount, "百万円"),
                    "source_url": source.get("url") or source.get("source_url") or source.get("source_page_url", ""),
                    "source_page": source.get("source_page_url", ""),
                    "source_table_title": "受注高・用途別内訳（国内建設事業）",
                    "source_quote": f"{raw_category} {fiscal_year + 1}年3月期 {amount:g}百万円",
                    "source_file": str(path),
                    "extraction_status": "parsed",
                    "fetched_at_utc": fetched_at,
                }
            )
    return rows


def _taisei_fiscal_year_from_header(value: Any) -> Optional[int]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    year = int(number)
    month = int(round((number - year) * 10))
    return year - 1 if month <= 3 else year


def _parse_obayashi_results_reference_pdf(path: Path, source: Dict[str, Any], fetched_at: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not _is_obayashi_full_year_results(path, source):
        return [], [f"skip non-full-year Obayashi results reference {path.name}"]
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return [], ["pdfplumber is required to parse Obayashi results reference PDFs"]
    texts: List[str] = []
    _suppress_pdfminer_warnings()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    rows = _parse_obayashi_results_reference_texts(texts, source, path, fetched_at)
    warnings: List[str] = []
    if not rows:
        warnings.append(f"no Obayashi building order use rows parsed from {path.name}")
    return rows, warnings


def _is_obayashi_full_year_results(path: Path, source: Dict[str, Any]) -> bool:
    fiscal_year = _source_fiscal_year(source)
    if fiscal_year is None:
        return False
    return path.name.startswith(f"{fiscal_year + 1}05")


def _parse_obayashi_results_reference_texts(texts: Sequence[str], source: Dict[str, Any], path: Path, fetched_at: str) -> List[Dict[str, Any]]:
    fiscal_year = _source_fiscal_year(source)
    if fiscal_year is None:
        return []
    period_index = fiscal_year - 2020
    if period_index < 0:
        return []
    amount_token_index = period_index * 2
    rows: List[Dict[str, Any]] = []
    in_building_order_section = False
    for text in texts:
        for line in text.splitlines():
            normalized = " ".join(line.split())
            if "３ 建設事業の工事種類別の内訳" in normalized:
                in_building_order_section = True
                continue
            if in_building_order_section and normalized.startswith("土 木"):
                in_building_order_section = False
            if not in_building_order_section:
                continue
            category = _obayashi_building_use_category(normalized)
            if not category:
                continue
            tokens = re.findall(r"△\s*\d[\d,]*|\d[\d,]*(?:\.\d+)?%?|－", normalized)
            if amount_token_index >= len(tokens):
                continue
            amount = _number(tokens[amount_token_index])
            if amount is None:
                continue
            raw_category, normalized_category = category
            rows.append(
                {
                    "company_id": source.get("company_id", ""),
                    "company_name": source.get("company_name", ""),
                    "fiscal_year": str(fiscal_year),
                    "fiscal_year_end": f"{fiscal_year + 1}-03-31",
                    "period_type": "annual",
                    "period_label": str(source.get("period_label") or f"{fiscal_year + 1}年3月期"),
                    "source_company_id": source.get("company_id", ""),
                    "source_doc_type": source.get("source_doc_type", ""),
                    "source_dataset_id": source.get("source_dataset_id", source.get("id", "")),
                    "source_metric_id": "building_orders_by_use",
                    "category_type": "use",
                    "scope": source.get("scope") or "standalone",
                    "business_scope": source.get("business_scope") or "building_orders",
                    "use_category_raw": raw_category,
                    "use_category_normalized": normalized_category,
                    "order_amount": amount,
                    "unit": "百万円",
                    "amount_million_yen": _amount_million_yen(amount, "百万円"),
                    "source_url": source.get("url") or source.get("source_url") or source.get("source_page_url", ""),
                    "source_page": source.get("source_page_url", ""),
                    "source_table_title": "建設事業の工事種類別の内訳（個別） 受注高 建築",
                    "source_quote": f"{raw_category} {fiscal_year + 1}年3月期 {amount:g}百万円",
                    "source_file": str(path),
                    "extraction_status": "parsed",
                    "fetched_at_utc": fetched_at,
                }
            )
    return rows


def _obayashi_building_use_category(line: str) -> Optional[Tuple[str, str]]:
    compact = re.sub(r"\s+", "", line)
    categories = [
        ("事務所・庁舎", "office"),
        ("宿泊施設", "lodging"),
        ("店舗", "commercial"),
        ("工場・発電所", "factory"),
        ("倉庫・流通施設", "logistics"),
        ("住宅", "housing"),
        ("教育研究文化施設", "education_research"),
        ("医療・福祉施設", "medical_welfare"),
        ("娯楽施設", "other_use"),
        ("その他", "other_use"),
    ]
    for raw, normalized in categories:
        if compact.startswith(re.sub(r"\s+", "", raw)):
            return raw, normalized
    return None


def _parse_kajima_q_order_texts(texts: Sequence[str], source: Dict[str, Any], path: Path, fetched_at: str) -> List[Dict[str, Any]]:
    fiscal_year = _source_fiscal_year(source)
    if fiscal_year is None:
        return []
    year_token = f"{fiscal_year % 100:02d}年度"
    rows: List[Dict[str, Any]] = []
    for text in texts:
        page_kind = _kajima_page_kind(text)
        if page_kind not in {"building", "civil"}:
            continue
        for line in text.splitlines():
            normalized = " ".join(line.split())
            if not normalized.startswith(f"{year_token} 累計"):
                continue
            amounts = _kajima_amount_pairs(normalized.removeprefix(f"{year_token} 累計").strip())
            if page_kind == "building" and len(amounts) >= 8:
                rows.extend(
                    [
                        _kajima_order_row(source, fiscal_year, "建築", "building", amounts[0], path, fetched_at),
                        _kajima_order_row(source, fiscal_year, "国内建築", "domestic_building", amounts[1] + amounts[4], path, fetched_at),
                        _kajima_order_row(source, fiscal_year, "海外建築", "overseas_building", amounts[7], path, fetched_at),
                    ]
                )
            elif page_kind == "civil" and len(amounts) >= 8:
                rows.extend(
                    [
                        _kajima_order_row(source, fiscal_year, "土木", "civil", amounts[0], path, fetched_at),
                        _kajima_order_row(source, fiscal_year, "国内土木", "domestic_civil", amounts[1] + amounts[4], path, fetched_at),
                        _kajima_order_row(source, fiscal_year, "海外土木", "overseas_civil", amounts[7], path, fetched_at),
                    ]
                )
    return rows


def _source_fiscal_year(source: Dict[str, Any]) -> Optional[int]:
    value = source.get("fiscal_year")
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def _kajima_page_kind(text: str) -> str:
    if "四半期別受注額の推移（建築）" in text:
        return "building"
    if "四半期別受注額の推移（土木）" in text:
        return "civil"
    return ""


def _kajima_amount_pairs(text: str) -> List[float]:
    tokens = re.findall(r"-?\d[\d,]*(?:\.\d+)?|-", text)
    amounts: List[float] = []
    for index in range(0, len(tokens), 2):
        value = _number(tokens[index])
        if value is not None:
            amounts.append(value)
    return amounts


def _kajima_order_row(
    source: Dict[str, Any],
    fiscal_year: int,
    raw_category: str,
    normalized_category: str,
    amount: float,
    path: Path,
    fetched_at: str,
) -> Dict[str, Any]:
    period_label = str(source.get("period_label") or f"{fiscal_year + 1}年3月期")
    return {
        "company_id": source.get("company_id", ""),
        "company_name": source.get("company_name", ""),
        "fiscal_year": str(fiscal_year),
        "fiscal_year_end": f"{fiscal_year + 1}-03-31",
        "period_type": "annual",
        "period_label": period_label,
        "source_company_id": source.get("company_id", ""),
        "source_doc_type": source.get("source_doc_type", ""),
        "source_dataset_id": source.get("source_dataset_id", source.get("id", "")),
        "source_metric_id": "building_orders_by_business_scope",
        "category_type": "business_scope",
        "scope": source.get("scope") or "standalone",
        "business_scope": normalized_category,
        "use_category_raw": raw_category,
        "use_category_normalized": normalized_category,
        "order_amount": amount,
        "unit": "億円",
        "amount_million_yen": _amount_million_yen(amount, "億円"),
        "source_url": source.get("url") or source.get("source_url") or source.get("source_page_url", ""),
        "source_page": source.get("source_page_url", ""),
        "source_table_title": "単体 四半期別受注額の推移",
        "source_quote": f"{raw_category} {period_label} {amount:g}億円",
        "source_file": str(path),
        "extraction_status": "parsed",
        "fetched_at_utc": fetched_at,
    }


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


def _extract_xls_tables(path: Path) -> Tuple[List[List[List[str]]], List[str]]:
    try:
        import xlrd  # type: ignore
    except ImportError:
        return [], ["xlrd is required to parse legacy Excel factbooks"]
    wb = xlrd.open_workbook(str(path))
    tables: List[List[List[str]]] = []
    for sheet in wb.sheets():
        rows = [[_cell_text(sheet.cell_value(row_index, col_index)) for col_index in range(sheet.ncols)] for row_index in range(sheet.nrows)]
        for block in _split_non_empty_blocks(rows):
            if len(block) >= 2:
                tables.append(block)
    return tables, []


def _extract_csv_tables(path: Path) -> Tuple[List[List[List[str]]], List[str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [[_cell_text(cell) for cell in row] for row in csv.reader(f)]
    return [block for block in _split_non_empty_blocks(rows) if len(block) >= 2], []


def _extract_zip_tables(path: Path) -> Tuple[List[List[List[str]]], List[str]]:
    tables: List[List[List[str]]] = []
    warnings: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(path) as zf:
            for index, info in enumerate(zf.infolist(), start=1):
                suffix = Path(info.filename).suffix.lower()
                if suffix not in {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".csv"}:
                    continue
                extracted = tmp_path / f"member_{index}{suffix}"
                extracted.write_bytes(zf.read(info))
                if suffix == ".xls":
                    member_tables, member_warnings = _extract_xls_tables(extracted)
                elif suffix == ".csv":
                    member_tables, member_warnings = _extract_csv_tables(extracted)
                else:
                    member_tables, member_warnings = _extract_xlsx_tables(extracted)
                tables.extend(member_tables)
                warnings.extend([f"{info.filename}: {warning}" for warning in member_warnings])
    if not tables:
        warnings.append(f"no supported tabular files found in {path.name}")
    return tables, warnings


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
    text = "" if value is None else str(value).strip()
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
