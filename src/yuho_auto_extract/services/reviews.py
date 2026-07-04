from __future__ import annotations

import csv
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from yuho_auto_extract.io_utils import prefer_existing_table, read_table
from yuho_auto_extract.review_queue import REVIEW_COLUMNS


VALID_DECISIONS = {"accept", "correct", "reject", "not_applicable"}


def upsert_resolved_reviews(root: Path, incoming_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    queue_path = root / "data" / "review" / "review_queue.csv"
    resolved_path = root / "data" / "review" / "review_resolved.csv"
    queue_rows = read_table(queue_path)
    queue_by_key = {_key(row): row for row in queue_rows if _key(row)}
    existing_rows = read_table(resolved_path) if resolved_path.exists() else []
    existing_by_key = {_key(row): row for row in existing_rows if _key(row)}

    changed = 0
    for row in incoming_rows:
        key = _key(row)
        if not key:
            raise ValueError("company_year_id and field_id are required")
        if key not in queue_by_key:
            raise ValueError(f"review key is not in review_queue.csv: {key[0]} / {key[1]}")
        normalized = _normalized_review_row(row)
        base = dict(queue_by_key[key])
        if key in existing_by_key:
            base.update(existing_by_key[key])
        base.update(normalized)
        existing_by_key[key] = base
        changed += 1

    rows = [existing_by_key[key] for key in sorted(existing_by_key)]
    _write_csv_atomic(resolved_path, rows, REVIEW_COLUMNS)
    return {"path": str(resolved_path), "changed": changed, "total": len(rows)}


def delete_resolved_reviews(root: Path, incoming_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    resolved_path = root / "data" / "review" / "review_resolved.csv"
    existing_rows = read_table(resolved_path) if resolved_path.exists() else []
    delete_keys = {_key(row) for row in incoming_rows if _key(row)}
    if not delete_keys:
        raise ValueError("company_year_id and field_id are required")

    kept_rows = [row for row in existing_rows if _key(row) not in delete_keys]
    deleted = len(existing_rows) - len(kept_rows)
    _write_csv_atomic(resolved_path, kept_rows, REVIEW_COLUMNS)
    return {"path": str(resolved_path), "deleted": deleted, "total": len(kept_rows)}


def _normalized_review_row(row: Dict[str, Any]) -> Dict[str, Any]:
    decision = str(row.get("review_decision", "")).strip().lower()
    if decision not in VALID_DECISIONS:
        raise ValueError("review_decision must be one of accept, correct, reject, not_applicable")
    corrected_value = row.get("corrected_value", "")
    if decision == "correct" and str(corrected_value).strip() == "":
        raise ValueError("corrected_value is required when review_decision is correct")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "company_year_id": str(row.get("company_year_id", "")).strip(),
        "field_id": str(row.get("field_id", "")).strip(),
        "review_decision": decision,
        "corrected_value": corrected_value,
        "reviewer_note": row.get("reviewer_note", ""),
        "reviewer": row.get("reviewer", ""),
        "reviewed_at": row.get("reviewed_at") or now,
        "applied_status": "",
        "applied_value": "",
        "applied_at": "",
    }


def mark_resolved_reviews_applied(root: Path, reviewed: str = "data/review/review_resolved.csv") -> Dict[str, Any]:
    resolved_path = root / reviewed
    if not resolved_path.exists():
        return {"path": str(resolved_path), "updated": 0, "total": 0}

    final_by_key = {
        _key(row): row
        for row in read_table(root / "data" / "final" / "final_master_long.csv")
        if _key(row)
    }
    extracted_path = prefer_existing_table(root / "data" / "intermediate" / "normalized_validated_long.parquet")
    extracted_keys = {_key(row) for row in read_table(extracted_path)} if extracted_path.exists() else set()
    rows = read_table(resolved_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = 0
    for row in rows:
        key = _key(row)
        decision = str(row.get("review_decision", "")).strip().lower()
        final_row = final_by_key.get(key)
        before = (row.get("applied_status", ""), row.get("applied_value", ""), row.get("applied_at", ""))
        if decision == "reject":
            if key in extracted_keys:
                row["applied_status"] = "rejected"
                row["applied_value"] = ""
                row["applied_at"] = now
            else:
                row["applied_status"] = "not_found"
                row["applied_value"] = ""
                row["applied_at"] = ""
        elif decision == "not_applicable":
            row["applied_status"] = "not_applicable"
            row["applied_value"] = ""
            row["applied_at"] = now
        elif final_row and str(final_row.get("review_status", "")).lower() in {"approved", "corrected"}:
            row["applied_status"] = "applied"
            row["applied_value"] = final_row.get("value", final_row.get("value_normalized", ""))
            row["applied_at"] = now
        elif key in extracted_keys:
            row["applied_status"] = "not_exported"
            row["applied_value"] = ""
            row["applied_at"] = ""
        else:
            row["applied_status"] = "not_found"
            row["applied_value"] = ""
            row["applied_at"] = ""
        after = (row.get("applied_status", ""), row.get("applied_value", ""), row.get("applied_at", ""))
        if after != before:
            updated += 1

    _write_csv_atomic(resolved_path, rows, REVIEW_COLUMNS)
    return {"path": str(resolved_path), "updated": updated, "total": len(rows)}


def mark_company_field_not_applicable(
    root: Path,
    company_id: str,
    field_id: str,
    note: str = "",
    start_year: Any = "",
    end_year: Any = "",
) -> Dict[str, Any]:
    company_id = company_id.strip()
    field_id = field_id.strip()
    if not company_id or not field_id:
        raise ValueError("company_id and field_id are required")
    start = _year_or_none(start_year)
    end = _year_or_none(end_year)
    if start is not None and end is not None and start > end:
        raise ValueError("start_year must be less than or equal to end_year")
    reason = note or f"{company_id} は持株会社等であり、この項目は同業ゼネコン比較の対象外"
    exclusion_result = _upsert_company_field_exclusion(root, company_id, field_id, reason, start, end)
    queue_rows = read_table(root / "data" / "review" / "review_queue.csv")
    targets = [
        {
            "company_year_id": row.get("company_year_id", ""),
            "field_id": row.get("field_id", ""),
            "review_decision": "not_applicable",
            "corrected_value": "",
            "reviewer_note": reason,
        }
        for row in queue_rows
        if _company_id_from_company_year(str(row.get("company_year_id", ""))) == company_id
        and str(row.get("field_id", "")) == field_id
        and _year_in_range(row.get("fiscal_year") or _year_from_company_year(str(row.get("company_year_id", ""))), start, end)
    ]
    result = upsert_resolved_reviews(root, targets) if targets else {"path": "", "changed": 0, "total": 0}
    stale_deleted = _delete_stale_not_applicable_reviews_outside_range(root, company_id, field_id, start, end)
    result["company_id"] = company_id
    result["field_id"] = field_id
    result["start_year"] = start or ""
    result["end_year"] = end or ""
    result["marked"] = len(targets)
    result["replaced_exclusions"] = exclusion_result["replaced"]
    result["stale_not_applicable_deleted"] = stale_deleted
    result["exclusion_path"] = str(root / "config" / "company_field_exclusions.csv")
    return result


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


def _year_from_company_year(company_year_id: str) -> Any:
    if "_" not in company_year_id:
        return None
    return _year_or_none(company_year_id.rsplit("_", 1)[1])


def _year_or_none(value: Any) -> Any:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _year_in_range(value: Any, start_year: Any, end_year: Any) -> bool:
    year = _year_or_none(value)
    if year is None:
        return start_year is None and end_year is None
    if start_year is not None and year < start_year:
        return False
    if end_year is not None and year > end_year:
        return False
    return True


def _upsert_company_field_exclusion(root: Path, company_id: str, field_id: str, reason: str, start_year: Any = None, end_year: Any = None) -> Dict[str, int]:
    path = root / "config" / "company_field_exclusions.csv"
    rows = read_table(path) if path.exists() else []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    same_rows = [
        row
        for row in rows
        if str(row.get("company_id", "")).strip() == company_id and str(row.get("field_id", "")).strip() == field_id
    ]
    kept_rows = [
        row
        for row in rows
        if not (str(row.get("company_id", "")).strip() == company_id and str(row.get("field_id", "")).strip() == field_id)
    ]
    created_at = same_rows[0].get("created_at") if same_rows else now
    replacement = {
        "company_id": company_id,
        "field_id": field_id,
        "start_year": start_year or "",
        "end_year": end_year or "",
        "reason": reason,
        "created_at": created_at or now,
        "updated_at": now,
    }
    kept_rows.append(replacement)
    _write_csv_atomic(path, kept_rows, ["company_id", "field_id", "start_year", "end_year", "reason", "created_at", "updated_at"])
    return {"replaced": len(same_rows)}


def _delete_stale_not_applicable_reviews_outside_range(root: Path, company_id: str, field_id: str, start_year: Any = None, end_year: Any = None) -> int:
    resolved_path = root / "data" / "review" / "review_resolved.csv"
    if not resolved_path.exists():
        return 0
    rows = read_table(resolved_path)
    kept: List[Dict[str, Any]] = []
    deleted = 0
    for row in rows:
        same_company = _company_id_from_company_year(str(row.get("company_year_id", ""))) == company_id
        same_field = str(row.get("field_id", "")).strip() == field_id
        not_applicable = str(row.get("review_decision", "")).strip().lower() == "not_applicable"
        year = row.get("fiscal_year") or _year_from_company_year(str(row.get("company_year_id", "")))
        if same_company and same_field and not_applicable and not _year_in_range(year, start_year, end_year):
            deleted += 1
            continue
        kept.append(row)
    if deleted:
        _write_csv_atomic(resolved_path, kept, REVIEW_COLUMNS)
    return deleted


def _write_csv_atomic(path: Path, rows: List[Dict[str, Any]], preferred_columns: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(preferred_columns)
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in columns})
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        finally:
            raise
