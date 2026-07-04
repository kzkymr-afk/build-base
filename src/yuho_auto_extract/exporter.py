from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from yuho_auto_extract.io_utils import is_blankish


REQUIRED_AUDIT_COLUMNS = [
    "source_quote",
    "data_scope",
    "unit_normalized",
    "extraction_method",
    "confidence",
    "source_segment_label",
    "normalized_segment_key",
    "segment_taxonomy_status",
    "applies_to_company_id",
    "field_creation_reason",
    "corroboration_count",
    "conflict_count",
    "resolution",
]


def apply_review_decisions(extracted_rows: Iterable[Dict[str, Any]], reviewed_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    reviewed = {(str(row.get("company_year_id")), str(row.get("field_id"))): row for row in reviewed_rows}
    final: List[Dict[str, Any]] = []
    for row in extracted_rows:
        key = (str(row.get("company_year_id")), str(row.get("field_id")))
        review = reviewed.get(key)
        copied = dict(row)
        if review:
            decision = str(review.get("review_decision", "")).lower()
            if decision == "reject":
                copied["review_status"] = "rejected"
                copied["review_required"] = True
            elif decision == "not_applicable":
                copied["review_status"] = "not_applicable"
                copied["review_required"] = False
            elif decision == "correct":
                copied["value"] = review.get("corrected_value")
                copied["value_normalized"] = review.get("corrected_value")
                copied["review_status"] = "corrected"
                copied["extraction_method"] = "MANUAL"
                copied["review_required"] = False
            elif decision == "accept":
                copied["review_status"] = "approved"
                copied["review_required"] = False
            else:
                copied.setdefault("review_status", "unreviewed")
        else:
            if copied.get("validation_status") == "fail" or _truthy(copied.get("review_required")):
                copied.setdefault("review_status", "unreviewed")
            else:
                copied.setdefault("review_status", "auto_accepted")
        if copied.get("review_status") not in {"rejected", "not_applicable"}:
            final.append(copied)
    return final


def filter_exportable_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    exportable: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("validation_status") == "fail" and row.get("review_status") not in {"approved", "corrected"}:
            continue
        if _truthy(row.get("review_required")) and row.get("review_status") not in {"approved", "corrected"}:
            continue
        if is_blankish(row.get("value")):
            continue
        copied = dict(row)
        _ensure_required_audit_columns(copied)
        exportable.append(copied)
    return exportable


def build_wide_values(
    rows: Iterable[Dict[str, Any]],
    company_year_master: Iterable[Dict[str, Any]],
    field_definition: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    fields = list(field_definition or [])
    preferred_methods = {
        str(field.get("field_id") or ""): str(field.get("preferred_method") or "")
        for field in fields
        if field.get("field_id")
    }
    base = {str(row["company_year_id"]): dict(row) for row in company_year_master}
    for row in base.values():
        for field in fields:
            field_id = str(field.get("field_id") or "")
            if field_id:
                row.setdefault(field_id, "")
    selected_scores: Dict[Tuple[str, str], Tuple[int, int, int]] = {}
    for row in rows:
        company_year_id = str(row.get("company_year_id", ""))
        if company_year_id not in base:
            base[company_year_id] = {"company_year_id": company_year_id}
            for field in fields:
                field_id = str(field.get("field_id") or "")
                if field_id:
                    base[company_year_id].setdefault(field_id, "")
        value = row.get("value", row.get("value_normalized"))
        if is_blankish(value):
            continue
        field_id = str(row.get("field_id"))
        key = (company_year_id, field_id)
        score = _wide_value_score(row, preferred_methods.get(field_id, ""))
        if score < selected_scores.get(key, (-1, -1, -1)):
            continue
        selected_scores[key] = score
        base[company_year_id][field_id] = value
    return [base[key] for key in sorted(base.keys())]


def _wide_value_score(row: Dict[str, Any], preferred_method: str) -> Tuple[int, int, int]:
    review_status = str(row.get("review_status") or "").lower()
    review_score = {
        "corrected": 4,
        "approved": 3,
        "auto_accepted": 2,
        "unreviewed": 1,
    }.get(review_status, 0)
    extraction_method = str(row.get("extraction_method") or "")
    preferred_score = 1 if preferred_method and extraction_method == preferred_method else 0
    source_score = {
        "XBRL_CSV": 5,
        "XBRL_SEGMENT_CONTEXT": 4,
        "XBRL_COST_TEXTBLOCK": 3,
        "LOCAL_RULE_TABLE": 2,
        "MANUAL_OBSIDIAN": 1,
    }.get(extraction_method, 0)
    return (review_score, preferred_score, source_score)


def build_source_audit(
    rows: Iterable[Dict[str, Any]],
    field_definition: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    field_names = {
        str(field.get("field_id") or ""): field.get("field_name_ja", "")
        for field in field_definition or []
        if field.get("field_id")
    }
    columns = [
        "company_year_id",
        "field_id",
        "field_name_ja",
        "value",
        "unit_normalized",
        "data_scope",
        "source_segment_label",
        "normalized_segment_key",
        "segment_taxonomy_status",
        "applies_to_company_id",
        "field_creation_reason",
        "source_doc_id",
        "source_file",
        "source_heading",
        "source_quote",
        "extraction_method",
        "confidence",
        "validation_status",
        "review_status",
        "run_id",
        "corroboration_count",
        "conflict_count",
        "resolution",
    ]
    audit_rows: List[Dict[str, Any]] = []
    for row in rows:
        copied = {column: row.get(column, "") for column in columns}
        copied["field_name_ja"] = row.get("field_name_ja") or field_names.get(str(row.get("field_id") or ""), "")
        audit_rows.append(copied)
    return audit_rows


def merge_without_overwriting_human(existing_rows: Iterable[Dict[str, Any]], new_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in existing_rows:
        merged[(str(row.get("company_year_id")), str(row.get("field_id")))] = dict(row)
    for row in new_rows:
        key = (str(row.get("company_year_id")), str(row.get("field_id")))
        old = merged.get(key)
        if old and str(old.get("review_status", "")).lower() in {"approved", "corrected"}:
            continue
        merged[key] = dict(row)
    return list(merged.values())


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _ensure_required_audit_columns(row: Dict[str, Any]) -> None:
    for column in REQUIRED_AUDIT_COLUMNS:
        row.setdefault(column, "")
