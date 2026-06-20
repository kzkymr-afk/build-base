from __future__ import annotations

import csv
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from yuho_auto_extract.io_utils import prefer_existing_table, read_table
from yuho_auto_extract.review_queue import REVIEW_COLUMNS


VALID_DECISIONS = {"accept", "correct", "reject"}


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
        raise ValueError("review_decision must be one of accept, correct, reject")
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


def _key(row: Dict[str, Any]) -> Tuple[str, str]:
    company_year_id = str(row.get("company_year_id", "")).strip()
    field_id = str(row.get("field_id", "")).strip()
    if not company_year_id or not field_id:
        return ("", "")
    return (company_year_id, field_id)


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
