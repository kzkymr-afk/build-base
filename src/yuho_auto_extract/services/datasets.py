from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from yuho_auto_extract.io_utils import is_blankish, prefer_existing_table, read_table
from yuho_auto_extract.services.algorithm_audit import read_algorithm_audit_manifest
from yuho_auto_extract.services import semantics_store


BASE_WIDE_COLUMNS = [
    "company_year_id",
    "fiscal_year",
    "fiscal_year_end",
    "operating_company_id",
    "operating_company_name",
    "reporting_entity_id",
    "data_scope_allowed",
    "analysis_treatment",
]

DEFAULT_RESULT_FIELDS = [
    "roe",
    "building_orders_total",
    "completed_building",
    "backlog_building_next",
    "net_sales_consolidated",
    "operating_income_consolidated",
    "ordinary_income_consolidated",
    "employees_consolidated",
]

FIELD_CATEGORY_LABELS = {
    "performance": "財務・収益",
    "financial_position": "財政状態",
    "orders": "受注・完成・繰越",
    "segment_orders": "セグメント受注",
    "construction": "完成工事",
    "segment": "セグメント",
    "cost": "工事原価",
    "expense": "費用",
    "human_capital": "人員・技術者",
    "people": "人員・技術者",
    "derived_ratio": "比率・派生",
}

FIELD_CATEGORY_PRESET_ORDER = [
    "performance",
    "financial_position",
    "orders",
    "segment_orders",
    "construction",
    "segment",
    "cost",
    "expense",
    "human_capital",
    "people",
    "derived_ratio",
]

REVIEW_CATEGORY_ORDER = [
    "missing",
    "new_candidate",
    "validation_issue",
    "scope_warning",
    "warning_candidate",
    "saved_unapplied",
    "recurrent",
    "resolved_done",
]

COST_COMPONENT_FIELDS = ["cost_materials", "cost_labor", "cost_subcontract", "cost_expense"]
SOURCE_SUMMARY_LIMIT = 250

DERIVED_RATIO_FIELDS = [
    {
        "field_id": "construction_segment_profit_margin",
        "field_name_ja": "建設セグメント利益率",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "segment_profit_construction",
        "denominator": "segment_sales_construction",
    },
    {
        "field_id": "building_completed_profit_margin_proxy",
        "field_name_ja": "建設セグメント利益率_完成工事高建築対比",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "segment_profit_construction",
        "denominator": "completed_building",
    },
    {
        "field_id": "construction_gross_profit_margin_consolidated",
        "field_name_ja": "完成工事総利益率_連結",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "construction_gross_profit_consolidated",
        "denominator": "construction_revenue_consolidated",
    },
    {
        "field_id": "construction_gross_profit_margin_standalone",
        "field_name_ja": "完成工事総利益率_単独",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "construction_gross_profit_standalone",
        "denominator": "construction_revenue_standalone",
    },
    {
        "field_id": "cost_materials_share",
        "field_name_ja": "完成工事原価構成比_材料費",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "cost_materials",
        "denominator_fields": COST_COMPONENT_FIELDS,
        "require_all_denominator_fields": True,
    },
    {
        "field_id": "cost_labor_share",
        "field_name_ja": "完成工事原価構成比_労務費",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "cost_labor",
        "denominator_fields": COST_COMPONENT_FIELDS,
        "require_all_denominator_fields": True,
    },
    {
        "field_id": "cost_subcontract_share",
        "field_name_ja": "完成工事原価構成比_外注費",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "cost_subcontract",
        "denominator_fields": COST_COMPONENT_FIELDS,
        "require_all_denominator_fields": True,
    },
    {
        "field_id": "cost_expense_share",
        "field_name_ja": "完成工事原価構成比_経費",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "cost_expense",
        "denominator_fields": COST_COMPONENT_FIELDS,
        "require_all_denominator_fields": True,
    },
    {
        "field_id": "rd_expense_to_net_sales_consolidated_ratio",
        "field_name_ja": "研究開発費率_売上高連結対比",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "rd_expense",
        "denominator": "net_sales_consolidated",
    },
    {
        "field_id": "entertainment_expense_to_net_sales_consolidated_ratio",
        "field_name_ja": "交際費率_売上高連結対比",
        "category": "derived_ratio",
        "target_unit": "%",
        "numerator": "entertainment_expense",
        "denominator": "net_sales_consolidated",
    },
]

DERIVED_RATIO_FIELD_IDS = [str(row["field_id"]) for row in DERIVED_RATIO_FIELDS]
DERIVED_RATIO_FIELDS_BY_ID = {str(row["field_id"]): row for row in DERIVED_RATIO_FIELDS}


def project_status(root: Path) -> Dict[str, Any]:
    final_dir = root / "data" / "final"
    ai_manifest = root / "data" / "ai_bundle" / "manifest.json"
    algorithm_audit_manifest = read_algorithm_audit_manifest(root)
    report_path = final_dir / "run_report.md"
    manifest = _read_json(ai_manifest)
    return {
        "project_root": str(root),
        "files": {
            "final_master_wide": (final_dir / "final_master_wide.csv").exists(),
            "source_audit": (final_dir / "source_audit.csv").exists(),
            "analysis_dataset": (final_dir / "analysis_dataset.csv").exists(),
            "review_queue": (root / "data" / "review" / "review_queue.csv").exists(),
            "review_resolved": (root / "data" / "review" / "review_resolved.csv").exists(),
            "stock_price_monthly": (root / "data" / "marts" / "market" / "stock_price_monthly.csv").exists(),
            "company_factbook_orders": (root / "data" / "marts" / "company_factbooks" / "building_orders_by_category.csv").exists(),
            "company_factbook_documents": (root / "data" / "marts" / "company_factbooks" / "source_documents.csv").exists(),
            "xbrl_fact_store_manifest": (root / "data" / "marts" / "xbrl_fact_store" / "manifest.json").exists(),
            "xbrl_fact_store_facts_json": (root / "data" / "marts" / "xbrl_fact_store" / "facts.json").exists(),
            "xbrl_fact_store_context_json": (root / "data" / "marts" / "xbrl_fact_store" / "context_index.json").exists(),
            "xbrl_fact_store_digest": (root / "data" / "marts" / "xbrl_fact_store" / "document_digest.md").exists(),
            "ai_bundle": (root / "data" / "ai_bundle").exists(),
            "algorithm_audit": (root / "data" / "algorithm_audit" / "manifest.json").exists(),
        },
        "ai_bundle_generated_at_utc": manifest.get("generated_at_utc", ""),
        "algorithm_audit_generated_at_utc": algorithm_audit_manifest.get("generated_at_utc", ""),
        "run_report": parse_run_report(report_path),
    }


def read_wide(
    root: Path,
    page: int = 1,
    page_size: int = 100,
    company: str = "",
    fiscal_year: str = "",
    fields: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    rows = read_table(root / "data" / "final" / "final_master_wide.csv")
    rows = _attach_company_names(root, rows)
    filtered = []
    for row in rows:
        if company and str(row.get("operating_company_id", "")) != company:
            continue
        if fiscal_year and str(row.get("fiscal_year", "")) != str(fiscal_year):
            continue
        filtered.append(row)
    selected_fields = [field for field in (fields or []) if field] or DEFAULT_RESULT_FIELDS
    filtered = [_with_derived_values(row, selected_fields) for row in filtered]
    keep = [column for column in BASE_WIDE_COLUMNS if column in _columns(filtered)] + selected_fields
    filtered = [{column: row.get(column, "") for column in keep} for row in filtered]
    return paginate(filtered, page, page_size)


def read_chart_data(
    root: Path,
    companies: Optional[Sequence[str]] = None,
    fiscal_years: Optional[Sequence[str]] = None,
    fields: Optional[Sequence[str]] = None,
    max_rows: int = 5000,
) -> Dict[str, Any]:
    company_filter = {str(item) for item in companies or [] if str(item)}
    year_filter = {str(item) for item in fiscal_years or [] if str(item)}
    field_definitions = _field_definitions(root)
    selected_fields = [field for field in (fields or []) if field in field_definitions]
    if not selected_fields:
        return {
            "rows": [],
            "columns": [],
            "total": 0,
            "omitted_rows": 0,
            "fields": [],
            "companies": [],
            "years": [],
            "sources": [],
        }

    rows = _attach_company_names(root, read_table(root / "data" / "final" / "final_master_wide.csv"))
    filtered = []
    for row in rows:
        company_id = str(row.get("operating_company_id", ""))
        fiscal_year = str(row.get("fiscal_year", ""))
        if company_filter and company_id not in company_filter:
            continue
        if year_filter and fiscal_year not in year_filter:
            continue
        row_with_derived = _with_derived_values(row, selected_fields)
        chart_row = {
            "company_year_id": row.get("company_year_id", ""),
            "fiscal_year": fiscal_year,
            "fiscal_year_end": row.get("fiscal_year_end", ""),
            "operating_company_id": company_id,
            "operating_company_name": row.get("operating_company_name", ""),
        }
        for field_id in selected_fields:
            chart_row[field_id] = _to_number(row_with_derived.get(field_id, ""))
            chart_row[f"{field_id}__raw"] = "" if is_blankish(row_with_derived.get(field_id, "")) else str(row_with_derived.get(field_id, ""))
        filtered.append(chart_row)

    filtered = sorted(
        filtered,
        key=lambda row: (_safe_sort_int(row.get("fiscal_year")), str(row.get("operating_company_id", ""))),
    )
    omitted = max(0, len(filtered) - max_rows)
    filtered = filtered[:max_rows]
    return {
        "rows": filtered,
        "columns": _columns(filtered),
        "total": len(filtered) + omitted,
        "omitted_rows": omitted,
        "fields": [_chart_field(field_definitions[field_id]) for field_id in selected_fields],
        "companies": _chart_companies(filtered),
        "years": sorted({str(row.get("fiscal_year", "")) for row in filtered if row.get("fiscal_year")}),
        "sources": _chart_source_summaries(root, filtered, selected_fields, field_definitions),
    }


def read_audit(
    root: Path,
    page: int = 1,
    page_size: int = 100,
    company_year_id: str = "",
    field_id: str = "",
    search: str = "",
) -> Dict[str, Any]:
    rows = read_table(root / "data" / "final" / "source_audit.csv")
    return paginate(_filter_rows(rows, company_year_id=company_year_id, field_id=field_id, search=search), page, page_size)


def read_fields(root: Path) -> Dict[str, Any]:
    path = root / "config" / "field_definition.csv"
    if not path.exists():
        path = root / "data" / "ai_bundle" / "field_definition.csv"
    return paginate(read_table(path), 1, 1000)


def read_options(root: Path) -> Dict[str, Any]:
    companies = read_table(root / "config" / "company_master.csv")
    years = sorted({str(row.get("fiscal_year", "")) for row in read_table(root / "config" / "company_year_master.csv") if row.get("fiscal_year")})
    fields = list(_field_definitions(root).values())
    return {
        "companies": [
            {
                "id": str(row.get("operating_company_id", "")),
                "name": str(row.get("operating_company_name", "")),
                "label": _company_label(row),
            }
            for row in companies
            if row.get("operating_company_id")
        ],
        "years": years,
        "fields": [
            {
                "id": str(row.get("field_id", "")),
                "name": str(row.get("field_name_ja", "")),
                "category": str(row.get("category", "")),
                "unit": str(row.get("target_unit", "")),
                "label": f"{row.get('field_name_ja', '')} ({row.get('field_id', '')})",
            }
            for row in fields
            if row.get("field_id")
        ],
        "default_result_fields": DEFAULT_RESULT_FIELDS,
        "field_presets": _result_field_presets(fields),
    }


def read_review_queue(
    root: Path,
    page: int = 1,
    page_size: int = 100,
    company: str = "",
    fiscal_year: str = "",
    field_id: str = "",
    search: str = "",
    review_status: str = "",
    review_category: str = "",
) -> Dict[str, Any]:
    rows = read_table(root / "data" / "review" / "review_queue.csv")
    resolved_path = root / "data" / "review" / "review_resolved.csv"
    resolved_by_key = {
        (str(row.get("company_year_id", "")), str(row.get("field_id", ""))): row
        for row in (read_table(resolved_path) if resolved_path.exists() else [])
        if row.get("company_year_id") and row.get("field_id")
    }
    company_names = _company_names(root)
    filtered_before_category = []
    for row in rows:
        row = dict(row)
        resolved = resolved_by_key.get((str(row.get("company_year_id", "")), str(row.get("field_id", ""))))
        if resolved:
            row.update(
                {
                    "review_saved": "yes",
                    "review_decision": resolved.get("review_decision", ""),
                    "corrected_value": resolved.get("corrected_value", ""),
                    "reviewer_note": resolved.get("reviewer_note", ""),
                    "reviewer": resolved.get("reviewer", ""),
                    "reviewed_at": resolved.get("reviewed_at", ""),
                    "applied_status": resolved.get("applied_status", ""),
                    "applied_value": resolved.get("applied_value", ""),
                    "applied_at": resolved.get("applied_at", ""),
                }
            )
        else:
            row["review_saved"] = ""
            row["applied_status"] = ""
            row["applied_value"] = ""
            row["applied_at"] = ""
        row_company_id = _company_id_from_year(str(row.get("company_year_id", "")))
        if row_company_id:
            row["company_name_ja"] = company_names.get(row_company_id, "")
        if review_status == "saved" and not resolved:
            continue
        if review_status == "unsaved" and resolved:
            continue
        if review_status == "active" and _is_resolved_review_done(row):
            continue
        row["review_category"] = _review_category(row)
        row["review_category_label"] = _review_category_label(row["review_category"])
        if company and row_company_id != company:
            continue
        if fiscal_year and str(row.get("fiscal_year", "")) != str(fiscal_year):
            continue
        if field_id and field_id.lower() not in str(row.get("field_id", "")).lower():
            continue
        if search and not _contains(row, search):
            continue
        filtered_before_category.append(row)
    filtered = [
        row
        for row in filtered_before_category
        if not review_category or str(row.get("review_category", "")) == review_category
    ]
    result = paginate(filtered, page, page_size)
    result["review_category_counts"] = dict(Counter(str(row.get("review_category", "")) for row in filtered_before_category))
    result["review_category_labels"] = {
        key: _review_category_label(key)
        for key in REVIEW_CATEGORY_ORDER
    }
    result["review_category_filter"] = review_category
    return result


def read_resolved_reviews(root: Path) -> Dict[str, Any]:
    path = root / "data" / "review" / "review_resolved.csv"
    return paginate(read_table(path) if path.exists() else [], 1, 1000)


def read_cell_detail(root: Path, company_year_id: str, field_id: str) -> Dict[str, Any]:
    company_year_id = company_year_id.strip()
    field_id = field_id.strip()
    wide_rows = _attach_company_names(root, read_table(root / "data" / "final" / "final_master_wide.csv"))
    wide_row = next((row for row in wide_rows if str(row.get("company_year_id", "")) == company_year_id), None)
    field = _field_definitions(root).get(field_id, {})
    audit_rows = _matching_rows(read_table(root / "data" / "final" / "source_audit.csv"), company_year_id, field_id)
    review_rows = _matching_rows(read_table(root / "data" / "review" / "review_queue.csv"), company_year_id, field_id)
    resolved_path = root / "data" / "review" / "review_resolved.csv"
    resolved_rows = _matching_rows(read_table(resolved_path) if resolved_path.exists() else [], company_year_id, field_id)
    failure_reason = _run_report_failures(root).get(company_year_id, "")
    if wide_row is not None:
        wide_row = _with_derived_values(wide_row, [field_id])
    current_value = "" if wide_row is None else str(wide_row.get(field_id, "") or "")

    status, status_label, summary, next_action = _classify_cell(
        value=current_value,
        has_audit=bool(audit_rows),
        review_rows=review_rows,
        resolved_rows=resolved_rows,
        failure_reason=failure_reason,
        row_found=wide_row is not None,
    )

    return {
        "company_year_id": company_year_id,
        "company_id": _company_id_from_year(company_year_id),
        "fiscal_year": str(wide_row.get("fiscal_year", "")) if wide_row else _year_from_company_year_id(company_year_id),
        "field_id": field_id,
        "field_name_ja": str(field.get("field_name_ja", "")) or _first_nonblank(audit_rows + review_rows, "field_name_ja") or field_id,
        "unit": str(field.get("target_unit", "")) or _first_nonblank(audit_rows + review_rows, "unit_normalized"),
        "data_scope_required": str(field.get("data_scope_required", "")),
        "preferred_method": str(field.get("preferred_method", "")),
        "current_value": current_value,
        "is_blank": _is_blank(current_value),
        "status": status,
        "status_label": status_label,
        "summary": summary,
        "next_action": next_action,
        "has_source_audit": bool(audit_rows),
        "has_review_candidate": any(not _is_blank(row.get("extracted_value", "")) for row in review_rows),
        "failure_reason": failure_reason,
        "wide_row": wide_row or {},
        "audit_rows": audit_rows[:20],
        "review_rows": review_rows[:20],
        "resolved_rows": resolved_rows[:20],
        "source_chain": _read_semantics_source_chain(root, company_year_id, field_id, resolved_rows),
    }


def _read_semantics_source_chain(
    root: Path,
    company_year_id: str,
    field_id: str,
    resolved_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    empty = {
        "status": "not_built",
        "fact_resolution": {},
        "corroborations": [],
        "mappings": [],
        "observed_items": [],
        "review_decisions": resolved_rows[:20],
    }
    if not semantics_store.semantics_db_path(root).exists():
        return empty

    conn = semantics_store.connect(root)
    try:
        fact = conn.execute(
            "select * from cell_resolutions where company_year_id = ? and concept_id = ?",
            (company_year_id, field_id),
        ).fetchone()
        corroborations = [
            _decode_semantics_row(dict(row))
            for row in conn.execute(
                """
                select * from corroborations
                where company_year_id = ? and concept_id = ?
                order by matched desc, check_kind, check_ref
                limit 20
                """,
                (company_year_id, field_id),
            )
        ]
        mappings = [
            _decode_semantics_row(dict(row))
            for row in conn.execute(
                """
                select * from concept_mappings
                where concept_id = ?
                order by
                  case status when 'confirmed' then 0 when 'proposed' then 1 else 2 end,
                  decided_by,
                  mapping_id
                limit 50
                """,
                (field_id,),
            )
        ]
        observed_ids = [str(row.get("observed_item_id") or "") for row in mappings if row.get("observed_item_id")]
        observed_items = []
        if observed_ids:
            placeholders = ",".join("?" for _ in observed_ids)
            observed_items = [
                _decode_semantics_row(dict(row))
                for row in conn.execute(
                    f"select * from observed_items where observed_item_id in ({placeholders}) order by item_kind, element_id",
                    observed_ids,
                )
            ]
        return {
            "status": "ready",
            "fact_resolution": _decode_semantics_row(dict(fact)) if fact is not None else {},
            "corroborations": corroborations,
            "mappings": mappings,
            "observed_items": observed_items,
            "review_decisions": resolved_rows[:20],
        }
    finally:
        conn.close()


def _decode_semantics_row(row: Dict[str, Any]) -> Dict[str, Any]:
    decoded = dict(row)
    for key in ("buckets_json", "sources_json", "detail_json", "evidence_json", "sample_values_json"):
        if key in decoded:
            decoded[key.removesuffix("_json")] = _safe_json_loads(decoded.get(key))
    return decoded


def _safe_json_loads(value: Any) -> Any:
    if value in (None, ""):
        return [] if value == "" else {}
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return value


def read_markdown(root: Path, rel_path: str) -> Dict[str, str]:
    allowed = {
        "run_report": root / "data" / "final" / "run_report.md",
        "field_coverage": root / "data" / "final" / "field_coverage.md",
        "ai_readme": root / "data" / "ai_bundle" / "AI_README.md",
        "algorithm_audit_readme": root / "data" / "algorithm_audit" / "README.md",
        "algorithm_audit_prompt": root / "data" / "algorithm_audit" / "ALGORITHM_AUDIT_PROMPT.md",
    }
    path = allowed.get(rel_path)
    if not path or not path.exists():
        return {"name": rel_path, "content": ""}
    return {"name": rel_path, "content": path.read_text(encoding="utf-8")}


def parse_run_report(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"summary": {}, "path": str(path), "exists": False}
    summary: Dict[str, str] = {}
    in_summary = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "## Summary":
            in_summary = True
            continue
        if in_summary and line.startswith("## "):
            break
        if in_summary and line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            summary[key.strip()] = value.strip()
    return {"summary": summary, "path": str(path), "exists": True}


def paginate(rows: Iterable[Dict[str, Any]], page: int, page_size: int) -> Dict[str, Any]:
    rows_list = list(rows)
    safe_page_size = max(1, min(int(page_size or 100), 500))
    safe_page = max(1, int(page or 1))
    total = len(rows_list)
    start = (safe_page - 1) * safe_page_size
    end = start + safe_page_size
    page_rows = rows_list[start:end]
    return {
        "rows": page_rows,
        "columns": _columns(rows_list),
        "page": safe_page,
        "page_size": safe_page_size,
        "total": total,
        "total_pages": max(1, math.ceil(total / safe_page_size)) if total else 1,
    }


def _filter_rows(rows: Iterable[Dict[str, Any]], company_year_id: str = "", field_id: str = "", search: str = "") -> List[Dict[str, Any]]:
    filtered = []
    for row in rows:
        if company_year_id and company_year_id.lower() not in str(row.get("company_year_id", "")).lower():
            continue
        if field_id and field_id.lower() not in str(row.get("field_id", "")).lower():
            continue
        if search and not _contains(row, search):
            continue
        filtered.append(row)
    return filtered


def _contains(row: Dict[str, Any], query: str) -> bool:
    needle = query.lower()
    return any(needle in str(value).lower() for value in row.values())


def _columns(rows: Sequence[Dict[str, Any]]) -> List[str]:
    columns: List[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns


def _result_field_presets(fields: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    field_ids = [str(row.get("field_id", "")).strip() for row in fields if str(row.get("field_id", "")).strip()]
    grouped: Dict[str, List[str]] = {}
    for row in fields:
        field_id = str(row.get("field_id", "")).strip()
        if not field_id:
            continue
        category = str(row.get("category", "") or "uncategorized").strip() or "uncategorized"
        grouped.setdefault(category, []).append(field_id)

    presets = [
        {"id": "all", "name": "全指標", "fields": field_ids},
        {"id": "core", "name": "主要指標", "fields": [field_id for field_id in DEFAULT_RESULT_FIELDS if field_id in field_ids]},
    ]
    ordered_categories = [
        category
        for category in FIELD_CATEGORY_PRESET_ORDER
        if category in grouped
    ]
    ordered_categories.extend(sorted(category for category in grouped if category not in FIELD_CATEGORY_PRESET_ORDER))
    for category in ordered_categories:
        preset_id = "derived_ratios" if category == "derived_ratio" else category
        presets.append(
            {
                "id": preset_id,
                "name": FIELD_CATEGORY_LABELS.get(category, category if category != "uncategorized" else "未分類"),
                "fields": grouped[category],
            }
        )
    return presets


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _attach_company_names(root: Path, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    names = _company_names(root)
    out = []
    for row in rows:
        copied = dict(row)
        copied["operating_company_name"] = names.get(str(row.get("operating_company_id", "")), "")
        out.append(copied)
    return out


def _company_names(root: Path) -> Dict[str, str]:
    return {
        str(row.get("operating_company_id", "")): str(row.get("operating_company_name", ""))
        for row in read_table(root / "config" / "company_master.csv")
        if row.get("operating_company_id")
    }


def _company_label(row: Dict[str, Any]) -> str:
    name = str(row.get("operating_company_name", ""))
    company_id = str(row.get("operating_company_id", ""))
    return f"{name}（{company_id}）" if name else company_id


def _company_id_from_year(company_year_id: str) -> str:
    if not company_year_id or "_" not in company_year_id:
        return company_year_id
    return company_year_id.rsplit("_", 1)[0]


def _year_from_company_year_id(company_year_id: str) -> str:
    if not company_year_id or "_" not in company_year_id:
        return ""
    return company_year_id.rsplit("_", 1)[1]


def _matching_rows(rows: Iterable[Dict[str, Any]], company_year_id: str, field_id: str) -> List[Dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("company_year_id", "")) == company_year_id and str(row.get("field_id", "")) == field_id
    ]


def _field_definitions(root: Path) -> Dict[str, Dict[str, Any]]:
    path = root / "config" / "field_definition.csv"
    if not path.exists():
        path = root / "data" / "ai_bundle" / "field_definition.csv"
    fields = {str(row.get("field_id", "")): row for row in read_table(path) if row.get("field_id")}
    for row in DERIVED_RATIO_FIELDS:
        fields.setdefault(str(row["field_id"]), dict(row))
    return fields


def _with_derived_values(row: Dict[str, Any], field_ids: Sequence[str]) -> Dict[str, Any]:
    derived_ids = [field_id for field_id in field_ids if field_id in DERIVED_RATIO_FIELDS_BY_ID]
    if not derived_ids:
        return row
    copied = dict(row)
    for field_id in derived_ids:
        value = _compute_ratio(copied, DERIVED_RATIO_FIELDS_BY_ID[field_id])
        copied[field_id] = "" if value is None else value
    return copied


def _compute_ratio(row: Dict[str, Any], definition: Dict[str, Any]) -> Optional[float]:
    numerator = _to_number(row.get(str(definition.get("numerator", "")), ""))
    if numerator is None:
        return None
    denominator = _ratio_denominator(row, definition)
    if denominator is None or denominator == 0:
        return None
    ratio = numerator / denominator * 100
    return round(ratio, 4) if math.isfinite(ratio) else None


def _ratio_denominator(row: Dict[str, Any], definition: Dict[str, Any]) -> Optional[float]:
    denominator_field = str(definition.get("denominator", ""))
    if denominator_field:
        return _to_number(row.get(denominator_field, ""))
    denominator_fields = [str(field_id) for field_id in definition.get("denominator_fields", []) if str(field_id)]
    if not denominator_fields:
        return None
    values = [_to_number(row.get(field_id, "")) for field_id in denominator_fields]
    if definition.get("require_all_denominator_fields") and any(value is None for value in values):
        return None
    numeric_values = [value for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values)


def _run_report_failures(root: Path) -> Dict[str, str]:
    path = root / "data" / "final" / "run_report.md"
    if not path.exists():
        return {}
    failures: Dict[str, str] = {}
    in_failed_documents = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "## Failed Documents":
            in_failed_documents = True
            continue
        if in_failed_documents and stripped.startswith("## "):
            break
        if not in_failed_documents or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 2 or cells[0] in {"company_year_id", "---"}:
            continue
        failures[cells[0]] = cells[1]
    return failures


def _classify_cell(
    *,
    value: Any,
    has_audit: bool,
    review_rows: Sequence[Dict[str, Any]],
    resolved_rows: Sequence[Dict[str, Any]],
    failure_reason: str,
    row_found: bool,
) -> tuple[str, str, str, str]:
    if not row_found:
        return (
            "row_not_found",
            "行なし",
            "final_master_wide.csv に対象の会社年度行がありません。",
            "一括実行で対象文書が解決できているか確認してください。",
        )
    if not _is_blank(value):
        if has_audit:
            return (
                "value_present",
                "値あり",
                "最終結果に値が入っています。source_audit.csv に根拠もあります。",
                "値の妥当性を確認したい場合は、根拠タブで出典を確認してください。",
            )
        return (
            "value_present_no_audit",
            "値あり・根拠未検出",
            "最終結果に値はありますが、このセルに対応する source_audit.csv の行が見つかりません。",
            "値の由来を追いたい場合は、抽出処理の監査ログ生成を確認してください。",
        )
    if failure_reason:
        return (
            "document_failed",
            "文書未取得",
            f"この会社年度は文書解決に失敗しています: {failure_reason}",
            "まず対象年度の有価証券報告書の docID / EDINET 取得条件を確認してから再実行してください。",
        )
    if any(str(row.get("review_decision", "")).strip().lower() == "not_applicable" for row in resolved_rows):
        return (
            "not_applicable",
            "対象外",
            "review_resolved.csv で、この会社年度・項目は対象外として保存されています。",
            "比較対象に戻す場合はレビュー画面で保存済みレビューを削除してください。",
        )
    if any(_resolved_review_value(row) for row in resolved_rows):
        return (
            "review_saved_not_applied",
            "レビュー保存済み",
            "review_resolved.csv に保存済みの判断がありますが、最終結果はまだ空欄です。",
            "実行タブまたはレビュー画面から「レビュー反映」を実行すると final_master_wide.csv に反映されます。",
        )
    if any(not _is_blank(row.get("extracted_value", "")) for row in review_rows):
        return (
            "blank_with_review_candidate",
            "レビュー候補あり",
            "抽出候補がありますが、自動採用されずレビュー待ちになっています。",
            "レビュー画面で accept または correct を保存し、その後「レビュー反映」を実行してください。",
        )
    if has_audit:
        return (
            "blank_with_source_audit",
            "根拠候補あり",
            "監査行はありますが、最終結果は空欄です。検証結果やスコープ判定で落ちた可能性があります。",
            "根拠タブで validation_status、data_scope、source_quote を確認し、必要ならレビューまたは抽出ルールを調整してください。",
        )
    if review_rows:
        return (
            "blank_review_needs_rule",
            "候補なし・要ルール確認",
            "レビューキューには載っていますが、抽出値が空です。タグ未検出や信頼度不足の可能性があります。",
            "review_reason を確認し、XBRLタグ候補、表パターン、セクション抽出条件の追加対象にしてください。",
        )
    return (
        "blank_no_candidate",
        "候補なし",
        "最終結果、監査行、レビュー候補のいずれにも値がありません。",
        "開示なし、対象外、または未抽出の可能性があります。根拠なしに 0 として埋めず、必要なら原典確認か抽出ルール追加に回してください。",
    )


def _resolved_review_value(row: Dict[str, Any]) -> str:
    decision = str(row.get("review_decision", "")).strip().lower()
    if decision == "reject":
        return ""
    if decision == "correct":
        value = row.get("corrected_value", "")
        return "" if _is_blank(value) else str(value).strip()
    if decision == "accept":
        value = row.get("extracted_value", "")
        return "" if _is_blank(value) else str(value).strip()
    return ""


def _is_resolved_review_done(row: Dict[str, Any]) -> bool:
    if str(row.get("review_saved", "")) != "yes":
        return False
    status = str(row.get("applied_status", "") or "").strip().lower()
    if status in {"applied", "rejected", "not_applicable"}:
        return True
    return str(row.get("review_decision", "")).strip().lower() == "not_applicable"


def _review_category(row: Dict[str, Any]) -> str:
    if _is_resolved_review_done(row):
        return "resolved_done"
    if str(row.get("review_saved", "")) == "yes":
        return "saved_unapplied"
    if _is_blank(row.get("extracted_value", "")):
        return "missing"
    reasons = {
        reason.strip()
        for reason in str(row.get("review_reason", "") or "").replace(",", ";").split(";")
        if reason.strip()
    }
    if "existing_human_reviewed" in reasons or "existing_value_mismatch" in reasons:
        return "recurrent"
    if reasons & {"validation_warn", "validation_fail"}:
        return "validation_issue"
    if "data_scope_mismatch" in reasons:
        return "scope_warning"
    if reasons & {"confidence_below_threshold", "unit_unknown"}:
        return "warning_candidate"
    return "new_candidate"


def _review_category_label(category: str) -> str:
    return {
        "missing": "未取得",
        "new_candidate": "新規候補",
        "validation_issue": "検算要確認",
        "scope_warning": "スコープ警告",
        "warning_candidate": "警告付き候補",
        "saved_unapplied": "保存済み未反映",
        "recurrent": "再発",
        "resolved_done": "対応済み",
    }.get(category, category or "未分類")


def _first_nonblank(rows: Sequence[Dict[str, Any]], key: str) -> str:
    for row in rows:
        value = row.get(key, "")
        if not _is_blank(value):
            return str(value)
    return ""


def _is_blank(value: Any) -> bool:
    return is_blankish(value)


def _to_number(value: Any) -> Optional[float]:
    if is_blankish(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = (
        text.replace(",", "")
        .replace("％", "%")
        .replace("−", "-")
        .replace("△", "-")
        .replace("▲", "-")
        .replace("%", "")
    )
    try:
        number = float(normalized)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _safe_sort_int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except ValueError:
        return 0


def _chart_field(row: Dict[str, Any]) -> Dict[str, str]:
    field_id = str(row.get("field_id", ""))
    name = str(row.get("field_name_ja", "")) or field_id
    unit = str(row.get("target_unit", ""))
    return {
        "id": field_id,
        "name": name,
        "category": str(row.get("category", "")),
        "unit": unit,
        "label": f"{name} ({field_id})",
    }


def _chart_source_summaries(
    root: Path,
    rows: Sequence[Dict[str, Any]],
    selected_fields: Sequence[str],
    field_definitions: Dict[str, Dict[str, Any]],
) -> List[Dict[str, str]]:
    audit_path = root / "data" / "final" / "source_audit.csv"
    direct_fields = [field_id for field_id in selected_fields if field_id not in DERIVED_RATIO_FIELDS_BY_ID]
    if not rows or not direct_fields or not audit_path.exists():
        return []

    row_by_company_year = {
        str(row.get("company_year_id", "")): row
        for row in rows
        if row.get("company_year_id")
    }
    target_keys = {
        (company_year_id, field_id)
        for company_year_id in row_by_company_year
        for field_id in direct_fields
    }
    summaries: List[Dict[str, str]] = []
    seen = set()
    for audit in read_table(audit_path):
        company_year_id = str(audit.get("company_year_id", ""))
        field_id = str(audit.get("field_id", ""))
        if (company_year_id, field_id) not in target_keys:
            continue
        signature = (
            company_year_id,
            field_id,
            str(audit.get("source_file", "")),
            str(audit.get("source_heading", "")),
            str(audit.get("source_quote", "")),
        )
        if signature in seen:
            continue
        seen.add(signature)
        row = row_by_company_year.get(company_year_id, {})
        field = field_definitions.get(field_id, {})
        summaries.append(
            {
                "company_year_id": company_year_id,
                "company_name": str(row.get("operating_company_name", "") or row.get("operating_company_id", "")),
                "period": str(row.get("fiscal_year", "")),
                "field_id": field_id,
                "field_name": str(audit.get("field_name_ja", "") or field.get("field_name_ja", "") or field_id),
                "value": str(audit.get("value", "")),
                "unit": str(audit.get("unit_normalized", "") or field.get("target_unit", "")),
                "data_scope": str(audit.get("data_scope", "")),
                "source_doc_id": str(audit.get("source_doc_id", "")),
                "source_file": str(audit.get("source_file", "")),
                "source_heading": str(audit.get("source_heading", "")),
                "source_quote": str(audit.get("source_quote", "")),
                "extraction_method": str(audit.get("extraction_method", "")),
                "confidence": str(audit.get("confidence", "")),
            }
        )
        if len(summaries) >= SOURCE_SUMMARY_LIMIT:
            break
    return summaries


def _chart_companies(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    by_id: Dict[str, Dict[str, str]] = {}
    for row in rows:
        company_id = str(row.get("operating_company_id", ""))
        if not company_id or company_id in by_id:
            continue
        name = str(row.get("operating_company_name", ""))
        by_id[company_id] = {
            "id": company_id,
            "name": name,
            "label": f"{name}（{company_id}）" if name else company_id,
        }
    return list(by_id.values())
