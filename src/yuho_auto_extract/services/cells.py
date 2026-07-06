from __future__ import annotations

import csv
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from yuho_auto_extract.io_utils import read_table
from yuho_auto_extract.review_queue import REVIEW_COLUMNS
from yuho_auto_extract.services import field_admin, mapping_review, reviews, semantics_concepts


VALID_REVIEW_DECISIONS = {"accept", "correct", "reject", "not_applicable"}
SIMILAR_SCOPES = {"cell_only", "same_company_all_years", "same_field_all_companies"}


def save_cell_review(
    root: Path,
    company_year_id: str,
    field_id: str,
    *,
    review_decision: str,
    corrected_value: Any = "",
    reviewer_note: str = "",
    reviewer: str = "web_cell_workbench",
) -> Dict[str, Any]:
    row = {
        "company_year_id": company_year_id,
        "field_id": field_id,
        "review_decision": review_decision,
        "corrected_value": corrected_value,
        "reviewer_note": reviewer_note,
        "reviewer": reviewer,
    }
    key = _key(row)
    if not key:
        raise ValueError("company_year_id and field_id are required")
    if review_decision not in VALID_REVIEW_DECISIONS:
        raise ValueError("review_decision must be one of accept, correct, reject, not_applicable")
    if review_decision == "correct" and str(corrected_value).strip() == "":
        raise ValueError("corrected_value is required when review_decision is correct")

    queue_rows = read_table(root / "data" / "review" / "review_queue.csv")
    queue_row = next((queue_row for queue_row in queue_rows if _key(queue_row) == key), None)
    if queue_row is not None:
        if review_decision == "accept" and str(queue_row.get("extracted_value", "")).strip() == "":
            raise ValueError("extracted_value is required when review_decision is accept")
        return reviews.upsert_resolved_reviews(root, [row])

    base = _synthetic_review_row(root, key[0], key[1])
    if review_decision == "accept" and str(base.get("extracted_value", "")).strip() == "":
        raise ValueError("extracted_value is required when review_decision is accept")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base.update(
        {
            "review_decision": review_decision,
            "corrected_value": corrected_value,
            "reviewer_note": reviewer_note,
            "reviewer": reviewer,
            "reviewed_at": now,
            "applied_status": "",
            "applied_value": "",
            "applied_at": "",
        }
    )
    result = _upsert_resolved_rows(root, [base])
    result["synthetic_review_rows"] = 1
    return result


def update_cell_field_name(root: Path, field_id: str, field_name_ja: str) -> Dict[str, Any]:
    field_id = field_id.strip()
    field_name_ja = field_name_ja.strip()
    if not field_id or not field_name_ja:
        raise ValueError("field_id and field_name_ja are required")
    field_result = field_admin.update_field_definition(root, field_id, {"field_name_ja": field_name_ja})
    concept_result: Dict[str, Any] = {"updated": False, "skipped": True}
    try:
        concept_result = semantics_concepts.update_concept(root, field_id, {"concept_name_ja": field_name_ja})
    except ValueError:
        concept_result = {"updated": False, "skipped": True}
    return {"field": field_result, "concept": concept_result}


def decide_cell_mapping(root: Path, mapping_id: str, decision: str, *, reviewer: str = "web_cell_workbench", note: str = "") -> Dict[str, Any]:
    decision = decision.strip().lower()
    if decision == "confirm":
        return mapping_review.confirm_mapping_proposal(root, mapping_id, reviewer=reviewer)
    if decision == "reject":
        return mapping_review.reject_mapping_proposal(root, mapping_id, reviewer=reviewer, note=note)
    raise ValueError("decision must be confirm or reject")


def apply_similar_reviews(
    root: Path,
    company_year_id: str,
    field_id: str,
    *,
    scope: str,
    review_decision: str,
    corrected_value: Any = "",
    reviewer_note: str = "",
    reviewer: str = "web_cell_workbench",
    preview: bool = True,
) -> Dict[str, Any]:
    scope = scope.strip()
    if scope not in SIMILAR_SCOPES:
        raise ValueError(f"scope must be one of {', '.join(sorted(SIMILAR_SCOPES))}")
    targets = _similar_targets(root, company_year_id, field_id, scope)
    preview_rows = targets[:20]
    if preview:
        return {"preview": True, "scope": scope, "target_count": len(targets), "targets": preview_rows}
    changed = 0
    for target in targets:
        result = save_cell_review(
            root,
            str(target.get("company_year_id", "")),
            field_id,
            review_decision=review_decision,
            corrected_value=corrected_value,
            reviewer_note=reviewer_note,
            reviewer=reviewer,
        )
        changed += int(result.get("changed") or 0)
    return {"preview": False, "scope": scope, "target_count": len(targets), "changed": changed, "targets": preview_rows}


def _synthetic_review_row(root: Path, company_year_id: str, field_id: str) -> Dict[str, Any]:
    wide_row = next(
        (row for row in read_table(root / "data" / "final" / "final_master_wide.csv") if str(row.get("company_year_id", "")) == company_year_id),
        {},
    )
    field = next(
        (row for row in read_table(root / "config" / "field_definition.csv") if str(row.get("field_id", "")) == field_id),
        {},
    )
    audit = next(
        (
            row
            for row in read_table(root / "data" / "final" / "source_audit.csv")
            if str(row.get("company_year_id", "")) == company_year_id and str(row.get("field_id", "")) == field_id
        ),
        {},
    )
    row = {column: "" for column in REVIEW_COLUMNS}
    row.update(
        {
            "company_year_id": company_year_id,
            "company_name": wide_row.get("operating_company_name", ""),
            "fiscal_year": wide_row.get("fiscal_year", _year_from_company_year(company_year_id)),
            "field_id": field_id,
            "field_name_ja": field.get("field_name_ja", field_id),
            "existing_value": wide_row.get(field_id, ""),
            "extracted_value": audit.get("value", ""),
            "unit_normalized": audit.get("unit_normalized", field.get("target_unit", "")),
            "data_scope": audit.get("data_scope", ""),
            "source_doc_id": audit.get("source_doc_id", ""),
            "source_heading": audit.get("source_heading", ""),
            "source_quote": audit.get("source_quote", ""),
            "confidence": audit.get("confidence", ""),
            "validation_status": audit.get("validation_status", ""),
            "review_reason": "cell_workbench_manual",
        }
    )
    return row


def _upsert_resolved_rows(root: Path, incoming_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    resolved_path = root / "data" / "review" / "review_resolved.csv"
    existing = read_table(resolved_path) if resolved_path.exists() else []
    by_key = {_key(row): row for row in existing if _key(row)}
    changed = 0
    for incoming in incoming_rows:
        key = _key(incoming)
        if not key:
            continue
        merged = {column: "" for column in REVIEW_COLUMNS}
        merged.update(by_key.get(key, {}))
        merged.update({column: incoming.get(column, "") for column in REVIEW_COLUMNS})
        by_key[key] = merged
        changed += 1
    rows = [by_key[key] for key in sorted(by_key)]
    _write_csv_atomic(resolved_path, rows, REVIEW_COLUMNS)
    return {"path": str(resolved_path), "changed": changed, "total": len(rows)}


def _similar_targets(root: Path, company_year_id: str, field_id: str, scope: str) -> List[Dict[str, Any]]:
    rows = read_table(root / "data" / "final" / "final_master_wide.csv")
    current = next((row for row in rows if str(row.get("company_year_id", "")) == company_year_id), {})
    company_id = str(current.get("operating_company_id") or _company_id_from_company_year(company_year_id))
    if scope == "cell_only":
        targets = [current or {"company_year_id": company_year_id}]
    elif scope == "same_company_all_years":
        targets = [row for row in rows if str(row.get("operating_company_id", "")) == company_id]
    else:
        targets = rows
    return [
        {
            "company_year_id": row.get("company_year_id", ""),
            "operating_company_id": row.get("operating_company_id", ""),
            "fiscal_year": row.get("fiscal_year", ""),
            "field_id": field_id,
            "current_value": row.get(field_id, ""),
        }
        for row in targets
        if row.get("company_year_id")
    ]


def _key(row: Dict[str, Any]) -> Tuple[str, str]:
    company_year_id = str(row.get("company_year_id", "")).strip()
    field_id = str(row.get("field_id", "")).strip()
    if not company_year_id or not field_id:
        return ("", "")
    return (company_year_id, field_id)


def _company_id_from_company_year(company_year_id: str) -> str:
    if "_" not in company_year_id:
        return ""
    return company_year_id.rsplit("_", 1)[0]


def _year_from_company_year(company_year_id: str) -> str:
    if "_" not in company_year_id:
        return ""
    return company_year_id.rsplit("_", 1)[1]


def _write_csv_atomic(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in fieldnames})
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
