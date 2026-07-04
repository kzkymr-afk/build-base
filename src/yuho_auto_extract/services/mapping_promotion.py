"""BuildBase P5c: AI提案（map / new_concept）の確定機構。

semantics.db 内の AI提案（decided_by like 'ai:%', status='proposed'）のうち、
- action='map' の提案は observed_item の実測値（edinet.db の xbrl_facts）と
  概念の既存確定値（data/final/final_master_long.csv）を company_year_id 単位で
  数値突合し、一致すれば confirmed へ昇格する。
- action='new_concept' の提案は concept_name_ja 完全一致でグルーピングし、
  canonical_concepts へ採用のうえ、対応する mapping を confirmed へ昇格する。

絶対制約:
    - 実 claude / AiRunner は一切使わない（このモジュールは純粋にDB操作とCSV/xbrl_facts
      の読み取り突合のみ）。
    - field_definition.csv は変更しない（読みも書きもしない）。
    - data/intermediate/edinet.db には絶対に書かない（読み取り専用で開く）。
    - 既存の human/deterministic confirmed mapping は絶対に上書き・削除しない。
      対象行の抽出条件は必ず decided_by like 'ai:%' かつ status='proposed'。
      さらに semantics_store.update_concept_mapping_status 自体も
      expected_current_status='proposed' ガードを持つため二重に防御される。
    - 冪等: 同じ入力に対して複数回実行しても、2回目以降は追加の変更が発生しない
      （対象行抽出条件に status='proposed' が含まれるため、確定済み行は
      そもそも候補集合に入らない）。
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..corroboration import _parse_fact_value
from . import semantics_store

EDINET_DB_RELATIVE_PATH = Path("data") / "intermediate" / "edinet.db"
FINAL_MASTER_LONG_RELATIVE_PATH = Path("data") / "final" / "final_master_long.csv"

# observed_items.normalized_scope -> xbrl_facts.consolidation_scope の許容値。
# 空文字（scope不明）の場合はこの辞書に鍵が無いので None を返し、呼び出し側で
# scope制約なしの検索にフォールバックする。
SCOPE_TO_CONSOLIDATION_SCOPE: Dict[str, List[str]] = {
    "standalone": ["個別"],
    "consolidated": ["連結"],
    # セグメントは連結スコープ内の内訳が多いが、実データでその他/個別に
    # またがる例もあるため広めに許容する。
    "segment": ["その他", "連結", "個別"],
}

RELATIVE_YEAR_CURRENT = ("当期", "当期末")

# 数値裏取りの確定閾値（仕様書§1.4で実データ検証済み）。
MIN_OVERLAP_COUNT = 3
MIN_MATCH_RATE = 0.8
FinalValue = Tuple[float, str]


def _now_utc_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# データ読み込みヘルパ
# ---------------------------------------------------------------------------


def load_final_master_long_index(root: Path) -> Dict[Tuple[str, str], List[FinalValue]]:
    """final_master_long.csv を1回読み込み、(company_year_id, field_id) -> [(value_normalized, unit)] に畳み込む。

    同一キーに複数行がある場合は全て保持する（呼び出し側で len==1 の場合のみ
    一意な値として採用する設計のため、ここでは畳み込まず素直にリストへ積む）。
    """
    path = root / FINAL_MASTER_LONG_RELATIVE_PATH
    index: Dict[Tuple[str, str], List[FinalValue]] = defaultdict(list)
    if not path.exists():
        return index
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            company_year_id = str(row.get("company_year_id") or "")
            field_id = str(row.get("field_id") or "")
            if not company_year_id or not field_id:
                continue
            unit_normalized = str(row.get("unit_normalized") or "")
            raw_value = row.get("value_normalized")
            value = _parse_fact_value(raw_value)
            if value is None:
                continue
            index[(company_year_id, field_id)].append((value, unit_normalized))
    return index


def comparable_fact_value(raw_fact_value: float, observed_unit: str, concept_unit: str) -> Optional[float]:
    """XBRL fact値をfinal_master_longの単位に合わせる。

    従来は全factを円として百万円へ変換していたため、%などの非金額指標が照合不能
    だった。%はそのまま百分点として比較し、金額は従来どおり円->百万円に変換する。
    """
    observed_unit = str(observed_unit or "")
    concept_unit = str(concept_unit or "")
    if concept_unit == "%" or observed_unit == "%":
        return raw_fact_value
    if concept_unit in {"百万円", ""}:
        return round(raw_fact_value / 1_000_000, 6)
    return raw_fact_value


def values_match_for_unit(concept_value: float, element_value: float, concept_unit: str) -> bool:
    concept_unit = str(concept_unit or "")
    tolerance = 0.5 if concept_unit == "%" else max(1.0, abs(concept_value) * 0.001)
    return abs(concept_value - element_value) <= tolerance


def open_edinet_db_readonly(root: Path) -> sqlite3.Connection:
    """data/intermediate/edinet.db を読み取り専用で開く。

    PRAGMA query_only=ON で防御的に書き込みを禁止する
    （P5cの絶対制約「edinet.dbに書かない」の実行時ガード）。
    """
    db_path = root / EDINET_DB_RELATIVE_PATH
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


# ---------------------------------------------------------------------------
# 1. map提案の数値裏取り
# ---------------------------------------------------------------------------


def corroborate_map_proposal(
    edinet_conn: sqlite3.Connection,
    final_rows_by_key: Dict[Tuple[str, str], List[FinalValue]],
    mapping_row: Dict[str, Any],
    observed_item_row: Dict[str, Any],
) -> Dict[str, Any]:
    """map提案1件を、observed_itemの実測値とconceptの既存確定値で突合する。

    mapping_row: concept_mappings の1行（action='map'想定）。
    observed_item_row: observed_items の対応行（element_id, normalized_scope を持つ）。
    final_rows_by_key: load_final_master_long_index() の戻り値。

    戻り値には overlap_count / match_count / match_rate / corroborated / detail を含む
    （evidence_json への監査証跡保存・CLI表示に使う）。
    """
    element_id = str(observed_item_row.get("element_id") or "")
    concept_id = str(mapping_row.get("concept_id") or "")
    normalized_scope = str(observed_item_row.get("normalized_scope") or "")
    observed_unit = str(observed_item_row.get("unit") or "")

    scopes = SCOPE_TO_CONSOLIDATION_SCOPE.get(normalized_scope)

    result: Dict[str, Any] = {
        "mapping_id": mapping_row.get("mapping_id"),
        "concept_id": concept_id,
        "element_id": element_id,
        "overlap_count": 0,
        "match_count": 0,
        "match_rate": 0.0,
        "corroborated": False,
        "detail": [],
    }
    if not element_id or not concept_id:
        return result

    sql = (
        "select company_year_id, value from xbrl_facts "
        "where element_id = ? and relative_year in (?, ?)"
    )
    params: List[Any] = [element_id, RELATIVE_YEAR_CURRENT[0], RELATIVE_YEAR_CURRENT[1]]
    if scopes:
        sql += f" and consolidation_scope in ({','.join(['?'] * len(scopes))})"
        params += scopes
    facts = edinet_conn.execute(sql, params).fetchall()

    fact_values_by_cy: Dict[str, set] = defaultdict(set)
    for row in facts:
        cy = row["company_year_id"] if isinstance(row, sqlite3.Row) else row[0]
        raw_value = row["value"] if isinstance(row, sqlite3.Row) else row[1]
        parsed = _parse_fact_value(raw_value)
        if parsed is not None:
            fact_values_by_cy[str(cy)].add(parsed)

    overlap = 0
    match = 0
    detail: List[Dict[str, Any]] = []
    for cy, values in fact_values_by_cy.items():
        if len(values) != 1:
            # 同一(company_year, element, scope)内で値が割れる = 曖昧なので突合対象外
            continue
        final_values = final_rows_by_key.get((cy, concept_id))
        if not final_values or len(final_values) != 1:
            # 概念に既存値が無い、または複数値で一意化できない
            continue
        concept_value, concept_unit = final_values[0]
        element_value = comparable_fact_value(next(iter(values)), observed_unit, concept_unit)
        if element_value is None:
            continue
        overlap += 1
        is_match = values_match_for_unit(concept_value, element_value, concept_unit)
        if is_match:
            match += 1
        detail.append(
            {
                "company_year_id": cy,
                "element_value": element_value,
                "concept_value": concept_value,
                "unit": concept_unit,
                "matched": is_match,
            }
        )

    match_rate = (match / overlap) if overlap else 0.0
    corroborated = overlap >= MIN_OVERLAP_COUNT and match_rate >= MIN_MATCH_RATE

    result.update(
        {
            "overlap_count": overlap,
            "match_count": match,
            "match_rate": match_rate,
            "corroborated": corroborated,
            "detail": detail,
        }
    )
    return result


def verify_map_proposal_numerically(root: Path, proposal: Dict[str, Any]) -> Dict[str, Any]:
    """単一のmap提案(concept_mappings行)を数値検証する薄いエントリポイント。

    proposal は observed_item_id を持つ concept_mappings 行を想定する。
    内部で semantics.db / edinet.db / final_master_long.csv を開いて突合する。
    バッチ実行（confirm_map_proposals）では効率のためにこの関数を直接使わず、
    corroborate_map_proposal に事前ロード済みインデックスを渡す形を使う。
    単発チェック（デバッグ・CLI単体確認）用にこの関数を提供する。
    """
    conn = semantics_store.connect(root)
    try:
        observed_items = semantics_store.fetch_observed_items(conn)
        observed_item = observed_items.get(str(proposal.get("observed_item_id") or ""))
        if observed_item is None:
            return {
                "mapping_id": proposal.get("mapping_id"),
                "concept_id": proposal.get("concept_id"),
                "element_id": None,
                "overlap_count": 0,
                "match_count": 0,
                "match_rate": 0.0,
                "corroborated": False,
                "detail": [],
                "reason": "observed_item_not_found",
            }
        final_rows_by_key = load_final_master_long_index(root)
        edinet_conn = open_edinet_db_readonly(root)
        try:
            return corroborate_map_proposal(edinet_conn, final_rows_by_key, proposal, observed_item)
        finally:
            edinet_conn.close()
    finally:
        conn.close()


def promote_verified_map_proposals(root: Path, *, dry_run: bool = True) -> Dict[str, Any]:
    """全ai map提案を数値検証し、verifiedのみconfirmedへ昇格する。

    対象行の抽出条件: action='map', status='proposed', decided_by like 'ai:%'。
    human/deterministic のconfirmed行は候補集合に一切入らない。

    dry_run=True（既定）の場合はDB書き込みを一切行わず、判定結果のみ返す。
    """
    conn = semantics_store.connect(root)
    edinet_conn = None
    try:
        edinet_conn = open_edinet_db_readonly(root)
        final_rows_by_key = load_final_master_long_index(root)
        observed_items = semantics_store.fetch_observed_items(conn)

        proposed_map_rows = [
            row
            for row in semantics_store.fetch_concept_mappings(conn)
            if row.get("action") == "map"
            and row.get("status") == "proposed"
            and str(row.get("decided_by") or "").startswith("ai:")
        ]

        results: List[Dict[str, Any]] = []
        confirmed_count = 0
        for mapping_row in proposed_map_rows:
            observed_item = observed_items.get(str(mapping_row.get("observed_item_id") or ""))
            if observed_item is None:
                results.append(
                    {
                        "mapping_id": mapping_row.get("mapping_id"),
                        "concept_id": mapping_row.get("concept_id"),
                        "element_id": None,
                        "overlap_count": 0,
                        "match_count": 0,
                        "match_rate": 0.0,
                        "corroborated": False,
                        "detail": [],
                        "reason": "observed_item_not_found",
                    }
                )
                continue
            outcome = corroborate_map_proposal(edinet_conn, final_rows_by_key, mapping_row, observed_item)
            results.append(outcome)
            if outcome["corroborated"]:
                if not dry_run:
                    decided_by = str(mapping_row.get("decided_by") or "")
                    model = decided_by.split(":", 1)[1] if ":" in decided_by else decided_by
                    updated = semantics_store.update_concept_mapping_status(
                        conn,
                        str(mapping_row["mapping_id"]),
                        new_status="confirmed",
                        new_decided_by=f"ai:{model}+corroboration",
                        evidence_patch={
                            "corroboration": {
                                "overlap_count": outcome["overlap_count"],
                                "match_count": outcome["match_count"],
                                "match_rate": outcome["match_rate"],
                            }
                        },
                    )
                    if updated:
                        confirmed_count += 1
                else:
                    confirmed_count += 1

        if not dry_run:
            semantics_store.write_csv_mirrors(root, conn)

        return {
            "map_proposals_checked": len(proposed_map_rows),
            "corroborated": confirmed_count,
            "not_corroborated": len(proposed_map_rows) - confirmed_count,
            "results": results,
            "dry_run": dry_run,
        }
    finally:
        conn.close()
        if edinet_conn is not None:
            edinet_conn.close()


# ---------------------------------------------------------------------------
# 2. new_concept提案の採用（重複解決含む）
# ---------------------------------------------------------------------------


def new_concept_id(concept_name_ja: str) -> str:
    """concept_name_ja から決定論的な concept_id を生成する。

    既存 mapping_id() 生成パターン（semantics_backfill.py）を踏襲した
    sha1(...)[:16] 方式。concept_name_ja のみをキーにする（重複解決後は
    完全一致名で1つに統合されるため衝突しない）。
    """
    raw = "new_concept|" + concept_name_ja.strip()
    return "nc_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _infer_data_scope(concept_name_ja: str) -> str:
    name = concept_name_ja.strip()
    if name.endswith("_単独") or "単独" in name:
        return "standalone"
    if name.endswith("_連結") or "連結" in name:
        return "consolidated"
    if "セグメント" in name:
        return "segment"
    return ""


def _infer_target_unit(observed_item: Optional[Dict[str, Any]]) -> str:
    if not observed_item:
        return ""
    unit = str(observed_item.get("unit") or "").strip()
    if unit == "円":
        return "百万円"
    return unit


def resolve_new_concept_duplicates(
    proposed_new_concept_rows: List[Dict[str, Any]],
    observed_items: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str, str, List[str]]]]:
    """new_concept提案を concept_name_ja 完全一致でグルーピングし、

    (作成するcanonical_concepts行のリスト, mapping更新タプルのリスト) を返す。
    mapping更新タプルは (mapping_id, new_concept_id, model, merged_from_mapping_ids)。

    concept_name_ja が欠落している提案はグループ化せずスキップする
    （proposed据え置き＝呼び出し側で単に対象から漏れるだけで実害なし）。
    """
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in proposed_new_concept_rows:
        evidence = json.loads(row.get("evidence_json") or "{}")
        new_concept = evidence.get("new_concept") or {}
        name_ja = str(new_concept.get("concept_name_ja") or "").strip()
        if not name_ja:
            continue
        groups[name_ja].append(row)

    new_concepts: List[Dict[str, Any]] = []
    mapping_updates: List[Tuple[str, str, str, List[str]]] = []

    for name_ja, rows in groups.items():
        concept_id = new_concept_id(name_ja)
        first_evidence = json.loads(rows[0].get("evidence_json") or "{}")
        first_new_concept = first_evidence.get("new_concept") or {}
        observed_item = observed_items.get(str(rows[0].get("observed_item_id") or ""))

        merged_from_all = [str(r["mapping_id"]) for r in rows]
        new_concepts.append(
            {
                "concept_id": concept_id,
                "concept_name_ja": name_ja,
                "category": str(first_new_concept.get("category") or ""),
                "data_scope": _infer_data_scope(name_ja),
                "target_unit": _infer_target_unit(observed_item),
                "period_type": "current_year",
                "definition_ja": str(first_new_concept.get("definition_ja") or ""),
                "calculation_formula": "",
                "status": "active",
                "merged_from": merged_from_all if len(rows) > 1 else [],
            }
        )

        for row in rows:
            decided_by = str(row.get("decided_by") or "")
            model = decided_by.split(":", 1)[1] if ":" in decided_by else decided_by
            merged_from = [mid for mid in merged_from_all if mid != str(row["mapping_id"])]
            mapping_updates.append((str(row["mapping_id"]), concept_id, model, merged_from))

    return new_concepts, mapping_updates


def adopt_new_concepts(root: Path, *, dry_run: bool = True) -> Dict[str, Any]:
    """ai new_concept提案から canonical_concepts を作成し、対応するmappingをconfirmする。

    対象行の抽出条件: action='new_concept', status='proposed', decided_by like 'ai:%'。
    concept_name_ja 完全一致で重複統合する（resolve_new_concept_duplicates）。

    dry_run=True（既定）の場合はDB書き込みを一切行わず、件数のみ返す。
    """
    conn = semantics_store.connect(root)
    try:
        proposed_new_concept_rows = [
            row
            for row in semantics_store.fetch_concept_mappings(conn)
            if row.get("action") == "new_concept"
            and row.get("status") == "proposed"
            and str(row.get("decided_by") or "").startswith("ai:")
        ]
        observed_items = semantics_store.fetch_observed_items(conn)

        new_concepts, mapping_updates = resolve_new_concept_duplicates(
            proposed_new_concept_rows, observed_items
        )

        if not dry_run:
            canonical_rows = [
                {k: v for k, v in c.items() if k != "merged_from"} for c in new_concepts
            ]
            semantics_store.upsert_canonical_concepts(conn, canonical_rows)
            confirmed = 0
            for mapping_id, concept_id, model, merged_from in mapping_updates:
                updated = semantics_store.update_concept_mapping_status(
                    conn,
                    mapping_id,
                    new_status="confirmed",
                    new_decided_by=f"ai:{model}+human_adopt",
                    new_action="map",
                    new_concept_id=concept_id,
                    evidence_patch={"adopted_concept_id": concept_id, "merged_from": merged_from},
                )
                if updated:
                    confirmed += 1
            semantics_store.write_csv_mirrors(root, conn)
        else:
            confirmed = len(mapping_updates)

        return {
            "new_concept_proposals_checked": len(proposed_new_concept_rows),
            "concepts_created": len(new_concepts),
            "mappings_confirmed": confirmed,
            "dry_run": dry_run,
        }
    finally:
        conn.close()
