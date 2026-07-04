from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..io_utils import read_table
from . import reviews

REASON_PREFIX = "identity_group_mismatch:"


def read_reconciliation_groups(root: Path, *, limit: int = 200) -> Dict[str, Any]:
    queue_rows = read_table(root / "data" / "review" / "review_queue.csv")
    groups: Dict[str, Dict[str, Any]] = {}
    for row in queue_rows:
        group_ids = _group_ids(row)
        if not group_ids:
            continue
        for group_id in group_ids:
            group = groups.setdefault(
                group_id,
                {
                    "group_id": group_id,
                    "rule_id": group_id.split(":", 1)[1] if ":" in group_id else group_id,
                    "item_count": 0,
                    "company_year_count": 0,
                    "field_count": 0,
                    "sample_rows": [],
                    "_company_year_ids": set(),
                    "_field_ids": set(),
                },
            )
            group["item_count"] += 1
            group["_company_year_ids"].add(str(row.get("company_year_id") or ""))
            group["_field_ids"].add(str(row.get("field_id") or ""))
            if len(group["sample_rows"]) < 8:
                group["sample_rows"].append(_review_row_summary(row))

    out = []
    for group in groups.values():
        copied = dict(group)
        copied["company_year_count"] = len(copied.pop("_company_year_ids"))
        copied["field_count"] = len(copied.pop("_field_ids"))
        out.append(copied)
    out.sort(key=lambda row: (-int(row.get("item_count") or 0), str(row.get("group_id") or "")))
    return {"total": len(out), "groups": out[:limit]}


def apply_reconciliation_group(
    root: Path,
    group_id: str,
    *,
    decision: str,
    corrected_value: Any = "",
    reviewer_note: str = "",
    reviewer: str = "",
) -> Dict[str, Any]:
    group_id = group_id.strip()
    if not group_id.startswith(REASON_PREFIX):
        raise ValueError("group_id must start with identity_group_mismatch:")
    queue_rows = read_table(root / "data" / "review" / "review_queue.csv")
    targets = [
        _review_decision_row(
            row,
            decision=decision,
            corrected_value=corrected_value,
            reviewer_note=reviewer_note or f"reconciliation group decision: {group_id}",
            reviewer=reviewer,
        )
        for row in queue_rows
        if group_id in _group_ids(row)
    ]
    if not targets:
        raise ValueError(f"reconciliation group not found: {group_id}")
    result = reviews.upsert_resolved_reviews(root, targets)
    result["group_id"] = group_id
    result["applied_items"] = len(targets)
    return result


def _group_ids(row: Dict[str, Any]) -> List[str]:
    ids = []
    for reason in str(row.get("review_reason") or "").replace(",", ";").split(";"):
        reason = reason.strip()
        if reason.startswith(REASON_PREFIX):
            ids.append(reason)
    return ids


def _review_row_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "company_year_id": row.get("company_year_id", ""),
        "field_id": row.get("field_id", ""),
        "field_name_ja": row.get("field_name_ja", ""),
        "existing_value": row.get("existing_value", ""),
        "extracted_value": row.get("extracted_value", ""),
        "review_reason": row.get("review_reason", ""),
    }


def _review_decision_row(
    row: Dict[str, Any],
    *,
    decision: str,
    corrected_value: Any,
    reviewer_note: str,
    reviewer: str,
) -> Dict[str, Any]:
    return {
        "company_year_id": row.get("company_year_id", ""),
        "field_id": row.get("field_id", ""),
        "review_decision": decision,
        "corrected_value": corrected_value,
        "reviewer_note": reviewer_note,
        "reviewer": reviewer,
    }
