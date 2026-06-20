from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.services import rule_candidates


IMPACT_COLUMNS = [
    "field_id",
    "field_name_ja",
    "before_filled_company_years",
    "after_filled_company_years",
    "filled_delta",
    "before_coverage_pct",
    "after_coverage_pct",
    "saved_review_count",
    "review_queue_after",
    "rule_candidate_status",
    "rule_evidence_count",
    "rule_confidence",
    "rule_needs_manual_check",
    "auto_applied",
    "applied_columns",
    "applied_sections",
    "recommended_action",
]


def capture_field_coverage(root: Path) -> Dict[str, Dict[str, Any]]:
    path = root / "data" / "final" / "field_coverage.csv"
    if not path.exists():
        return {}
    return {
        str(row.get("field_id", "")).strip(): dict(row)
        for row in read_table(path)
        if str(row.get("field_id", "")).strip()
    }


def write_review_learning_impact(
    root: Path,
    before_coverage: Mapping[str, Mapping[str, Any]],
    learning_result: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    learning_result = learning_result or {}
    after_coverage = capture_field_coverage(root)
    fields = _field_rows(root)
    candidate_by_field = _candidate_rows(root)
    saved_counts = _count_by_field(_read_optional(root / "data" / "review" / "review_resolved.csv"))
    queue_counts = _count_by_field(_read_optional(root / "data" / "review" / "review_queue.csv"))
    applied_by_field = _applied_by_field(learning_result)
    auto_field_ids = set(str(field_id) for field_id in learning_result.get("auto_field_ids", []) if str(field_id))

    rows: List[Dict[str, Any]] = []
    field_ids = _ordered_field_ids(fields, before_coverage, after_coverage, candidate_by_field, saved_counts, queue_counts)
    for field_id in field_ids:
        field = fields.get(field_id, {})
        before = before_coverage.get(field_id, {})
        after = after_coverage.get(field_id, {})
        before_filled = _as_int(before.get("filled_company_years"))
        after_filled = _as_int(after.get("filled_company_years"))
        delta = after_filled - before_filled if before else 0
        candidate = candidate_by_field.get(field_id, {})
        applied = applied_by_field.get(field_id, {})
        rows.append(
            {
                "field_id": field_id,
                "field_name_ja": field.get("field_name_ja") or after.get("field_name_ja") or before.get("field_name_ja") or field_id,
                "before_filled_company_years": before_filled if before else "",
                "after_filled_company_years": after_filled if after else "",
                "filled_delta": delta,
                "before_coverage_pct": _coverage_pct(before),
                "after_coverage_pct": _coverage_pct(after),
                "saved_review_count": saved_counts.get(field_id, 0),
                "review_queue_after": queue_counts.get(field_id, 0),
                "rule_candidate_status": candidate.get("candidate_status", ""),
                "rule_evidence_count": candidate.get("evidence_count", ""),
                "rule_confidence": candidate.get("confidence", ""),
                "rule_needs_manual_check": candidate.get("needs_manual_check", ""),
                "auto_applied": "yes" if field_id in auto_field_ids else "",
                "applied_columns": ";".join(applied.get("columns", [])),
                "applied_sections": ";".join(applied.get("sections", [])),
                "recommended_action": candidate.get("recommended_action", ""),
            }
        )

    rows = sorted(rows, key=_impact_sort_key)
    csv_path = root / "data" / "review" / "review_learning_impact.csv"
    md_path = root / "data" / "review" / "review_learning_impact.md"
    write_table(csv_path, [{column: row.get(column, "") for column in IMPACT_COLUMNS} for row in rows])
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(_impact_markdown(rows, learning_result), encoding="utf-8")
    summary = _impact_summary(rows, learning_result)
    return {
        "path": str(csv_path),
        "markdown_path": str(md_path),
        "rows": len(rows),
        "summary": summary,
    }


def _field_rows(root: Path) -> Dict[str, Dict[str, Any]]:
    path = root / "config" / "field_definition.csv"
    if not path.exists():
        return {}
    return {
        str(row.get("field_id", "")).strip(): dict(row)
        for row in read_table(path)
        if str(row.get("field_id", "")).strip()
    }


def _candidate_rows(root: Path) -> Dict[str, Dict[str, Any]]:
    rows = rule_candidates.read_rule_candidates(root, candidate_status="all")
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        field_id = str(row.get("field_id", "")).strip()
        if field_id:
            out[field_id] = row
    return out


def _read_optional(path: Path) -> List[Dict[str, Any]]:
    return read_table(path) if path.exists() else []


def _count_by_field(rows: Iterable[Mapping[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        field_id = str(row.get("field_id", "")).strip()
        if field_id:
            counts[field_id] += 1
    return counts


def _applied_by_field(learning_result: Mapping[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    applied: Dict[str, Dict[str, List[str]]] = {}
    result = learning_result.get("applied_result", {})
    if not isinstance(result, Mapping):
        return applied
    for item in result.get("updated_fields", []) or []:
        field_id = str(item.get("field_id", "")).strip()
        if not field_id:
            continue
        applied.setdefault(field_id, {"columns": [], "sections": []})["columns"].extend(
            [str(column) for column in item.get("columns", []) if str(column)]
        )
    for section in result.get("updated_sections", []) or []:
        field_id = str(section).replace("review_", "", 1) if str(section).startswith("review_") else ""
        if field_id:
            applied.setdefault(field_id, {"columns": [], "sections": []})["sections"].append(str(section))
    return applied


def _ordered_field_ids(
    fields: Mapping[str, Any],
    *collections: Mapping[str, Any] | Counter[str],
) -> List[str]:
    field_ids = list(fields)
    for collection in collections:
        for field_id in collection:
            if field_id not in field_ids:
                field_ids.append(field_id)
    return field_ids


def _coverage_pct(row: Mapping[str, Any]) -> str:
    if not row:
        return ""
    value = _as_float(row.get("coverage_pct"))
    return f"{value:.4f}"


def _as_int(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _impact_sort_key(row: Mapping[str, Any]) -> tuple[int, int, str]:
    return (-_as_int(row.get("filled_delta")), -_as_int(row.get("saved_review_count")), str(row.get("field_id", "")))


def _impact_summary(rows: Sequence[Mapping[str, Any]], learning_result: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "improved_fields": sum(1 for row in rows if _as_int(row.get("filled_delta")) > 0),
        "worsened_fields": sum(1 for row in rows if _as_int(row.get("filled_delta")) < 0),
        "total_filled_delta": sum(_as_int(row.get("filled_delta")) for row in rows),
        "auto_applied_candidates": len([field_id for field_id in learning_result.get("auto_field_ids", []) if str(field_id)]),
        "active_candidates": learning_result.get("generated", {}).get("status_counts", {}).get("active", ""),
        "applied_candidates": learning_result.get("generated", {}).get("status_counts", {}).get("applied", ""),
    }


def _impact_markdown(rows: Sequence[Mapping[str, Any]], learning_result: Mapping[str, Any]) -> str:
    summary = _impact_summary(rows, learning_result)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    notable = [row for row in rows if _as_int(row.get("filled_delta")) or _as_int(row.get("saved_review_count")) or row.get("rule_candidate_status")]
    notable = notable[:40]
    lines = [
        "# Review Learning Impact",
        "",
        f"- generated_at_utc: {generated_at}",
        f"- improved_fields: {summary['improved_fields']}",
        f"- total_filled_delta: {summary['total_filled_delta']}",
        f"- auto_applied_candidates: {summary['auto_applied_candidates']}",
        f"- active_candidates: {summary['active_candidates']}",
        f"- applied_candidates: {summary['applied_candidates']}",
        "",
        "## Field Deltas",
        "",
        "| field_id | field_name_ja | before | after | delta | evidence | candidate | queue_after |",
        "|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    if not notable:
        lines.append("| - | - | - | - | - | - | - | - |")
    for row in notable:
        candidate = "/".join(
            part
            for part in [
                str(row.get("rule_candidate_status", "")),
                str(row.get("rule_confidence", "")),
                str(row.get("rule_needs_manual_check", "")),
            ]
            if part
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("field_id", "")),
                    str(row.get("field_name_ja", "")),
                    str(row.get("before_filled_company_years", "")),
                    str(row.get("after_filled_company_years", "")),
                    str(row.get("filled_delta", "")),
                    str(row.get("rule_evidence_count", "")),
                    candidate or "-",
                    str(row.get("review_queue_after", "")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Reading",
            "",
            "- `delta` が正なら、レビュー学習後の再取得で取得済み会社年度が増えています。",
            "- `evidence` は保存済みレビューから作られた証跡数です。",
            "- `candidate` は `status/confidence/needs_manual_check` です。",
            "- `queue_after` が残っている項目は、まだレビューまたは抽出ロジック強化が必要です。",
            "",
        ]
    )
    return "\n".join(lines)
