"""BuildBase P4a: 対応層カバレッジレポート。

semantics.db の observed_items / canonical_concepts / concept_mappings を
読み取り専用で集計し、observed総数・mapped/unmapped・taxonomy_kind別・
concept別被覆・mappings のstatus別/decided_by別を可視化する。

このモジュールは semantics.db への書き込みを一切行わない（読み取り専用）。
出力:
  data/reports/semantics_coverage.csv         — observed_item別の被覆状況
  data/reports/semantics_coverage_summary.json — 集計サマリ（機械可読）
  data/reports/semantics_coverage_summary.md   — 集計サマリ（人間可読）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from ..io_utils import ensure_parent, write_table
from . import semantics_store

REPORTS_DIR = Path("data") / "reports"
COVERAGE_CSV_FILENAME = "semantics_coverage.csv"
SUMMARY_JSON_FILENAME = "semantics_coverage_summary.json"
SUMMARY_MD_FILENAME = "semantics_coverage_summary.md"


def _count_by(rows: Sequence[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _count_by_prefix(rows: Sequence[Dict[str, Any]], key: str, sep: str = ":") -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        prefix = value.split(sep, 1)[0] if sep in value else value
        counts[prefix] = counts.get(prefix, 0) + 1
    return counts


def _observed_ids_with_mapping(mappings: Sequence[Dict[str, Any]]) -> set:
    return {str(m.get("observed_item_id") or "") for m in mappings if m.get("observed_item_id")}


def _observed_ids_with_confirmed_map(mappings: Sequence[Dict[str, Any]]) -> set:
    return {
        str(m.get("observed_item_id") or "")
        for m in mappings
        if m.get("observed_item_id")
        and str(m.get("status") or "") == "confirmed"
        and str(m.get("action") or "") == "map"
    }


def _concepts_without_mapping(concepts: Sequence[Dict[str, Any]], mappings: Sequence[Dict[str, Any]]) -> List[str]:
    mapped_concept_ids = {
        str(m.get("concept_id") or "")
        for m in mappings
        if str(m.get("action") or "") == "map" and m.get("concept_id")
    }
    return sorted(
        str(c.get("concept_id") or "")
        for c in concepts
        if str(c.get("concept_id") or "") not in mapped_concept_ids
    )


def _group_count_map_by_concept(mappings: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for m in mappings:
        if str(m.get("action") or "") != "map":
            continue
        concept_id = str(m.get("concept_id") or "")
        if not concept_id:
            continue
        counts[concept_id] = counts.get(concept_id, 0) + 1
    return counts


def build_coverage_report(conn) -> Dict[str, Any]:
    """semantics.db の対応層3テーブルを集計して辞書として返す（純粋な読み取り集計）。"""
    observed = [dict(row) for row in conn.execute("select * from observed_items")]
    mappings = [dict(row) for row in conn.execute("select * from concept_mappings")]
    concepts = [dict(row) for row in conn.execute("select * from canonical_concepts")]

    mapped_ids = _observed_ids_with_mapping(mappings)
    confirmed_mapped_ids = _observed_ids_with_confirmed_map(mappings)

    return {
        "observed_total": len(observed),
        "observed_by_item_kind": _count_by(observed, "item_kind"),
        "observed_by_taxonomy_kind": _count_by(observed, "taxonomy_kind"),
        "observed_with_any_mapping": len(mapped_ids),
        "observed_with_confirmed_map": len(confirmed_mapped_ids),
        "observed_unmapped": len(observed) - len(mapped_ids),
        "mappings_total": len(mappings),
        "mappings_by_status": _count_by(mappings, "status"),
        "mappings_by_action": _count_by(mappings, "action"),
        "mappings_by_decided_by_prefix": _count_by_prefix(mappings, "decided_by", sep=":"),
        "concepts_total": len(concepts),
        "concepts_with_zero_mapped_observed": _concepts_without_mapping(concepts, mappings),
        "concept_observed_count": _group_count_map_by_concept(mappings),
    }


def _coverage_rows(conn) -> List[Dict[str, Any]]:
    """observed_item別の被覆詳細行（CSV出力用）。"""
    observed = [dict(row) for row in conn.execute("select * from observed_items order by observed_item_id")]
    mappings = [dict(row) for row in conn.execute("select * from concept_mappings")]

    by_observed: Dict[str, List[Dict[str, Any]]] = {}
    for m in mappings:
        oid = str(m.get("observed_item_id") or "")
        if not oid:
            continue
        by_observed.setdefault(oid, []).append(m)

    rows: List[Dict[str, Any]] = []
    for item in observed:
        oid = str(item.get("observed_item_id") or "")
        item_mappings = by_observed.get(oid, [])
        confirmed_map = [m for m in item_mappings if str(m.get("status") or "") == "confirmed" and str(m.get("action") or "") == "map"]
        rows.append(
            {
                "observed_item_id": oid,
                "item_kind": item.get("item_kind"),
                "taxonomy_kind": item.get("taxonomy_kind"),
                "label_ja": item.get("label_ja"),
                "mapping_count": len(item_mappings),
                "has_confirmed_map": bool(confirmed_map),
                "mapped_concept_ids": ";".join(sorted({str(m.get("concept_id") or "") for m in confirmed_map if m.get("concept_id")})),
                "proposed_only": bool(item_mappings) and not confirmed_map,
            }
        )
    return rows


def _summary_markdown(summary: Dict[str, Any]) -> str:
    lines = ["# semantics coverage summary", ""]
    lines.append(f"- observed_total: {summary.get('observed_total', 0)}")
    lines.append(f"- observed_with_any_mapping: {summary.get('observed_with_any_mapping', 0)}")
    lines.append(f"- observed_with_confirmed_map: {summary.get('observed_with_confirmed_map', 0)}")
    lines.append(f"- observed_unmapped: {summary.get('observed_unmapped', 0)}")
    lines.append(f"- mappings_total: {summary.get('mappings_total', 0)}")
    lines.append(f"- concepts_total: {summary.get('concepts_total', 0)}")
    lines.append("")
    lines.append("## observed_by_item_kind")
    for k, v in sorted((summary.get("observed_by_item_kind") or {}).items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## observed_by_taxonomy_kind")
    for k, v in sorted((summary.get("observed_by_taxonomy_kind") or {}).items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## mappings_by_status")
    for k, v in sorted((summary.get("mappings_by_status") or {}).items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## mappings_by_action")
    for k, v in sorted((summary.get("mappings_by_action") or {}).items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## mappings_by_decided_by_prefix")
    for k, v in sorted((summary.get("mappings_by_decided_by_prefix") or {}).items()):
        lines.append(f"- {k}: {v}")
    lines.append("")
    zero_mapped = summary.get("concepts_with_zero_mapped_observed") or []
    lines.append(f"## concepts_with_zero_mapped_observed ({len(zero_mapped)})")
    for concept_id in zero_mapped:
        lines.append(f"- {concept_id}")
    return "\n".join(lines) + "\n"


def build_and_write_coverage_report(root: Path) -> Dict[str, Any]:
    """semantics.db を読み取り、data/reports/ にレポート3種を書き出す。読み取り専用。"""
    conn = semantics_store.connect(root)
    try:
        summary = build_coverage_report(conn)
        rows = _coverage_rows(conn)
    finally:
        conn.close()

    reports_dir = root / REPORTS_DIR
    ensure_parent(reports_dir / "placeholder")

    csv_path = reports_dir / COVERAGE_CSV_FILENAME
    json_path = reports_dir / SUMMARY_JSON_FILENAME
    md_path = reports_dir / SUMMARY_MD_FILENAME

    write_table(csv_path, rows)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_summary_markdown(summary), encoding="utf-8")

    return {
        "summary": summary,
        "report_csv_path": csv_path,
        "report_json_path": json_path,
        "report_md_path": md_path,
    }
