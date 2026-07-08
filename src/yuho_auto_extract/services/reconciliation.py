from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..io_utils import read_table
from . import reviews, semantics_store

REASON_PREFIX = "identity_group_mismatch:"
CELL_RESOLUTION_PREFIX = "needs_reconciliation:"


def read_reconciliation_groups(root: Path, *, limit: int = 200) -> Dict[str, Any]:
    queue_rows = read_table(root / "data" / "review" / "review_queue.csv")
    queue_by_key: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for row in queue_rows:
        key = (str(row.get("company_year_id") or ""), str(row.get("field_id") or ""))
        queue_by_key.setdefault(key, []).append(row)

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
                    "source": "review_queue",
                    "apply_supported": True,
                    "review_queue_match_count": 0,
                    "_company_year_ids": set(),
                    "_field_ids": set(),
                },
            )
            group["item_count"] += 1
            group["review_queue_match_count"] += 1
            group["_company_year_ids"].add(str(row.get("company_year_id") or ""))
            group["_field_ids"].add(str(row.get("field_id") or ""))
            if len(group["sample_rows"]) < 8:
                group["sample_rows"].append(_review_row_summary(row))

    _append_cell_resolution_groups(root, groups, queue_by_key)

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
    preview: bool = False,
) -> Dict[str, Any]:
    group_id = group_id.strip()
    if not group_id.startswith(REASON_PREFIX):
        raise ValueError("only identity_group_mismatch groups can be applied in bulk; use the cell workbench for needs_reconciliation groups")
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
    if preview:
        resolved_rows = read_table(root / "data" / "review" / "review_resolved.csv")
        return {
            "preview": True,
            "group_id": group_id,
            "applied_items": 0,
            "target_count": len(targets),
            "total": len(resolved_rows),
            "targets": targets[:20],
        }
    result = reviews.upsert_resolved_reviews(root, targets)
    result["preview"] = False
    result["group_id"] = group_id
    result["applied_items"] = len(targets)
    result["target_count"] = len(targets)
    return result


def _group_ids(row: Dict[str, Any]) -> List[str]:
    ids = []
    for reason in str(row.get("review_reason") or "").replace(",", ";").split(";"):
        reason = reason.strip()
        if reason.startswith(REASON_PREFIX):
            ids.append(reason)
    return ids


def _append_cell_resolution_groups(
    root: Path,
    groups: Dict[str, Dict[str, Any]],
    queue_by_key: Dict[tuple[str, str], List[Dict[str, Any]]],
) -> None:
    for row in _cell_resolution_rows(root):
        if str(row.get("resolution") or "") != "needs_reconciliation":
            continue
        company_year_id = str(row.get("company_year_id") or "")
        concept_id = str(row.get("concept_id") or row.get("field_id") or "")
        if not company_year_id or not concept_id:
            continue
        group_id = f"{CELL_RESOLUTION_PREFIX}{concept_id}"
        group = groups.setdefault(
            group_id,
            {
                "group_id": group_id,
                "rule_id": concept_id,
                "item_count": 0,
                "company_year_count": 0,
                "field_count": 0,
                "sample_rows": [],
                "source": "cell_resolutions",
                "apply_supported": False,
                "review_queue_match_count": 0,
                "_company_year_ids": set(),
                "_field_ids": set(),
            },
        )
        key = (company_year_id, concept_id)
        matching_queue_rows = queue_by_key.get(key, [])
        group["item_count"] += 1
        group["review_queue_match_count"] += len(matching_queue_rows)
        group["_company_year_ids"].add(company_year_id)
        group["_field_ids"].add(concept_id)
        if len(group["sample_rows"]) < 8:
            group["sample_rows"].append(_cell_resolution_summary(row, matching_queue_rows[0] if matching_queue_rows else {}))


def _cell_resolution_rows(root: Path) -> List[Dict[str, Any]]:
    if semantics_store.semantics_db_path(root).exists():
        conn = semantics_store.connect(root)
        try:
            return [dict(row) for row in semantics_store.fetch_cell_resolutions(conn).values()]
        finally:
            conn.close()
    csv_path = root / "data" / "marts" / "semantics" / "cell_resolutions.csv"
    return read_table(csv_path) if csv_path.exists() else []


def _review_row_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "company_year_id": row.get("company_year_id", ""),
        "field_id": row.get("field_id", ""),
        "field_name_ja": row.get("field_name_ja", ""),
        "existing_value": row.get("existing_value", ""),
        "extracted_value": row.get("extracted_value", ""),
        "review_reason": row.get("review_reason", ""),
    }


def _cell_resolution_summary(row: Dict[str, Any], queue_row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "company_year_id": row.get("company_year_id", ""),
        "field_id": row.get("concept_id", "") or row.get("field_id", ""),
        "field_name_ja": queue_row.get("field_name_ja", ""),
        "existing_value": row.get("value", ""),
        "extracted_value": queue_row.get("extracted_value", ""),
        "review_reason": row.get("review_reason", "") or row.get("resolution", ""),
        "resolution": row.get("resolution", ""),
        "corroboration_count": row.get("corroboration_count", ""),
        "conflict_count": row.get("conflict_count", ""),
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
