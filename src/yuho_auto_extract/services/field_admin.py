from __future__ import annotations

import csv
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from yuho_auto_extract.io_utils import read_table, write_table


FIELD_DEFINITION_COLUMNS = [
    "field_id",
    "field_name_ja",
    "category",
    "target_unit",
    "data_scope_required",
    "period_type",
    "preferred_method",
    "xbrl_tag_candidates",
    "context_filters",
    "section_keywords",
    "synonyms_ja",
    "calculation_formula",
    "validation_rule_ids",
    "review_threshold",
    "notes",
]

EDITABLE_COLUMNS = {
    "field_name_ja",
    "category",
    "target_unit",
    "data_scope_required",
    "period_type",
    "preferred_method",
    "xbrl_tag_candidates",
    "context_filters",
    "section_keywords",
    "synonyms_ja",
    "calculation_formula",
    "validation_rule_ids",
    "review_threshold",
    "notes",
}

LIST_COLUMNS = [
    "field_id",
    "field_name_ja",
    "category",
    "target_unit",
    "data_scope_required",
    "period_type",
    "preferred_method",
    "synonyms_ja",
    "xbrl_tag_candidates",
    "section_keywords",
    "notes",
]

CATEGORY_LABELS = {
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


def read_field_definitions(root: Path, category: str = "", search: str = "") -> Dict[str, Any]:
    rows = _read_rows(root)
    filtered = _filter_rows(rows, category=category, search=search)
    return {
        "path": str(_field_definition_path(root)),
        "rows": filtered,
        "columns": LIST_COLUMNS,
        "editable_columns": sorted(EDITABLE_COLUMNS),
        "categories": _category_options(rows),
        "total": len(filtered),
        "all_total": len(rows),
    }


def update_field_definition(root: Path, field_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    field_id = str(field_id or "").strip()
    if not field_id:
        raise ValueError("field_id is required")
    invalid = sorted(key for key in updates if key not in EDITABLE_COLUMNS)
    if invalid:
        raise ValueError(f"not editable columns: {', '.join(invalid)}")
    rows = _read_rows(root)
    target_index = next((index for index, row in enumerate(rows) if str(row.get("field_id", "")).strip() == field_id), -1)
    if target_index < 0:
        raise ValueError(f"field_id not found: {field_id}")
    before = dict(rows[target_index])
    normalized_updates = {key: _normalize_cell(value) for key, value in updates.items()}
    changed_columns = [key for key, value in normalized_updates.items() if str(before.get(key, "")) != str(value)]
    if not changed_columns:
        return {
            "path": str(_field_definition_path(root)),
            "field": before,
            "changed_columns": [],
            "backup_path": "",
            "xlsx_written": "",
        }
    rows[target_index] = {**before, **normalized_updates}
    backup_path = _backup_file(_field_definition_path(root))
    _write_csv_atomic(_field_definition_path(root), rows, FIELD_DEFINITION_COLUMNS)
    xlsx_written = _sync_xlsx(root, rows)
    return {
        "path": str(_field_definition_path(root)),
        "field": rows[target_index],
        "changed_columns": changed_columns,
        "backup_path": str(backup_path),
        "xlsx_written": str(xlsx_written),
    }


def append_field_terms(
    root: Path,
    field_id: str,
    synonyms: Sequence[str] = (),
    xbrl_tags: Sequence[str] = (),
    section_keywords: Sequence[str] = (),
    note: str = "",
) -> Dict[str, Any]:
    rows = _read_rows(root)
    field_id = str(field_id or "").strip()
    target = next((row for row in rows if str(row.get("field_id", "")).strip() == field_id), None)
    if target is None:
        raise ValueError(f"field_id not found: {field_id}")
    updates = {
        "synonyms_ja": _merge_terms(target.get("synonyms_ja", ""), synonyms),
        "xbrl_tag_candidates": _merge_terms(target.get("xbrl_tag_candidates", ""), xbrl_tags),
        "section_keywords": _merge_terms(target.get("section_keywords", ""), section_keywords),
    }
    if note:
        updates["notes"] = _append_note(target.get("notes", ""), note)
    return update_field_definition(root, field_id, updates)


def add_field_definitions(root: Path, new_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """存在しない field_id の行のみ新規追加する（P4b: 新概念の増分エンリッチ）。

    既に field_definition.csv に存在する field_id は無視する（no-op、冪等性のため）。
    実際に追加する行が1件もない場合は csv/xlsx への書き込み・バックアップを一切行わない。
    既存行・既存列は変更しない（追記ではなく末尾への行追加のみ）。
    """
    rows = _read_rows(root)
    existing_ids = {str(row.get("field_id", "")).strip() for row in rows}
    seen_new_ids: set = set()
    to_add: List[Dict[str, Any]] = []
    for candidate in new_rows:
        field_id = str(candidate.get("field_id", "")).strip()
        if not field_id or field_id in existing_ids or field_id in seen_new_ids:
            continue
        seen_new_ids.add(field_id)
        normalized = {column: _normalize_cell(candidate.get(column, "")) for column in FIELD_DEFINITION_COLUMNS}
        normalized["field_id"] = field_id
        to_add.append(normalized)
    if not to_add:
        return {
            "path": str(_field_definition_path(root)),
            "added": 0,
            "added_field_ids": [],
            "backup_path": "",
            "xlsx_backup_path": "",
            "xlsx_written": "",
        }
    csv_backup = _backup_file(_field_definition_path(root))
    xlsx_path = root / "config" / "field_definition.xlsx"
    xlsx_backup = _backup_file(xlsx_path) if xlsx_path.exists() else None
    combined = rows + to_add
    _write_csv_atomic(_field_definition_path(root), combined, FIELD_DEFINITION_COLUMNS)
    xlsx_written = _sync_xlsx(root, combined)
    return {
        "path": str(_field_definition_path(root)),
        "added": len(to_add),
        "added_field_ids": [row["field_id"] for row in to_add],
        "backup_path": str(csv_backup),
        "xlsx_backup_path": str(xlsx_backup) if xlsx_backup else "",
        "xlsx_written": str(xlsx_written),
    }


def _read_rows(root: Path) -> List[Dict[str, Any]]:
    rows = read_table(_field_definition_path(root))
    return [{column: row.get(column, "") for column in _columns(rows)} for row in rows]


def _columns(rows: Iterable[Dict[str, Any]]) -> List[str]:
    columns = list(FIELD_DEFINITION_COLUMNS)
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns


def _filter_rows(rows: Sequence[Dict[str, Any]], category: str = "", search: str = "") -> List[Dict[str, Any]]:
    category = category.strip()
    query = search.strip().lower()
    out: List[Dict[str, Any]] = []
    for row in rows:
        if category and str(row.get("category", "")) != category:
            continue
        haystack = " ".join(str(row.get(column, "")) for column in LIST_COLUMNS).lower()
        if query and query not in haystack:
            continue
        copied = dict(row)
        copied["category_label"] = CATEGORY_LABELS.get(str(row.get("category", "")), str(row.get("category", "")) or "未分類")
        out.append(copied)
    return out


def _category_options(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    seen = sorted({str(row.get("category", "") or "uncategorized") for row in rows})
    return [{"id": item, "label": CATEGORY_LABELS.get(item, item if item != "uncategorized" else "未分類")} for item in seen]


def _field_definition_path(root: Path) -> Path:
    return root / "config" / "field_definition.csv"


def _sync_xlsx(root: Path, rows: List[Dict[str, Any]]) -> Path:
    return write_table(root / "config" / "field_definition.xlsx", rows)


def _backup_file(path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}.{timestamp}.bak{path.suffix}")
    shutil.copyfile(path, backup)
    return backup


def _write_csv_atomic(path: Path, rows: List[Dict[str, Any]], preferred_columns: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(preferred_columns)
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in columns})
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        finally:
            raise


def _merge_terms(existing: Any, additions: Sequence[str]) -> str:
    values = _split_terms(existing)
    seen = {value.lower() for value in values}
    for item in additions:
        value = str(item or "").strip()
        if not value or value.lower() in seen:
            continue
        values.append(value)
        seen.add(value.lower())
    return ";".join(values)


def _split_terms(value: Any) -> List[str]:
    return [item.strip() for item in str(value or "").replace("\n", ";").split(";") if item.strip()]


def _append_note(existing: Any, note: str) -> str:
    note = note.strip()
    if not note:
        return str(existing or "")
    existing_text = str(existing or "").strip()
    return f"{existing_text} / {note}" if existing_text else note


def _normalize_cell(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
