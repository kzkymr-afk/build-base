"""BuildBase P4a: 対応層バックフィル。

既存資産（metric_catalog.csv, field_mappings.csv, source_audit.csv,
review_resolved.csv, company_field_exclusions.csv, field_definition.csv）から
semantics.db の対応層3テーブル（observed_items / canonical_concepts /
concept_mappings）を冪等に構築する。

P4aのスコープ厳守:
    - field_definition.csv のビュー化・生成はしない（既存抽出コードの読み元は
      一切変更しない）。
    - パイプライン挙動は変更しない。
    - 既存の抽出コード・corroboration.py・exporter・review_queue・
      semantics_corroborate・golden は一切変更しない（import も片方向のみ）。

冪等性: backfill_semantics() は何度実行しても同じ最終状態になる
（observed_items / concept_mappings は完全置換、canonical_concepts はupsert）。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config_loader import load_pipeline_config
from ..io_utils import is_blankish, prefer_existing_table, read_table
from . import semantics_store

# --- item_kind / source 定数 ------------------------------------------------

ITEM_KIND_XBRL = "xbrl"
ITEM_KIND_LOCAL_TABLE = "local_table"
ITEM_KIND_MANUAL = "manual"

SOURCE_METRIC_CATALOG = "metric_catalog"
SOURCE_LOCAL_RULE_TABLE = "source_audit_local_rule_table"
SOURCE_REVIEW_RESOLVED = "review_resolved"
SOURCE_COMPANY_FIELD_EXCLUSIONS = "company_field_exclusions"

ACTION_MAP = "map"
ACTION_IGNORE = "ignore"
ACTION_DIFFERENT_SCOPE = "different_scope"

STATUS_PROPOSED = "proposed"
STATUS_CONFIRMED = "confirmed"

DECIDED_BY_TAG_MATCH = "deterministic:xbrl_tag_candidates_match"
DECIDED_BY_LOCAL_TABLE = "deterministic:local_table_extractor_field_id"
DECIDED_BY_FIELD_MAPPINGS = "human:field_mappings_backfill"
DECIDED_BY_REVIEW_RESOLVED = "human:review_resolved_backfill"
DECIDED_BY_EXCLUSIONS = "human:company_field_exclusions_backfill"

LOCAL_RULE_TABLE_METHOD = "LOCAL_RULE_TABLE"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# ID生成規則（決定論的）
# ---------------------------------------------------------------------------

def xbrl_observed_item_id(discovered_metric_id: str) -> str:
    """metric_catalog.csv の discovered_metric_id をそのまま継承する。新規ハッシュなし。"""
    return str(discovered_metric_id or "")


def local_observed_item_id(source_heading: str, field_id: str, company_id: str) -> str:
    raw = "|".join(["local_table", source_heading or "", field_id or "", company_id or ""])
    return "lt_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def review_resolved_observed_item_id(company_year_id: str, field_id: str, source_doc_id: str) -> str:
    raw = "|".join(["review_resolved", company_year_id or "", field_id or "", source_doc_id or ""])
    return "rr_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def concept_id_from_field_id(field_id: str) -> str:
    """P4a時点ではconcept_id=field_idそのもの（65件を無変更で継承）。"""
    return str(field_id or "")


def mapping_id(observed_item_id: str, concept_id: str, action: str, decided_by: str) -> str:
    """同一(observed_item, concept, action, decided_by)の重複バックフィル実行を
    同一mapping_idに畳み込み、再実行の冪等性を保証する。
    """
    raw = "|".join([observed_item_id or "", concept_id or "", action or "", decided_by or ""])
    return "cmap_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def exclusion_mapping_id(company_id: str, field_id: str, start_year: str, end_year: str) -> str:
    raw = "|".join(
        ["company_field_exclusions", company_id or "", field_id or "", start_year or "", end_year or ""]
    )
    return "cmap_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _company_from_company_year(company_year_id: str) -> str:
    """'ANDO_HAZAMA_2015' -> 'ANDO_HAZAMA'。末尾の '_' 区切り4桁年度部分を取り除く。"""
    text = str(company_year_id or "")
    if "_" in text:
        prefix, _, suffix = text.rpartition("_")
        if suffix.isdigit() and len(suffix) == 4:
            return prefix
    return text


# ---------------------------------------------------------------------------
# taxonomy_kind 分類（純関数）
# ---------------------------------------------------------------------------

def classify_taxonomy_kind(element_id: str) -> str:
    """element_id のプレフィックスから taxonomy_kind を分類する。

    実データ分布（2,163件）: jppfs_cor 39.3% / jpcrp030000-asr等の会社拡張 41.8% /
    jpcrp_cor 10.1% / jpigp_cor(IFRS) 8.8%。element_id が空文字はローカル表専用
    (taxonomy_kind='local')なので呼び出し側で item_kind により明示的に付与する
    （この関数は element_id が非空の場合のみ呼ぶ想定）。
    """
    if not element_id:
        return "local"
    prefix = element_id.split(":", 1)[0] if ":" in element_id else element_id
    if prefix.startswith("jppfs_cor"):
        return "jppfs"
    if prefix.startswith("jpcrp_cor"):
        return "jpcrp"
    if prefix.startswith("jpigp_cor"):
        return "ifrs"
    if prefix.startswith("jpcrp03") or prefix.startswith("jpcrp04"):
        return "extension"
    return "extension"


# ---------------------------------------------------------------------------
# matched_field_ids の分割（xbrl_discovered_metrics.py と互換。新規実装しない）
# ---------------------------------------------------------------------------

def _split_matched_field_ids(value: Any) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(";") if part.strip()]


# ---------------------------------------------------------------------------
# ①field_definition.csv -> canonical_concepts
# ---------------------------------------------------------------------------

def _concept_row(field: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "concept_id": str(field.get("field_id") or ""),
        "concept_name_ja": field.get("field_name_ja"),
        "category": field.get("category"),
        "data_scope": field.get("data_scope_required"),
        "target_unit": field.get("target_unit"),
        "period_type": field.get("period_type"),
        "definition_ja": field.get("notes"),
        "calculation_formula": field.get("calculation_formula"),
        "status": "active",
        "merged_into_concept_id": "",
    }


# ---------------------------------------------------------------------------
# ②metric_catalog.csv -> observed_items(xbrl)
# ---------------------------------------------------------------------------

def _xbrl_observed_item_row(row: Dict[str, Any]) -> Dict[str, Any]:
    element_id = str(row.get("element_id") or "")
    observed_item_id = xbrl_observed_item_id(row.get("discovered_metric_id"))
    sample_values = {
        "sample_company_year_id": row.get("sample_company_year_id"),
        "sample_source_doc_id": row.get("sample_source_doc_id"),
        "sample_context_id": row.get("sample_context_id"),
        "sample_value": row.get("sample_value"),
        "sample_value_display": row.get("sample_value_display"),
        "sample_source_quote": row.get("sample_source_quote"),
        "value_count": row.get("value_count"),
        "company_count": row.get("company_count"),
        "year_count": row.get("year_count"),
        "company_year_count": row.get("company_year_count"),
        "matched_field_ids": row.get("matched_field_ids"),
    }
    return {
        "observed_item_id": observed_item_id,
        "item_kind": ITEM_KIND_XBRL,
        "element_id": element_id,
        "element_local_name": row.get("element_local_name"),
        "normalized_scope": row.get("normalized_scope"),
        "period_bucket": row.get("period_bucket"),
        "taxonomy_kind": classify_taxonomy_kind(element_id),
        "section_name": "",
        "row_label": "",
        "company_scope": "",
        "label_ja": row.get("discovered_metric_label"),
        "unit": row.get("unit"),
        "first_fiscal_year": row.get("first_fiscal_year"),
        "last_fiscal_year": row.get("last_fiscal_year"),
        "sample_values": sample_values,
        "source": SOURCE_METRIC_CATALOG,
    }


# ---------------------------------------------------------------------------
# ③source_audit.csv の LOCAL_RULE_TABLE -> observed_items(local_table)
# ---------------------------------------------------------------------------

def _local_observed_item_row(observed_item_id: str, row: Dict[str, Any], company_id: str) -> Dict[str, Any]:
    return {
        "observed_item_id": observed_item_id,
        "item_kind": ITEM_KIND_LOCAL_TABLE,
        "element_id": "",
        "element_local_name": "",
        "normalized_scope": row.get("data_scope"),
        "period_bucket": "",
        "taxonomy_kind": "local",
        "section_name": row.get("source_heading"),
        "row_label": "",
        "company_scope": company_id,
        "label_ja": row.get("field_name_ja"),
        "unit": row.get("unit_normalized"),
        "first_fiscal_year": "",
        "last_fiscal_year": "",
        "sample_values": {
            "sample_source_doc_id": row.get("source_doc_id"),
            "sample_company_year_id": row.get("company_year_id"),
            "sample_value": row.get("value"),
            "sample_source_quote": row.get("source_quote"),
        },
        "source": SOURCE_LOCAL_RULE_TABLE,
    }


# ---------------------------------------------------------------------------
# ④-4 review_resolved.csv -> 擬似observed_item
# ---------------------------------------------------------------------------

def _review_resolved_observed_item_row(observed_item_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
    company_id = _company_from_company_year(str(row.get("company_year_id") or ""))
    return {
        "observed_item_id": observed_item_id,
        "item_kind": ITEM_KIND_MANUAL,
        "element_id": "",
        "element_local_name": "",
        "normalized_scope": row.get("data_scope"),
        "period_bucket": "",
        "taxonomy_kind": "local",
        "section_name": row.get("source_heading"),
        "row_label": "",
        "company_scope": company_id,
        "label_ja": row.get("field_name_ja"),
        "unit": row.get("unit_normalized"),
        "first_fiscal_year": row.get("fiscal_year"),
        "last_fiscal_year": row.get("fiscal_year"),
        "sample_values": {
            "sample_source_doc_id": row.get("source_doc_id"),
            "sample_company_year_id": row.get("company_year_id"),
            "sample_source_quote": row.get("source_quote"),
            "has_citation": bool(row.get("source_heading")),
        },
        "source": SOURCE_REVIEW_RESOLVED,
    }


# ---------------------------------------------------------------------------
# mapping row builder
# ---------------------------------------------------------------------------

def _mapping_row(
    *,
    observed_item_id: str,
    concept_id: Optional[str],
    action: str,
    status: str,
    decided_by: str,
    confidence: Optional[float] = None,
    evidence: Optional[Dict[str, Any]] = None,
    valid_from_year: Optional[str] = None,
    valid_to_year: Optional[str] = None,
    company_scope: str = "",
    mapping_id_override: Optional[str] = None,
) -> Dict[str, Any]:
    concept_id_str = str(concept_id or "")
    mid = mapping_id_override or mapping_id(observed_item_id, concept_id_str, action, decided_by)
    return {
        "mapping_id": mid,
        "observed_item_id": observed_item_id,
        "concept_id": concept_id_str,
        "action": action,
        "status": status,
        "decided_by": decided_by,
        "confidence": confidence,
        "evidence": evidence or {},
        "valid_from_year": valid_from_year,
        "valid_to_year": valid_to_year,
        "company_scope": company_scope,
    }


# ---------------------------------------------------------------------------
# 4-2. field_mappings.csv の変換規則
# ---------------------------------------------------------------------------

def _mapping_from_field_mappings_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    observed_item_id = xbrl_observed_item_id(row.get("discovered_metric_id"))
    if not observed_item_id:
        return None
    status = str(row.get("mapping_status") or "")
    target_field_id = str(row.get("target_field_id") or "").strip()
    mapping_note = row.get("mapping_note")

    if status == "accepted" and target_field_id:
        return _mapping_row(
            observed_item_id=observed_item_id,
            concept_id=target_field_id,
            action=ACTION_MAP,
            status=STATUS_CONFIRMED,
            decided_by=DECIDED_BY_FIELD_MAPPINGS,
            evidence={"mapping_note": mapping_note} if mapping_note else {},
        )
    if status == "rejected" and target_field_id:
        return _mapping_row(
            observed_item_id=observed_item_id,
            concept_id=None,
            action=ACTION_IGNORE,
            status=STATUS_CONFIRMED,
            decided_by=DECIDED_BY_FIELD_MAPPINGS,
            evidence={"rejected_against_field_id": target_field_id, "mapping_note": mapping_note},
        )
    if status == "rejected" and not target_field_id:
        return _mapping_row(
            observed_item_id=observed_item_id,
            concept_id=None,
            action=ACTION_IGNORE,
            status=STATUS_CONFIRMED,
            decided_by=DECIDED_BY_FIELD_MAPPINGS,
            evidence={"mapping_note": mapping_note} if mapping_note else {},
        )
    if status == "candidate":
        return _mapping_row(
            observed_item_id=observed_item_id,
            concept_id=target_field_id or None,
            action=ACTION_MAP,
            status=STATUS_PROPOSED,
            decided_by=DECIDED_BY_FIELD_MAPPINGS,
            evidence={"mapping_note": mapping_note} if mapping_note else {},
        )
    if status == "separate":
        return _mapping_row(
            observed_item_id=observed_item_id,
            concept_id=target_field_id or None,
            action=ACTION_DIFFERENT_SCOPE,
            status=STATUS_CONFIRMED,
            decided_by=DECIDED_BY_FIELD_MAPPINGS,
            evidence={"mapping_note": mapping_note} if mapping_note else {},
        )
    # 'unmapped' や未知の値は将来のCSV変化への備え。マッピング行は作らない。
    return None


# ---------------------------------------------------------------------------
# 4-4. review_resolved.csv の変換規則
# ---------------------------------------------------------------------------

def _mappings_from_review_resolved_row(row: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """1行から (observed_item行 or None, mapping行 or None) を返す。"""
    decision = str(row.get("review_decision") or "")
    applied_status = str(row.get("applied_status") or "")
    company_year_id = str(row.get("company_year_id") or "")
    field_id = str(row.get("field_id") or "")
    source_doc_id = str(row.get("source_doc_id") or "")
    if not company_year_id or not field_id:
        return None, None

    observed_item_id = review_resolved_observed_item_id(company_year_id, field_id, source_doc_id)

    if decision in {"correct", "accept"} and applied_status == "applied":
        item_row = _review_resolved_observed_item_row(observed_item_id, row)
        evidence = {
            "corrected_value": row.get("corrected_value"),
            "source_heading": row.get("source_heading"),
            "source_quote": row.get("source_quote"),
            "reviewer_note": row.get("reviewer_note"),
            "reviewed_at": row.get("reviewed_at"),
            "has_citation": bool(row.get("source_heading")),
        }
        map_row = _mapping_row(
            observed_item_id=observed_item_id,
            concept_id=field_id,
            action=ACTION_MAP,
            status=STATUS_CONFIRMED,
            decided_by=DECIDED_BY_REVIEW_RESOLVED,
            evidence=evidence,
        )
        return item_row, map_row

    if decision == "not_applicable" and applied_status == "not_applicable":
        item_row = _review_resolved_observed_item_row(observed_item_id, row)
        company_id = _company_from_company_year(company_year_id)
        evidence = {
            "reviewer_note": row.get("reviewer_note"),
            "reviewed_at": row.get("reviewed_at"),
        }
        map_row = _mapping_row(
            observed_item_id=observed_item_id,
            concept_id=None,
            action=ACTION_IGNORE,
            status=STATUS_CONFIRMED,
            decided_by=DECIDED_BY_REVIEW_RESOLVED,
            company_scope=company_id,
            evidence=evidence,
        )
        return item_row, map_row

    return None, None


# ---------------------------------------------------------------------------
# 4-5. company_field_exclusions.csv の変換規則
# ---------------------------------------------------------------------------

def _mapping_from_exclusion_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    company_id = str(row.get("company_id") or "")
    field_id = str(row.get("field_id") or "")
    if not company_id or not field_id:
        return None
    start_year = str(row.get("start_year") or "")
    end_year = str(row.get("end_year") or "")
    return _mapping_row(
        observed_item_id="",
        concept_id=field_id,
        action=ACTION_IGNORE,
        status=STATUS_CONFIRMED,
        decided_by=DECIDED_BY_EXCLUSIONS,
        company_scope=company_id,
        valid_from_year=start_year or None,
        valid_to_year=end_year or None,
        evidence={
            "reason": row.get("reason"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        },
        mapping_id_override=exclusion_mapping_id(company_id, field_id, start_year, end_year),
    )


# ---------------------------------------------------------------------------
# 重複解決
# ---------------------------------------------------------------------------

def _dedupe_by_id(items: Sequence[Dict[str, Any]], key: str = "observed_item_id") -> List[Dict[str, Any]]:
    """同一idの場合は後勝ちで上書きする（実装順序: xbrl -> local -> review）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        out[str(item.get(key) or "")] = item
    out.pop("", None)
    return list(out.values())


def _dedupe_mappings(mappings: Sequence[Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """mapping_id が同一の行は後勝ちで畳み込む。

    mapping_id は (observed_item_id, concept_id, action, decided_by) から決定論的に
    生成されるため、異なる決定主体からの重複mappingは別idとして共存する
    （P4aでは統合しない。カバレッジレポート側で可視化）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for m in mappings:
        if not m:
            continue
        out[str(m.get("mapping_id") or "")] = m
    out.pop("", None)
    return list(out.values())


# ---------------------------------------------------------------------------
# メインエントリポイント
# ---------------------------------------------------------------------------

def backfill_semantics(root: Path) -> Dict[str, Any]:
    """冪等。既存資産のみを読み込み、対応層3テーブルを完全置換で書き込む。

    パイプライン挙動・既存の抽出コード・corroboration.py・exporter・
    review_queue・semantics_corroborate・golden は一切変更しない
    （読み込みのみ、書き込み先は semantics.db の新規3テーブルのみ）。
    """
    run_id = uuid.uuid4().hex
    semantics_store.backup_semantics_db(root)
    conn = semantics_store.connect(root)
    try:
        # --- ① field_definition.csv -> canonical_concepts ---
        cfg = load_pipeline_config(root)
        concepts = [_concept_row(f) for f in cfg.field_definition]

        # --- ② metric_catalog.csv -> observed_items(xbrl) ---
        catalog_path = root / "data" / "marts" / "xbrl_discovered_metrics" / "metric_catalog.csv"
        catalog_rows = read_table(catalog_path) if catalog_path.exists() else []
        xbrl_items = [_xbrl_observed_item_row(r) for r in catalog_rows]

        # --- ③ source_audit.csv の LOCAL_RULE_TABLE -> observed_items(local_table) ---
        audit_path = prefer_existing_table(root / "data" / "final" / "source_audit.csv")
        audit_rows = read_table(audit_path) if audit_path.exists() else []
        local_rows = [r for r in audit_rows if str(r.get("extraction_method") or "") == LOCAL_RULE_TABLE_METHOD]

        local_items_by_id: Dict[str, Dict[str, Any]] = {}
        local_field_map: List[Dict[str, Any]] = []
        for r in local_rows:
            company_id = _company_from_company_year(str(r.get("company_year_id") or ""))
            oid = local_observed_item_id(str(r.get("source_heading") or ""), str(r.get("field_id") or ""), company_id)
            if oid not in local_items_by_id:
                local_items_by_id[oid] = _local_observed_item_row(oid, r, company_id)
            local_field_map.append({"observed_item_id": oid, "field_id": r.get("field_id"), "row": r})

        # --- ④ concept_mappings 構築 ---
        mappings: List[Dict[str, Any]] = []

        # 4-1. field_definition のタグ突合（deterministic, metric_catalogのmatched_field_ids列を再利用）
        for r in catalog_rows:
            oid = xbrl_observed_item_id(r.get("discovered_metric_id"))
            for fid in _split_matched_field_ids(r.get("matched_field_ids")):
                mappings.append(
                    _mapping_row(
                        observed_item_id=oid,
                        concept_id=fid,
                        action=ACTION_MAP,
                        status=STATUS_PROPOSED,
                        decided_by=DECIDED_BY_TAG_MATCH,
                        evidence={"matched_via": "matched_field_ids"},
                    )
                )

        # 4-2. field_mappings.csv 1,044件の人間判断
        field_mappings_path = root / "data" / "marts" / "xbrl_discovered_metrics" / "field_mappings.csv"
        field_mappings_rows = read_table(field_mappings_path) if field_mappings_path.exists() else []
        for r in field_mappings_rows:
            m = _mapping_from_field_mappings_row(r)
            if m:
                mappings.append(m)

        # 4-3. ローカル表 field_id 直結（deterministic）
        for entry in local_field_map:
            row = entry["row"]
            mappings.append(
                _mapping_row(
                    observed_item_id=entry["observed_item_id"],
                    concept_id=str(entry.get("field_id") or ""),
                    action=ACTION_MAP,
                    status=STATUS_CONFIRMED,
                    decided_by=DECIDED_BY_LOCAL_TABLE,
                    evidence={
                        "source_heading": row.get("source_heading"),
                        "sample_source_doc_id": row.get("source_doc_id"),
                        "sample_company_year_id": row.get("company_year_id"),
                        "sample_value": row.get("value"),
                    },
                )
            )

        # 4-4. review_resolved.csv 255件（observed_item逆引き）
        review_path = root / "data" / "review" / "review_resolved.csv"
        review_rows = read_table(review_path) if review_path.exists() else []
        review_items: List[Dict[str, Any]] = []
        review_skipped = 0
        for r in review_rows:
            item_row, map_row = _mappings_from_review_resolved_row(r)
            if item_row is not None:
                review_items.append(item_row)
            if map_row is not None:
                mappings.append(map_row)
            if item_row is None and map_row is None:
                review_skipped += 1

        # 4-5. company_field_exclusions.csv 28件（observed_item無し）
        exclusions_path = root / "config" / "company_field_exclusions.csv"
        exclusion_rows = read_table(exclusions_path) if exclusions_path.exists() else []
        for r in exclusion_rows:
            m = _mapping_from_exclusion_row(r)
            if m:
                mappings.append(m)

        # --- ⑤ 重複解決 ---
        all_items = xbrl_items + list(local_items_by_id.values()) + review_items
        all_items = _dedupe_by_id(all_items)
        all_mappings = _dedupe_mappings(mappings)

        # --- ⑥ 書き込み（observed_items/concept_mappings は完全置換、concepts はupsert） ---
        semantics_store.upsert_canonical_concepts(conn, concepts)
        semantics_store.replace_observed_items(conn, all_items)
        semantics_store.replace_concept_mappings(conn, all_mappings)
        conn.commit()
        semantics_store.write_csv_mirrors(root, conn)

        return {
            "run_id": run_id,
            "concepts_upserted": len(concepts),
            "observed_items_total": len(all_items),
            "observed_items_by_kind": _count_by(all_items, "item_kind"),
            "concept_mappings_total": len(all_mappings),
            "concept_mappings_by_action": _count_by(all_mappings, "action"),
            "concept_mappings_by_status": _count_by(all_mappings, "status"),
            "field_mappings_csv_rows": len(field_mappings_rows),
            "source_audit_local_rule_table_rows": len(local_rows),
            "review_resolved_rows": len(review_rows),
            "review_resolved_skipped": review_skipped,
            "company_field_exclusions_rows": len(exclusion_rows),
        }
    finally:
        conn.close()


def _count_by(rows: Sequence[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts
