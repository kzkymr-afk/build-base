"""BuildBase P6: マッピング提案レビュー用のDTO組み立てとconfirm/reject。

concept_mappings(status='proposed') に observed_items / canonical_concepts を
joinして1件=1レビュー対象のDTOを返す（読み取り専用）。

confirm/reject は semantics_store.update_concept_mapping_status に一本化する
（このモジュールは新規update SQLを書かない。既存の
expected_current_status='proposed' 二重ガードに完全に依存する）。

絶対制約:
    - 実 claude / AiRunner は一切使わない（このモジュールは純粋にDB read/updateのみ）。
    - status='confirmed'（human/deterministic/ai+corroboration/ai+human_adopt）の
      既存行は絶対に変更しない。update対象は必ず status='proposed' のみ
      （semantics_store.update_concept_mapping_status 自体のガードで担保）。
    - data/intermediate/edinet.db には書かない（このモジュールは触れない）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import mapping_promotion, semantics_store

# 数値照合の判定閾値（P5cの確定ゲートと揃える）
_CORROBORATION_MIN_OVERLAP = 3
_CORROBORATION_MATCH_OK = 0.8
_CORROBORATION_MATCH_CONFLICT = 0.5


def _corroboration_verdict(result: Dict[str, Any]) -> str:
    """数値突合結果から、非エンジニア向けの判定ラベルを導く。

    corroborated: 一致率高（承認して安全）／conflicts: 一致率低で別物（却下推奨）／
    unverifiable: 概念側に既存値が無い/未抽出で数値検証できない／weak: 部分一致で要確認。
    """
    overlap = int(result.get("overlap_count") or 0)
    rate = float(result.get("match_rate") or 0.0)
    if overlap == 0:
        return "unverifiable"
    if overlap >= _CORROBORATION_MIN_OVERLAP and rate >= _CORROBORATION_MATCH_OK:
        return "corroborated"
    if overlap >= _CORROBORATION_MIN_OVERLAP and rate <= _CORROBORATION_MATCH_CONFLICT:
        return "conflicts"
    return "weak"


def _safe_json(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _proposal_dto(
    mapping_row: Dict[str, Any],
    observed_items: Dict[str, Dict[str, Any]],
    concepts: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    observed_item = observed_items.get(str(mapping_row.get("observed_item_id") or ""), {})
    concept_id = str(mapping_row.get("concept_id") or "")
    concept = concepts.get(concept_id, {})
    evidence = _safe_json(mapping_row.get("evidence_json"))
    sample_values = _safe_json(observed_item.get("sample_values_json"))

    decided_by = str(mapping_row.get("decided_by") or "")
    decided_by_kind = decided_by.split(":", 1)[0] if ":" in decided_by else decided_by

    return {
        "mapping_id": mapping_row.get("mapping_id"),
        "action": mapping_row.get("action"),
        "status": mapping_row.get("status"),
        "decided_by": decided_by,
        "decided_by_kind": decided_by_kind,  # 'ai' | 'deterministic'
        "confidence": mapping_row.get("confidence"),
        "rationale": evidence.get("rationale") or evidence.get("matched_via") or "",
        "new_concept_proposal": evidence.get("new_concept"),
        "observed_item": {
            "observed_item_id": observed_item.get("observed_item_id"),
            "item_kind": observed_item.get("item_kind"),
            "element_id": observed_item.get("element_id"),
            "element_local_name": observed_item.get("element_local_name"),
            "label_ja": observed_item.get("label_ja"),
            "normalized_scope": observed_item.get("normalized_scope"),
            "unit": observed_item.get("unit"),
            "taxonomy_kind": observed_item.get("taxonomy_kind"),
            "section_name": observed_item.get("section_name"),
            "sample_values": sample_values,
        },
        "concept": {
            "concept_id": concept.get("concept_id"),
            "concept_name_ja": concept.get("concept_name_ja"),
            "category": concept.get("category"),
            "data_scope": concept.get("data_scope"),
            "target_unit": concept.get("target_unit"),
        } if concept else None,
    }


def _attach_corroboration(
    root: Path,
    page_rows: List[Dict[str, Any]],
    observed_items: Dict[str, Dict[str, Any]],
    dtos: List[Dict[str, Any]],
) -> None:
    """map/different_scope 提案に、要素実値 vs 概念既存値の数値突合を付与する（読み取り専用）。

    P5c の corroborate_map_proposal を再利用。edinet.db/final が無い環境では
    静かにスキップ（UIは corroboration 無しで表示される）。
    """
    from collections import defaultdict

    targets = [
        (i, row)
        for i, row in enumerate(page_rows)
        if str(row.get("action") or "") in ("map", "different_scope")
    ]
    if not targets:
        return
    element_ids = sorted(
        {
            str(observed_items.get(str(row.get("observed_item_id") or ""), {}).get("element_id") or "")
            for _, row in targets
        }
        - {""}
    )
    if not element_ids:
        return
    try:
        final_index = mapping_promotion.load_final_master_long_index(root)
        edinet_conn = mapping_promotion.open_edinet_db_readonly(root)
    except Exception:
        return
    # edinet.db は element_id 先頭のインデックスが無く1件ずつ引くと遅い。
    # 対象要素の当期ファクトを1クエリ(チャンク分割)で一括ロードして in-memory 突合する
    # （読み取り専用・edinet.dbへ書き込まない）。
    rel = mapping_promotion.RELATIVE_YEAR_CURRENT
    facts_by_elem: Dict[str, List[Any]] = defaultdict(list)
    try:
        chunk = 400
        for start in range(0, len(element_ids), chunk):
            batch = element_ids[start:start + chunk]
            placeholders = ",".join(["?"] * len(batch))
            sql = (
                "select element_id, company_year_id, consolidation_scope, value from xbrl_facts "
                f"where relative_year in (?, ?) and element_id in ({placeholders})"
            )
            for r in edinet_conn.execute(sql, [rel[0], rel[1], *batch]):
                value = mapping_promotion._parse_fact_value(r["value"])
                if value is not None:
                    facts_by_elem[str(r["element_id"])].append(
                        (str(r["company_year_id"]), str(r["consolidation_scope"]), value)
                    )
    finally:
        edinet_conn.close()

    for i, row in targets:
        observed_item = observed_items.get(str(row.get("observed_item_id") or ""), {})
        element_id = str(observed_item.get("element_id") or "")
        concept_id = str(row.get("concept_id") or "")
        observed_unit = str(observed_item.get("unit") or "")
        scopes = mapping_promotion.SCOPE_TO_CONSOLIDATION_SCOPE.get(str(observed_item.get("normalized_scope") or ""))
        by_cy: Dict[str, set] = defaultdict(set)
        for cy, scope, raw_value in facts_by_elem.get(element_id, []):
            if scopes is None or scope in scopes:
                by_cy[cy].add(raw_value)
        overlap = 0
        match = 0
        examples: List[Dict[str, Any]] = []
        for cy, values in by_cy.items():
            if len(values) != 1:
                continue
            element_value = next(iter(values))
            final_values = final_index.get((cy, concept_id))
            if not final_values or len(final_values) != 1:
                continue
            concept_value, concept_unit = final_values[0]
            element_value = mapping_promotion.comparable_fact_value(next(iter(values)), observed_unit, concept_unit)
            if element_value is None:
                continue
            overlap += 1
            is_match = mapping_promotion.values_match_for_unit(concept_value, element_value, concept_unit)
            if is_match:
                match += 1
            if len(examples) < 5:
                examples.append(
                    {"company_year_id": cy, "element_value": element_value, "concept_value": concept_value, "unit": concept_unit, "matched": is_match}
                )
        result = {"overlap_count": overlap, "match_count": match, "match_rate": (match / overlap) if overlap else 0.0}
        dtos[i]["corroboration"] = {**result, "verdict": _corroboration_verdict(result), "examples": examples}


def read_mapping_proposals(
    root: Path,
    *,
    action: str = "",
    decided_by_kind: str = "",
    min_confidence: Optional[float] = None,
    limit: int = 200,
    include_corroboration: bool = True,
    verdict: str = "",
) -> Dict[str, Any]:
    """status='proposed' の concept_mappings をDTO化して返す（読み取り専用）。

    action: 'map'|'different_scope'|'ignore'|'new_concept' で絞り込み（空文字は全件）。
    decided_by_kind: 'ai'|'deterministic' で絞り込み（decided_byの ':' 前方一致）。
    min_confidence: confidence がNone(=deterministic提案)の行は常に通す
        （confidenceフィルタはAI提案にのみ意味を持つため）。
    """
    conn = semantics_store.connect(root)
    try:
        observed_items = semantics_store.fetch_observed_items(conn)
        concepts = semantics_store.fetch_canonical_concepts(conn)
        proposed = [row for row in semantics_store.fetch_concept_mappings(conn) if row.get("status") == "proposed"]
    finally:
        conn.close()

    if action:
        proposed = [row for row in proposed if str(row.get("action") or "") == action]
    if decided_by_kind:
        proposed = [row for row in proposed if str(row.get("decided_by") or "").startswith(f"{decided_by_kind}:")]
    if min_confidence is not None:
        proposed = [
            row for row in proposed
            if row.get("confidence") is None or float(row.get("confidence")) >= min_confidence
        ]

    # verdict 指定時は全件に数値照合を付けてから絞り込む（照合は map/different_scope のみ算出）。
    if verdict:
        dtos_all = [_proposal_dto(row, observed_items, concepts) for row in proposed]
        _attach_corroboration(root, proposed, observed_items, dtos_all)
        dtos_all = [d for d in dtos_all if (d.get("corroboration") or {}).get("verdict") == verdict]
        action_counts = {}
        for d in dtos_all:
            key = str(d.get("action") or "")
            action_counts[key] = action_counts.get(key, 0) + 1
        return {"total": len(dtos_all), "action_counts": action_counts, "proposals": dtos_all[:limit]}

    total = len(proposed)
    action_counts: Dict[str, int] = {}
    for row in proposed:
        key = str(row.get("action") or "")
        action_counts[key] = action_counts.get(key, 0) + 1

    page = proposed[:limit]
    dtos = [_proposal_dto(row, observed_items, concepts) for row in page]
    if include_corroboration:
        _attach_corroboration(root, page, observed_items, dtos)
    return {"total": total, "action_counts": action_counts, "proposals": dtos}


def bulk_reject_conflicting_proposals(
    root: Path,
    *,
    reviewer: str = "auto",
    min_overlap: int = 3,
    max_match_rate: float = 0.1,
    preview: bool = False,
) -> Dict[str, Any]:
    """数値照合で明確に不一致（重複>=min_overlap かつ 一致率<=max_match_rate）の
    proposed な map/different_scope 提案を一括却下する。

    安全策: (1) status='proposed' かつ action='map' のみが対象（confirmedは不変。
    different_scope は「スコープ違いを明示的に記録」する提案で、数値が一致しないのが
    正常なので自動却下の対象にしない）、(2) 閾値は「'conflicts'表示（<=0.5）」より厳しい
    <=0.1（ほぼ全不一致）に固定し、境界ケース（部分一致）は自動却下せず手動レビューに残す。
    update_concept_mapping_status の expected_current_status='proposed' ガードで二重防御。
    """
    conn = semantics_store.connect(root)
    try:
        observed_items = semantics_store.fetch_observed_items(conn)
        proposed = [
            r
            for r in semantics_store.fetch_concept_mappings(conn)
            if r.get("status") == "proposed" and str(r.get("action") or "") == "map"
        ]
    finally:
        conn.close()
    if not proposed:
        return {"preview": preview, "rejected": 0, "candidates": 0, "examples": []}

    dtos = [_proposal_dto(row, observed_items, {}) for row in proposed]
    _attach_corroboration(root, proposed, observed_items, dtos)

    candidates = []
    for row, dto in zip(proposed, dtos):
        corr = dto.get("corroboration") or {}
        overlap = int(corr.get("overlap_count") or 0)
        raw_rate = corr.get("match_rate")
        rate = float(raw_rate) if raw_rate not in (None, "") else 1.0
        if overlap >= min_overlap and rate <= max_match_rate:
            candidates.append((row, dto, corr))

    examples: List[Dict[str, Any]] = []
    for row, dto, corr in candidates[:12]:
        examples.append(
            {
                "mapping_id": row.get("mapping_id"),
                "element_local_name": dto["observed_item"]["element_local_name"],
                "label_ja": dto["observed_item"].get("label_ja"),
                "concept_id": row.get("concept_id"),
                "concept_name_ja": (dto.get("concept") or {}).get("concept_name_ja"),
                "overlap_count": corr.get("overlap_count"),
                "match_rate": corr.get("match_rate"),
            }
        )
    if preview:
        return {"preview": True, "rejected": 0, "candidates": len(candidates), "examples": examples}

    rejected = 0
    conn = semantics_store.connect(root)
    try:
        for row, dto, corr in candidates:
            updated = semantics_store.update_concept_mapping_status(
                conn,
                row.get("mapping_id"),
                new_status="rejected",
                new_decided_by=f"{row.get('decided_by')}+auto_reject_conflict",
                evidence_patch={
                    "auto_reject": {
                        "reason": "numeric_conflict",
                        "reviewer": reviewer,
                        "overlap_count": corr.get("overlap_count"),
                        "match_rate": corr.get("match_rate"),
                    }
                },
            )
            if updated:
                rejected += 1
        if rejected:
            semantics_store.write_csv_mirrors(root, conn)
    finally:
        conn.close()
    return {"preview": False, "rejected": rejected, "candidates": len(candidates), "examples": examples}


def read_conflict_summary(root: Path) -> Dict[str, int]:
    """cell_resolutions.resolution の件数分布（矛盾サマリ用、任意機能）。"""
    conn = semantics_store.connect(root)
    try:
        resolutions = semantics_store.fetch_cell_resolutions(conn)
    finally:
        conn.close()
    counts: Dict[str, int] = {}
    for row in resolutions.values():
        key = str(row.get("resolution") or "")
        counts[key] = counts.get(key, 0) + 1
    return counts


def confirm_mapping_proposal(root: Path, mapping_id: str, *, reviewer: str = "") -> Dict[str, Any]:
    """人間レビューによる承認。status='proposed'の行のみ対象（ガードはstore側）。"""
    conn = semantics_store.connect(root)
    try:
        mapping_row = next(
            (r for r in semantics_store.fetch_concept_mappings(conn) if r.get("mapping_id") == mapping_id), None
        )
        if mapping_row is None:
            return {"updated": False, "reason": "not_found", "mapping_id": mapping_id}
        decided_by = str(mapping_row.get("decided_by") or "")
        updated = semantics_store.update_concept_mapping_status(
            conn,
            mapping_id,
            new_status="confirmed",
            new_decided_by=f"{decided_by}+human_review",
            evidence_patch={"human_review": {"decision": "confirm", "reviewer": reviewer}},
        )
        if updated:
            semantics_store.write_csv_mirrors(root, conn)
        else:
            return {"updated": False, "reason": "not_proposed", "mapping_id": mapping_id}
        return {"updated": True, "mapping_id": mapping_id, "new_status": "confirmed"}
    finally:
        conn.close()


def reject_mapping_proposal(root: Path, mapping_id: str, *, reviewer: str = "", note: str = "") -> Dict[str, Any]:
    """人間レビューによる却下。status='proposed'の行のみ対象（ガードはstore側）。"""
    conn = semantics_store.connect(root)
    try:
        mapping_row = next(
            (r for r in semantics_store.fetch_concept_mappings(conn) if r.get("mapping_id") == mapping_id), None
        )
        if mapping_row is None:
            return {"updated": False, "reason": "not_found", "mapping_id": mapping_id}
        decided_by = str(mapping_row.get("decided_by") or "")
        updated = semantics_store.update_concept_mapping_status(
            conn,
            mapping_id,
            new_status="rejected",
            new_decided_by=f"{decided_by}+human_review",
            evidence_patch={"human_review": {"decision": "reject", "reviewer": reviewer, "note": note}},
        )
        if updated:
            semantics_store.write_csv_mirrors(root, conn)
        else:
            return {"updated": False, "reason": "not_proposed", "mapping_id": mapping_id}
        return {"updated": True, "mapping_id": mapping_id, "new_status": "rejected"}
    finally:
        conn.close()
