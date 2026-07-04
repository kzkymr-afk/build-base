"""BuildBase P4b: field_definition.csv/.xlsx の増分エンリッチ。

方針（ユーザー確定）: field_definition の完全ビュー化はしない。
semantics.db の対応層（canonical_concepts / concept_mappings / observed_items）から
確定した内容のみを2種類の操作で反映する。

1. 既存概念への追記（enrich_existing）:
   decided_by like '%+corroboration%' かつ status='confirmed' の concept_mappings が
   参照する observed_items.element_local_name を、対応する field_id の
   xbrl_tag_candidates 末尾に追記する（既に含まれていれば追記しない = no-op）。
   他の手キュレーション列（synonyms_ja / section_keywords / validation_rule_ids /
   calculation_formula / review_threshold 等）は一切変更しない。

2. 新概念行の追加（new_concept_rows）:
   field_definition.csv に存在しない concept_id（canonical_concepts のうち
   status != 'merged'）について、その concept_id 宛の
   decided_by like '%+human_adopt%' かつ status='confirmed' の確定 mapping を
   1件以上持つものだけを対象に、field_definition.csv へ新規行を追加する。

いずれの操作も冪等（同じ入力に対して複数回実行しても結果が変わらない）。
既存の抽出コード・corroboration.py・golden・semantics_* は変更しない。
edinet.db には書き込まない。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Sequence

from . import field_admin, semantics_store

DEFAULT_REVIEW_THRESHOLD = "0.85"
DEFAULT_PREFERRED_METHOD = "XBRL_CSV"
DEFAULT_PERIOD_TYPE = "current_year"
DEFAULT_ENRICH_NOTE_PREFIX = "P4bで追加"


def build_enrichment_plan(root: Path) -> Dict[str, Any]:
    """dry-run表示用の計画を返す。DBにもcsv/xlsxにも一切書き込まない。"""
    existing_rows = field_admin.read_field_definitions(root)["rows"]
    existing_by_id = {str(row.get("field_id", "")).strip(): row for row in existing_rows}

    conn = sqlite3.connect(str(semantics_store.semantics_db_path(root)))
    conn.row_factory = sqlite3.Row
    try:
        appends = _plan_existing_appends(conn, existing_by_id)
        new_rows = _plan_new_concept_rows(conn, existing_by_id)
    finally:
        conn.close()

    return {
        "appends": appends,
        "new_rows": new_rows,
        "append_count": sum(1 for item in appends if item["tags_to_add"]),
        "new_row_count": len(new_rows),
    }


def apply_enrichment(root: Path) -> Dict[str, Any]:
    """計画を実際に field_definition.csv / .xlsx に適用する。

    変更前に csv・xlsx の両方をタイムスタンプ付き .bak でバックアップする。
    実際に変更が発生する対象がなければ書き込み・バックアップは行わない。
    """
    plan = build_enrichment_plan(root)

    append_results: List[Dict[str, Any]] = []
    for item in plan["appends"]:
        if not item["tags_to_add"]:
            continue
        # append_field_terms は csv 側のバックアップは取るが xlsx 側は取らないため、
        # 既存コードを変更せず呼び出し側で xlsx バックアップを明示的に先に取る。
        xlsx_path = root / "config" / "field_definition.xlsx"
        xlsx_backup = field_admin._backup_file(xlsx_path) if xlsx_path.exists() else None
        result = field_admin.append_field_terms(
            root,
            item["field_id"],
            synonyms=(),
            xbrl_tags=item["tags_to_add"],
            section_keywords=(),
            note=f"{DEFAULT_ENRICH_NOTE_PREFIX}（確定AI map+corroboration由来のxbrl_tag_candidates追記）",
        )
        result["field_id"] = item["field_id"]
        result["xlsx_backup_path"] = str(xlsx_backup) if xlsx_backup else ""
        append_results.append(result)

    add_result = field_admin.add_field_definitions(root, plan["new_rows"])

    return {
        "append_results": append_results,
        "add_result": add_result,
        "appended_count": sum(1 for r in append_results if r.get("changed_columns")),
        "added_count": add_result.get("added", 0),
    }


def _plan_existing_appends(conn: sqlite3.Connection, existing_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT cm.concept_id AS concept_id, oi.element_local_name AS element_local_name
        FROM concept_mappings cm
        JOIN observed_items oi ON oi.observed_item_id = cm.observed_item_id
        WHERE cm.decided_by LIKE '%+corroboration%' AND cm.status = 'confirmed'
        ORDER BY cm.mapping_id
        """
    ).fetchall()

    by_field: Dict[str, List[str]] = {}
    for row in rows:
        field_id = str(row["concept_id"] or "").strip()
        element_local_name = str(row["element_local_name"] or "").strip()
        if not field_id or not element_local_name:
            continue
        by_field.setdefault(field_id, []).append(element_local_name)

    plan: List[Dict[str, Any]] = []
    for field_id, candidates in by_field.items():
        existing_row = existing_by_id.get(field_id)
        if existing_row is None:
            # field_definition.csv に存在しない field_id への corroboration map は
            # このフェーズ（既存概念への追記）の対象外。新概念フェーズでも扱わない
            # （仕様上、追記対象は常に既存65行 = corroboration対象は全て既存概念）。
            continue
        existing_tags = field_admin._split_terms(existing_row.get("xbrl_tag_candidates", ""))
        existing_lower = {tag.lower() for tag in existing_tags}
        seen_in_batch: set = set()
        tags_to_add: List[str] = []
        for element_local_name in candidates:
            key = element_local_name.lower()
            if key in existing_lower or key in seen_in_batch:
                continue
            seen_in_batch.add(key)
            tags_to_add.append(element_local_name)
        plan.append(
            {
                "field_id": field_id,
                "existing_tags": existing_tags,
                "tags_to_add": tags_to_add,
            }
        )
    return plan


def _plan_new_concept_rows(conn: sqlite3.Connection, existing_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    concepts = conn.execute(
        """
        SELECT concept_id, concept_name_ja, category, target_unit, data_scope,
               period_type, calculation_formula
        FROM canonical_concepts
        WHERE status != 'merged'
        ORDER BY concept_id
        """
    ).fetchall()

    new_rows: List[Dict[str, Any]] = []
    for concept in concepts:
        concept_id = str(concept["concept_id"] or "").strip()
        if not concept_id or concept_id in existing_by_id:
            continue

        mappings = conn.execute(
            """
            SELECT oi.element_local_name AS element_local_name, oi.normalized_scope AS normalized_scope
            FROM concept_mappings cm
            JOIN observed_items oi ON oi.observed_item_id = cm.observed_item_id
            WHERE cm.concept_id = ? AND cm.decided_by LIKE '%+human_adopt%' AND cm.status = 'confirmed'
            ORDER BY cm.mapping_id
            """,
            (concept_id,),
        ).fetchall()
        if not mappings:
            # 確定mappingを持たない新概念は採用対象外（仕様どおり）。
            continue

        tags: List[str] = []
        seen: set = set()
        scopes: List[str] = []
        for mapping in mappings:
            element_local_name = str(mapping["element_local_name"] or "").strip()
            if element_local_name and element_local_name.lower() not in seen:
                seen.add(element_local_name.lower())
                tags.append(element_local_name)
            scope = str(mapping["normalized_scope"] or "").strip()
            if scope:
                scopes.append(scope)

        data_scope = str(concept["data_scope"] or "").strip()
        if not data_scope and scopes:
            data_scope = scopes[0]

        target_unit = str(concept["target_unit"] or "").strip()
        if not target_unit:
            target_unit = ""  # 単位不明な場合は空欄のままレビューに委ねる（人数/比率の誤爆防止）

        period_type = str(concept["period_type"] or "").strip() or DEFAULT_PERIOD_TYPE

        new_rows.append(
            {
                "field_id": concept_id,
                "field_name_ja": str(concept["concept_name_ja"] or "").strip(),
                "category": str(concept["category"] or "").strip(),
                "target_unit": target_unit,
                "data_scope_required": data_scope,
                "period_type": period_type,
                "preferred_method": DEFAULT_PREFERRED_METHOD,
                "xbrl_tag_candidates": ";".join(tags),
                "context_filters": "",
                "section_keywords": "",
                "synonyms_ja": "",
                "calculation_formula": str(concept["calculation_formula"] or "").strip(),
                "validation_rule_ids": "",
                "review_threshold": DEFAULT_REVIEW_THRESHOLD,
                "notes": f"{DEFAULT_ENRICH_NOTE_PREFIX}（AI提案+人間採用, decided_by=ai:*+human_adopt）",
            }
        )
    return new_rows
