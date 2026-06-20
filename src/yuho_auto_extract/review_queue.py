from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Tuple

from yuho_auto_extract.io_utils import is_blankish


REVIEW_COLUMNS = [
    "company_year_id",
    "company_name",
    "fiscal_year",
    "field_id",
    "field_name_ja",
    "existing_value",
    "extracted_value",
    "difference",
    "difference_pct",
    "unit_normalized",
    "data_scope",
    "source_segment_label",
    "normalized_segment_key",
    "segment_taxonomy_status",
    "applies_to_company_id",
    "field_creation_reason",
    "source_doc_id",
    "source_heading",
    "source_quote",
    "confidence",
    "validation_status",
    "review_reason",
    "review_decision",
    "corrected_value",
    "reviewer_note",
    "reviewer",
    "reviewed_at",
    "applied_status",
    "applied_value",
    "applied_at",
]


def build_review_queue(
    extracted_rows: Iterable[Dict[str, Any]],
    field_definitions: Iterable[Dict[str, Any]],
    company_year_master: Iterable[Dict[str, Any]],
    existing_rows: Iterable[Dict[str, Any]] = (),
) -> List[Dict[str, Any]]:
    extracted = list(extracted_rows)
    keys_with_extracted_value = {
        (str(row.get("company_year_id", "")), str(row.get("field_id", "")))
        for row in extracted
        if _value(row) is not None
    }
    fields = {str(row["field_id"]): row for row in field_definitions}
    company_years = {str(row["company_year_id"]): row for row in company_year_master}
    existing = _index_existing(existing_rows)
    queue: List[Dict[str, Any]] = []
    for row in extracted:
        field_id = str(row.get("field_id", ""))
        field = fields.get(field_id, {})
        company_year_id = str(row.get("company_year_id", ""))
        existing_row = existing.get((company_year_id, field_id), {})
        reasons = _review_reasons(row, field, existing_row)
        if not reasons:
            continue
        company_year = company_years.get(company_year_id, {})
        existing_value = _value(existing_row)
        extracted_value = _value(row)
        difference = None if existing_value is None or extracted_value is None else extracted_value - existing_value
        difference_pct = None if existing_value in (None, 0) or difference is None else difference / existing_value
        item = {
            "company_year_id": company_year_id,
            "company_name": row.get("operating_company_name") or row.get("operating_company_id") or company_year.get("operating_company_id"),
            "fiscal_year": row.get("fiscal_year") or company_year.get("fiscal_year"),
            "field_id": field_id,
            "field_name_ja": field.get("field_name_ja", ""),
            "existing_value": existing_value,
            "extracted_value": extracted_value,
            "difference": difference,
            "difference_pct": difference_pct,
            "unit_normalized": row.get("unit_normalized"),
            "data_scope": row.get("data_scope"),
            "source_segment_label": row.get("source_segment_label"),
            "normalized_segment_key": row.get("normalized_segment_key"),
            "segment_taxonomy_status": row.get("segment_taxonomy_status"),
            "applies_to_company_id": row.get("applies_to_company_id"),
            "field_creation_reason": row.get("field_creation_reason"),
            "source_doc_id": row.get("source_doc_id"),
            "source_heading": row.get("source_heading"),
            "source_quote": row.get("source_quote"),
            "confidence": row.get("confidence"),
            "validation_status": row.get("validation_status"),
            "review_reason": ";".join(reasons),
            "review_decision": "",
            "corrected_value": "",
            "reviewer_note": "",
            "reviewer": "",
            "reviewed_at": "",
        }
        queue.append({column: item.get(column, "") for column in REVIEW_COLUMNS})
    return _suppress_blank_candidates_when_value_exists(queue, keys_with_extracted_value)


def _review_reasons(row: Dict[str, Any], field: Dict[str, Any], existing_row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    if str(row.get("review_required", "")).lower() in {"true", "1"} or row.get("review_required") is True:
        reasons.append(str(row.get("review_reason") or "review_required"))
    threshold = float(field.get("review_threshold") or 0)
    try:
        confidence = float(row.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if threshold and confidence < threshold:
        reasons.append("confidence_below_threshold")
    if row.get("data_scope") not in (None, "", field.get("data_scope_required")):
        reasons.append("data_scope_mismatch")
    if not row.get("unit_normalized") and _value(row) is not None:
        reasons.append("unit_unknown")
    if row.get("validation_status") in {"warn", "fail"}:
        reasons.append("validation_" + str(row.get("validation_status")))
    if existing_row:
        if str(existing_row.get("review_status", "")).lower() in {"approved", "corrected"}:
            reasons.append("existing_human_reviewed")
        existing_value = _value(existing_row)
        extracted_value = _value(row)
        if existing_value is not None and extracted_value is not None and abs(existing_value - extracted_value) > max(10, abs(existing_value) * 0.01):
            reasons.append("existing_value_mismatch")
    return [reason for reason in dict.fromkeys(";".join(reasons).split(";")) if reason]


def _index_existing(rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        company_year_id = str(row.get("company_year_id", ""))
        field_id = str(row.get("field_id", ""))
        if company_year_id and field_id:
            out[(company_year_id, field_id)] = row
    return out


def _suppress_blank_candidates_when_value_exists(
    rows: List[Dict[str, Any]],
    keys_with_value: Iterable[Tuple[str, str]] = (),
) -> List[Dict[str, Any]]:
    keys_with_value = set(keys_with_value) or {
        (str(row.get("company_year_id", "")), str(row.get("field_id", "")))
        for row in rows
        if not is_blankish(row.get("extracted_value"))
    }
    if not keys_with_value:
        return rows
    return [
        row
        for row in rows
        if (
            (str(row.get("company_year_id", "")), str(row.get("field_id", ""))) not in keys_with_value
            or not is_blankish(row.get("extracted_value"))
        )
    ]


def _value(row: Dict[str, Any]) -> Any:
    if not row:
        return None
    value = row.get("value", row.get("value_normalized", row.get("extracted_value", row.get("existing_value"))))
    if is_blankish(value):
        return None
    try:
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except (TypeError, ValueError):
        return value
