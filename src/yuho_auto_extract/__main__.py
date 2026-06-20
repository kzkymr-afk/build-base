from __future__ import annotations

import argparse
import json
import os
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .ai_bundle import build_ai_bundle
from .analysis_builder import build_analysis_dataset
from .config_loader import field_map, load_pipeline_config
from .document_resolver import resolve_target_documents
from .edinet_db import build_edinet_db, extract_from_edinet_db, split_local_review_rows
from .exporter import apply_review_decisions, build_source_audit, build_wide_values, filter_exportable_rows
from .io_utils import prefer_existing_table, read_table, write_table
from .logging_utils import configure_logging
from .normalizer import normalize_extraction
from .review_queue import build_review_queue
from .run_reporter import build_run_report, write_run_report
from .section_locator import locate_candidate_blocks
from .services.algorithm_audit import build_algorithm_audit_bundle
from .validator import attach_validation_status, validate_records
from .xbrl_csv_parser import extract_xbrl_csv_long


def main(argv: List[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging()
    root = Path(args.root).resolve()
    _load_env_file(root)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2
    return args.handler(root, args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m yuho_auto_extract")
    parser.add_argument("--root", default=".", help="Project root containing config/ and data/")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("index")
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.set_defaults(handler=cmd_index)

    p = sub.add_parser("index-annual")
    p.add_argument("--fiscal-years", nargs="*", type=int)
    p.set_defaults(handler=cmd_index_annual)

    p = sub.add_parser("resolve")
    p.add_argument("--fiscal-years", nargs="*", type=int)
    p.set_defaults(handler=cmd_resolve)

    p = sub.add_parser("download")
    p.add_argument("--target-documents", default="data/intermediate/target_documents.parquet")
    p.set_defaults(handler=cmd_download)

    p = sub.add_parser("build-edinet-db")
    p.add_argument("--db", default="data/intermediate/edinet.db")
    p.set_defaults(handler=cmd_build_edinet_db)

    p = sub.add_parser("extract-from-db")
    p.add_argument("--db", default="data/intermediate/edinet.db")
    p.add_argument("--output", default="data/intermediate/db_extracted_long.csv")
    p.add_argument("--no-pipeline", action="store_true")
    p.set_defaults(handler=cmd_extract_from_db)

    p = sub.add_parser("run-local")
    p.add_argument("--db", default="data/intermediate/edinet.db")
    p.set_defaults(handler=cmd_run_local)

    p = sub.add_parser("run-all")
    p.add_argument("--db", default="data/intermediate/edinet.db")
    p.add_argument("--fiscal-years", nargs="*", type=int)
    p.set_defaults(handler=cmd_run_all)

    sub.add_parser("extract-xbrl").set_defaults(handler=cmd_extract_xbrl)
    sub.add_parser("locate-sections").set_defaults(handler=cmd_locate_sections)

    sub.add_parser("normalize").set_defaults(handler=cmd_normalize)
    sub.add_parser("validate").set_defaults(handler=cmd_validate)

    p = sub.add_parser("build-review-queue")
    p.add_argument("--existing-excel", default="")
    p.set_defaults(handler=cmd_build_review_queue)

    sub.add_parser("split-local-review").set_defaults(handler=cmd_split_local_review)

    p = sub.add_parser("export-final")
    p.add_argument("--reviewed", default="data/review/review_resolved.xlsx")
    p.set_defaults(handler=cmd_export_final)

    sub.add_parser("build-analysis").set_defaults(handler=cmd_build_analysis)
    sub.add_parser("report").set_defaults(handler=cmd_report)
    sub.add_parser("build-ai-bundle").set_defaults(handler=cmd_build_ai_bundle)
    sub.add_parser("build-algorithm-audit").set_defaults(handler=cmd_build_algorithm_audit)
    p = sub.add_parser("automation-status")
    p.add_argument("--fiscal-year", type=int)
    p.set_defaults(handler=cmd_automation_status)
    p = sub.add_parser("roll-forward-year")
    p.add_argument("--fiscal-year", required=True, type=int)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(handler=cmd_roll_forward_year)
    p = sub.add_parser("annual-refresh")
    p.add_argument("--fiscal-year", type=int)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(handler=cmd_annual_refresh)
    sub.add_parser("init-xlsx").set_defaults(handler=cmd_init_xlsx)
    p = sub.add_parser("web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=8765, type=int)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(handler=cmd_web)
    return parser


def cmd_index(root: Path, args: argparse.Namespace) -> int:
    from .edinet_client import EdinetClient, save_index_response

    cfg = load_pipeline_config(root)
    edinet_cfg = cfg.model_config.get("edinet", {})
    client = EdinetClient(
        base_url=edinet_cfg.get("base_url", "https://api.edinet-fsa.go.jp/api/v2"),
        timeout=int(edinet_cfg.get("request_timeout_seconds", 30)),
        retry_count=int(edinet_cfg.get("retry_count", 3)),
        retry_backoff_seconds=int(edinet_cfg.get("retry_backoff_seconds", 2)),
    )
    rows = _collect_index_rows(client, root, _date_range(_parse_date(args.start_date), _parse_date(args.end_date)))
    written = write_table(root / "data" / "intermediate" / "document_index.parquet", rows)
    print(f"wrote {written} rows={len(rows)}")
    return 0


def cmd_index_annual(root: Path, args: argparse.Namespace) -> int:
    from .edinet_client import EdinetClient

    cfg = load_pipeline_config(root)
    edinet_cfg = cfg.model_config.get("edinet", {})
    client = EdinetClient(
        base_url=edinet_cfg.get("base_url", "https://api.edinet-fsa.go.jp/api/v2"),
        timeout=int(edinet_cfg.get("request_timeout_seconds", 30)),
        retry_count=int(edinet_cfg.get("retry_count", 3)),
        retry_backoff_seconds=int(edinet_cfg.get("retry_backoff_seconds", 2)),
    )
    fiscal_years = args.fiscal_years or _configured_fiscal_years(cfg.company_year_master)
    dates = _annual_index_dates(cfg.company_master, fiscal_years)
    rows = _collect_index_rows(client, root, dates)
    written = write_table(root / "data" / "intermediate" / "document_index.parquet", rows)
    print(f"wrote {written} rows={len(rows)} fiscal_years={','.join(str(y) for y in fiscal_years)} dates={len(dates)}")
    return 0


def cmd_resolve(root: Path, args: argparse.Namespace) -> int:
    cfg = load_pipeline_config(root)
    docs = read_table(prefer_existing_table(root / "data" / "intermediate" / "document_index.parquet"))
    targets = resolve_target_documents(
        docs,
        cfg.company_master,
        cfg.company_year_master,
        cfg.document_filter,
        fiscal_years=args.fiscal_years,
    )
    written = write_table(root / "data" / "intermediate" / "target_documents.parquet", targets)
    print(f"wrote {written} rows={len(targets)}")
    return 0


def cmd_download(root: Path, args: argparse.Namespace) -> int:
    from .downloader import download_target_documents
    from .edinet_client import EdinetClient

    cfg = load_pipeline_config(root)
    edinet_cfg = cfg.model_config.get("edinet", {})
    client = EdinetClient(
        base_url=edinet_cfg.get("base_url", "https://api.edinet-fsa.go.jp/api/v2"),
        timeout=int(edinet_cfg.get("request_timeout_seconds", 30)),
        retry_count=int(edinet_cfg.get("retry_count", 3)),
        retry_backoff_seconds=int(edinet_cfg.get("retry_backoff_seconds", 2)),
    )
    targets = read_table(prefer_existing_table(root / args.target_documents))
    manifest = download_target_documents(client, targets, root / "data" / "raw" / "documents")
    written = write_table(root / "data" / "raw" / "download_manifest.parquet", manifest)
    print(f"wrote {written} rows={len(manifest)}")
    return 0


def cmd_build_edinet_db(root: Path, args: argparse.Namespace) -> int:
    db_path = _project_path(root, args.db)
    counts = build_edinet_db(root, db_path)
    print(f"wrote {db_path}")
    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    return 0


def cmd_extract_from_db(root: Path, args: argparse.Namespace) -> int:
    db_path = _project_path(root, args.db)
    output_path = _project_path(root, args.output)
    counts = extract_from_edinet_db(root, db_path, output_path, write_pipeline=not args.no_pipeline)
    print(f"wrote {output_path}")
    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    return 0


def cmd_run_local(root: Path, args: argparse.Namespace) -> int:
    input_error = _local_run_input_error(root)
    if input_error:
        print(input_error)
        return 2
    db_path = _project_path(root, args.db)
    counts = build_edinet_db(root, db_path)
    print(f"wrote {db_path}")
    print(f"xbrl_facts: {counts.get('xbrl_facts', 0)}")
    extract_counts = extract_from_edinet_db(root, db_path, root / "data" / "intermediate" / "db_extracted_long.csv", write_pipeline=True)
    print(f"db_extracted_rows: {extract_counts.get('combined_rows', 0)}")
    cmd_normalize(root, argparse.Namespace())
    cmd_validate(root, argparse.Namespace())
    cmd_build_review_queue(root, argparse.Namespace(existing_excel=""))
    split_counts = split_local_review_rows(root)
    print(f"local_auto_accepted: {split_counts.get('accepted_rows', 0)}")
    print(f"local_manual_rows: {split_counts.get('manual_rows', 0)}")
    cmd_export_final(root, argparse.Namespace(reviewed="data/review/review_resolved_local_pass.xlsx"))
    cmd_build_analysis(root, argparse.Namespace())
    cmd_report(root, argparse.Namespace())
    return 0


def cmd_run_all(root: Path, args: argparse.Namespace) -> int:
    if _local_run_input_error(root):
        fiscal_years = args.fiscal_years or list(range(2015, 2025))
        print("入力データが不足しているため、EDINET取得から実行します。")
        year_args = argparse.Namespace(fiscal_years=fiscal_years)
        for command in [cmd_index_annual, cmd_resolve]:
            code = command(root, year_args)
            if code:
                return code
        code = cmd_download(root, argparse.Namespace(target_documents="data/intermediate/target_documents.csv"))
        if code:
            return code
        code = cmd_locate_sections(root, argparse.Namespace())
        if code:
            return code
    return cmd_run_local(root, argparse.Namespace(db=args.db))


def cmd_extract_xbrl(root: Path, args: argparse.Namespace) -> int:
    cfg = load_pipeline_config(root)
    run_id = _run_id()
    targets = read_table(prefer_existing_table(root / "data" / "intermediate" / "target_documents.parquet"))
    rows: List[Dict[str, Any]] = []
    for target in targets:
        if target.get("resolution_status") != "resolved":
            continue
        csv_zip = root / "data" / "raw" / "documents" / str(target.get("docID")) / "csv.zip"
        rows.extend(extract_xbrl_csv_long(csv_zip, cfg.field_definition, target, run_id))
    written = write_table(root / "data" / "intermediate" / "xbrl_extracted_long.parquet", rows)
    print(f"wrote {written} rows={len(rows)}")
    return 0


def cmd_locate_sections(root: Path, args: argparse.Namespace) -> int:
    cfg = load_pipeline_config(root)
    run_id = _run_id()
    targets = read_table(prefer_existing_table(root / "data" / "intermediate" / "target_documents.parquet"))
    rows: List[Dict[str, Any]] = []
    for target in targets:
        if target.get("resolution_status") != "resolved":
            continue
        doc_dir = root / "data" / "raw" / "documents" / str(target.get("docID"))
        _extract_zip_text_payloads(doc_dir / "xbrl.zip", doc_dir / "extracted_xbrl")
        rows.extend(locate_candidate_blocks(doc_dir, target, cfg.extraction_sections, run_id))
    written = write_table(root / "data" / "intermediate" / "candidate_blocks.jsonl", rows)
    print(f"wrote {written} rows={len(rows)}")
    return 0


def cmd_normalize(root: Path, args: argparse.Namespace) -> int:
    cfg = load_pipeline_config(root)
    fields = field_map(cfg.field_definition)
    rows: List[Dict[str, Any]] = []
    for path in [
        prefer_existing_table(root / "data" / "intermediate" / "xbrl_extracted_long.parquet"),
        root / "data" / "intermediate" / "ai_extracted_long.jsonl",
    ]:
        if path.exists():
            rows.extend(read_table(path))
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        field = fields.get(str(row.get("field_id")))
        if not field:
            copied = dict(row)
            copied["review_required"] = True
            copied["review_reason"] = "field_definition_missing"
            normalized.append(copied)
            continue
        normalized.append(normalize_extraction(row, field))
    written = write_table(root / "data" / "intermediate" / "normalized_long.parquet", normalized)
    print(f"wrote {written} rows={len(normalized)}")
    return 0


def cmd_validate(root: Path, args: argparse.Namespace) -> int:
    cfg = load_pipeline_config(root)
    rows = read_table(prefer_existing_table(root / "data" / "intermediate" / "normalized_long.parquet"))
    results = validate_records(rows, cfg.validation_rules)
    rows_with_status = attach_validation_status(rows, results)
    validation_path = write_table(root / "data" / "intermediate" / "validation_results.parquet", results)
    normalized_path = write_table(root / "data" / "intermediate" / "normalized_validated_long.parquet", rows_with_status)
    print(f"wrote {validation_path} rows={len(results)}")
    print(f"wrote {normalized_path} rows={len(rows_with_status)}")
    return 0


def cmd_build_review_queue(root: Path, args: argparse.Namespace) -> int:
    cfg = load_pipeline_config(root)
    rows = _read_validated_or_normalized(root)
    existing = read_table(Path(args.existing_excel)) if args.existing_excel else []
    queue = build_review_queue(rows, cfg.field_definition, cfg.company_year_master, existing)
    written = write_table(root / "data" / "review" / "review_queue.xlsx", queue)
    write_table(root / "data" / "review" / "review_queue.csv", queue)
    print(f"wrote {written} rows={len(queue)}")
    return 0


def cmd_split_local_review(root: Path, args: argparse.Namespace) -> int:
    counts = split_local_review_rows(root)
    print("wrote data/review/review_resolved_local_pass.xlsx")
    print("wrote data/review/review_queue_local_needs_manual.xlsx")
    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    return 0


def cmd_export_final(root: Path, args: argparse.Namespace) -> int:
    cfg = load_pipeline_config(root)
    extracted = _read_validated_or_normalized(root)
    reviewed_path = prefer_existing_table(root / args.reviewed)
    reviewed = read_table(reviewed_path) if reviewed_path.exists() else []
    final_long = apply_review_decisions(extracted, reviewed)
    exportable = filter_exportable_rows(final_long)
    long_path = write_table(root / "data" / "final" / "final_master_long.parquet", exportable)
    long_csv_path = write_table(root / "data" / "final" / "final_master_long.csv", exportable)
    wide = build_wide_values(exportable, cfg.company_year_master, cfg.field_definition)
    wide_path = write_table(root / "data" / "final" / "wide_values.xlsx", wide)
    audit = build_source_audit(exportable, cfg.field_definition)
    audit_path = write_table(root / "data" / "final" / "source_audit.xlsx", audit)
    write_table(root / "data" / "final" / "wide_values.csv", wide)
    write_table(root / "data" / "final" / "final_master_wide.csv", wide)
    write_table(root / "data" / "final" / "source_audit.csv", audit)
    final_wide_path = root / "data" / "final" / "final_master_wide.xlsx"
    _write_final_workbook(final_wide_path, wide, audit, cfg.field_definition, cfg.company_year_master)
    print(f"wrote {long_path} rows={len(exportable)}")
    print(f"wrote {long_csv_path} rows={len(exportable)}")
    print(f"wrote {wide_path} rows={len(wide)}")
    print(f"wrote {audit_path}")
    print(f"wrote {final_wide_path}")
    return 0


def cmd_build_analysis(root: Path, args: argparse.Namespace) -> int:
    wide_path = prefer_existing_table(root / "data" / "final" / "wide_values.xlsx")
    wide = read_table(wide_path)
    analysis = build_analysis_dataset(wide)
    written = write_table(root / "data" / "final" / "analysis_dataset.xlsx", analysis)
    csv_written = write_table(root / "data" / "final" / "analysis_dataset.csv", analysis)
    print(f"wrote {written} rows={len(analysis)}")
    print(f"wrote {csv_written} rows={len(analysis)}")
    return 0


def cmd_report(root: Path, args: argparse.Namespace) -> int:
    cfg = load_pipeline_config(root)
    run_id = _run_id()
    targets = _read_optional(root / "data" / "intermediate" / "target_documents.parquet")
    rows = _read_optional(root / "data" / "intermediate" / "normalized_validated_long.parquet")
    validations = _read_optional(root / "data" / "intermediate" / "validation_results.parquet")
    queue = _read_optional(root / "data" / "review" / "review_queue.xlsx")
    report = build_run_report(run_id, targets, rows, validations, queue)
    path = write_run_report(root / "data" / "final" / "run_report.md", report)
    coverage = _build_field_coverage(root, cfg.field_definition, cfg.company_year_master)
    coverage_path = write_table(root / "data" / "final" / "field_coverage.csv", coverage)
    markdown_path = root / "data" / "final" / "field_coverage.md"
    markdown_path.write_text(_field_coverage_markdown(coverage), encoding="utf-8")
    bundle_files = build_ai_bundle(root)
    print(f"wrote {path}")
    print(f"wrote {coverage_path}")
    print(f"wrote {markdown_path}")
    print(f"wrote {root / 'data' / 'ai_bundle'} files={len(bundle_files) + 2}")
    return 0


def cmd_build_ai_bundle(root: Path, args: argparse.Namespace) -> int:
    copied = build_ai_bundle(root)
    print(f"wrote {root / 'data' / 'ai_bundle'} files={len(copied) + 2}")
    return 0


def cmd_build_algorithm_audit(root: Path, args: argparse.Namespace) -> int:
    result = build_algorithm_audit_bundle(root)
    summary = result.get("summary", {})
    print(f"wrote {result.get('bundle_dir')} files={len(result.get('files', []))}")
    print(f"risk_flags: {summary.get('risk_flags', 0)}")
    print(f"review_derived_sections: {summary.get('review_derived_sections', 0)}")
    return 0


def cmd_automation_status(root: Path, args: argparse.Namespace) -> int:
    from .services import automation

    result = automation.automation_status(root, fiscal_year=args.fiscal_year)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_roll_forward_year(root: Path, args: argparse.Namespace) -> int:
    from .services import automation

    result = automation.roll_forward_company_years(root, args.fiscal_year, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_annual_refresh(root: Path, args: argparse.Namespace) -> int:
    from .services import pipeline

    return pipeline.annual_refresh(
        root,
        fiscal_year=args.fiscal_year,
        force=args.force,
        dry_run=args.dry_run,
        log=print,
    )


def cmd_init_xlsx(root: Path, args: argparse.Namespace) -> int:
    config_dir = root / "config"
    for name in ["company_master", "company_year_master", "field_definition"]:
        rows = read_table(config_dir / f"{name}.csv")
        written = write_table(config_dir / f"{name}.xlsx", rows)
        print(f"wrote {written}")
    return 0


def cmd_web(root: Path, args: argparse.Namespace) -> int:
    os.environ.setdefault("YUHO_PROJECT_ROOT", str(root))
    from .web_api.__main__ import main as web_main

    argv = ["--host", args.host, "--port", str(args.port)]
    if args.reload:
        argv.append("--reload")
    return web_main(argv)


def _read_validated_or_normalized(root: Path) -> List[Dict[str, Any]]:
    validated = prefer_existing_table(root / "data" / "intermediate" / "normalized_validated_long.parquet")
    if validated.exists():
        return read_table(validated)
    return read_table(prefer_existing_table(root / "data" / "intermediate" / "normalized_long.parquet"))


def _build_field_coverage(
    root: Path,
    field_definitions: List[Dict[str, Any]],
    company_year_master: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    wide_path = root / "data" / "final" / "wide_values.csv"
    wide_rows = read_table(wide_path) if wide_path.exists() else []
    total = len(wide_rows) if wide_rows else len(company_year_master)
    if wide_rows:
        counts = {
            str(field.get("field_id") or ""): sum(1 for row in wide_rows if _has_output_value(row.get(str(field.get("field_id") or ""))))
            for field in field_definitions
            if field.get("field_id")
        }
    else:
        final_long = _read_optional(root / "data" / "final" / "final_master_long.parquet")
        counts = {}
        for row in final_long:
            if _has_output_value(row.get("value")):
                counts[str(row.get("field_id") or "")] = counts.get(str(row.get("field_id") or ""), 0) + 1
    coverage = []
    for field in field_definitions:
        field_id = str(field.get("field_id") or "")
        filled = counts.get(field_id, 0)
        coverage.append(
            {
                "field_id": field_id,
                "field_name_ja": field.get("field_name_ja", ""),
                "category": field.get("category", ""),
                "preferred_method": field.get("preferred_method", ""),
                "filled_company_years": filled,
                "total_company_years": total,
                "coverage_pct": round(filled / total, 4) if total else 0,
            }
        )
    return sorted(coverage, key=lambda row: (-float(row["filled_company_years"]), str(row["field_id"])))


def _field_coverage_markdown(rows: List[Dict[str, Any]]) -> str:
    total_fields = len(rows)
    total_company_years = rows[0].get("total_company_years", 0) if rows else 0
    filled_fields = sum(1 for row in rows if int(row.get("filled_company_years") or 0) > 0)
    lines = [
        "# Field Coverage",
        "",
        f"- fields: {total_fields}",
        f"- company_years: {total_company_years}",
        f"- filled_fields: {filled_fields}",
        "",
        "| field_id | field_name_ja | filled | coverage |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in rows:
        pct = float(row.get("coverage_pct") or 0) * 100
        lines.append(
            f"| {row.get('field_id', '')} | {row.get('field_name_ja', '')} | {row.get('filled_company_years', 0)} | {pct:.1f}% |"
        )
    return "\n".join(lines) + "\n"


def _has_output_value(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return text not in {"", "nan", "NaN", "None", "null"}


def _local_run_input_error(root: Path) -> str:
    target_path = prefer_existing_table(root / "data" / "intermediate" / "target_documents.parquet")
    candidate_path = root / "data" / "intermediate" / "candidate_blocks.jsonl"
    document_dir = root / "data" / "raw" / "documents"
    if not target_path.exists() or not read_table(target_path):
        return (
            "入力データが不足しています: data/intermediate/target_documents がありません。\n"
            "先に index-annual、resolve、download、locate-sections を実行してください。"
        )
    if not document_dir.exists() or not any(document_dir.glob("*/csv.zip")):
        return (
            "入力データが不足しています: data/raw/documents/*/csv.zip がありません。\n"
            "先に download を実行してください。"
        )
    if not candidate_path.exists() or candidate_path.stat().st_size == 0:
        return (
            "入力データが不足しています: data/intermediate/candidate_blocks.jsonl がありません。\n"
            "先に locate-sections を実行してください。"
        )
    return ""


def _read_optional(path: Path) -> List[Dict[str, Any]]:
    actual = prefer_existing_table(path)
    return read_table(actual) if actual.exists() else []


def _project_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_env_file(root: Path) -> None:
    for name in [".env", ".env.txt", "env.txt"]:
        path = root / name
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        return


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _collect_index_rows(client: Any, root: Path, dates: Iterable[date]) -> List[Dict[str, Any]]:
    from .edinet_client import save_index_response

    rows: List[Dict[str, Any]] = []
    for current in dates:
        response = client.list_documents(current, doc_type=2)
        save_index_response(root / "data" / "raw" / "edinet_index" / f"{current.isoformat()}.json", response)
        for item in response.get("results", []):
            copied = dict(item)
            copied["fileDate"] = current.isoformat()
            rows.append(copied)
    return rows


def _configured_fiscal_years(company_years: List[Dict[str, Any]]) -> List[int]:
    return sorted({int(row["fiscal_year"]) for row in company_years})


def _annual_index_dates(company_master: List[Dict[str, Any]], fiscal_years: List[int]) -> List[date]:
    fiscal_months = {int(row.get("fiscal_year_end_month") or 3) for row in company_master}
    dates = set()
    for fiscal_year in fiscal_years:
        for month in fiscal_months:
            if month == 12:
                start = date(fiscal_year + 1, 3, 1)
                end = date(fiscal_year + 1, 4, 30)
            else:
                start = date(fiscal_year + 1, 6, 1)
                end = date(fiscal_year + 1, 7, 31)
            dates.update(_date_range(start, end))
    return sorted(dates)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def _extract_zip_text_payloads(zip_path: Path, output_dir: Path) -> None:
    if not zip_path.exists():
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    allowed = {".html", ".htm", ".xhtml", ".txt", ".csv"}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            suffix = Path(name).suffix.lower()
            if suffix not in allowed:
                continue
            target = output_dir / Path(name).name
            if not target.exists():
                target.write_bytes(zf.read(info))


def _write_final_workbook(path: Path, wide: List[Dict[str, Any]], audit: List[Dict[str, Any]], fields: List[Dict[str, Any]], company_years: List[Dict[str, Any]]) -> None:
    write_table(path.with_name("final_master_wide_values.csv"), wide)
    write_table(path.with_name("final_master_source_audit.csv"), audit)
    write_table(path.with_name("final_master_field_definition.csv"), fields)
    write_table(path.with_name("final_master_company_year_master.csv"), company_years)
    try:
        import openpyxl  # type: ignore
    except ImportError:
        write_table(path, wide)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    _write_sheet(wb.active, "wide_values", wide)
    _write_sheet(wb.create_sheet(), "source_audit", audit)
    _write_sheet(wb.create_sheet(), "field_definition", fields)
    _write_sheet(wb.create_sheet(), "company_year_master", company_years)
    _write_sheet(wb.create_sheet(), "run_summary", [{"sheet": "wide_values", "rows": len(wide)}, {"sheet": "source_audit", "rows": len(audit)}])
    wb.save(path)


def _write_sheet(ws: Any, title: str, rows: List[Dict[str, Any]]) -> None:
    ws.title = title[:31]
    if not rows:
        return
    headers: List[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    ws.append(headers)
    for row in rows:
        ws.append([row.get(key, "") for key in headers])


if __name__ == "__main__":
    raise SystemExit(main())
