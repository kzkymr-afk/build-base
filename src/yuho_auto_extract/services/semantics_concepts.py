from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Sequence

from ..io_utils import is_blankish, read_table
from . import semantics_store

ALLOWED_UPDATE_FIELDS = {
    "concept_name_ja",
    "category",
    "data_scope",
    "target_unit",
    "period_type",
    "definition_ja",
    "calculation_formula",
    "status",
    "merged_into_concept_id",
}
ALLOWED_STATUSES = {"active", "merged", "retired"}


def list_concepts(
    root: Path,
    *,
    status: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    final_value_counts = _final_value_counts(root)
    conn = semantics_store.connect(root)
    try:
        rows = []
        for concept in semantics_store.fetch_canonical_concepts(conn).values():
            if status and str(concept.get("status") or "") != status:
                continue
            haystack = " ".join(str(concept.get(key) or "") for key in ("concept_id", "concept_name_ja", "category", "definition_ja"))
            if search and search.lower() not in haystack.lower():
                continue
            rows.append({**concept, **_concept_stats(conn, str(concept.get("concept_id") or ""), final_value_counts)})
    finally:
        conn.close()
    rows.sort(key=lambda row: (str(row.get("status") or ""), str(row.get("category") or ""), str(row.get("concept_id") or "")))
    total = len(rows)
    start = max(page - 1, 0) * page_size
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "rows": rows[start : start + page_size],
        "status_counts": _count_by(rows, "status"),
    }


def update_concept(root: Path, concept_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    concept_id = concept_id.strip()
    if not concept_id:
        raise ValueError("concept_id is required")
    clean_updates = {key: str(value or "") for key, value in updates.items() if key in ALLOWED_UPDATE_FIELDS}
    if not clean_updates:
        raise ValueError("no supported updates")
    if "status" in clean_updates and clean_updates["status"] not in ALLOWED_STATUSES:
        raise ValueError(f"unsupported status: {clean_updates['status']}")

    conn = semantics_store.connect(root)
    try:
        _require_concept(conn, concept_id)
        set_clause = ", ".join(f"{key} = ?" for key in clean_updates)
        params = list(clean_updates.values()) + [semantics_store._now_utc_iso(), concept_id]  # type: ignore[attr-defined]
        conn.execute(f"update canonical_concepts set {set_clause}, updated_at_utc = ? where concept_id = ?", params)
        conn.commit()
        return {"updated": True, "concept": _get_concept_with_stats(conn, concept_id)}
    finally:
        conn.close()


def merge_concepts(root: Path, source_concept_id: str, target_concept_id: str) -> Dict[str, Any]:
    source_concept_id = source_concept_id.strip()
    target_concept_id = target_concept_id.strip()
    if not source_concept_id or not target_concept_id:
        raise ValueError("source_concept_id and target_concept_id are required")
    if source_concept_id == target_concept_id:
        raise ValueError("source and target concepts must be different")

    conn = semantics_store.connect(root)
    try:
        _require_concept(conn, source_concept_id)
        _require_concept(conn, target_concept_id)
        now = semantics_store._now_utc_iso()  # type: ignore[attr-defined]
        conn.execute(
            """
            update canonical_concepts
            set status = 'merged', merged_into_concept_id = ?, updated_at_utc = ?
            where concept_id = ?
            """,
            (target_concept_id, now, source_concept_id),
        )
        cursor = conn.execute(
            """
            update concept_mappings
            set concept_id = ?, superseded_by = coalesce(superseded_by, ?), updated_at_utc = ?
            where concept_id = ? and status != 'rejected'
            """,
            (target_concept_id, target_concept_id, now, source_concept_id),
        )
        conn.commit()
        return {
            "merged": True,
            "source": _get_concept_with_stats(conn, source_concept_id),
            "target": _get_concept_with_stats(conn, target_concept_id),
            "mappings_retargeted": cursor.rowcount,
        }
    finally:
        conn.close()


def split_concept(root: Path, source_concept_id: str, new_concepts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    source_concept_id = source_concept_id.strip()
    if not source_concept_id:
        raise ValueError("source_concept_id is required")
    if not new_concepts:
        raise ValueError("new_concepts is required")

    conn = semantics_store.connect(root)
    try:
        source = _require_concept(conn, source_concept_id)
        rows: List[Dict[str, Any]] = []
        for entry in new_concepts:
            concept_id = str(entry.get("concept_id") or "").strip()
            if not concept_id:
                concept_id = _split_concept_id(source_concept_id, str(entry.get("concept_name_ja") or ""))
            if conn.execute("select 1 from canonical_concepts where concept_id = ?", (concept_id,)).fetchone() is not None:
                raise ValueError(f"concept already exists: {concept_id}")
            rows.append(
                {
                    "concept_id": concept_id,
                    "concept_name_ja": str(entry.get("concept_name_ja") or ""),
                    "category": str(entry.get("category") or source.get("category") or ""),
                    "data_scope": str(entry.get("data_scope") or source.get("data_scope") or ""),
                    "target_unit": str(entry.get("target_unit") or source.get("target_unit") or ""),
                    "period_type": str(entry.get("period_type") or source.get("period_type") or ""),
                    "definition_ja": str(entry.get("definition_ja") or ""),
                    "calculation_formula": str(entry.get("calculation_formula") or ""),
                    "status": "active",
                    "merged_into_concept_id": "",
                }
            )
        written = semantics_store.upsert_canonical_concepts(conn, rows)
        return {"split": True, "source": _get_concept_with_stats(conn, source_concept_id), "created": rows, "created_count": written}
    finally:
        conn.close()


def _require_concept(conn: Any, concept_id: str) -> Dict[str, Any]:
    row = conn.execute("select * from canonical_concepts where concept_id = ?", (concept_id,)).fetchone()
    if row is None:
        raise ValueError(f"concept not found: {concept_id}")
    return dict(row)


def _get_concept_with_stats(conn: Any, concept_id: str) -> Dict[str, Any]:
    return {**_require_concept(conn, concept_id), **_concept_stats(conn, concept_id)}


def _concept_stats(conn: Any, concept_id: str, final_value_counts: Dict[str, int] | None = None) -> Dict[str, Any]:
    mapping_count = conn.execute("select count(*) from concept_mappings where concept_id = ?", (concept_id,)).fetchone()[0]
    confirmed_count = conn.execute(
        "select count(*) from concept_mappings where concept_id = ? and status = 'confirmed'",
        (concept_id,),
    ).fetchone()[0]
    proposed_count = conn.execute(
        "select count(*) from concept_mappings where concept_id = ? and status = 'proposed'",
        (concept_id,),
    ).fetchone()[0]
    final_value_count = int((final_value_counts or {}).get(concept_id, 0))
    return {
        "mapping_count": mapping_count,
        "confirmed_mapping_count": confirmed_count,
        "proposed_mapping_count": proposed_count,
        "final_value_count": final_value_count,
        "coverage_hint": f"{final_value_count}件" if final_value_count else "未実値化",
    }


def _final_value_counts(root: Path) -> Dict[str, int]:
    path = root / "data" / "final" / "final_master_long.csv"
    if not path.exists():
        return {}
    counts: Counter[str] = Counter()
    for row in read_table(path):
        concept_id = str(row.get("field_id") or row.get("concept_id") or "")
        if concept_id and not is_blankish(row.get("value")):
            counts[concept_id] += 1
    return dict(counts)


def _count_by(rows: Sequence[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _split_concept_id(source_concept_id: str, name: str) -> str:
    raw = re.sub(r"[^0-9A-Za-z_]+", "_", name.strip().lower()).strip("_")
    suffix = raw[:32] or "split"
    return f"{source_concept_id}_{suffix}"
