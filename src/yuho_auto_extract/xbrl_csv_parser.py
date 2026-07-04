from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List


ENCODINGS = ("utf-8-sig", "utf-16", "cp932")


def extract_xbrl_csv_long(
    csv_zip_path: Path,
    field_definitions: Iterable[Dict[str, Any]],
    target: Dict[str, Any],
    run_id: str,
) -> List[Dict[str, Any]]:
    if not csv_zip_path.exists():
        return []
    rows = _read_all_csv_rows(csv_zip_path)
    outputs: List[Dict[str, Any]] = []
    for field in field_definitions:
        if str(field.get("preferred_method")) != "XBRL_CSV":
            continue
        candidates = _match_field_candidates(rows, field)
        if not candidates:
            outputs.append(_not_found_record(field, target, run_id, csv_zip_path))
            continue
        chosen = candidates[0]
        outputs.append(_record_from_csv_row(chosen, field, target, run_id, csv_zip_path, _effective_candidate_count(candidates)))
    return outputs


def _read_all_csv_rows(csv_zip_path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with zipfile.ZipFile(csv_zip_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            content = zf.read(name)
            text = _decode(content)
            sample = text[:4096]
            delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            for row in reader:
                copied = dict(row)
                copied["_source_csv"] = name
                rows.append(copied)
    return rows


def _decode(content: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _match_field_candidates(rows: List[Dict[str, Any]], field: Dict[str, Any]) -> List[Dict[str, Any]]:
    tag_candidates = [part.strip() for part in str(field.get("xbrl_tag_candidates") or "").split(";") if part.strip()]
    context_filters = [part.strip() for part in str(field.get("context_filters") or "").split(";") if part.strip()]
    matches: List[Dict[str, Any]] = []
    for row in rows:
        element = _first_present(row, ["要素ID", "element_id", "element", "xbrl_element", "タグ", "tag"])
        label = _first_present(row, ["項目名", "item_name", "label", "名称"])
        if tag_candidates and not any(_matches_candidate(element, label, tag) for tag in tag_candidates):
            continue
        if context_filters and not _matches_context_filters(row, context_filters):
            continue
        matches.append(row)
    return _prefer_best_candidate_priority(_prefer_primary_rows(matches), tag_candidates)


def _record_from_csv_row(
    row: Dict[str, Any],
    field: Dict[str, Any],
    target: Dict[str, Any],
    run_id: str,
    source_file: Path,
    candidate_count: int,
) -> Dict[str, Any]:
    value = _first_present(row, ["値", "value", "Value", "金額"])
    unit = _first_present(row, ["単位", "unit", "Unit"])
    unit_id = _first_present(row, ["ユニットID", "unit_id"])
    context = _first_present(row, ["コンテキストID", "contextRef", "context_ref", "コンテキスト"])
    element = _first_present(row, ["要素ID", "element_id", "element", "xbrl_element", "タグ", "tag"])
    label = _first_present(row, ["項目名", "item_name", "label", "名称"])
    review_required = candidate_count > 1
    record = {
        "run_id": run_id,
        "company_year_id": target.get("company_year_id"),
        "operating_company_id": target.get("operating_company_id"),
        "fiscal_year": target.get("fiscal_year"),
        "source_doc_id": target.get("docID"),
        "source_file": str(source_file),
        "source_heading": element,
        "source_quote": f"{label}: {value}" if label and value is not None else str(value) if value is not None else None,
        "field_id": field.get("field_id"),
        "value_raw": value,
        "unit_raw": unit if unit is not None else unit_id,
        "context_ref": context,
        "xbrl_element": element,
        "data_scope": "segment" if field.get("data_scope_required") == "segment" else _scope_from_row(row) or field.get("data_scope_required"),
        "extraction_method": "XBRL_CSV",
        "confidence": 0.95 if not review_required else 0.75,
        "review_required": review_required,
        "review_reason": "multiple_xbrl_candidates" if review_required else "",
        "candidate_count": candidate_count,
    }
    record.update(_segment_metadata(field, label, target))
    return record


def _not_found_record(field: Dict[str, Any], target: Dict[str, Any], run_id: str, source_file: Path) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "company_year_id": target.get("company_year_id"),
        "operating_company_id": target.get("operating_company_id"),
        "fiscal_year": target.get("fiscal_year"),
        "source_doc_id": target.get("docID"),
        "source_file": str(source_file),
        "field_id": field.get("field_id"),
        "value_raw": None,
        "unit_raw": field.get("target_unit"),
        "data_scope": field.get("data_scope_required"),
        "extraction_method": "XBRL_CSV",
        "confidence": 0.0,
        "review_required": True,
        "review_reason": "xbrl_tag_not_found",
    }


def _first_present(row: Dict[str, Any], keys: List[str]) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
        if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
            return lowered[key.lower()]
    return None


def _prefer_primary_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    non_text = [row for row in rows if "TextBlock" not in str(_first_present(row, ["要素ID", "element_id", "element", "xbrl_element", "タグ", "tag"]) or "")]
    if non_text:
        rows = non_text
    non_summary = [
        row
        for row in rows
        if "SummaryOfBusinessResults" not in str(_first_present(row, ["要素ID", "element_id", "element", "xbrl_element", "タグ", "tag"]) or "")
    ]
    return non_summary or rows


def _prefer_best_candidate_priority(rows: List[Dict[str, Any]], candidates: List[str]) -> List[Dict[str, Any]]:
    if not rows or not candidates:
        return rows
    ranked = [(_candidate_priority(row, candidates), row) for row in rows]
    best = min(priority for priority, _row in ranked)
    if best >= len(candidates):
        return rows
    return [row for priority, row in ranked if priority == best]


def _candidate_priority(row: Dict[str, Any], candidates: List[str]) -> int:
    element = _first_present(row, ["要素ID", "element_id", "element", "xbrl_element", "タグ", "tag"])
    label = _first_present(row, ["項目名", "item_name", "label", "名称"])
    local_name = str(element or "").split(":")[-1]
    label_text = str(label or "")
    for index, token in enumerate(candidates):
        if _candidate_token_matches(local_name, label_text, token):
            return index
    return len(candidates)


def _effective_candidate_count(rows: List[Dict[str, Any]]) -> int:
    values = {
        str(_first_present(row, ["値", "value", "Value", "金額"]) or "").strip()
        for row in rows
    }
    values.discard("")
    return len(values) if values else len(rows)


def _contains_token(value: Any, token: str) -> bool:
    return token.lower() in str(value or "").lower()


def _matches_candidate(element: Any, label: Any, token: str) -> bool:
    local_name = str(element or "").split(":")[-1]
    return _candidate_token_matches(local_name, str(label or ""), token)


def _candidate_token_matches(local_name: str, label: str, token: str) -> bool:
    if _is_ascii_token(token):
        return local_name.lower() == token.lower()
    return _contains_token(label, token)


def _is_ascii_token(token: str) -> bool:
    try:
        token.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _scope_from_row(row: Dict[str, Any]) -> Any:
    scope = str(_first_present(row, ["連結・個別", "consolidation_scope"]) or "")
    if "連結" in scope:
        return "consolidated"
    if "個別" in scope or "単独" in scope:
        return "standalone"
    return None


def _segment_metadata(field: Dict[str, Any], label: Any, target: Dict[str, Any]) -> Dict[str, Any]:
    field_id = str(field.get("field_id") or "")
    if not (field_id.startswith("segment_") or field_id.startswith("employees_segment_")):
        return {}
    taxonomy_status = "company_specific" if _company_specific_field_id(field_id, target) else "common"
    return {
        "source_segment_label": str(label or ""),
        "normalized_segment_key": _normalized_segment_key(field_id),
        "segment_taxonomy_status": taxonomy_status,
        "applies_to_company_id": target.get("operating_company_id") if taxonomy_status == "company_specific" else "",
        "field_creation_reason": "company_specific_disclosure" if taxonomy_status == "company_specific" else "",
    }


def _company_specific_field_id(field_id: str, target: Dict[str, Any]) -> bool:
    company_id = str(target.get("operating_company_id") or "")
    return bool(company_id and f"_{company_id}_" in field_id)


def _normalized_segment_key(field_id: str) -> str:
    for prefix in ("segment_sales_", "segment_profit_", "segment_orders_", "employees_segment_"):
        if field_id.startswith(prefix):
            return field_id.removeprefix(prefix)
    return field_id


def _matches_context_filters(row: Dict[str, Any], filters: List[str]) -> bool:
    context = str(_first_present(row, ["コンテキストID", "contextRef", "context_ref", "コンテキスト"]) or "")
    relative_year = str(_first_present(row, ["相対年度", "relative_year"]) or "")
    period = str(_first_present(row, ["期間・時点", "period_or_instant"]) or "")
    scope = _scope_from_row(row)
    period_token = next((token for token in filters if token in {"CurrentYearDuration", "CurrentYearInstant"}), "")
    if period_token and "NonConsolidatedMember" in filters:
        if context != f"{period_token}_NonConsolidatedMember":
            return False
    elif period_token and "ConsolidatedMember" in filters:
        if context not in {period_token, f"{period_token}_ConsolidatedMember"}:
            return False
    elif period_token and not context.startswith(period_token):
        if not (period_token == "CurrentYearDuration" and ("当期" in relative_year or "当年度" in relative_year) and "期間" in period):
            if not (period_token == "CurrentYearInstant" and ("当期" in relative_year or "当年度" in relative_year) and "時点" in period):
                return False
    for token in filters:
        if token in context:
            continue
        if token == "CurrentYearDuration" and ("当期" in relative_year or "当年度" in relative_year) and "期間" in period:
            continue
        if token == "CurrentYearInstant" and ("当期" in relative_year or "当年度" in relative_year) and "時点" in period:
            continue
        if token == "ConsolidatedMember" and (scope == "consolidated" or (period_token and context == period_token)):
            continue
        if token in {"NonConsolidatedMember", "StandaloneMember"} and scope == "standalone":
            continue
        return False
    return True
