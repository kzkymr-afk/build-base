from __future__ import annotations

import argparse
import contextlib
import io
import os
from pathlib import Path
from typing import Callable, Dict, Optional

from yuho_auto_extract import __main__ as cli
from yuho_auto_extract.io_utils import prefer_existing_table, read_table, write_table
from yuho_auto_extract.services import automation, reviews


LogCallback = Callable[[str], None]


def run_all(root: Path, log: Optional[LogCallback] = None, fiscal_years: Optional[list[int]] = None) -> int:
    """Run the same full pipeline as the CLI without spawning a subprocess."""
    _prepare(root)
    args = argparse.Namespace(db="data/intermediate/edinet.db", fiscal_years=fiscal_years or [])
    return _call("run-all", cli.cmd_run_all, root, args, log)


def import_manual_technicians(root: Path, log: Optional[LogCallback] = None) -> int:
    """Import the curated Obsidian engineer-count note into the local mart."""
    _prepare(root)
    return _call("import-manual-technicians", cli.cmd_import_manual_technicians, root, argparse.Namespace(note_path=""), log)


def reextract_with_review(root: Path, log: Optional[LogCallback] = None) -> int:
    """Re-extract using current rules, then apply saved human review decisions."""
    _prepare(root)
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

    year_args = argparse.Namespace(fiscal_years=[int(target_fiscal_year)], period_type="annual")
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


def refresh_stock_prices(
    root: Path,
    log: Optional[LogCallback] = None,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Fetch monthly stock prices and merge them into the market mart."""
    _prepare(root)
    from yuho_auto_extract.services import market

    summary = market.refresh_stock_prices_if_due(root, force=force, dry_run=dry_run, log=log)
    status = str(summary.get("status") or "")
    return 0 if status in {"succeeded", "partial_success", "dry_run", "skipped"} else 1


def refresh_company_factbooks(
    root: Path,
    log: Optional[LogCallback] = None,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """Fetch configured company factbook/data book sources and merge the pilot mart."""
    _prepare(root)
    from yuho_auto_extract.services import company_factbooks

    summary = company_factbooks.refresh_company_factbooks(root, force=force, dry_run=dry_run, log=log)
    status = str(summary.get("status") or "")
    return 0 if status in {"succeeded", "partial_success", "dry_run"} else 1


def build_xbrl_fact_store(root: Path, log: Optional[LogCallback] = None) -> int:
    """Build normalized XBRL Fact Store files for reviewable extraction."""
    _prepare(root)
    args = argparse.Namespace(doc_id="", company_year_id="")
    return _call("xbrl-fact-store", cli.cmd_build_xbrl_fact_store, root, args, log)


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


def build_algorithm_audit(root: Path, log: Optional[LogCallback] = None) -> int:
    _prepare(root)
    return _call("build-algorithm-audit", cli.cmd_build_algorithm_audit, root, argparse.Namespace(), log)


def build_corroboration_report(root: Path, log: Optional[LogCallback] = None) -> int:
    """Build the read-only corroboration report for existing extracted cells."""
    _prepare(root)
    return _call("build-corroboration-report", cli.cmd_build_corroboration_report, root, argparse.Namespace(), log)


def build_algorithm_audit_findings(root: Path, log: Optional[LogCallback] = None) -> int:
    """P7: build the read-only deterministic algorithm audit findings."""
    _prepare(root)
    return _call("audit-findings", cli.cmd_audit_findings, root, argparse.Namespace(), log)


def corroborate(root: Path, log: Optional[LogCallback] = None) -> int:
    """Run P2 evidence-based corroboration: writes semantics.db and annotates
    normalized_validated_long with corroboration_count/conflict_count/corroboration_status."""
    _prepare(root)
    return _call("corroborate", cli.cmd_corroborate, root, argparse.Namespace(), log)


def golden_freeze(root: Path, log: Optional[LogCallback] = None) -> int:
    """P3: review_resolved.csv + cell_resolutions(auto_confirmed) + MANUAL_OBSIDIAN
    の3源からgolden集合を凍結し、semantics.db の golden_values/golden_negative を
    完全置換する。明示実行専用（他のジョブから自動起動しない）。"""
    _prepare(root)
    return _call("golden-freeze", cli.cmd_golden_freeze, root, argparse.Namespace(), log)


def ai_map_observed_items(
    root: Path,
    log: Optional[LogCallback] = None,
    tier: str = "bulk",
    limit: int = 50,
    dry_run: bool = True,
) -> int:
    """P5: 未マップobserved_itemsをAIにマッピング提案させ、proposedとして書き込む。
    dry_run=Trueの場合はカード生成のみでAI呼び出しを行わない（コスト0）。
    dry_run=Falseの場合も、実行にはClaudeCliRunnerが必要——Web API経由では
    当面 dry_run 固定を強く推奨（人間が明示的にCLIから叩く運用にする）。"""
    _prepare(root)
    return _call(
        "ai-map-observed-items",
        cli.cmd_ai_map_observed_items,
        root,
        argparse.Namespace(tier=tier, limit=limit, dry_run=dry_run),
        log,
    )


def promote_ai_mappings(
    root: Path,
    log: Optional[LogCallback] = None,
    confirm_verified_maps: Optional[bool] = None,
    adopt_new_concepts: Optional[bool] = None,
    dry_run: bool = True,
) -> int:
    """P5c: AI提案(map/new_concept)の数値裏取り確定・新概念採用を実行する。
    confirm_verified_maps/adopt_new_concepts が両方Noneの場合は両方実行する。
    dry_run=Trueの場合はDB書き込みを行わず判定結果のみ表示する（既定）。"""
    _prepare(root)
    return _call(
        "promote-ai-mappings",
        cli.cmd_promote_ai_mappings,
        root,
        argparse.Namespace(
            confirm_verified_maps=confirm_verified_maps,
            adopt_new_concepts=adopt_new_concepts,
            dry_run=dry_run,
        ),
        log,
    )


def regression_check(root: Path, log: Optional[LogCallback] = None, mode: str = "light") -> int:
    """P3: 一時shadow_root上で再抽出し、凍結済みgoldenとdiffする。
    data/reports/regression_diff.csv・regression_summary.json を書く。
    既存 data/final・data/intermediate は一切変更しない。"""
    _prepare(root)
    return _call("regression-check", cli.cmd_regression_check, root, argparse.Namespace(mode=mode), log)


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
