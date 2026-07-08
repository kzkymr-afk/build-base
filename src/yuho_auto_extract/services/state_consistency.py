from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from yuho_auto_extract.exporter import _wide_value_score, build_wide_values
from yuho_auto_extract.io_utils import is_blankish, prefer_existing_table, read_table

from . import datasets, mapping_review, reconciliation, semantics_store


APPLIED_DONE_STATUSES = {"applied", "rejected", "not_applicable"}
REVIEW_DECISIONS = {"accept", "correct", "reject", "not_applicable"}
FINDING_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "info": 4}


def run_state_consistency_audit(
    root: Path,
    *,
    sample_limit: int = 10,
    include_preview: bool = True,
    include_static: bool = True,
) -> Dict[str, Any]:
    root = root.resolve()
    resolved_rows = _read_optional(root / "data" / "review" / "review_resolved.csv")
    queue_rows = _read_optional(root / "data" / "review" / "review_queue.csv")
    final_rows = _read_optional(root / "data" / "final" / "final_master_long.csv")
    wide_rows = _read_optional(root / "data" / "final" / "final_master_wide.csv")
    audit_rows = _read_optional(root / "data" / "final" / "source_audit.csv")
    findings: List[Dict[str, Any]] = []

    final_by_key = _by_key(final_rows)
    wide_by_company_year = {
        str(row.get("company_year_id") or ""): row
        for row in wide_rows
        if row.get("company_year_id")
    }
    audit_keys = _key_set(audit_rows)

    findings.extend(_check_duplicate_keys("review_resolved_duplicate_keys", "review_resolved.csv", resolved_rows, sample_limit))
    findings.extend(_check_final_duplicate_ambiguity(final_rows, root, sample_limit))
    findings.extend(_check_review_decisions(resolved_rows, sample_limit))
    findings.extend(_check_review_application(resolved_rows, final_by_key, sample_limit))
    findings.extend(_check_final_audit_contract(final_rows, audit_rows, audit_keys, sample_limit))
    findings.extend(_check_wide_long_contract(root, final_rows, wide_by_company_year, sample_limit))
    findings.extend(_check_cell_detail_contract(root, resolved_rows, sample_limit))
    findings.extend(_check_semantics_mirrors(root, sample_limit))
    if include_preview:
        findings.extend(_check_action_previews(root, sample_limit))
    if include_static:
        findings.extend(_check_falsey_patterns(root, sample_limit))

    review_status_counts = Counter(_clean_status(row.get("applied_status")) or "blank" for row in resolved_rows)
    review_decision_counts = Counter(str(row.get("review_decision") or "").strip().lower() or "blank" for row in resolved_rows)
    severity_counts = Counter(str(finding["severity"]) for finding in findings)
    p0_p1_count = sum(1 for finding in findings if finding["severity"] in {"P0", "P1"})

    findings.sort(key=lambda row: (FINDING_ORDER.get(str(row.get("severity")), 99), str(row.get("finding_id"))))
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": str(root),
        "status": "needs_attention" if p0_p1_count else "pass",
        "p0_p1_count": p0_p1_count,
        "finding_count": len(findings),
        "severity_counts": dict(severity_counts),
        "scoreboard": {
            "review_resolved_rows": len(resolved_rows),
            "review_queue_rows": len(queue_rows),
            "final_master_long_rows": len(final_rows),
            "final_master_wide_rows": len(wide_rows),
            "source_audit_rows": len(audit_rows),
            "saved_not_applied_reviews": sum(
                1 for row in resolved_rows if _clean_status(row.get("applied_status")) not in APPLIED_DONE_STATUSES
            ),
            "review_applied_status_counts": dict(review_status_counts),
            "review_decision_counts": dict(review_decision_counts),
        },
        "findings": findings,
    }


def format_markdown(result: Dict[str, Any]) -> str:
    lines = [
        "# BuildBase State Consistency Audit",
        "",
        f"- generated_at_utc: {result.get('generated_at_utc', '')}",
        f"- status: {result.get('status', '')}",
        f"- p0_p1_count: {result.get('p0_p1_count', 0)}",
        f"- finding_count: {result.get('finding_count', 0)}",
        "",
        "## Scoreboard",
        "",
    ]
    for key, value in (result.get("scoreboard") or {}).items():
        if isinstance(value, dict):
            lines.append(f"- {key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            lines.append(f"- {key}: {value}")
    lines.extend(["", "## Findings", ""])
    findings = result.get("findings") or []
    if not findings:
        lines.append("- No findings.")
    for finding in findings:
        lines.extend(
            [
                f"### {finding.get('severity')} {finding.get('finding_id')}",
                "",
                str(finding.get("title") or ""),
                "",
                f"- count: {finding.get('count', 0)}",
                f"- detail: {finding.get('detail', '')}",
            ]
        )
        samples = finding.get("samples") or []
        if samples:
            lines.append("- samples:")
            for sample in samples[:10]:
                lines.append(f"  - `{json.dumps(sample, ensure_ascii=False, sort_keys=True, default=str)}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_audit_outputs(result: Dict[str, Any], json_path: Path | None = None, markdown_path: Path | None = None) -> Dict[str, str]:
    paths: Dict[str, str] = {}
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        paths["json_path"] = str(json_path)
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(format_markdown(result), encoding="utf-8")
        paths["markdown_path"] = str(markdown_path)
    return paths


def _read_optional(path: Path) -> List[Dict[str, Any]]:
    return read_table(path) if path.exists() else []


def _key(row: Dict[str, Any]) -> Tuple[str, str]:
    company_year_id = str(row.get("company_year_id") or "").strip()
    field_id = str(row.get("field_id") or row.get("concept_id") or "").strip()
    return (company_year_id, field_id) if company_year_id and field_id else ("", "")


def _key_set(rows: Iterable[Dict[str, Any]]) -> set[Tuple[str, str]]:
    return {_key(row) for row in rows if _key(row) != ("", "")}


def _by_key(rows: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = _key(row)
        if key != ("", ""):
            out[key] = row
    return out


def _finding(
    finding_id: str,
    severity: str,
    title: str,
    detail: str,
    samples: Sequence[Dict[str, Any]],
    *,
    count: int | None = None,
) -> Dict[str, Any]:
    return {
        "finding_id": finding_id,
        "severity": severity,
        "title": title,
        "detail": detail,
        "count": len(samples) if count is None else count,
        "samples": list(samples),
    }


def _check_duplicate_keys(finding_id: str, label: str, rows: Sequence[Dict[str, Any]], sample_limit: int) -> List[Dict[str, Any]]:
    counts: Counter[Tuple[str, str]] = Counter(_key(row) for row in rows if _key(row) != ("", ""))
    duplicates = [
        {"company_year_id": key[0], "field_id": key[1], "count": count}
        for key, count in counts.items()
        if count > 1
    ]
    if not duplicates:
        return []
    return [
        _finding(
            finding_id,
            "P1",
            f"{label} has duplicate company_year_id/field_id keys.",
            "同じセルに複数行があると、UI表示、反映、golden化のどれが採用値か曖昧になります。",
            duplicates[:sample_limit],
            count=len(duplicates),
        )
    ]


def _check_review_decisions(rows: Sequence[Dict[str, Any]], sample_limit: int) -> List[Dict[str, Any]]:
    invalid = [
        _sample(row, ["company_year_id", "field_id", "review_decision"])
        for row in rows
        if str(row.get("review_decision") or "").strip().lower() not in REVIEW_DECISIONS
    ]
    if not invalid:
        return []
    return [
        _finding(
            "invalid_review_decision",
            "P1",
            "review_resolved.csv contains invalid review decisions.",
            "レビュー判断が既知値でない行は反映処理やUI分類から落ちる可能性があります。",
            invalid[:sample_limit],
            count=len(invalid),
        )
    ]


def _check_review_application(
    resolved_rows: Sequence[Dict[str, Any]],
    final_by_key: Dict[Tuple[str, str], Dict[str, Any]],
    sample_limit: int,
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    applied_missing: List[Dict[str, Any]] = []
    applied_value_mismatch: List[Dict[str, Any]] = []
    not_applicable_present: List[Dict[str, Any]] = []
    rejected_present: List[Dict[str, Any]] = []
    for row in resolved_rows:
        key = _key(row)
        status = _clean_status(row.get("applied_status"))
        decision = str(row.get("review_decision") or "").strip().lower()
        final_row = final_by_key.get(key)
        if status == "applied":
            if final_row is None:
                applied_missing.append(_sample(row, ["company_year_id", "field_id", "review_decision", "applied_status", "applied_value"]))
                continue
            expected = _expected_review_value(row)
            final_value = _first_nonblank(final_row.get("value"), final_row.get("value_normalized"))
            applied_value = row.get("applied_value")
            if not _blank_or_equal(expected, final_value) or not _blank_or_equal(applied_value, final_value):
                applied_value_mismatch.append(
                    {
                        **_sample(row, ["company_year_id", "field_id", "review_decision", "applied_value"]),
                        "expected": expected,
                        "final_value": final_value,
                    }
                )
        if status == "not_applicable" and final_row is not None and not is_blankish(_first_nonblank(final_row.get("value"), final_row.get("value_normalized"))):
            not_applicable_present.append(_sample(row, ["company_year_id", "field_id", "review_decision", "applied_status"]))
        if status == "rejected" and final_row is not None and not is_blankish(_first_nonblank(final_row.get("value"), final_row.get("value_normalized"))):
            rejected_present.append(_sample(row, ["company_year_id", "field_id", "review_decision", "applied_status"]))
        if decision == "not_applicable" and status == "applied":
            not_applicable_present.append(_sample(row, ["company_year_id", "field_id", "review_decision", "applied_status"]))
    if applied_missing:
        findings.append(
            _finding(
                "applied_review_missing_final",
                "P0",
                "Applied reviews are missing from final_master_long.",
                "反映済みと表示されるレビューが最終表に存在しません。",
                applied_missing[:sample_limit],
                count=len(applied_missing),
            )
        )
    if applied_value_mismatch:
        findings.append(
            _finding(
                "applied_review_value_mismatch",
                "P1",
                "Applied review values differ from final values.",
                "保存済みレビューの反映値と最終表の値が一致しません。",
                applied_value_mismatch[:sample_limit],
                count=len(applied_value_mismatch),
            )
        )
    if not_applicable_present:
        findings.append(
            _finding(
                "not_applicable_review_present_in_final",
                "P1",
                "Not-applicable reviews still have final values.",
                "対象外として反映済みのセルが最終表に残っています。",
                not_applicable_present[:sample_limit],
                count=len(not_applicable_present),
            )
        )
    if rejected_present:
        findings.append(
            _finding(
                "rejected_review_present_in_final",
                "P1",
                "Rejected reviews still have final values.",
                "却下済みとして反映済みのセルが最終表に残っています。",
                rejected_present[:sample_limit],
                count=len(rejected_present),
            )
        )
    return findings


def _check_final_audit_contract(
    final_rows: Sequence[Dict[str, Any]],
    audit_rows: Sequence[Dict[str, Any]],
    audit_keys: set[Tuple[str, str]],
    sample_limit: int,
) -> List[Dict[str, Any]]:
    final_keys = _key_set(row for row in final_rows if not is_blankish(_first_nonblank(row.get("value"), row.get("value_normalized"))))
    missing_audit = [
        {"company_year_id": key[0], "field_id": key[1]}
        for key in sorted(final_keys - audit_keys)
    ]
    audit_without_final = [
        {"company_year_id": key[0], "field_id": key[1]}
        for key in sorted(audit_keys - final_keys)
    ]
    findings: List[Dict[str, Any]] = []
    if missing_audit:
        findings.append(
            _finding(
                "final_value_missing_source_audit",
                "P1",
                "Final values are missing source_audit rows.",
                "最終表に値があるのに出典チェーンの入口がありません。",
                missing_audit[:sample_limit],
                count=len(missing_audit),
            )
        )
    if audit_without_final:
        findings.append(
            _finding(
                "source_audit_without_final_value",
                "P2",
                "source_audit contains rows without final values.",
                "監査行と最終表のキー集合がズレています。生成順序や古い監査ファイルの可能性があります。",
                audit_without_final[:sample_limit],
                count=len(audit_without_final),
            )
        )
    return findings


def _check_wide_long_contract(
    root: Path,
    final_rows: Sequence[Dict[str, Any]],
    wide_by_company_year: Dict[str, Dict[str, Any]],
    sample_limit: int,
) -> List[Dict[str, Any]]:
    company_year_path = prefer_existing_table(root / "config" / "company_year_master.csv")
    field_definition_path = prefer_existing_table(root / "config" / "field_definition.csv")
    company_year_master = read_table(company_year_path) if company_year_path.exists() else []
    field_definition = read_table(field_definition_path) if field_definition_path.exists() else []
    expected_wide = build_wide_values(final_rows, company_year_master, field_definition)
    expected_by_company_year = {
        str(row.get("company_year_id") or ""): row
        for row in expected_wide
        if row.get("company_year_id")
    }
    mismatches: List[Dict[str, Any]] = []
    missing_rows: List[Dict[str, Any]] = []
    all_company_year_ids = set(expected_by_company_year) | set(wide_by_company_year)
    for company_year_id in sorted(all_company_year_ids):
        expected_row = expected_by_company_year.get(company_year_id, {})
        wide_row = wide_by_company_year.get(company_year_id)
        if wide_row is None:
            missing_rows.append({"company_year_id": company_year_id})
            continue
        for field_id, expected_value in expected_row.items():
            if field_id in {"company_year_id"} or field_id not in wide_row:
                continue
            if is_blankish(expected_value) and is_blankish(wide_row.get(field_id, "")):
                continue
            if not _value_equal(expected_value, wide_row.get(field_id, "")):
                mismatches.append(
                    {
                        "company_year_id": company_year_id,
                        "field_id": field_id,
                        "expected_wide_value": expected_value,
                        "stored_wide_value": wide_row.get(field_id, ""),
                    }
                )
                if len(mismatches) >= sample_limit:
                    break
        if len(mismatches) >= sample_limit:
            break
    findings: List[Dict[str, Any]] = []
    if missing_rows:
        findings.append(
            _finding(
                "final_long_missing_wide_row",
                "P1",
                "Expected wide rows are missing from stored final_master_wide.",
                "exporterと同じロジックで再生成したwideにある会社年度が、保存済みwideにありません。",
                missing_rows[:sample_limit],
                count=len(missing_rows),
            )
        )
    if mismatches:
        findings.append(
            _finding(
                "final_long_wide_value_mismatch",
                "P1",
                "final_master_long and wide values differ.",
                "exporterと同じロジックで再生成したwide値と、保存済みwide値が一致しません。",
                mismatches[:sample_limit],
                count=len(mismatches),
            )
        )
    return findings


def _check_final_duplicate_ambiguity(final_rows: Sequence[Dict[str, Any]], root: Path, sample_limit: int) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in final_rows:
        key = _key(row)
        if key != ("", ""):
            grouped.setdefault(key, []).append(row)
    field_definition_path = prefer_existing_table(root / "config" / "field_definition.csv")
    field_definition = read_table(field_definition_path) if field_definition_path.exists() else []
    preferred_methods = {
        str(field.get("field_id") or ""): str(field.get("preferred_method") or "")
        for field in field_definition
        if field.get("field_id")
    }
    ambiguous: List[Dict[str, Any]] = []
    duplicate_count = 0
    for key, rows in grouped.items():
        if len(rows) <= 1:
            continue
        duplicate_count += 1
        scored: Dict[Tuple[int, int, int], set[str]] = {}
        for row in rows:
            score = _wide_value_score(row, preferred_methods.get(key[1], ""))
            value = str(_first_nonblank(row.get("value"), row.get("value_normalized")))
            if is_blankish(value):
                continue
            scored.setdefault(score, set()).add(value)
        if scored:
            top_score = max(scored)
            if len(scored[top_score]) > 1:
                ambiguous.append(
                    {
                        "company_year_id": key[0],
                        "field_id": key[1],
                        "top_score": list(top_score),
                        "values": sorted(scored[top_score]),
                    }
                )
    findings: List[Dict[str, Any]] = []
    if ambiguous:
        findings.append(
            _finding(
                "final_duplicate_top_score_ambiguous",
                "P2",
                "Duplicate final rows have equally scored different values.",
                "wide値の選定が行順に依存する可能性があります。",
                ambiguous[:sample_limit],
                count=len(ambiguous),
            )
        )
    if duplicate_count:
        findings.append(
            _finding(
                "final_master_long_duplicate_keys_observed",
                "info",
                "final_master_long contains duplicate cell keys by design.",
                "複数根拠行があるセルの件数です。top scoreが曖昧な場合のみP2以上で扱います。",
                [],
                count=duplicate_count,
            )
        )
    return findings


def _check_cell_detail_contract(root: Path, resolved_rows: Sequence[Dict[str, Any]], sample_limit: int) -> List[Dict[str, Any]]:
    samples = [row for row in resolved_rows if _key(row) != ("", "")][:sample_limit]
    failures: List[Dict[str, Any]] = []
    for row in samples:
        company_year_id, field_id = _key(row)
        try:
            detail = datasets.read_cell_detail(root, company_year_id, field_id)
        except Exception as exc:
            failures.append({"company_year_id": company_year_id, "field_id": field_id, "error": str(exc)})
            continue
        review_state = detail.get("review_state") or {}
        if not review_state.get("saved"):
            failures.append({"company_year_id": company_year_id, "field_id": field_id, "error": "review_state.saved is false"})
        if str(review_state.get("applied_status") or "") != str(row.get("applied_status") or ""):
            failures.append(
                {
                    "company_year_id": company_year_id,
                    "field_id": field_id,
                    "expected_applied_status": row.get("applied_status", ""),
                    "detail_applied_status": review_state.get("applied_status", ""),
                }
            )
    if not failures:
        return []
    return [
        _finding(
            "cell_detail_review_state_mismatch",
            "P1",
            "Cell detail does not reflect saved review state.",
            "review_resolved.csv の保存状態と Cell Workbench のセル詳細が一致しません。",
            failures[:sample_limit],
            count=len(failures),
        )
    ]


def _check_semantics_mirrors(root: Path, sample_limit: int) -> List[Dict[str, Any]]:
    if not semantics_store.semantics_db_path(root).exists():
        return []
    conn = semantics_store.connect(root)
    try:
        db_counts = {
            "cell_resolutions": len(semantics_store.fetch_cell_resolutions(conn)),
            "concept_mappings": len(semantics_store.fetch_concept_mappings(conn)),
        }
    finally:
        conn.close()
    csv_counts = {
        "cell_resolutions": len(_read_optional(root / "data" / "marts" / "semantics" / "cell_resolutions.csv")),
        "concept_mappings": len(_read_optional(root / "data" / "marts" / "semantics" / "concept_mappings.csv")),
    }
    mismatches = [
        {"table": key, "db_count": db_counts[key], "csv_count": csv_counts[key]}
        for key in sorted(db_counts)
        if db_counts[key] != csv_counts[key]
    ]
    if not mismatches:
        return []
    return [
        _finding(
            "semantics_csv_mirror_stale",
            "P2",
            "semantics.db and CSV mirrors have different row counts.",
            "永続DBとCSVミラーの件数が違います。UI/APIがDB、監査がCSVを見る場合に誤解が出ます。",
            mismatches[:sample_limit],
            count=len(mismatches),
        )
    ]


def _check_action_previews(root: Path, sample_limit: int) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    try:
        preview = mapping_review.bulk_reject_conflicting_proposals(root, reviewer="state_consistency_audit", preview=True)
        if preview.get("preview") is not True or int(preview.get("rejected") or 0) != 0:
            findings.append(
                _finding(
                    "mapping_bulk_preview_contract_broken",
                    "P1",
                    "Mapping bulk reject preview does not behave as a dry-run.",
                    "preview=True なのにpreviewフラグまたはrejected件数がdry-run契約を満たしていません。",
                    [preview],
                )
            )
    except Exception as exc:
        findings.append(
            _finding(
                "mapping_bulk_preview_error",
                "P1",
                "Mapping bulk reject preview raised an error.",
                "一括却下の事前確認が失敗しています。",
                [{"error": str(exc)}],
            )
        )
    try:
        groups = reconciliation.read_reconciliation_groups(root)
        applyable = [
            group for group in groups.get("groups", [])
            if group.get("apply_supported") is not False and str(group.get("group_id") or "").startswith(reconciliation.REASON_PREFIX)
        ]
        if applyable:
            resolved_before = len(_read_optional(root / "data" / "review" / "review_resolved.csv"))
            group = applyable[0]
            preview = reconciliation.apply_reconciliation_group(
                root,
                str(group.get("group_id") or ""),
                decision="accept",
                reviewer_note="state consistency preview",
                reviewer="state_consistency_audit",
                preview=True,
            )
            resolved_after = len(_read_optional(root / "data" / "review" / "review_resolved.csv"))
            if preview.get("preview") is not True or int(preview.get("applied_items") or 0) != 0 or resolved_before != resolved_after:
                findings.append(
                    _finding(
                        "reconciliation_preview_contract_broken",
                        "P1",
                        "Reconciliation group preview does not behave as a dry-run.",
                        "preview=True の照合グループ保存がdry-run契約を満たしていません。",
                        [{**preview, "resolved_before": resolved_before, "resolved_after": resolved_after}],
                    )
                )
    except Exception as exc:
        findings.append(
            _finding(
                "reconciliation_preview_error",
                "P1",
                "Reconciliation group preview raised an error.",
                "照合グループの事前確認が失敗しています。",
                [{"error": str(exc)}],
            )
        )
    return findings


def _check_falsey_patterns(root: Path, sample_limit: int) -> List[Dict[str, Any]]:
    patterns = [
        re.compile(r"float\([^\n]*\bor\b"),
        re.compile(r"int\([^\n]*\bor\b"),
        re.compile(r"\bor\s+(?:1\.0|100|0\.005)\b"),
        re.compile(r"\|\|\s*(?:0|1|0\.0|1\.0)\b"),
    ]
    paths = list((root / "src" / "yuho_auto_extract").rglob("*.py")) + list((root / "web" / "src").rglob("*.ts")) + list((root / "web" / "src").rglob("*.tsx"))
    samples: List[Dict[str, Any]] = []
    total = 0
    for path in sorted(paths):
        if "dist" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(lines, start=1):
            if any(pattern.search(line) for pattern in patterns):
                total += 1
                if len(samples) < sample_limit:
                    samples.append({"path": str(path.relative_to(root)), "line": lineno, "text": line.strip()})
    if not samples:
        return []
    return [
        _finding(
            "suspicious_falsey_numeric_defaults",
            "P3",
            "Numeric defaults still contain falsey-style expressions.",
            "0と未設定を区別すべき箇所が混じる可能性があります。各行は仕様確認対象です。",
            samples,
            count=total,
        )
    ]


def _expected_review_value(row: Dict[str, Any]) -> Any:
    decision = str(row.get("review_decision") or "").strip().lower()
    if decision == "correct":
        return row.get("corrected_value")
    if decision == "accept":
        return _first_nonblank(row.get("corrected_value"), row.get("extracted_value"))
    return ""


def _first_nonblank(*values: Any) -> Any:
    for value in values:
        if not is_blankish(value):
            return value
    return ""


def _blank_or_equal(left: Any, right: Any) -> bool:
    if is_blankish(left):
        return True
    return _value_equal(left, right)


def _value_equal(left: Any, right: Any) -> bool:
    if is_blankish(left) and is_blankish(right):
        return True
    left_text = str(left).replace(",", "").strip()
    right_text = str(right).replace(",", "").strip()
    try:
        return abs(float(left_text) - float(right_text)) <= max(1e-9, abs(float(left_text)) * 1e-9)
    except ValueError:
        return left_text == right_text


def _clean_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _sample(row: Dict[str, Any], columns: Sequence[str]) -> Dict[str, Any]:
    return {column: row.get(column, "") for column in columns}
