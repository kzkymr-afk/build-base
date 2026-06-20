from __future__ import annotations

import argparse
import contextlib
import io
import os
from pathlib import Path
from typing import Callable, Dict, Optional

from yuho_auto_extract import __main__ as cli
from yuho_auto_extract.io_utils import prefer_existing_table, read_table, write_table
from yuho_auto_extract.services import automation, review_learning_impact, reviews, rule_candidates


LogCallback = Callable[[str], None]


def run_all(root: Path, log: Optional[LogCallback] = None, fiscal_years: Optional[list[int]] = None) -> int:
    """Run the same full pipeline as the CLI without spawning a subprocess."""
    _prepare(root)
    args = argparse.Namespace(db="data/intermediate/edinet.db", fiscal_years=fiscal_years or [])
    return _call("run-all", cli.cmd_run_all, root, args, log)


def reextract_with_review(root: Path, log: Optional[LogCallback] = None) -> int:
    """Re-extract using current rules, then apply saved human review decisions."""
    _prepare(root)
    before_coverage = review_learning_impact.capture_field_coverage(root)
    learning_result = _sync_review_learning_rules(root, log)
    code = _call("locate-sections", cli.cmd_locate_sections, root, argparse.Namespace(), log)
    if code:
        return code
    code = run_all(root, log=log)
    if code:
        return code
    if log:
        log("[saved-review] apply review_resolved.csv after re-extraction")
    code = apply_review(root, log=log)
    if code:
        return code
    impact = review_learning_impact.write_review_learning_impact(root, before_coverage, learning_result)
    summary = impact.get("summary", {})
    if log:
        log(
            "[review-learning-impact] "
            f"improved_fields={summary.get('improved_fields', 0)} "
            f"total_filled_delta={summary.get('total_filled_delta', 0)} "
            f"path={impact.get('markdown_path', '')}"
        )
    return 0


def annual_refresh(
    root: Path,
    log: Optional[LogCallback] = None,
    fiscal_year: Optional[int] = None,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Add the next annual company-year rows, fetch new EDINET documents, and rebuild outputs."""
    _prepare(root)
    status = automation.automation_status(root, fiscal_year=fiscal_year)
    target_fiscal_year = fiscal_year or status.get("target_fiscal_year")
    if not target_fiscal_year:
        if log:
            log("[annual-refresh] no active annual filing window; pass --fiscal-year or wait for the configured window")
        automation.write_annual_refresh_summary(root, {"status": "blocked", "reason": "target_fiscal_year_missing", "plan": status})
        return 2

    roll_forward_plan = automation.roll_forward_company_years(root, int(target_fiscal_year), dry_run=True)
    summary = {
        "status": "dry_run" if dry_run else "running",
        "target_fiscal_year": target_fiscal_year,
        "force": force,
        "dry_run": dry_run,
        "plan": status,
        "roll_forward": roll_forward_plan,
        "steps": [],
    }
    if dry_run:
        if log:
            log(
                "[annual-refresh] roll-forward "
                f"fiscal_year={target_fiscal_year} added={roll_forward_plan.get('added_rows', 0)} "
                f"existing={roll_forward_plan.get('existing_rows', 0)} dry_run=True"
            )
        review_gate = status.get("review_gate", {})
        summary["would_block"] = not force and not review_gate.get("ready", False)
        summary["blocking_reasons"] = review_gate.get("blocking_reasons", [])
        automation.write_annual_refresh_summary(root, summary)
        if log:
            if summary["would_block"]:
                log("[annual-refresh] dry run: real run would stop at review gate")
                log("[annual-refresh] blocking reasons: " + (", ".join(summary["blocking_reasons"]) or "not ready"))
            log("[annual-refresh] dry run completed; no EDINET requests were sent")
        return 0

    review_gate = status.get("review_gate", {})
    if not force and not review_gate.get("ready", False):
        reasons = review_gate.get("blocking_reasons", [])
        if log:
            log("[annual-refresh] review gate blocked: " + (", ".join(reasons) or "not ready"))
            log("[annual-refresh] use force only when you intentionally want to collect before review completion")
        automation.write_annual_refresh_summary(
            root,
            {"status": "blocked", "reason": "review_gate", "target_fiscal_year": target_fiscal_year, "plan": status},
        )
        return 2

    if not os.getenv("EDINET_API_KEY"):
        if log:
            log("[annual-refresh] EDINET_API_KEY is missing; annual collection did not start")
        automation.write_annual_refresh_summary(
            root,
            {"status": "blocked", "reason": "edinet_api_key_missing", "target_fiscal_year": target_fiscal_year, "plan": status},
        )
        return 2

    roll_forward = automation.roll_forward_company_years(root, int(target_fiscal_year), dry_run=False)
    summary["roll_forward"] = roll_forward
    if log:
        log(
            "[annual-refresh] roll-forward "
            f"fiscal_year={target_fiscal_year} added={roll_forward.get('added_rows', 0)} "
            f"existing={roll_forward.get('existing_rows', 0)} dry_run=False"
        )

    previous_index = _read_existing(root / "data" / "intermediate" / "document_index.parquet")
    previous_targets = _read_existing(root / "data" / "intermediate" / "target_documents.parquet")
    previous_manifest = _read_existing(root / "data" / "raw" / "download_manifest.parquet")

    year_args = argparse.Namespace(fiscal_years=[int(target_fiscal_year)])
    for name, func, args in [
        ("index-annual", cli.cmd_index_annual, year_args),
        ("resolve", cli.cmd_resolve, year_args),
    ]:
        code = _call(name, func, root, args, log)
        summary["steps"].append({"name": name, "exit_code": code})
        if code:
            summary["status"] = "failed"
            automation.write_annual_refresh_summary(root, summary)
            return code

    new_index = _read_existing(root / "data" / "intermediate" / "document_index.parquet")
    index_merge = automation.merge_table_by_key(
        root,
        "data/intermediate/document_index.parquet",
        previous_index + new_index,
        ["docID", "fileDate"],
    )
    if log:
        log(f"[annual-refresh] merged document_index rows={index_merge.get('merged_rows', 0)}")
    summary["document_index_merge"] = index_merge

    new_targets = _read_existing(root / "data" / "intermediate" / "target_documents.parquet")
    new_targets_path = root / "data" / "intermediate" / "target_documents_annual_refresh.parquet"
    write_table(new_targets_path, new_targets)
    target_merge = automation.merge_table_by_key(
        root,
        "data/intermediate/target_documents.parquet",
        previous_targets + new_targets,
        ["company_year_id"],
    )
    if log:
        log(f"[annual-refresh] merged target_documents rows={target_merge.get('merged_rows', 0)}")
    summary["target_documents_merge"] = target_merge

    code = _call(
        "download",
        cli.cmd_download,
        root,
        argparse.Namespace(target_documents="data/intermediate/target_documents_annual_refresh.parquet"),
        log,
    )
    summary["steps"].append({"name": "download", "exit_code": code})
    if code:
        summary["status"] = "failed"
        automation.write_annual_refresh_summary(root, summary)
        return code

    new_manifest = _read_existing(root / "data" / "raw" / "download_manifest.parquet")
    manifest_merge = automation.merge_table_by_key(
        root,
        "data/raw/download_manifest.parquet",
        previous_manifest + new_manifest,
        ["docID", "type"],
    )
    summary["download_manifest_merge"] = manifest_merge

    code = reextract_with_review(root, log=log)
    summary["steps"].append({"name": "reextract-with-review", "exit_code": code})
    summary["status"] = "succeeded" if code == 0 else "failed"
    automation.write_annual_refresh_summary(root, summary)
    return code


def _sync_review_learning_rules(root: Path, log: Optional[LogCallback] = None) -> Dict[str, object]:
    generated = rule_candidates.generate_rule_candidates(root)
    status_counts = generated.get("status_counts", {})
    active_rows = generated.get("rows", [])
    auto_field_ids = [
        str(row.get("field_id") or "").strip()
        for row in active_rows
        if str(row.get("confidence") or "").strip().lower() == "high"
        and str(row.get("needs_manual_check") or "").strip().lower() == "no"
    ]
    auto_field_ids = [field_id for field_id in dict.fromkeys(auto_field_ids) if field_id]
    if log:
        log(
            "[review-learning] candidates "
            f"active={status_counts.get('active', 0)} "
            f"applied={status_counts.get('applied', 0)} "
            f"all={status_counts.get('all', 0)}"
        )
    if not auto_field_ids:
        if log:
            log("[review-learning] no high-confidence candidates to auto-apply")
        return {"generated": generated, "auto_field_ids": [], "applied_result": {}}
    result = rule_candidates.apply_rule_candidates(root, auto_field_ids)
    if log:
        log(
            "[review-learning] auto-applied "
            f"fields={','.join(auto_field_ids)} "
            f"updated_sections={','.join(result.get('updated_sections', [])) or '-'}"
        )
    return {"generated": generated, "auto_field_ids": auto_field_ids, "applied_result": result}


def rebuild_report(root: Path, log: Optional[LogCallback] = None) -> int:
    """Regenerate run report, field coverage, and AI bundle."""
    _prepare(root)
    return _call("report", cli.cmd_report, root, argparse.Namespace(), log)


def apply_review(root: Path, log: Optional[LogCallback] = None, reviewed: str = "data/review/review_resolved.csv") -> int:
    """Apply human review decisions and rebuild downstream outputs."""
    _prepare(root)
    steps = [
        ("export-final", cli.cmd_export_final, argparse.Namespace(reviewed=reviewed)),
        ("build-analysis", cli.cmd_build_analysis, argparse.Namespace()),
        ("report", cli.cmd_report, argparse.Namespace()),
    ]
    for name, func, args in steps:
        code = _call(name, func, root, args, log)
        if code:
            return code
        if name == "export-final":
            result = reviews.mark_resolved_reviews_applied(root, reviewed=reviewed)
            if log:
                log(f"[review-applied] marked={result['updated']} total={result['total']}")
    return 0


def build_ai_bundle_only(root: Path, log: Optional[LogCallback] = None) -> int:
    _prepare(root)
    return _call("build-ai-bundle", cli.cmd_build_ai_bundle, root, argparse.Namespace(), log)


def build_algorithm_audit(root: Path, log: Optional[LogCallback] = None) -> int:
    _prepare(root)
    return _call("build-algorithm-audit", cli.cmd_build_algorithm_audit, root, argparse.Namespace(), log)


def _read_existing(path: Path) -> list[dict]:
    actual = prefer_existing_table(path)
    return read_table(actual) if actual.exists() else []


def _prepare(root: Path) -> None:
    cli.configure_logging()
    cli._load_env_file(root)


def _call(name: str, func: Callable[[Path, argparse.Namespace], int], root: Path, args: argparse.Namespace, log: Optional[LogCallback]) -> int:
    if log:
        log(f"[{name}] start")
    capture = _LineCapture(log)
    with contextlib.redirect_stdout(capture):
        code = func(root, args)
    capture.flush()
    if log:
        log(f"[{name}] done exit={code}")
    return code


class _LineCapture(io.TextIOBase):
    def __init__(self, log: Optional[LogCallback]) -> None:
        self._log = log
        self._buffer = ""
        self._emitting = False

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        if not value:
            return 0
        if self._emitting:
            return len(value)
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line)
        return len(value)

    def flush(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""

    def _emit(self, line: str) -> None:
        if self._log and line.strip():
            self._emitting = True
            try:
                self._log(line.rstrip())
            finally:
                self._emitting = False
