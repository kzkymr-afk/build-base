from __future__ import annotations

import glob
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from yuho_auto_extract.ai_runner import AiRunner, ai_call_result_to_ai_calls_record
from yuho_auto_extract.io_utils import is_blankish, read_table, read_yaml, write_table
from yuho_auto_extract.services.automation import merge_records_by_key
from yuho_auto_extract.services.datasets import paginate
from yuho_auto_extract.services import factbook_parsers, semantics_store
from yuho_auto_extract.services.ai_mapping import load_ai_config


FetchText = Callable[[str, Dict[str, Any]], str]

ORDER_COLUMNS = [
    "company_id",
    "company_name",
    "fiscal_year",
    "fiscal_year_end",
    "period_type",
    "period_label",
    "source_company_id",
    "source_doc_type",
    "source_dataset_id",
    "source_metric_id",
    "category_type",
    "scope",
    "business_scope",
    "use_category_raw",
    "use_category_normalized",
    "use_category_label",
    "order_amount",
    "unit",
    "amount_million_yen",
    "source_url",
    "source_page",
    "source_table_title",
    "source_quote",
    "source_file",
    "extraction_status",
    "fetched_at_utc",
]

SOURCE_DOCUMENT_COLUMNS = [
    "source_dataset_id",
    "company_id",
    "company_name",
    "source_doc_type",
    "source_metric_id",
    "target_metric_ids",
    "category_type",
    "fiscal_year",
    "period_type",
    "period_label",
    "title",
    "url",
    "file_name",
    "file_ext",
    "parser_status",
    "note",
    "discovered_at_utc",
]

TARGET_COVERAGE_COLUMNS = [
    "company_id",
    "company_name",
    "target_metric_id",
    "target_metric_name",
    "candidate_documents",
    "parsed_rows",
    "latest_candidate_year",
    "latest_parsed_year",
    "coverage_status",
    "coverage_message",
    "common_use_categories",
]

DEFAULT_TARGET_METRICS = [
    {"id": "building_orders_by_use", "name": "建築用途別受注高"},
    {"id": "completed_building_by_use", "name": "建築用途別完工高"},
]

FACTBOOK_CHART_FIELDS = [
    {"id": "order_amount", "name": "受注高", "category": "company_factbook", "unit": "億円"},
    {"id": "amount_million_yen", "name": "受注高", "category": "company_factbook", "unit": "百万円"},
]

VALIDATION_COLUMNS = [
    "company_id",
    "company_name",
    "fiscal_year",
    "period_type",
    "period_label",
    "source_metric_id",
    "category_type",
    "use_category_raw",
    "use_category_normalized",
    "use_category_label",
    "factbook_value",
    "factbook_unit",
    "factbook_amount_million_yen",
    "yuho_field_id",
    "yuho_field_name",
    "yuho_value_million_yen",
    "diff_million_yen",
    "diff_pct",
    "validation_status",
    "validation_message",
    "source_dataset_id",
    "source_url",
    "source_file",
    "source_quote",
]

DEFAULT_FACTBOOK_YUHO_FIELD_MAP = {
    ("business_scope", "domestic_building"): "segment_orders_domestic_building",
    ("business_scope", "domestic_civil"): "segment_orders_domestic_civil",
    ("business_scope", "overseas_building"): "segment_orders_overseas_building",
    ("business_scope", "overseas_civil"): "segment_orders_overseas_civil",
    ("business_scope", "overseas_construction"): "segment_orders_overseas_construction",
    ("business_scope", "building"): "segment_orders_building",
    ("business_scope", "civil"): "segment_orders_civil",
}


def factbook_status(root: Path) -> Dict[str, Any]:
    cfg = _factbook_config(root)
    rows = _read_optional(_canonical_path(root, cfg))
    docs = _read_optional(_source_documents_path(root, cfg))
    last = _read_json(root / "data" / "automation" / "company_factbooks_last.json")
    validation_summary = _read_json(root / "data" / "reports" / "company_factbook_yuho_validation_summary.json")
    target_coverage_summary = _read_json(root / "data" / "reports" / "company_factbook_target_coverage_summary.json")
    configured_sources = _configured_sources(cfg)
    enabled_sources = [source for source in configured_sources if _bool(source.get("enabled"), True)]
    parsed_rows = [row for row in rows if str(row.get("extraction_status") or "") == "parsed"]
    unsupported_docs = [row for row in docs if str(row.get("parser_status") or "") != "parsed"]
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "cadence": "annual_or_quarterly",
        "as_of": datetime.now().date().isoformat(),
        "source_count": len(configured_sources),
        "enabled_source_count": len(enabled_sources),
        "order_rows": len(rows),
        "parsed_order_rows": len(parsed_rows),
        "source_documents": len(docs),
        "unsupported_documents": len(unsupported_docs),
        "latest_fiscal_year": _latest_period(rows),
        "last_run_at_utc": last.get("run_at_utc", ""),
        "last_status": last.get("status", ""),
        "last_error_count": len(last.get("errors", []) or []),
        "output_path": str(_canonical_path(root, cfg)),
        "source_document_path": str(_source_documents_path(root, cfg)),
        "validation": validation_summary,
        "target_coverage": target_coverage_summary,
        "message": _status_message(cfg, rows, docs, last),
    }


def factbook_options(root: Path) -> Dict[str, Any]:
    rows = _read_optional(_canonical_path(root, _factbook_config(root)))
    companies_by_id = _companies_from_master(root)
    for source in _configured_sources(_factbook_config(root)):
        company_id = str(source.get("company_id") or "")
        if company_id and company_id not in companies_by_id:
            companies_by_id[company_id] = {
                "id": company_id,
                "name": str(source.get("company_name") or company_id),
                "label": _company_label({"id": company_id, "name": str(source.get("company_name") or company_id)}),
            }
    for row in rows:
        company_id = str(row.get("company_id") or "")
        if company_id and company_id not in companies_by_id:
            companies_by_id[company_id] = {
                "id": company_id,
                "name": str(row.get("company_name") or company_id),
                "label": _company_label({"id": company_id, "name": str(row.get("company_name") or company_id)}),
            }
    category_fields = _category_field_options(root, rows)
    return {
        "companies": sorted(companies_by_id.values(), key=lambda item: item["id"]),
        "years": sorted({str(row.get("fiscal_year") or "") for row in rows if row.get("fiscal_year")}, key=_period_sort_key),
        "category_types": sorted({str(row.get("category_type") or "") for row in rows if row.get("category_type")}),
        "fields": category_fields or [_field_option(field) for field in FACTBOOK_CHART_FIELDS],
        "default_result_fields": [field["id"] for field in category_fields[:6]] or ["order_amount"],
        "field_presets": [
            {"id": "use", "name": "用途別", "fields": [field["id"] for field in category_fields if field.get("category") == "use"][:8]},
            {
                "id": "business_scope",
                "name": "建築/土木等",
                "fields": [field["id"] for field in category_fields if field.get("category") == "business_scope"][:8],
            },
        ],
    }


def read_factbook_orders_page(
    root: Path,
    page: int = 1,
    page_size: int = 100,
    company: str = "",
    fiscal_year: str = "",
    category_type: str = "",
    search: str = "",
) -> Dict[str, Any]:
    cfg = _factbook_config(root)
    rows = _read_optional(_canonical_path(root, cfg))
    filtered = []
    for row in rows:
        if company and str(row.get("company_id") or "") != company:
            continue
        if fiscal_year and str(row.get("fiscal_year") or "") != str(fiscal_year):
            continue
        if category_type and str(row.get("category_type") or "") != category_type:
            continue
        if search and not _contains(row, search):
            continue
        filtered.append(row)
    filtered = sorted(
        filtered,
        key=lambda item: (
            str(item.get("company_id") or ""),
            _period_sort_key(item.get("fiscal_year")),
            str(item.get("category_type") or ""),
            str(item.get("use_category_label") or ""),
        ),
        reverse=True,
    )
    result = paginate(filtered, page, page_size)
    result["columns"] = [column for column in ORDER_COLUMNS if column in result["columns"]]
    return result


def read_factbook_documents_page(
    root: Path,
    page: int = 1,
    page_size: int = 100,
    company: str = "",
    fiscal_year: str = "",
    search: str = "",
) -> Dict[str, Any]:
    cfg = _factbook_config(root)
    rows = _read_optional(_source_documents_path(root, cfg))
    filtered = []
    for row in rows:
        if company and str(row.get("company_id") or "") != company:
            continue
        if fiscal_year and str(row.get("fiscal_year") or "") != str(fiscal_year):
            continue
        if search and not _contains(row, search):
            continue
        filtered.append(row)
    filtered = sorted(filtered, key=lambda item: (str(item.get("company_id") or ""), _period_sort_key(item.get("fiscal_year")), str(item.get("title") or "")), reverse=True)
    result = paginate(filtered, page, page_size)
    result["columns"] = [column for column in SOURCE_DOCUMENT_COLUMNS if column in result["columns"]]
    return result


def read_factbook_chart_data(
    root: Path,
    companies: Optional[Sequence[str]] = None,
    fiscal_years: Optional[Sequence[str]] = None,
    fields: Optional[Sequence[str]] = None,
    max_rows: int = 5000,
) -> Dict[str, Any]:
    rows = _read_optional(_canonical_path(root, _factbook_config(root)))
    company_filter = {str(item) for item in companies or [] if str(item)}
    year_filter = {str(item) for item in fiscal_years or [] if str(item)}
    field_defs = {field["id"]: field for field in _category_field_options(root, rows)}
    selected_fields = [field for field in (fields or []) if field in field_defs]
    if not selected_fields:
        selected_fields = list(field_defs.keys())[:6]

    by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        company_id = str(row.get("company_id") or "")
        fiscal_year = str(row.get("fiscal_year") or "")
        if company_filter and company_id not in company_filter:
            continue
        if year_filter and fiscal_year not in year_filter:
            continue
        field_id = _category_field_id(row)
        if field_id not in selected_fields:
            continue
        value = _safe_float(row.get("order_amount"))
        if value is None:
            continue
        point = by_key.setdefault(
            (company_id, fiscal_year),
            {
                "company_year_id": f"{company_id}_{fiscal_year}",
                "fiscal_year": fiscal_year,
                "fiscal_year_end": row.get("fiscal_year_end", ""),
                "operating_company_id": company_id,
                "operating_company_name": row.get("company_name", ""),
            },
        )
        point[field_id] = value
        point[f"{field_id}__raw"] = str(row.get("order_amount") or "")

    chart_rows = sorted(by_key.values(), key=lambda item: (_period_sort_key(item.get("fiscal_year")), str(item.get("operating_company_id") or "")))
    omitted = max(0, len(chart_rows) - max_rows)
    chart_rows = chart_rows[:max_rows]
    return {
        "rows": chart_rows,
        "columns": _columns(chart_rows),
        "total": len(chart_rows) + omitted,
        "omitted_rows": omitted,
        "fields": [_field_option(field_defs[field_id]) for field_id in selected_fields if field_id in field_defs],
        "companies": _chart_companies(chart_rows),
        "years": sorted({str(row.get("fiscal_year") or "") for row in chart_rows if row.get("fiscal_year")}, key=_period_sort_key),
    }


def refresh_company_factbooks(
    root: Path,
    force: bool = False,
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
    fetcher: Optional[FetchText] = None,
    ai_runner: Optional[AiRunner] = None,
    ai_tier: str = "bulk",
    company_ids: Optional[Sequence[str]] = None,
    source_ids: Optional[Sequence[str]] = None,
    fiscal_years: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    cfg = _factbook_config(root)
    if not bool(cfg.get("enabled", True)) and not force:
        summary = {"status": "blocked", "reason": "company_factbooks_disabled", "run_at_utc": _now_utc()}
        _write_summary(root, summary)
        return summary

    enabled_sources = [source for source in _configured_sources(cfg) if _bool(source.get("enabled"), True)]
    requested_company_ids = {str(company_id).strip().upper() for company_id in (company_ids or []) if str(company_id).strip()}
    if requested_company_ids:
        enabled_sources = [
            source
            for source in enabled_sources
            if str(source.get("company_id") or "").strip().upper() in requested_company_ids
        ]
    requested_source_ids = {str(source_id).strip() for source_id in (source_ids or []) if str(source_id).strip()}
    if requested_source_ids:
        enabled_sources = [
            source
            for source in enabled_sources
            if str(source.get("id") or "").strip() in requested_source_ids
            or str(source.get("source_dataset_id") or "").strip() in requested_source_ids
        ]
    requested_fiscal_years = {str(year).strip() for year in (fiscal_years or []) if str(year).strip()}
    canonical_path = _canonical_path(root, cfg)
    documents_path = _source_documents_path(root, cfg)
    raw_dir = _raw_store(root, cfg)
    existing_orders = _read_optional(canonical_path)
    existing_documents = _read_optional(documents_path)
    summary: Dict[str, Any] = {
        "status": "dry_run" if dry_run else "running",
        "run_at_utc": _now_utc(),
        "sources": len(enabled_sources),
        "existing_order_rows": len(existing_orders),
        "existing_source_documents": len(existing_documents),
        "new_order_rows": 0,
        "new_source_documents": 0,
        "merged_order_rows": len(existing_orders),
        "merged_source_documents": len(existing_documents),
        "dry_run": dry_run,
        "errors": [],
        "parser_warnings": [],
        "ai_calls_made": 0,
        "company_ids": sorted(requested_company_ids),
        "source_ids": sorted(requested_source_ids),
        "fiscal_years": sorted(requested_fiscal_years),
    }
    if log:
        scope = f" companies={','.join(sorted(requested_company_ids))}" if requested_company_ids else ""
        source_scope = f" source_ids={','.join(sorted(requested_source_ids))}" if requested_source_ids else ""
        year_scope = f" fiscal_years={','.join(sorted(requested_fiscal_years))}" if requested_fiscal_years else ""
        log(f"[company-factbooks] plan sources={len(enabled_sources)} dry_run={dry_run}{scope}{source_scope}{year_scope}")
    if dry_run:
        _write_summary(root, summary)
        return summary

    fetch = fetcher or _fetch_text
    fetched_at = _now_utc()
    ai_config = _factbook_ai_config(root, ai_tier) if ai_runner is not None else None
    order_rows: List[Dict[str, Any]] = []
    source_documents: List[Dict[str, Any]] = []
    successful_dataset_ids: set[str] = set()
    for source in enabled_sources:
        try:
            parser = str(source.get("parser") or "").strip()
            if log:
                log(f"[company-factbooks] source {source.get('id', '')} parser={parser}")
            parsed_orders, parsed_documents, warnings = _parse_source(root, cfg, source, raw_dir, fetch, fetched_at)
            if requested_fiscal_years:
                parsed_orders = [row for row in parsed_orders if _matches_requested_fiscal_year(row, requested_fiscal_years)]
                parsed_documents = [row for row in parsed_documents if _matches_requested_fiscal_year(row, requested_fiscal_years)]
            if _bool(source.get("parse_documents"), False):
                document_orders, parsed_documents, document_warnings, ai_calls = _parse_candidate_documents(
                    root,
                    cfg,
                    source,
                    raw_dir,
                    parsed_documents,
                    fetcher,
                    fetched_at,
                    ai_runner=ai_runner,
                    ai_config=ai_config,
                )
                parsed_orders.extend(document_orders)
                warnings.extend(document_warnings)
                summary["ai_calls_made"] += ai_calls
            order_rows.extend(parsed_orders)
            source_documents.extend(parsed_documents)
            summary["parser_warnings"].extend(warnings)
            dataset_id = str(source.get("source_dataset_id") or source.get("id") or "").strip()
            if dataset_id:
                successful_dataset_ids.add(dataset_id)
            if log:
                log(
                    f"[company-factbooks] parsed {source.get('id', '')} "
                    f"orders={len(parsed_orders)} documents={len(parsed_documents)} warnings={len(warnings)}"
                )
            sleep = float(cfg.get("polite_sleep_seconds") or 0)
            if sleep > 0:
                time.sleep(sleep)
        except Exception as exc:
            error = {"source_id": source.get("id", ""), "error": str(exc)}
            summary["errors"].append(error)
            if log:
                log(f"[company-factbooks] ERROR {source.get('id', '')}: {exc}")

    manual_orders, manual_documents, manual_warnings = _load_manual_csv_rows(root, cfg, fetched_at)
    order_rows.extend(manual_orders)
    source_documents.extend(manual_documents)
    summary["parser_warnings"].extend(manual_warnings)

    normalized_orders = _normalize_order_rows(root, cfg, order_rows)
    existing_orders_for_merge = [
        row
        for row in existing_orders
        if not _matches_refresh_replace_scope(row, successful_dataset_ids, requested_fiscal_years)
    ]
    merged_orders = merge_records_by_key(
        existing_orders_for_merge,
        normalized_orders,
        ["company_id", "fiscal_year", "period_type", "source_dataset_id", "source_metric_id", "category_type", "use_category_raw"],
    )
    merged_documents = merge_records_by_key(
        existing_documents,
        _normalize_document_rows(source_documents),
        ["source_dataset_id", "url"],
    )
    merged_orders = _order_columns(merged_orders)
    merged_documents = _document_columns(merged_documents)
    write_table(canonical_path, merged_orders)
    orders_json_path = _write_json_sidecar(canonical_path, merged_orders)
    write_table(canonical_path.with_suffix(".parquet"), merged_orders)
    write_table(documents_path, merged_documents)
    documents_json_path = _write_json_sidecar(documents_path, merged_documents)

    summary["new_order_rows"] = len(normalized_orders)
    summary["new_source_documents"] = len(source_documents)
    summary["merged_order_rows"] = len(merged_orders)
    summary["merged_source_documents"] = len(merged_documents)
    summary["latest_fiscal_year"] = _latest_period(merged_orders)
    summary["order_json_path"] = str(orders_json_path)
    summary["source_document_json_path"] = str(documents_json_path)
    if summary["errors"] and not normalized_orders and not source_documents:
        summary["status"] = "failed"
    elif summary["errors"]:
        summary["status"] = "partial_success"
    else:
        summary["status"] = "succeeded"
    _write_summary(root, summary)
    if log:
        log(
            "[company-factbooks] done "
            f"status={summary['status']} order_rows={summary['merged_order_rows']} "
            f"documents={summary['merged_source_documents']} errors={len(summary['errors'])}"
        )
    return summary


def validate_factbook_against_yuho(root: Path, output_path: Optional[Path] = None) -> Dict[str, Any]:
    cfg = _factbook_config(root)
    rows = _read_optional(_canonical_path(root, cfg))
    wide_path = root / "data" / "final" / "final_master_wide.csv"
    wide_rows = _read_optional(wide_path)
    field_rows = _read_optional(root / "data" / "final" / "final_master_field_definition.csv") or _read_optional(root / "config" / "field_definition.csv")
    field_names = {str(row.get("field_id") or ""): str(row.get("field_name_ja") or "") for row in field_rows}
    wide_by_company_year = {str(row.get("company_year_id") or ""): row for row in wide_rows}
    validation_cfg = cfg.get("validation") or {}
    tolerance_abs = _config_float(validation_cfg, "absolute_tolerance_million_yen", 100)
    tolerance_pct = _config_float(validation_cfg, "relative_tolerance", 0.005)

    validation_rows: List[Dict[str, Any]] = []
    for row in rows:
        validation_rows.append(_validate_factbook_row(row, cfg, wide_by_company_year, field_names, tolerance_abs, tolerance_pct))

    output = output_path or root / "data" / "reports" / "company_factbook_yuho_validation.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    validation_output_rows = _validation_columns(validation_rows)
    write_table(output, validation_output_rows)
    json_output = _write_json_sidecar(output, validation_output_rows)
    pending_rows = [row for row in validation_output_rows if str(row.get("validation_status") or "") not in {"pass", "forecast_not_checked"}]
    pending_path = root / "data" / "reports" / "company_factbook_pending_rows.csv"
    write_table(pending_path, pending_rows)
    pending_json_path = _write_json_sidecar(pending_path, pending_rows)
    counts: Dict[str, int] = {}
    for row in validation_rows:
        status = str(row.get("validation_status") or "")
        counts[status] = counts.get(status, 0) + 1
    comparable_rows = counts.get("pass", 0) + counts.get("mismatch", 0)
    incomplete_rows = sum(
        counts.get(status, 0)
        for status in ["missing_yuho_row", "missing_yuho_value", "missing_factbook_value", "no_mapping"]
    )
    if counts.get("mismatch", 0):
        status = "mismatch"
    elif incomplete_rows:
        status = "incomplete"
    else:
        status = "completed"
    summary = {
        "status": status,
        "validated_at_utc": _now_utc(),
        "rows": len(validation_rows),
        "comparable_rows": comparable_rows,
        "incomplete_rows": incomplete_rows,
        "status_counts": counts,
        "output_path": str(output),
        "json_output_path": str(json_output),
        "pending_rows": len(pending_rows),
        "pending_output_path": str(pending_path),
        "pending_json_output_path": str(pending_json_path),
        "wide_path": str(wide_path),
        "absolute_tolerance_million_yen": tolerance_abs,
        "relative_tolerance": tolerance_pct,
    }
    summary_path = root / "data" / "reports" / "company_factbook_yuho_validation_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def factbook_validation_summary(root: Path, sample_limit: int = 12) -> Dict[str, Any]:
    summary_path = root / "data" / "reports" / "company_factbook_yuho_validation_summary.json"
    validation_path = root / "data" / "reports" / "company_factbook_yuho_validation.csv"
    pending_path = root / "data" / "reports" / "company_factbook_pending_rows.csv"
    summary = _read_json(summary_path)
    rows = _read_optional(validation_path)
    pending_rows = _read_optional(pending_path)

    if not rows and summary:
        return {
            **summary,
            "output_exists": validation_path.exists(),
            "pending_output_exists": pending_path.exists(),
            "by_status": summary.get("status_counts") or {},
            "by_category_type": {},
            "by_source_metric_id": {},
            "by_status_category": {},
            "top_no_mapping_categories": [],
            "top_missing_yuho_fields": [],
            "pending_samples": [],
        }

    by_status = _count_by(rows, "validation_status")
    by_category_type = _count_by(rows, "category_type")
    by_source_metric_id = _count_by(rows, "source_metric_id")
    by_status_category: Dict[str, Dict[str, int]] = {}
    for row in rows:
        status = str(row.get("validation_status") or "")
        category_type = str(row.get("category_type") or "")
        if not status:
            continue
        by_status_category.setdefault(status, {})
        by_status_category[status][category_type] = by_status_category[status].get(category_type, 0) + 1

    no_mapping_rows = [row for row in rows if str(row.get("validation_status") or "") == "no_mapping"]
    missing_value_rows = [row for row in rows if str(row.get("validation_status") or "") == "missing_yuho_value"]
    top_no_mapping_categories = _top_row_groups(no_mapping_rows, ["category_type", "use_category_normalized", "use_category_label", "source_metric_id"])
    top_missing_yuho_fields = _top_row_groups(missing_value_rows, ["yuho_field_id", "yuho_field_name", "category_type", "source_metric_id"])
    sample_columns = [
        "company_name",
        "fiscal_year",
        "validation_status",
        "validation_message",
        "source_metric_id",
        "category_type",
        "use_category_label",
        "factbook_amount_million_yen",
        "yuho_field_id",
        "yuho_value_million_yen",
        "source_quote",
    ]
    samples = [
        {column: row.get(column, "") for column in sample_columns}
        for row in pending_rows[: max(0, sample_limit)]
    ]

    return {
        **summary,
        "rows": int(summary.get("rows") or len(rows)),
        "pending_rows": int(summary.get("pending_rows") or len(pending_rows)),
        "output_path": str(validation_path),
        "pending_output_path": str(pending_path),
        "output_exists": validation_path.exists(),
        "pending_output_exists": pending_path.exists(),
        "by_status": by_status,
        "by_category_type": by_category_type,
        "by_source_metric_id": by_source_metric_id,
        "by_status_category": by_status_category,
        "top_no_mapping_categories": top_no_mapping_categories,
        "top_missing_yuho_fields": top_missing_yuho_fields,
        "pending_samples": samples,
    }


def build_factbook_target_coverage(root: Path, output_path: Optional[Path] = None) -> Dict[str, Any]:
    cfg = _factbook_config(root)
    rows = _read_optional(_canonical_path(root, cfg))
    docs = _read_optional(_source_documents_path(root, cfg))
    companies = _companies_from_master(root)
    for source in _configured_sources(cfg):
        company_id = str(source.get("company_id") or "")
        if company_id:
            companies.setdefault(
                company_id,
                {
                    "id": company_id,
                    "name": str(source.get("company_name") or company_id),
                    "label": _company_label({"id": company_id, "name": str(source.get("company_name") or company_id)}),
                },
            )
    for row in list(rows) + list(docs):
        company_id = str(row.get("company_id") or "")
        if company_id:
            companies.setdefault(
                company_id,
                {
                    "id": company_id,
                    "name": str(row.get("company_name") or company_id),
                    "label": _company_label({"id": company_id, "name": str(row.get("company_name") or company_id)}),
                },
            )

    target_metrics = _target_metrics(cfg)
    coverage_rows: List[Dict[str, Any]] = []
    common_categories = ";".join(_common_use_category_labels(cfg))
    for company_id, company in sorted(companies.items()):
        company_docs = [row for row in docs if str(row.get("company_id") or "") == company_id]
        company_rows = [row for row in rows if str(row.get("company_id") or "") == company_id]
        for metric in target_metrics:
            metric_id = str(metric.get("id") or "")
            target_docs = [row for row in company_docs if metric_id in _target_metric_ids_from_row(row)]
            parsed_rows = [
                row
                for row in company_rows
                if str(row.get("extraction_status") or "") == "parsed"
                and _factbook_row_matches_target_metric(row, metric_id)
            ]
            if parsed_rows:
                status = "parsed"
                message = "用途別の数値行を抽出済みです。"
            elif target_docs:
                status = "candidate_documents"
                message = "公式資料候補はあります。用途別表の構造化パーサー追加が必要です。"
            else:
                status = "no_candidate"
                message = "現時点の公式資料候補では用途別表を確認できていません。非開示の可能性もあります。"
            coverage_rows.append(
                {
                    "company_id": company_id,
                    "company_name": company.get("name", ""),
                    "target_metric_id": metric_id,
                    "target_metric_name": metric.get("name", metric_id),
                    "candidate_documents": len(target_docs),
                    "parsed_rows": len(parsed_rows),
                    "latest_candidate_year": _latest_period(target_docs),
                    "latest_parsed_year": _latest_period(parsed_rows),
                    "coverage_status": status,
                    "coverage_message": message,
                    "common_use_categories": common_categories,
                }
            )

    output = output_path or root / "data" / "reports" / "company_factbook_target_coverage.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    coverage_output_rows = _target_coverage_columns(coverage_rows)
    write_table(output, coverage_output_rows)
    json_output = _write_json_sidecar(output, coverage_output_rows)
    status_counts: Dict[str, int] = {}
    for row in coverage_rows:
        status = str(row.get("coverage_status") or "")
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = {
        "status": "completed",
        "built_at_utc": _now_utc(),
        "rows": len(coverage_rows),
        "target_metrics": [metric["id"] for metric in target_metrics],
        "common_use_categories": _common_use_category_labels(cfg),
        "status_counts": status_counts,
        "output_path": str(output),
        "json_output_path": str(json_output),
    }
    summary_path = root / "data" / "reports" / "company_factbook_target_coverage_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def _parse_source(
    root: Path,
    cfg: Dict[str, Any],
    source: Dict[str, Any],
    raw_dir: Path,
    fetch: FetchText,
    fetched_at: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    parser = str(source.get("parser") or "")
    if parser == "irpocket_json_chart":
        return _parse_irpocket_json_chart(source, raw_dir, fetch, fetched_at)
    if parser == "link_index":
        return [], _parse_link_index(source, raw_dir, fetch, fetched_at), []
    if parser == "html_tables":
        return _parse_html_tables(root, cfg, source, raw_dir, fetch, fetched_at)
    return [], [], [f"unsupported parser: {parser} source={source.get('id', '')}"]


def _parse_irpocket_json_chart(
    source: Dict[str, Any],
    raw_dir: Path,
    fetch: FetchText,
    fetched_at: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    data_url = str(source.get("data_url") or "")
    style_url = str(source.get("style_url") or "")
    data_text = fetch(data_url, source)
    style_text = fetch(style_url, source) if style_url else "{}"
    source_dir = raw_dir / _safe_name(str(source.get("id") or "source"))
    source_dir.mkdir(parents=True, exist_ok=True)
    data_file = source_dir / "data.json"
    style_file = source_dir / "style.json"
    data_file.write_text(data_text, encoding="utf-8")
    style_file.write_text(style_text, encoding="utf-8")
    data = json.loads(data_text)
    style = json.loads(style_text) if style_text.strip() else {}
    series_names = [str(item.get("name") or "") for item in style.get("series", []) if isinstance(item, dict)]
    categories = data.get("categories", []) or []
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for series_index, series in enumerate(data.get("series", []) or []):
        raw_category = series_names[series_index] if series_index < len(series_names) else str(series.get("name") or f"series_{series_index + 1}")
        values = series.get("data", []) or []
        for index, category in enumerate(categories):
            if index >= len(values):
                continue
            value = _safe_float((values[index] or {}).get("y") if isinstance(values[index], dict) else values[index])
            if value is None:
                continue
            period = _parse_irpocket_period(category)
            rows.append(
                {
                    "company_id": source.get("company_id", ""),
                    "company_name": source.get("company_name", ""),
                    "fiscal_year": period["fiscal_year"],
                    "fiscal_year_end": period["fiscal_year_end"],
                    "period_type": period["period_type"],
                    "period_label": period["period_label"],
                    "source_company_id": source.get("company_id", ""),
                    "source_doc_type": source.get("source_doc_type", ""),
                    "source_dataset_id": source.get("source_dataset_id", source.get("id", "")),
                    "source_metric_id": source.get("source_metric_id", ""),
                    "category_type": source.get("category_type", ""),
                    "scope": source.get("scope", ""),
                    "business_scope": source.get("business_scope", ""),
                    "use_category_raw": raw_category,
                    "order_amount": value,
                    "unit": source.get("unit", "億円"),
                    "amount_million_yen": _amount_million_yen(value, str(source.get("unit") or "億円")),
                    "source_url": data_url,
                    "source_page": source.get("source_page_url", ""),
                    "source_table_title": source.get("source_table_title", ""),
                    "source_quote": f"{raw_category} {period['period_label']} {value}{source.get('unit', '億円')}",
                    "source_file": str(data_file),
                    "extraction_status": "parsed",
                    "fetched_at_utc": fetched_at,
                }
            )
    documents = [
        _document_row(source, data_url, source.get("source_table_title", "IRPocket data JSON"), "json", "parsed", fetched_at, note=str(source.get("note", ""))),
    ]
    if not rows:
        warnings.append(f"no rows parsed from {source.get('id', '')}")
    return rows, documents, warnings


def _parse_link_index(source: Dict[str, Any], raw_dir: Path, fetch: FetchText, fetched_at: str) -> List[Dict[str, Any]]:
    url = str(source.get("source_page_url") or "")
    html = fetch(url, source)
    source_dir = raw_dir / _safe_name(str(source.get("id") or "source"))
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "index.html").write_text(html, encoding="utf-8")
    docs = _extract_link_documents(source, url, html, fetched_at)
    if _bool(source.get("follow_links"), False):
        docs.extend(_follow_link_index_pages(source, source_dir, url, html, fetch, fetched_at))
    if not docs and url:
        docs.append(
            _document_row(
                source,
                url,
                source.get("source_table_title") or source.get("source_doc_type") or source.get("id") or url,
                "html",
                "pending_parser",
                fetched_at,
                note=f"{source.get('note', '')} 公式入口ページを保存。HTML内に対象ダウンロードリンクが見つからない場合はJS/API型ページの可能性あり。".strip(),
            )
        )
    return _dedupe_documents(docs)


def _parse_candidate_documents(
    root: Path,
    cfg: Dict[str, Any],
    source: Dict[str, Any],
    raw_dir: Path,
    documents: List[Dict[str, Any]],
    text_fetcher: Optional[FetchText],
    fetched_at: str,
    *,
    ai_runner: Optional[AiRunner] = None,
    ai_config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], int]:
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    parsed_documents: List[Dict[str, Any]] = []
    ai_calls_made = 0
    source_dir = raw_dir / _safe_name(str(source.get("id") or "source"))
    source_dir.mkdir(parents=True, exist_ok=True)
    for index, document in enumerate(documents, start=1):
        ext = str(document.get("file_ext") or "").lower().lstrip(".")
        if ext not in {"pdf", "xlsx", "xlsm", "xltx", "xltm", "xls", "csv", "zip"}:
            parsed_documents.append(document)
            continue
        url = str(document.get("url") or "")
        if not url:
            parsed_documents.append(document)
            continue
        try:
            filename = _safe_name(str(document.get("file_name") or Path(urlparse(url).path).name or f"document_{index}.{ext}"))
            if not Path(filename).suffix:
                filename = f"{filename}.{ext}"
            path = source_dir / filename
            path.write_bytes(_fetch_document_bytes(url, source, text_fetcher))
            parser_source = {
                **source,
                **document,
                "source_url": url,
                "url": url,
                "source_page_url": source.get("source_page_url", ""),
                "source_metric_id": document.get("source_metric_id") or source.get("source_metric_id", ""),
                "category_type": document.get("category_type") or source.get("category_type", ""),
                "period_type": document.get("period_type") or "annual",
                "period_label": document.get("period_label") or source.get("period_label", ""),
                "fiscal_year": document.get("fiscal_year") or source.get("fiscal_year", ""),
            }
            ai_call_results: List[Any] = []
            parsed_rows, parser_warnings = factbook_parsers.parse_document(
                path,
                parser_source,
                cfg,
                fetched_at,
                ai_runner=ai_runner,
                ai_config=ai_config,
                ai_call_results=ai_call_results,
            )
            if ai_call_results:
                ai_calls_made += len(ai_call_results)
                _record_factbook_ai_calls(root, ai_call_results)
            rows.extend(parsed_rows)
            warnings.extend(parser_warnings)
            parsed_documents.append(
                {
                    **document,
                    "parser_status": "parsed" if parsed_rows else "pending_parser",
                    "note": _append_note(document.get("note", ""), "機械抽出済み" if parsed_rows else "機械抽出では用途別数値を確定できませんでした"),
                }
            )
        except Exception as exc:
            warnings.append(f"document parse failed source={source.get('id', '')} url={url}: {exc}")
            parsed_documents.append({**document, "parser_status": "pending_parser", "note": _append_note(document.get("note", ""), f"document parse failed: {exc}")})
    return rows, parsed_documents, warnings, ai_calls_made


def _extract_link_documents(source: Dict[str, Any], url: str, html: str, fetched_at: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    docs: List[Dict[str, Any]] = []
    for link in soup.find_all("a", href=True):
        text = " ".join(link.get_text(" ", strip=True).split())
        href = str(link.get("href") or "")
        absolute = urljoin(url, href)
        heading_context = _nearest_heading_text(link)
        if not _link_matches(f"{text} {heading_context}", href, source):
            continue
        context = f"{text} {href} {heading_context}"
        fiscal_year, period_label = _infer_fiscal_year_period(context)
        ext = Path(urlparse(absolute).path).suffix.lower().lstrip(".") or "html"
        docs.append(
            {
                **_document_row(source, absolute, text or Path(urlparse(absolute).path).name, ext, "pending_parser", fetched_at, note=str(source.get("note", ""))),
                "fiscal_year": fiscal_year,
                "period_label": period_label or text,
                "period_type": _infer_period_type(text + " " + href),
            }
        )
    return docs


def _follow_link_index_pages(
    source: Dict[str, Any],
    source_dir: Path,
    base_url: str,
    html: str,
    fetch: FetchText,
    fetched_at: str,
) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc
    max_follow = int(source.get("max_follow_links") or 8)
    docs: List[Dict[str, Any]] = []
    followed = 0
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        if followed >= max_follow:
            break
        text = " ".join(link.get_text(" ", strip=True).split())
        href = str(link.get("href") or "")
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc and parsed.netloc != base_host:
            continue
        if absolute in seen or _looks_like_download(absolute):
            continue
        heading_context = _nearest_heading_text(link)
        if not _follow_link_matches(f"{text} {heading_context}", href, source):
            continue
        seen.add(absolute)
        try:
            sub_html = fetch(absolute, source)
        except Exception:
            continue
        followed += 1
        (source_dir / f"follow_{followed:02d}.html").write_text(sub_html, encoding="utf-8")
        docs.extend(_extract_link_documents(source, absolute, sub_html, fetched_at))
    return docs


def _parse_html_tables(
    root: Path,
    cfg: Dict[str, Any],
    source: Dict[str, Any],
    raw_dir: Path,
    fetch: FetchText,
    fetched_at: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    try:
        import pandas as pd  # type: ignore
    except ImportError:
        return [], [], ["html_tables parser requires pandas"]
    url = str(source.get("source_page_url") or "")
    html = fetch(url, source)
    source_dir = raw_dir / _safe_name(str(source.get("id") or "source"))
    source_dir.mkdir(parents=True, exist_ok=True)
    html_file = source_dir / "index.html"
    html_file.write_text(html, encoding="utf-8")
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    try:
        tables = pd.read_html(html)
    except ValueError:
        return [], [_document_row(source, url, source.get("source_table_title", "HTML"), "html", "pending_parser", fetched_at)], ["no HTML tables found"]
    for table_index, table in enumerate(tables):
        text = table.to_csv(index=False)
        if not _table_matches(text, source):
            continue
        rows.extend(_parse_table_records(source, table, fetched_at, str(html_file), table_index))
    if not rows:
        warnings.append(f"no matched rows parsed from html tables: {source.get('id', '')}")
    return rows, [_document_row(source, url, source.get("source_table_title", "HTML"), "html", "parsed" if rows else "pending_parser", fetched_at)], warnings


def _load_manual_csv_rows(root: Path, cfg: Dict[str, Any], fetched_at: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    docs: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for pattern in cfg.get("manual_csv_globs", []) or []:
        glob_path = root / str(pattern)
        for path_text in glob.glob(str(glob_path)):
            path = Path(path_text)
            try:
                manual = read_table(path)
            except Exception as exc:
                warnings.append(f"manual CSV read failed {path}: {exc}")
                continue
            for raw in manual:
                rows.append(_manual_order_row(raw, str(path), fetched_at))
            docs.append(
                {
                    "source_dataset_id": "manual_csv",
                    "company_id": "",
                    "company_name": "",
                    "source_doc_type": "manual_csv",
                    "source_metric_id": "building_orders_by_use",
                    "category_type": "use",
                    "fiscal_year": "",
                    "period_type": "manual",
                    "period_label": path.name,
                    "title": path.name,
                    "url": str(path),
                    "file_name": path.name,
                    "file_ext": "csv",
                    "parser_status": "parsed",
                    "note": "ユーザーがCSV化して配置した用途別受注データ",
                    "discovered_at_utc": fetched_at,
                }
            )
    return rows, docs, warnings


def _manual_order_row(raw: Dict[str, Any], path: str, fetched_at: str) -> Dict[str, Any]:
    amount = _safe_float(_first_value(raw, ["order_amount", "amount", "value", "受注高", "金額"]))
    unit = str(_first_value(raw, ["unit", "単位"]) or "億円")
    company_id = str(_first_value(raw, ["company_id", "operating_company_id", "会社ID"]) or "")
    fiscal_year = str(_first_value(raw, ["fiscal_year", "年度"]) or "")
    raw_category = str(_first_value(raw, ["use_category_raw", "category", "用途", "用途区分"]) or "")
    return {
        "company_id": company_id,
        "company_name": _first_value(raw, ["company_name", "operating_company_name", "会社名"]) or "",
        "fiscal_year": fiscal_year,
        "fiscal_year_end": _first_value(raw, ["fiscal_year_end", "決算日"]) or "",
        "period_type": _first_value(raw, ["period_type", "期間種別"]) or "annual",
        "period_label": _first_value(raw, ["period_label", "期間"]) or fiscal_year,
        "source_company_id": company_id,
        "source_doc_type": _first_value(raw, ["source_doc_type", "資料種別"]) or "manual_csv",
        "source_dataset_id": _first_value(raw, ["source_dataset_id"]) or "manual_csv",
        "source_metric_id": _first_value(raw, ["source_metric_id"]) or "building_orders_by_use",
        "category_type": _first_value(raw, ["category_type"]) or "use",
        "scope": _first_value(raw, ["scope", "スコープ"]) or "standalone",
        "business_scope": _first_value(raw, ["business_scope"]) or "building_orders",
        "use_category_raw": raw_category,
        "use_category_normalized": _first_value(raw, ["use_category_normalized", "normalized_category"]) or "",
        "order_amount": amount,
        "unit": unit,
        "amount_million_yen": _amount_million_yen(amount, unit),
        "source_url": _first_value(raw, ["source_url", "url"]) or path,
        "source_page": _first_value(raw, ["source_page"]) or "",
        "source_table_title": _first_value(raw, ["source_table_title", "table_title"]) or "",
        "source_quote": _first_value(raw, ["source_quote", "quote", "引用"]) or "",
        "source_file": path,
        "extraction_status": "parsed",
        "fetched_at_utc": fetched_at,
    }


def _parse_table_records(source: Dict[str, Any], table: Any, fetched_at: str, source_file: str, table_index: int) -> List[Dict[str, Any]]:
    # Generic fallback: expects first column to be category and remaining columns to be years.
    out: List[Dict[str, Any]] = []
    columns = [str(column) for column in table.columns]
    if len(columns) < 2:
        return out
    category_column = columns[0]
    for _, record in table.iterrows():
        raw_category = str(record.get(category_column) or "").strip()
        if not raw_category:
            continue
        for column in columns[1:]:
            fiscal_year, period_label = _infer_fiscal_year_period(column)
            value = _safe_float(record.get(column))
            if value is None:
                continue
            out.append(
                {
                    "company_id": source.get("company_id", ""),
                    "company_name": source.get("company_name", ""),
                    "fiscal_year": fiscal_year,
                    "fiscal_year_end": "",
                    "period_type": "annual",
                    "period_label": period_label or column,
                    "source_company_id": source.get("company_id", ""),
                    "source_doc_type": source.get("source_doc_type", ""),
                    "source_dataset_id": source.get("source_dataset_id", source.get("id", "")),
                    "source_metric_id": source.get("source_metric_id", ""),
                    "category_type": source.get("category_type", ""),
                    "scope": source.get("scope", ""),
                    "business_scope": source.get("business_scope", ""),
                    "use_category_raw": raw_category,
                    "order_amount": value,
                    "unit": source.get("unit", "億円"),
                    "amount_million_yen": _amount_million_yen(value, str(source.get("unit") or "億円")),
                    "source_url": source.get("source_page_url", ""),
                    "source_page": source.get("source_page_url", ""),
                    "source_table_title": source.get("source_table_title", f"table_{table_index}"),
                    "source_quote": f"{raw_category} {column} {value}{source.get('unit', '億円')}",
                    "source_file": source_file,
                    "extraction_status": "parsed",
                    "fetched_at_utc": fetched_at,
                }
            )
    return out


def _normalize_order_rows(root: Path, cfg: Dict[str, Any], rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    company_names = _company_names(root)
    normalized = []
    for row in rows:
        copied = dict(row)
        company_id = str(copied.get("company_id") or copied.get("operating_company_id") or "")
        raw_category = str(copied.get("use_category_raw") or "")
        normalized_category = str(copied.get("use_category_normalized") or "") or _normalize_category(raw_category, cfg)
        copied["company_id"] = company_id
        copied["company_name"] = copied.get("company_name") or company_names.get(company_id, "")
        copied["use_category_normalized"] = normalized_category
        copied["use_category_label"] = _category_label(normalized_category, raw_category, cfg)
        copied["order_amount"] = _clean_number(copied.get("order_amount"))
        copied["amount_million_yen"] = _clean_number(copied.get("amount_million_yen"))
        copied["fetched_at_utc"] = copied.get("fetched_at_utc") or _now_utc()
        normalized.append({column: copied.get(column, "") for column in ORDER_COLUMNS})
    return normalized


def _normalize_document_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{column: row.get(column, "") for column in SOURCE_DOCUMENT_COLUMNS} for row in rows]


def _order_columns(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{column: row.get(column, "") for column in ORDER_COLUMNS} for row in rows]


def _document_columns(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{column: row.get(column, "") for column in SOURCE_DOCUMENT_COLUMNS} for row in rows]


def _validation_columns(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{column: row.get(column, "") for column in VALIDATION_COLUMNS} for row in rows]


def _count_by(rows: Iterable[Dict[str, Any]], column: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(column) or "")
        if not value:
            value = "(blank)"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _top_row_groups(rows: Iterable[Dict[str, Any]], columns: Sequence[str], limit: int = 12) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, ...], int] = {}
    for row in rows:
        key = tuple(str(row.get(column) or "") for column in columns)
        groups[key] = groups.get(key, 0) + 1
    out: List[Dict[str, Any]] = []
    for key, count in sorted(groups.items(), key=lambda item: (-item[1], item[0]))[:limit]:
        grouped = {column: value for column, value in zip(columns, key)}
        grouped["count"] = count
        out.append(grouped)
    return out


def _target_coverage_columns(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{column: row.get(column, "") for column in TARGET_COVERAGE_COLUMNS} for row in rows]


def _factbook_row_matches_target_metric(row: Dict[str, Any], metric_id: str) -> bool:
    source_metric_id = str(row.get("source_metric_id") or "")
    category_type = str(row.get("category_type") or "")
    if source_metric_id == metric_id and category_type == "use":
        return True
    if metric_id == "building_orders_by_use" and source_metric_id == "building_orders_by_business_scope":
        return category_type == "business_scope"
    if metric_id == "completed_building_by_use" and source_metric_id == "completed_building_by_business_scope":
        return category_type == "business_scope"
    return False


def _factbook_ai_config(root: Path, tier: str) -> Dict[str, Any]:
    ai_config = load_ai_config(root)
    tiers = ai_config.get("tiers") or {}
    tier_cfg = tiers.get(tier) or {}
    return {
        "tier": tier,
        "tier_config": tier_cfg,
        "timeout_seconds": ai_config.get("timeout_seconds"),
    }


def _record_factbook_ai_calls(root: Path, call_results: Sequence[Any]) -> None:
    if not call_results:
        return
    with semantics_store.connect(root) as conn:
        for call_result in call_results:
            semantics_store.insert_ai_call(conn, ai_call_result_to_ai_calls_record(call_result))


def _write_json_sidecar(path: Path, rows: Iterable[Dict[str, Any]]) -> Path:
    json_path = path.with_suffix(".json")
    write_table(json_path, list(rows))
    return json_path


def _category_field_options(root: Path, rows: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    fields: Dict[str, Dict[str, str]] = {}
    for row in rows:
        field_id = _category_field_id(row)
        if not field_id:
            continue
        category_label = str(row.get("use_category_label") or row.get("use_category_raw") or field_id)
        category_type = str(row.get("category_type") or "category")
        fields.setdefault(
            field_id,
            {
                "id": field_id,
                "name": category_label,
                "category": category_type,
                "unit": str(row.get("unit") or "億円"),
                "label": f"{category_label} ({category_type})",
            },
        )
    return sorted(fields.values(), key=lambda item: (item["category"], item["name"]))


def _category_field_id(row: Dict[str, Any]) -> str:
    normalized = str(row.get("use_category_normalized") or "").strip()
    raw = str(row.get("use_category_raw") or "").strip()
    category_type = str(row.get("category_type") or "category").strip() or "category"
    slug = _slug(normalized or raw)
    return f"order_amount__{category_type}__{slug}" if slug else ""


def _field_option(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or row.get("id") or ""),
        "category": str(row.get("category") or ""),
        "unit": str(row.get("unit") or ""),
        "label": str(row.get("label") or f"{row.get('name', row.get('id', ''))} ({row.get('id', '')})"),
    }


def _document_row(source: Dict[str, Any], url: str, title: Any, ext: str, parser_status: str, discovered_at: str, note: str = "") -> Dict[str, Any]:
    parsed = urlparse(url)
    file_name = Path(parsed.path).name or str(title or "")
    return {
        "source_dataset_id": source.get("source_dataset_id", source.get("id", "")),
        "company_id": source.get("company_id", ""),
        "company_name": source.get("company_name", ""),
        "source_doc_type": source.get("source_doc_type", ""),
        "source_metric_id": source.get("source_metric_id", ""),
        "target_metric_ids": ";".join(_source_target_metric_ids(source)),
        "category_type": source.get("category_type", ""),
        "fiscal_year": "",
        "period_type": "",
        "period_label": "",
        "title": str(title or file_name),
        "url": url,
        "file_name": file_name,
        "file_ext": ext,
        "parser_status": parser_status,
        "note": note,
        "discovered_at_utc": discovered_at,
    }


def _configured_sources(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources = [dict(source) for source in (cfg.get("sources", []) or [])]
    defaults = dict(cfg.get("document_source_defaults") or {})
    for item in cfg.get("company_document_sources", []) or []:
        source = {**defaults, **dict(item)}
        company_id = str(source.get("company_id") or "").strip()
        if not company_id:
            continue
        source.setdefault("id", f"{company_id.lower()}_company_factbook_documents")
        source.setdefault("parser", "link_index")
        source.setdefault("enabled", True)
        source.setdefault("source_doc_type", "factbook_or_ir_data")
        source.setdefault("source_dataset_id", f"{company_id.lower()}_company_factbook_documents")
        source.setdefault("source_metric_id", "building_use_metrics")
        source.setdefault("category_type", "use")
        source.setdefault("scope", "mixed")
        source.setdefault("business_scope", "building")
        if not source.get("target_metric_ids"):
            source["target_metric_ids"] = [metric["id"] for metric in DEFAULT_TARGET_METRICS]
        sources.append(source)
    return sources


def _target_metrics(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    configured = cfg.get("target_metrics") or []
    metrics = configured if isinstance(configured, list) and configured else DEFAULT_TARGET_METRICS
    out: List[Dict[str, str]] = []
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        metric_id = str(metric.get("id") or "").strip()
        if metric_id:
            out.append({"id": metric_id, "name": str(metric.get("name") or metric_id)})
    return out or [dict(metric) for metric in DEFAULT_TARGET_METRICS]


def _source_target_metric_ids(source: Dict[str, Any]) -> List[str]:
    raw = source.get("target_metric_ids")
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
        if values:
            return values
    if isinstance(raw, str):
        values = [item.strip() for item in re.split(r"[;,、\s]+", raw) if item.strip()]
        if values:
            return values
    metric_id = str(source.get("source_metric_id") or "").strip()
    if metric_id in {"building_use_metrics", "major_indicators"}:
        return [metric["id"] for metric in DEFAULT_TARGET_METRICS]
    return [metric_id] if metric_id else []


def _target_metric_ids_from_row(row: Dict[str, Any]) -> List[str]:
    raw = str(row.get("target_metric_ids") or "")
    values = [item.strip() for item in re.split(r"[;,、\s]+", raw) if item.strip()]
    if values:
        return values
    return _source_target_metric_ids(row)


def _common_use_category_labels(cfg: Dict[str, Any]) -> List[str]:
    configured = cfg.get("common_use_categories") or []
    if isinstance(configured, list) and configured:
        labels = []
        for item in configured:
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("name") or item.get("id") or "").strip()
            else:
                label = str(item).strip()
            if label:
                labels.append(label)
        if labels:
            return labels
    labels = cfg.get("category_labels", {}) or {}
    category_ids = [
        "office",
        "logistics",
        "factory",
        "housing",
        "commercial",
        "medical_welfare",
        "education_research",
        "lodging",
        "public_cultural",
        "other_use",
    ]
    return [str(labels.get(category_id) or category_id) for category_id in category_ids]


def _dedupe_documents(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[Tuple[str, str]] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("source_dataset_id") or ""), str(row.get("url") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _link_matches(text: str, href: str, source: Dict[str, Any]) -> bool:
    combined = f"{text} {href}"
    text_filters = [str(item) for item in source.get("link_text_includes", []) or [] if str(item)]
    href_filters = [str(item) for item in source.get("href_includes", []) or [] if str(item)]
    match_mode = str(source.get("link_match_mode") or "all")
    if match_mode == "any":
        text_hit = any(item in text for item in text_filters) if text_filters else False
        href_hit = any(item in href for item in href_filters) if href_filters else False
        return text_hit or href_hit
    if text_filters and not any(item in text for item in text_filters):
        return False
    if href_filters and not any(item in href for item in href_filters):
        return False
    if not text_filters and not href_filters:
        return any(token in combined.lower() for token in [".pdf", ".xls", ".xlsx"])
    return True


def _follow_link_matches(text: str, href: str, source: Dict[str, Any]) -> bool:
    text_filters = [str(item) for item in source.get("follow_link_text_includes", []) or [] if str(item)]
    href_filters = [str(item) for item in source.get("follow_href_includes", []) or [] if str(item)]
    if not text_filters and not href_filters:
        return False
    return any(item in text for item in text_filters) or any(item in href for item in href_filters)


def _looks_like_download(url: str) -> bool:
    ext = Path(urlparse(url).path).suffix.lower()
    return ext in {".pdf", ".xls", ".xlsx", ".csv", ".zip", ".doc", ".docx", ".ppt", ".pptx"}


def _table_matches(text: str, source: Dict[str, Any]) -> bool:
    keywords = [str(item) for item in source.get("table_text_includes", []) or [] if str(item)]
    if not keywords:
        return True
    return all(keyword in text for keyword in keywords)


def _parse_irpocket_period(value: Any) -> Dict[str, str]:
    period_type = "annual"
    raw = value
    if isinstance(value, dict):
        period_type = "forecast_annual" if str(value.get("name") or "") == "latest" else "annual"
        categories = value.get("categories") or []
        raw = categories[0] if categories else ""
    text = str(raw or "")
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", text)
    if not match:
        return {"fiscal_year": text, "fiscal_year_end": "", "period_type": period_type, "period_label": text}
    year = int(match.group(1))
    month = int(match.group(2))
    fiscal_year = year - 1 if month <= 3 else year
    fiscal_year_end = f"{year:04d}-{month:02d}-31" if month == 3 else ""
    label = f"{year}年{month}月期"
    if period_type == "forecast_annual":
        label += "予想"
    return {
        "fiscal_year": str(fiscal_year),
        "fiscal_year_end": fiscal_year_end,
        "period_type": period_type,
        "period_label": label,
    }


def _infer_fiscal_year_period(text: str) -> Tuple[str, str]:
    match = re.search(r"(20\d{2})年\s*(\d{1,2})月期", text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        fiscal_year = year - 1 if month <= 3 else year
        return str(fiscal_year), f"{year}年{month}月期"
    match = re.search(r"(20\d{2})年度", text)
    if match:
        return match.group(1), f"{match.group(1)}年度"
    match = re.search(r"(20\d{2})", text)
    if match:
        return match.group(1), match.group(1)
    return "", ""


def _infer_period_type(text: str) -> str:
    if "第1四半期" in text:
        return "q1"
    if "第2四半期" in text or "中間" in text:
        return "q2"
    if "第3四半期" in text:
        return "q3"
    if "第4四半期" in text or "通期" in text:
        return "annual"
    return "document"


def _nearest_heading_text(link: Any) -> str:
    for heading in link.find_all_previous(["h1", "h2", "h3", "h4"], limit=8):
        text = " ".join(heading.get_text(" ", strip=True).split())
        if text:
            return text
    return ""


def _normalize_category(raw: str, cfg: Dict[str, Any]) -> str:
    text = _compact(raw)
    for category, keywords in (cfg.get("category_keyword_map", {}) or {}).items():
        for keyword in keywords or []:
            if _compact(str(keyword)) in text:
                return str(category)
    return _slug(raw) or "other_use"


def _category_label(normalized: str, raw: str, cfg: Dict[str, Any]) -> str:
    labels = cfg.get("category_labels", {}) or {}
    return str(labels.get(normalized) or raw or normalized)


def _amount_million_yen(value: Any, unit: str) -> Any:
    number = _safe_float(value)
    if number is None:
        return ""
    normalized_unit = str(unit)
    if "億" in normalized_unit:
        return round(number * 100, 6)
    if "百万円" in normalized_unit:
        return number
    if "千円" in normalized_unit:
        return round(number / 1000, 6)
    if "円" in normalized_unit:
        return round(number / 1_000_000, 6)
    return ""


def _fetch_text(url: str, cfg: Dict[str, Any]) -> str:
    headers = {"User-Agent": str(cfg.get("user_agent") or "Mozilla/5.0 BuildBase/company-factbooks")}
    timeout = int(cfg.get("request_timeout_seconds") or 30)
    retry_count = int(cfg.get("retry_count") or 2)
    backoff = float(cfg.get("retry_backoff_seconds") or 2)
    last_error: Optional[Exception] = None
    for attempt in range(retry_count + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt < retry_count:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"request failed for {url}: {last_error}")


def _fetch_document_bytes(url: str, cfg: Dict[str, Any], text_fetcher: Optional[FetchText] = None) -> bytes:
    if text_fetcher is not None:
        return text_fetcher(url, cfg).encode("utf-8")
    headers = {"User-Agent": str(cfg.get("user_agent") or "Mozilla/5.0 BuildBase/company-factbooks")}
    timeout = int(cfg.get("request_timeout_seconds") or 30)
    retry_count = int(cfg.get("retry_count") or 2)
    backoff = float(cfg.get("retry_backoff_seconds") or 2)
    last_error: Optional[Exception] = None
    for attempt in range(retry_count + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.content
        except Exception as exc:
            last_error = exc
            if attempt < retry_count:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"document request failed for {url}: {last_error}")


def _factbook_config(root: Path) -> Dict[str, Any]:
    path = root / "config" / "company_factbook_sources.yml"
    if not path.exists():
        return {"enabled": False, "sources": []}
    return read_yaml(path)


def _config_float(config: Dict[str, Any], key: str, default: float) -> float:
    value = config.get(key)
    if value is None or value == "":
        return float(default)
    return float(value)


def _validate_factbook_row(
    row: Dict[str, Any],
    cfg: Dict[str, Any],
    wide_by_company_year: Dict[str, Dict[str, Any]],
    field_names: Dict[str, str],
    tolerance_abs: float,
    tolerance_pct: float,
) -> Dict[str, Any]:
    company_id = str(row.get("company_id") or "")
    fiscal_year = str(row.get("fiscal_year") or "")
    factbook_value = _safe_float(row.get("amount_million_yen"))
    field_id = _yuho_field_for_factbook_row(row, cfg)
    base = {
        "company_id": company_id,
        "company_name": row.get("company_name", ""),
        "fiscal_year": fiscal_year,
        "period_type": row.get("period_type", ""),
        "period_label": row.get("period_label", ""),
        "source_metric_id": row.get("source_metric_id", ""),
        "category_type": row.get("category_type", ""),
        "use_category_raw": row.get("use_category_raw", ""),
        "use_category_normalized": row.get("use_category_normalized", ""),
        "use_category_label": row.get("use_category_label", ""),
        "factbook_value": row.get("order_amount", ""),
        "factbook_unit": row.get("unit", ""),
        "factbook_amount_million_yen": row.get("amount_million_yen", ""),
        "yuho_field_id": field_id,
        "yuho_field_name": field_names.get(field_id, ""),
        "source_dataset_id": row.get("source_dataset_id", ""),
        "source_url": row.get("source_url", ""),
        "source_file": row.get("source_file", ""),
        "source_quote": row.get("source_quote", ""),
    }
    if str(row.get("extraction_status") or "") != "parsed":
        return {**base, "validation_status": "not_parsed", "validation_message": "ファクトブック行が未抽出です。"}
    if str(row.get("period_type") or "").startswith("forecast"):
        return {**base, "validation_status": "forecast_not_checked", "validation_message": "予想値のため有報照合対象外です。"}
    if not field_id:
        return {**base, "validation_status": "no_mapping", "validation_message": "有報側の同一粒度項目が未定義です。"}
    if factbook_value is None:
        return {**base, "validation_status": "missing_factbook_value", "validation_message": "ファクトブック値が数値化できません。"}
    wide = wide_by_company_year.get(f"{company_id}_{fiscal_year}")
    if not wide:
        return {**base, "validation_status": "missing_yuho_row", "validation_message": "有報側の会社年度行がありません。"}
    yuho_value = _safe_float(wide.get(field_id))
    if yuho_value is None:
        return {**base, "validation_status": "missing_yuho_value", "validation_message": "有報側の照合項目が空欄です。"}
    diff = factbook_value - yuho_value
    diff_pct = abs(diff) / max(abs(yuho_value), 1.0)
    passed = abs(diff) <= tolerance_abs or diff_pct <= tolerance_pct
    return {
        **base,
        "yuho_value_million_yen": _clean_number(yuho_value),
        "diff_million_yen": _clean_number(diff),
        "diff_pct": round(diff_pct, 6),
        "validation_status": "pass" if passed else "mismatch",
        "validation_message": "許容差内です。" if passed else "ファクトブック値と有報値が許容差を超えています。",
    }


def _yuho_field_for_factbook_row(row: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    configured = cfg.get("yuho_field_mappings") or {}
    category_type = str(row.get("category_type") or "")
    normalized = str(row.get("use_category_normalized") or "")
    raw = str(row.get("use_category_raw") or "")
    for key in (
        f"{category_type}:{normalized}",
        f"{category_type}:{raw}",
        normalized,
        raw,
    ):
        if key and configured.get(key):
            return str(configured[key])
    return DEFAULT_FACTBOOK_YUHO_FIELD_MAP.get((category_type, normalized), "")


def _canonical_path(root: Path, cfg: Dict[str, Any]) -> Path:
    return root / str(cfg.get("canonical_store") or "data/marts/company_factbooks/building_orders_by_category.csv")


def _source_documents_path(root: Path, cfg: Dict[str, Any]) -> Path:
    return root / str(cfg.get("source_document_store") or "data/marts/company_factbooks/source_documents.csv")


def _raw_store(root: Path, cfg: Dict[str, Any]) -> Path:
    return root / str(cfg.get("raw_store") or "data/raw/company_factbooks")


def _write_summary(root: Path, summary: Dict[str, Any]) -> None:
    path = root / "data" / "automation" / "company_factbooks_last.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_optional(path: Path) -> List[Dict[str, Any]]:
    return read_table(path) if path.exists() else []


def _append_note(note: Any, addition: str) -> str:
    base = str(note or "").strip()
    return f"{base} {addition}".strip() if base else addition


def _companies_from_master(root: Path) -> Dict[str, Dict[str, str]]:
    companies = {}
    path = root / "config" / "company_master.csv"
    if not path.exists():
        return companies
    for row in read_table(path):
        company_id = str(row.get("operating_company_id") or "")
        if not company_id:
            continue
        name = str(row.get("operating_company_name") or "")
        companies[company_id] = {"id": company_id, "name": name, "label": f"{name}（{company_id}）" if name else company_id}
    return companies


def _company_names(root: Path) -> Dict[str, str]:
    return {company_id: row["name"] for company_id, row in _companies_from_master(root).items()}


def _company_label(row: Dict[str, str]) -> str:
    name = row.get("name", "")
    company_id = row.get("id", "")
    return f"{name}（{company_id}）" if name else company_id


def _chart_companies(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    by_id = {}
    for row in rows:
        company_id = str(row.get("operating_company_id") or "")
        if not company_id:
            continue
        name = str(row.get("operating_company_name") or "")
        by_id[company_id] = {"id": company_id, "name": name, "label": f"{name}（{company_id}）" if name else company_id}
    return sorted(by_id.values(), key=lambda item: item["id"])


def _latest_period(rows: Sequence[Dict[str, Any]]) -> str:
    periods = [str(row.get("fiscal_year") or "") for row in rows if row.get("fiscal_year")]
    return max(periods, key=_period_sort_key) if periods else ""


def _matches_requested_fiscal_year(row: Dict[str, Any], requested_fiscal_years: set[str]) -> bool:
    return str(row.get("fiscal_year") or "").strip() in requested_fiscal_years


def _matches_refresh_replace_scope(row: Dict[str, Any], dataset_ids: set[str], fiscal_years: set[str]) -> bool:
    dataset_id = str(row.get("source_dataset_id") or "").strip()
    if not dataset_id or dataset_id not in dataset_ids:
        return False
    if fiscal_years and str(row.get("fiscal_year") or "").strip() not in fiscal_years:
        return False
    return True


def _period_sort_key(value: Any) -> Tuple[int, str]:
    text = str(value or "")
    number = int(text) if text.isdigit() else -1
    return number, text


def _columns(rows: Sequence[Dict[str, Any]]) -> List[str]:
    columns: List[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns


def _contains(row: Dict[str, Any], query: str) -> bool:
    needle = query.lower()
    return any(needle in str(value).lower() for value in row.values())


def _status_message(cfg: Dict[str, Any], rows: Sequence[Dict[str, Any]], docs: Sequence[Dict[str, Any]], last: Dict[str, Any]) -> str:
    if not bool(cfg.get("enabled", True)):
        return "ファクトブック取得は無効です。"
    if last.get("status") == "failed":
        return f"前回のファクトブック取得は失敗しました。エラー{len(last.get('errors', []) or [])}件。"
    if rows:
        return f"ファクトブック由来の受注カテゴリデータを{len(rows)}行保持しています。文書候補は{len(docs)}件です。"
    if docs:
        return f"公式資料候補を{len(docs)}件発見済みです。値抽出は未対応資料のパーサー追加後に進みます。"
    return "ファクトブック由来データはまだありません。取得を実行してください。"


def _safe_float(value: Any) -> Optional[float]:
    if is_blankish(value):
        return None
    text = str(value).strip().replace(",", "").replace("△", "-").replace("▲", "-")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", "."}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _clean_number(value: Any) -> Any:
    number = _safe_float(value)
    if number is None:
        return ""
    return int(number) if float(number).is_integer() else round(number, 6)


def _bool(value: Any, default: bool = False) -> bool:
    if is_blankish(value):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _first_value(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if not is_blankish(value):
            return value
    return ""


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_") or "source"


def _slug(value: str) -> str:
    text = _compact(value).lower()
    if not text:
        return ""
    asciiish = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if asciiish:
        return asciiish
    return re.sub(r"\W+", "_", text).strip("_")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))
