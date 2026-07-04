from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from yuho_auto_extract.config_loader import load_pipeline_config
from yuho_auto_extract.io_utils import ensure_parent, is_blankish, prefer_existing_table, read_table, write_table
from yuho_auto_extract.normalizer import normalize_numeric
from yuho_auto_extract.xbrl_fact_store import FACT_STORE_DIR


DISCOVERED_METRICS_DIR = Path("data") / "marts" / "xbrl_discovered_metrics"
FIELD_MAPPINGS_PATH = DISCOVERED_METRICS_DIR / "field_mappings.csv"
SIMILARITY_THRESHOLD = 0.50
MAPPING_COLUMNS = [
    "discovered_metric_id",
    "discovered_metric_label",
    "element_local_name",
    "normalized_scope",
    "period_bucket",
    "unit",
    "target_field_id",
    "mapping_status",
    "mapping_note",
    "updated_at_utc",
]
VALID_MAPPING_STATUSES = {"candidate", "accepted", "rejected", "separate", "unmapped"}


def build_xbrl_discovered_metrics(root: Path, current_year_only: bool = True) -> Dict[str, Any]:
    """Turn every numeric XBRL fact into a reviewable discovered-metric mart.

    This is intentionally wider than field_definition.csv. Similar labels remain
    separate metrics first, and are only reported as merge suggestions.
    """
    cfg = load_pipeline_config(root)
    facts = _read_fact_store(root)
    company_names = {
        str(row.get("operating_company_id") or ""): str(row.get("operating_company_name") or "")
        for row in cfg.company_master
    }

    value_rows: List[Dict[str, Any]] = []
    excluded = Counter()
    for fact in facts:
        reason = _exclusion_reason(fact, current_year_only=current_year_only)
        if reason:
            excluded[reason] += 1
            continue
        value_rows.append(_value_row(fact, company_names))

    value_rows.sort(
        key=lambda row: (
            str(row.get("discovered_metric_label") or ""),
            str(row.get("operating_company_id") or ""),
            str(row.get("fiscal_year") or ""),
            str(row.get("context_id") or ""),
            _safe_int(row.get("csv_row")),
        )
    )
    catalog_rows = _catalog_rows(value_rows, cfg.field_definition)
    suggestions = _similarity_suggestions(catalog_rows)

    out_dir = root / DISCOVERED_METRICS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    value_csv = write_table(out_dir / "value_long.csv", value_rows)
    value_json = write_table(out_dir / "value_long.json", value_rows)
    value_parquet = write_table(out_dir / "value_long.parquet", value_rows)
    catalog_csv = write_table(out_dir / "metric_catalog.csv", catalog_rows)
    catalog_json = write_table(out_dir / "metric_catalog.json", catalog_rows)
    suggestions_csv = write_table(out_dir / "similarity_suggestions.csv", suggestions)
    suggestions_json = write_table(out_dir / "similarity_suggestions.json", suggestions)
    readme_path = out_dir / "README.md"
    ensure_parent(readme_path)
    readme_path.write_text(_readme(catalog_rows, value_rows, suggestions, excluded, current_year_only), encoding="utf-8")

    manifest = {
        "generated_at_utc": _now_utc(),
        "mart_dir": str(DISCOVERED_METRICS_DIR),
        "source": str(FACT_STORE_DIR / "facts.parquet"),
        "current_year_only": current_year_only,
        "facts_read": len(facts),
        "numeric_current_facts": len(value_rows),
        "discovered_metrics": len(catalog_rows),
        "similarity_suggestions": len(suggestions),
        "excluded_counts": dict(sorted(excluded.items())),
        "value_long_csv": _relative(value_csv, root),
        "value_long_json": _relative(value_json, root),
        "value_long_parquet": _relative(value_parquet, root),
        "metric_catalog_csv": _relative(catalog_csv, root),
        "metric_catalog_json": _relative(catalog_json, root),
        "similarity_suggestions_csv": _relative(suggestions_csv, root),
        "similarity_suggestions_json": _relative(suggestions_json, root),
        "readme": _relative(readme_path, root),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def read_xbrl_discovered_metrics(
    root: Path,
    page: int = 1,
    page_size: int = 100,
    search: str = "",
    mapping_status: str = "",
    target_field_id: str = "",
) -> Dict[str, Any]:
    catalog = _read_metric_catalog(root)
    mappings = _mapping_by_metric_id(root)
    fields = _field_options(root)
    fields_by_id = {str(row["field_id"]): row for row in fields}
    rows = [_catalog_with_mapping(row, mappings.get(str(row.get("discovered_metric_id") or "")), fields_by_id) for row in catalog]
    rows = _filter_metric_rows(rows, search=search, mapping_status=mapping_status, target_field_id=target_field_id)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 100), 500))
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "rows": rows[start : start + page_size],
        "columns": [
            "mapping_status",
            "target_field_name_ja",
            "discovered_metric_label",
            "element_local_name",
            "normalized_scope",
            "period_bucket",
            "unit",
            "value_count",
            "company_year_count",
            "matched_field_ids",
            "sample_value_display",
            "sample_company_year_id",
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if total else 1,
        "field_options": fields,
        "status_options": _mapping_status_options(),
        "mapping_counts": _mapping_counts(rows),
        "mapping_path": str(root / FIELD_MAPPINGS_PATH),
    }


def upsert_xbrl_metric_mapping(
    root: Path,
    discovered_metric_id: str,
    target_field_id: str = "",
    mapping_status: str = "candidate",
    note: str = "",
) -> Dict[str, Any]:
    metric_id = str(discovered_metric_id or "").strip()
    status = str(mapping_status or "").strip() or "candidate"
    target_field_id = str(target_field_id or "").strip()
    if status not in VALID_MAPPING_STATUSES:
        raise ValueError(f"invalid mapping_status: {status}")
    catalog_by_id = {str(row.get("discovered_metric_id") or ""): row for row in _read_metric_catalog(root)}
    metric = catalog_by_id.get(metric_id)
    if metric is None:
        raise ValueError(f"discovered_metric_id not found: {metric_id}")
    fields_by_id = {str(row["field_id"]): row for row in _field_options(root)}
    if target_field_id and target_field_id not in fields_by_id:
        raise ValueError(f"target_field_id not found: {target_field_id}")
    if status in {"candidate", "accepted"} and not target_field_id:
        raise ValueError("target_field_id is required for candidate/accepted mapping")

    mappings = _read_mappings(root)
    before_count = len(mappings)
    mappings = [row for row in mappings if str(row.get("discovered_metric_id") or "") != metric_id]
    if status != "unmapped":
        mappings.append(
            {
                "discovered_metric_id": metric_id,
                "discovered_metric_label": metric.get("discovered_metric_label", ""),
                "element_local_name": metric.get("element_local_name", ""),
                "normalized_scope": metric.get("normalized_scope", ""),
                "period_bucket": metric.get("period_bucket", ""),
                "unit": metric.get("unit", ""),
                "target_field_id": target_field_id,
                "mapping_status": status,
                "mapping_note": str(note or "").strip(),
                "updated_at_utc": _now_utc(),
            }
        )
    written = _write_mappings(root, mappings)
    return {
        "discovered_metric_id": metric_id,
        "mapping_status": status,
        "target_field_id": target_field_id,
        "target_field_name_ja": fields_by_id.get(target_field_id, {}).get("field_name_ja", ""),
        "changed": before_count != len(mappings) or status != "unmapped",
        "mapping_path": str(written),
    }


def bulk_upsert_xbrl_metric_mappings(
    root: Path,
    discovered_metric_ids: Sequence[str],
    target_field_id: str = "",
    mapping_status: str = "rejected",
    note: str = "",
) -> Dict[str, Any]:
    metric_ids = [metric_id for metric_id in dict.fromkeys(str(value or "").strip() for value in discovered_metric_ids) if metric_id]
    if not metric_ids:
        raise ValueError("discovered_metric_ids are required")
    status = str(mapping_status or "").strip() or "rejected"
    target_field_id = str(target_field_id or "").strip()
    if status not in VALID_MAPPING_STATUSES:
        raise ValueError(f"invalid mapping_status: {status}")
    catalog_by_id = {str(row.get("discovered_metric_id") or ""): row for row in _read_metric_catalog(root)}
    missing_ids = [metric_id for metric_id in metric_ids if metric_id not in catalog_by_id]
    if missing_ids:
        raise ValueError(f"discovered_metric_id not found: {', '.join(missing_ids[:10])}")
    fields_by_id = {str(row["field_id"]): row for row in _field_options(root)}
    if target_field_id and target_field_id not in fields_by_id:
        raise ValueError(f"target_field_id not found: {target_field_id}")
    if status in {"candidate", "accepted"} and not target_field_id:
        raise ValueError("target_field_id is required for candidate/accepted mapping")

    target_ids = set(metric_ids)
    mappings = [row for row in _read_mappings(root) if str(row.get("discovered_metric_id") or "") not in target_ids]
    if status != "unmapped":
        updated_at = _now_utc()
        for metric_id in metric_ids:
            metric = catalog_by_id[metric_id]
            mappings.append(
                {
                    "discovered_metric_id": metric_id,
                    "discovered_metric_label": metric.get("discovered_metric_label", ""),
                    "element_local_name": metric.get("element_local_name", ""),
                    "normalized_scope": metric.get("normalized_scope", ""),
                    "period_bucket": metric.get("period_bucket", ""),
                    "unit": metric.get("unit", ""),
                    "target_field_id": target_field_id,
                    "mapping_status": status,
                    "mapping_note": str(note or "").strip(),
                    "updated_at_utc": updated_at,
                }
            )
    written = _write_mappings(root, mappings)
    return {
        "requested": len(metric_ids),
        "changed": len(metric_ids),
        "mapping_status": status,
        "target_field_id": target_field_id,
        "target_field_name_ja": fields_by_id.get(target_field_id, {}).get("field_name_ja", ""),
        "mapping_path": str(written),
    }


def _read_fact_store(root: Path) -> List[Dict[str, Any]]:
    path = prefer_existing_table(root / FACT_STORE_DIR / "facts.parquet")
    if not path.exists():
        raise FileNotFoundError(f"XBRL Fact Store is missing: {path}")
    return read_table(path)


def _read_metric_catalog(root: Path) -> List[Dict[str, Any]]:
    path = prefer_existing_table(root / DISCOVERED_METRICS_DIR / "metric_catalog.parquet")
    if not path.exists():
        path = root / DISCOVERED_METRICS_DIR / "metric_catalog.csv"
    if not path.exists():
        raise FileNotFoundError(f"XBRL discovered metric catalog is missing: {path}")
    return read_table(path)


def _read_mappings(root: Path) -> List[Dict[str, Any]]:
    path = root / FIELD_MAPPINGS_PATH
    if not path.exists():
        return []
    rows = read_table(path)
    return [{column: row.get(column, "") for column in MAPPING_COLUMNS} for row in rows]


def _write_mappings(root: Path, rows: Sequence[Dict[str, Any]]) -> Path:
    normalized = [{column: row.get(column, "") for column in MAPPING_COLUMNS} for row in rows]
    return write_table(root / FIELD_MAPPINGS_PATH, normalized)


def _mapping_by_metric_id(root: Path) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("discovered_metric_id") or ""): row for row in _read_mappings(root)}


def _field_options(root: Path) -> List[Dict[str, str]]:
    cfg = load_pipeline_config(root)
    rows: List[Dict[str, str]] = []
    for field in cfg.field_definition:
        field_id = str(field.get("field_id") or "")
        if not field_id:
            continue
        name = str(field.get("field_name_ja") or field_id)
        rows.append(
            {
                "field_id": field_id,
                "field_name_ja": name,
                "category": str(field.get("category") or ""),
                "target_unit": str(field.get("target_unit") or ""),
                "label": f"{name} / {field_id}",
            }
        )
    return rows


def _catalog_with_mapping(
    row: Dict[str, Any],
    mapping: Dict[str, Any] | None,
    fields_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    copied = dict(row)
    mapping = mapping or {}
    target_field_id = str(mapping.get("target_field_id") or "")
    status = str(mapping.get("mapping_status") or "unmapped")
    copied["target_field_id"] = target_field_id
    copied["target_field_name_ja"] = fields_by_id.get(target_field_id, {}).get("field_name_ja", "")
    copied["mapping_status"] = status
    copied["mapping_status_label"] = _mapping_status_label(status)
    copied["mapping_note"] = mapping.get("mapping_note", "")
    copied["mapping_updated_at_utc"] = mapping.get("updated_at_utc", "")
    return copied


def _filter_metric_rows(
    rows: Sequence[Dict[str, Any]],
    search: str = "",
    mapping_status: str = "",
    target_field_id: str = "",
) -> List[Dict[str, Any]]:
    query = str(search or "").strip().lower()
    status = str(mapping_status or "").strip()
    target = str(target_field_id or "").strip()
    out: List[Dict[str, Any]] = []
    for row in rows:
        row_status = str(row.get("mapping_status") or "unmapped")
        if status and row_status != status:
            continue
        if target and str(row.get("target_field_id") or "") != target:
            continue
        if query:
            haystack = " ".join(
                str(row.get(key, ""))
                for key in [
                    "discovered_metric_id",
                    "discovered_metric_label",
                    "normalized_label",
                    "element_id",
                    "element_local_name",
                    "matched_field_ids",
                    "target_field_id",
                    "target_field_name_ja",
                    "sample_source_quote",
                ]
            ).lower()
            if query not in haystack:
                continue
        out.append(row)
    return out


def _mapping_counts(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts = Counter(str(row.get("mapping_status") or "unmapped") for row in rows)
    return {key: counts.get(key, 0) for key in ["unmapped", "candidate", "accepted", "separate", "rejected"]}


def _mapping_status_options() -> List[Dict[str, str]]:
    return [{"id": status, "label": _mapping_status_label(status)} for status in ["unmapped", "candidate", "accepted", "separate", "rejected"]]


def _mapping_status_label(status: str) -> str:
    return {
        "unmapped": "未判断",
        "candidate": "確認中",
        "accepted": "採用",
        "separate": "別管理",
        "rejected": "使わない",
    }.get(status, status or "未判断")


def _exclusion_reason(fact: Dict[str, Any], current_year_only: bool) -> str:
    if _truthy(fact.get("is_text_block")):
        return "text_block"
    numeric = _numeric_value(fact)
    if numeric is None:
        return "non_numeric"
    if current_year_only and not _is_current_year_fact(fact):
        return "non_current_year"
    if not str(fact.get("element_local_name") or fact.get("element_id") or "").strip():
        return "missing_element"
    return ""


def _value_row(fact: Dict[str, Any], company_names: Dict[str, str]) -> Dict[str, Any]:
    numeric = _numeric_value(fact)
    label = _metric_label(fact)
    normalized_label = _normalize_label(label)
    period_bucket = _period_bucket(fact.get("period_or_instant"), fact.get("context_id"))
    metric_id = _metric_id(
        [
            fact.get("element_id"),
            fact.get("element_local_name"),
            normalized_label,
            fact.get("normalized_scope"),
            period_bucket,
            fact.get("unit"),
        ]
    )
    operating_company_id = str(fact.get("operating_company_id") or "")
    return {
        "discovered_metric_id": metric_id,
        "discovered_metric_label": label,
        "normalized_label": normalized_label,
        "element_id": fact.get("element_id", ""),
        "element_local_name": fact.get("element_local_name", ""),
        "company_year_id": fact.get("company_year_id", ""),
        "operating_company_id": operating_company_id,
        "operating_company_name": company_names.get(operating_company_id, ""),
        "fiscal_year": fact.get("fiscal_year", ""),
        "source_doc_id": fact.get("source_doc_id", ""),
        "context_id": fact.get("context_id", ""),
        "relative_year": fact.get("relative_year", ""),
        "normalized_scope": fact.get("normalized_scope", ""),
        "period_or_instant": fact.get("period_or_instant", ""),
        "period_bucket": period_bucket,
        "unit": fact.get("unit", ""),
        "unit_id": fact.get("unit_id", ""),
        "value": fact.get("value", ""),
        "value_numeric": numeric,
        "value_display": _format_number(numeric),
        "source_quote": fact.get("source_quote", ""),
        "source_file": fact.get("source_file", ""),
        "csv_file": fact.get("csv_file", ""),
        "csv_row": fact.get("csv_row", ""),
    }


def _catalog_rows(value_rows: Sequence[Dict[str, Any]], field_definition: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in value_rows:
        grouped[str(row.get("discovered_metric_id") or "")].append(row)

    rows: List[Dict[str, Any]] = []
    for metric_id, values in grouped.items():
        sample = values[0]
        fiscal_years = sorted({_safe_int(row.get("fiscal_year")) for row in values if str(row.get("fiscal_year") or "").strip()})
        company_ids = sorted({str(row.get("operating_company_id") or "") for row in values if row.get("operating_company_id")})
        company_year_ids = sorted({str(row.get("company_year_id") or "") for row in values if row.get("company_year_id")})
        matched_field_ids = _matched_field_ids(sample, field_definition)
        rows.append(
            {
                "discovered_metric_id": metric_id,
                "discovered_metric_label": sample.get("discovered_metric_label", ""),
                "normalized_label": sample.get("normalized_label", ""),
                "element_id": sample.get("element_id", ""),
                "element_local_name": sample.get("element_local_name", ""),
                "normalized_scope": sample.get("normalized_scope", ""),
                "period_bucket": sample.get("period_bucket", ""),
                "unit": sample.get("unit", ""),
                "value_count": len(values),
                "company_count": len(company_ids),
                "year_count": len(fiscal_years),
                "company_year_count": len(company_year_ids),
                "first_fiscal_year": fiscal_years[0] if fiscal_years else "",
                "last_fiscal_year": fiscal_years[-1] if fiscal_years else "",
                "matched_field_ids": ";".join(matched_field_ids),
                "sample_company_year_id": sample.get("company_year_id", ""),
                "sample_source_doc_id": sample.get("source_doc_id", ""),
                "sample_context_id": sample.get("context_id", ""),
                "sample_value": sample.get("value", ""),
                "sample_value_display": sample.get("value_display", ""),
                "sample_source_quote": sample.get("source_quote", ""),
            }
        )
    rows.sort(
        key=lambda row: (
            str(row.get("normalized_label") or ""),
            str(row.get("normalized_scope") or ""),
            str(row.get("period_bucket") or ""),
            str(row.get("element_local_name") or ""),
        )
    )
    return rows


def _matched_field_ids(metric: Dict[str, Any], fields: Sequence[Dict[str, Any]]) -> List[str]:
    local_name = str(metric.get("element_local_name") or "")
    label = str(metric.get("discovered_metric_label") or "")
    matched: List[str] = []
    for field in fields:
        field_id = str(field.get("field_id") or "")
        if not field_id:
            continue
        tokens = _split_values(field.get("xbrl_tag_candidates"))
        if any(_candidate_matches(local_name, label, token) for token in tokens):
            matched.append(field_id)
    return matched


def _similarity_suggestions(catalog_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in catalog_rows:
        for bucket_key in _suggestion_bucket_keys(row):
            buckets[bucket_key].append(row)

    suggestions: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for rows in buckets.values():
        if len(rows) < 2:
            continue
        rows = sorted(rows, key=lambda row: int(row.get("value_count") or 0), reverse=True)[:80]
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                key = tuple(sorted([str(left.get("discovered_metric_id") or ""), str(right.get("discovered_metric_id") or "")]))
                if key in seen:
                    continue
                seen.add(key)
                label_similarity = _similarity(left.get("normalized_label"), right.get("normalized_label"))
                element_similarity = _similarity(left.get("element_local_name"), right.get("element_local_name"))
                if label_similarity < SIMILARITY_THRESHOLD and element_similarity < 0.72:
                    continue
                suggestions.append(_suggestion_row(left, right, label_similarity, element_similarity))
    suggestions.sort(key=lambda row: (-float(row["label_similarity"]), row["left_metric_label"], row["right_metric_label"]))
    return suggestions


def _suggestion_bucket_keys(row: Dict[str, Any]) -> List[Tuple[str, str, str, str]]:
    label = str(row.get("normalized_label") or "")
    local_name = str(row.get("element_local_name") or "").lower()
    if len(label) < 3 and not local_name:
        return []
    base = (
        str(row.get("normalized_scope") or ""),
        str(row.get("period_bucket") or ""),
        str(row.get("unit") or ""),
    )
    keys = []
    if len(label) >= 3:
        keys.append(base + (f"suffix:{label[-3:]}",))
    for token in ["総利益", "売上高", "営業利益", "経常利益", "純利益", "総資産", "純資産"]:
        if token in label:
            keys.append(base + (f"label-token:{token}",))
    for token in ["grossprofit", "netsales", "operatingincome", "ordinaryincome", "profitloss", "totalassets", "netassets"]:
        if token in local_name:
            keys.append(base + (f"element-token:{token}",))
    return list(dict.fromkeys(keys))


def _suggestion_row(
    left: Dict[str, Any],
    right: Dict[str, Any],
    label_similarity: float,
    element_similarity: float,
) -> Dict[str, Any]:
    return {
        "left_metric_id": left.get("discovered_metric_id", ""),
        "right_metric_id": right.get("discovered_metric_id", ""),
        "left_metric_label": left.get("discovered_metric_label", ""),
        "right_metric_label": right.get("discovered_metric_label", ""),
        "left_element_local_name": left.get("element_local_name", ""),
        "right_element_local_name": right.get("element_local_name", ""),
        "normalized_scope": left.get("normalized_scope", ""),
        "period_bucket": left.get("period_bucket", ""),
        "unit": left.get("unit", ""),
        "left_value_count": left.get("value_count", ""),
        "right_value_count": right.get("value_count", ""),
        "left_matched_field_ids": left.get("matched_field_ids", ""),
        "right_matched_field_ids": right.get("matched_field_ids", ""),
        "label_similarity": round(label_similarity, 4),
        "element_similarity": round(element_similarity, 4),
        "suggestion_reason": "similar_label_or_element_do_not_merge_automatically",
    }


def _readme(
    catalog_rows: Sequence[Dict[str, Any]],
    value_rows: Sequence[Dict[str, Any]],
    suggestions: Sequence[Dict[str, Any]],
    excluded: Counter,
    current_year_only: bool,
) -> str:
    lines = [
        "# XBRL Discovered Metrics",
        "",
        "有報XBRL Fact Store の数値ファクトを、定義済み項目に限定せず全量候補化したマートです。",
        "似ている項目は自動統合せず、`similarity_suggestions` に候補としてだけ出します。",
        "",
        "## Files",
        "",
        "- `metric_catalog.csv`: 発見項目の一覧。1行が1つのXBRL由来項目です。",
        "- `value_long.csv`: 会社・年度ごとの値。1行が1つの証拠付き数値です。",
        "- `similarity_suggestions.csv`: 表記やタグが近い項目の統合候補です。",
        "",
        "## Summary",
        "",
        f"- current_year_only: {str(current_year_only).lower()}",
        f"- discovered_metrics: {len(catalog_rows)}",
        f"- value_rows: {len(value_rows)}",
        f"- similarity_suggestions: {len(suggestions)}",
        f"- excluded: {', '.join(f'{key}={value}' for key, value in sorted(excluded.items())) or 'none'}",
        "",
        "## Workflow",
        "",
        "1. `metric_catalog.csv` で、欲しい項目がどの発見項目として存在するか確認する。",
        "2. 1年度で正しい発見項目を選べたら、同じ `discovered_metric_id` を使って他年度を横展開する。",
        "3. `similarity_suggestions.csv` で、売上総利益/完成工事総利益のような近い項目を統合するか判断する。",
        "4. 統合してよいものだけ、後で `field_definition.csv` や項目管理画面へ反映する。",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _numeric_value(fact: Dict[str, Any]) -> float | None:
    value = normalize_numeric(fact.get("value_numeric"))
    if value is not None:
        return value
    return normalize_numeric(fact.get("value"))


def _is_current_year_fact(fact: Dict[str, Any]) -> bool:
    context = str(fact.get("context_id") or "")
    relative_year = str(fact.get("relative_year") or "")
    if context.startswith("CurrentYear") or context.startswith("Current"):
        return True
    if "当期" in relative_year or "当年度" in relative_year or "当連結" in relative_year:
        return True
    if not context and not relative_year:
        return True
    return False


def _metric_label(fact: Dict[str, Any]) -> str:
    item_name = str(fact.get("item_name") or "").strip()
    if item_name:
        return item_name
    local_name = str(fact.get("element_local_name") or "").strip()
    if local_name:
        return local_name
    return str(fact.get("element_id") or "").strip()


def _normalize_label(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"【[^】]*】", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("_", "")
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"[、，,。:：;；/／\\|｜]", "", text)
    return text.lower()


def _period_bucket(period_or_instant: Any, context_id: Any) -> str:
    text = f"{period_or_instant or ''} {context_id or ''}"
    if "Instant" in text or "時点" in text:
        return "instant"
    if "Duration" in text or "期間" in text:
        return "duration"
    return ""


def _metric_id(parts: Sequence[Any]) -> str:
    text = "\x1f".join(str(part or "").strip() for part in parts)
    return "xm_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _candidate_matches(local_name: str, label: str, token: str) -> bool:
    token = str(token or "").strip()
    if not token:
        return False
    if _is_ascii(token):
        return local_name.lower() == token.lower()
    return token in label


def _split_values(value: Any) -> List[str]:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return []
    return [part.strip() for part in re.split(r"[;\n]+", text) if part.strip() and part.strip().lower() != "nan"]


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value - round(value)) < 0.000001:
        return f"{round(value):,}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def _similarity(left: Any, right: Any) -> float:
    left_text = str(left or "")
    right_text = str(right or "")
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value or "0")))
    except ValueError:
        return 0


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _is_ascii(value: str) -> bool:
    try:
        value.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
