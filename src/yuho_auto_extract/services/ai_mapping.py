"""BuildBase P5: AIランナー＋マッピング基盤。

未マップ observed_items（xbrl系）を canonical_concepts に対応付ける判断を
AIに提案させ、concept_mappings へ status='proposed' として書き込む。

絶対制約（再確認）:
    - AI提案は必ず status='proposed', decided_by='ai:<model>' で書く。
      'confirmed' には絶対にしない。確定は照合エンジン（semantics_corroborate.py、
      既存P2実装）の責務のまま変更しない。
    - 既存の human/deterministic mapping を上書きしない。
      semantics_store.replace_concept_mappings(..., delete_first=False) の
      追記モードで書く（delete_first=True だと既存の人間判断を全て消してしまう）。
    - 抽出コード・corroboration.py・golden・semantics_backfill は変更しない。
    - runner: AiRunner は必須引数。デフォルトでClaudeCliRunner()を生成しない
      （本番コードパスで誤って実claudeを呼んでしまう事故を型レベルで防止する）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..ai_runner import AiCallResult, AiRunner, BudgetExceeded, ai_call_result_to_ai_calls_record
from ..io_utils import ensure_parent, read_yaml
from . import semantics_backfill, semantics_store

AI_EVIDENCE_DIR = Path("data") / "ai_evidence" / "mapping_cards"
INSTRUCTIONS_FILENAME = "AI_MAPPING_INSTRUCTIONS.md"
MANIFEST_FILENAME = "manifest.json"

ACTION_MAP = "map"
ACTION_NEW_CONCEPT = "new_concept"
ACTION_DIFFERENT_SCOPE = "different_scope"
ACTION_IGNORE = "ignore"
VALID_ACTIONS = {ACTION_MAP, ACTION_NEW_CONCEPT, ACTION_DIFFERENT_SCOPE, ACTION_IGNORE}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# config/ai.yml 読み込み
# ---------------------------------------------------------------------------


def load_ai_config(root: Path) -> Dict[str, Any]:
    path = root / "config" / "ai.yml"
    if not path.exists():
        raise FileNotFoundError(f"config/ai.yml is missing: {path}")
    return read_yaml(path)


# ---------------------------------------------------------------------------
# 未マップ observed_items 抽出
# ---------------------------------------------------------------------------


def select_unmapped_xbrl_observed_items(
    conn: sqlite3.Connection,
    taxonomy_kind: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """未判断のxbrl観測項目を取得する。

    item_kind='xbrl' かつ、concept_mappings に status='confirmed' の行も
    decided_by='ai:*' の行も持たない observed_items が対象。
    後者を除外することで、AIが一度判断した項目を再処理しない（冪等・コスト保護）。
    決定的proposed（decided_by='deterministic:*'）はAI未判断のため対象に残す。
    """
    sql = (
        "select oi.* from observed_items oi "
        "where oi.item_kind = 'xbrl' "
        "and not exists ("
        "  select 1 from concept_mappings cm "
        "  where cm.observed_item_id = oi.observed_item_id "
        "  and (cm.status = 'confirmed' or cm.decided_by like 'ai:%')"
        ") "
    )
    params: List[Any] = []
    if taxonomy_kind:
        sql += "and oi.taxonomy_kind = ? "
        params.append(taxonomy_kind)
    sql += "order by oi.taxonomy_kind, oi.observed_item_id"
    if limit is not None:
        sql += " limit ?"
        params.append(int(limit))
    return [dict(row) for row in conn.execute(sql, params)]


# ---------------------------------------------------------------------------
# 他社の類似確定マッピング（ヒント）
# ---------------------------------------------------------------------------


def find_similar_confirmed_concept_hints(
    conn: sqlite3.Connection,
    element_id: str,
) -> List[Dict[str, Any]]:
    """element_id 一致で status='confirmed' の concept_id をヒントとして返す。

    (concept_id, matched_via='element_id', match_count) の形。
    """
    if not element_id:
        return []
    rows = conn.execute(
        """
        select distinct cm.concept_id, count(*) as n
        from observed_items oi2
        join concept_mappings cm on cm.observed_item_id = oi2.observed_item_id
        where oi2.element_id = ?
          and cm.status = 'confirmed'
          and cm.action = 'map'
        group by cm.concept_id
        order by n desc
        """,
        (element_id,),
    )
    return [{"concept_id": row["concept_id"], "matched_via": "element_id", "match_count": row["n"]} for row in rows]


# ---------------------------------------------------------------------------
# カード生成
# ---------------------------------------------------------------------------


def _sample_values(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item.get("sample_values_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_mapping_cards(
    conn: sqlite3.Connection,
    observed_items: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """observed_items から1枚=1observed_itemのカード辞書リストを生成する（純関数寄り）。"""
    cards: List[Dict[str, Any]] = []
    for item in observed_items:
        sample_values = _sample_values(item)
        hints = find_similar_confirmed_concept_hints(conn, str(item.get("element_id") or ""))
        cards.append(
            {
                "observed_item_id": item.get("observed_item_id"),
                "element_local_name": item.get("element_local_name"),
                "label_ja": item.get("label_ja"),
                "taxonomy_kind": item.get("taxonomy_kind"),
                "normalized_scope": item.get("normalized_scope"),
                "period_bucket": item.get("period_bucket"),
                "unit": item.get("unit"),
                "first_fiscal_year": item.get("first_fiscal_year"),
                "last_fiscal_year": item.get("last_fiscal_year"),
                "sample_value_display": sample_values.get("sample_value_display"),
                "sample_source_quote": sample_values.get("sample_source_quote"),
                "similar_confirmed_hints": hints,
            }
        )
    return cards


def _concept_summary(concept: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "concept_id": concept.get("concept_id"),
        "concept_name_ja": concept.get("concept_name_ja"),
        "category": concept.get("category"),
        "data_scope": concept.get("data_scope"),
        "target_unit": concept.get("target_unit"),
    }
    if concept.get("definition_ja"):
        out["definition_ja"] = concept.get("definition_ja")
    if concept.get("calculation_formula"):
        out["calculation_formula"] = concept.get("calculation_formula")
    return out


def _instructions_text() -> str:
    return """## BuildBaseの追跡スコープ（最重要・判断の前提）

BuildBaseはゼネコン（総合建設会社）の**営業戦略分析**専用データベースです。
追跡対象は建設業の営業・財務KPIに限定されます:
- 受注高（用途別・官民・海外・特命/競争）、完成工事高、繰越高
- 建築/土木/不動産のセグメント別の受注・完工・売上・利益
- 建設収益・建設総利益・工事原価内訳（材料/労務/外注/経費）
- 技術者数・従業員数・平均年齢/勤続/給与
- 主要財務指標（売上高・営業利益・経常利益・当期純利益・総資産・純資産・
  自己資本・自己資本比率・ROE・現預金）
- 株価

**スコープ外の項目は new_concept にせず、必ず ignore を選ぶこと。**
「複数社で標準的に出現する」ことは new_concept の理由にならない。BuildBaseの
営業戦略分析の追跡対象でなければ ignore。スコープ外の代表例:
- 個別勘定科目の内訳（社債・借入金・自己株式・土地・建物・棚卸資産・引当金 等）
- キャッシュフロー計算書の個別項目、株主資本等変動計算書の内部移動項目
- 特別損益の細目、その他包括利益(OCI)の構成要素、役員報酬の細目、税効果の細目

## 行動の定義

- action="map": 観測項目の意味が既存概念のどれかと確実に一致する場合。
  concept_id に対応する既存concept_idを指定すること。
- action="ignore": 上記スコープ外、または既存概念に該当せず独立管理の価値が低い。
  **スコープ外項目は迷わず ignore**。concept_id は null。
- action="new_concept": **BuildBaseの追跡スコープ内**で、かつ既存概念のどれにも
  該当しない建設営業KPI/主要財務指標の場合のみ（例: 未追跡の用途別受注カテゴリ、
  未追跡のセグメント指標）。スコープ外の汎用財務項目には使わない。
  new_conceptフィールドに {concept_name_ja, category, definition_ja} を記述。
  迷ったら new_concept でなく ignore。concept_id は null。
- action="different_scope": 意味は既存概念に近いが、スコープ（連結/個別）・
  期間（当期/前期）・単位・タクソノミ差異（日本基準/IFRS）などが異なるため
  同一概念に統合すべきでない場合。concept_id に「最も近い基準となる既存
  concept_id」を必ず指定（省略・nullは不可＝バリデーションエラー）。
  例: {"action":"different_scope","concept_id":"operating_income_consolidated",
       "rationale":"IFRS個別の営業利益。基準と別スコープ"}

少しでも意味が別物であればignoreまたはdifferent_scopeとし、確実な一致のみmap。
値は作らず判断のみ。**rationaleは40字以内の日本語1行で簡潔に**（冗長禁止・コスト削減）。
"""


def _prompt_group(card: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in card.items()}


def build_mapping_prompt(
    chunk_index: int,
    cards: Sequence[Dict[str, Any]],
    concepts: Sequence[Dict[str, Any]],
) -> str:
    concept_summaries = [_concept_summary(c) for c in concepts]
    lines = [
        f"# BuildBase 観測項目マッピング判断 chunk_{chunk_index:03d}",
        "",
        "以下の既存概念（canonical_concepts）のいずれかに、各観測項目(observed_item)を",
        "対応付けてください。値は作らず、判断のみ行ってください。",
        "",
        "## 既存概念一覧",
        "```json",
        json.dumps(concept_summaries, ensure_ascii=False, sort_keys=True),
        "```",
        "",
        _instructions_text(),
        "",
        f"## 観測項目（本チャンク{len(cards)}件）",
        "```json",
        json.dumps([_prompt_group(card) for card in cards], ensure_ascii=False, sort_keys=True),
        "```",
        "",
        "## 出力形式",
        "厳密なJSON配列のみを出力してください。説明文・コードフェンスは付けないでください。",
        '各要素: {"observed_item_id":"...","action":"map|new_concept|different_scope|ignore",'
        '"concept_id":"...(または null)","new_concept":null,"rationale":"...","confidence":0.9}',
    ]
    return "\n".join(lines)


def write_mapping_card_chunks(
    root: Path,
    observed_items: Sequence[Dict[str, Any]],
    cards: Sequence[Dict[str, Any]],
    concepts: Sequence[Dict[str, Any]],
    chunk_size: int,
) -> List[Dict[str, Any]]:
    """カードをチャンクに分割してプロンプトファイルを書き出す。

    major_financial_evidence.py の _write_prompt_chunks パターンを踏襲。
    戻り値は各チャンクの {input_ref, path, observed_item_ids, prompt} のリスト。
    """
    out_dir = root / AI_EVIDENCE_DIR
    chunk_dir = out_dir / "prompt_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    ensure_parent(out_dir / INSTRUCTIONS_FILENAME)
    (out_dir / INSTRUCTIONS_FILENAME).write_text(_instructions_text(), encoding="utf-8")

    chunks: List[Dict[str, Any]] = []
    size = max(1, int(chunk_size or 12))
    for chunk_index, start in enumerate(range(0, len(cards), size), start=1):
        chunk_cards = list(cards[start : start + size])
        chunk_items = list(observed_items[start : start + size])
        prompt = build_mapping_prompt(chunk_index, chunk_cards, concepts)
        input_ref = f"chunk_{chunk_index:03d}"
        path = chunk_dir / f"{input_ref}.md"
        path.write_text(prompt, encoding="utf-8")
        chunks.append(
            {
                "input_ref": input_ref,
                "path": path,
                "observed_item_ids": [item.get("observed_item_id") for item in chunk_items],
                "prompt": prompt,
            }
        )

    manifest = {
        "generated_at_utc": _now_utc_iso(),
        "evidence_dir": str(AI_EVIDENCE_DIR),
        "observed_items": len(observed_items),
        "chunks": len(chunks),
        "chunk_size": size,
        "chunk_paths": [str(AI_EVIDENCE_DIR / "prompt_chunks" / c["input_ref"]) + ".md" for c in chunks],
    }
    (out_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return chunks


# ---------------------------------------------------------------------------
# AI判断のバリデーション
# ---------------------------------------------------------------------------


def validate_ai_decision(decision: Dict[str, Any]) -> None:
    """1件のAI判断辞書を検証する。不正なら ValueError を送出する。"""
    observed_item_id = str(decision.get("observed_item_id") or "").strip()
    if not observed_item_id:
        raise ValueError("observed_item_id is required")
    action = str(decision.get("action") or "").strip()
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid action: {action!r}")
    concept_id = decision.get("concept_id")
    rationale = str(decision.get("rationale") or "").strip()
    if not rationale:
        raise ValueError(f"rationale is required for observed_item_id={observed_item_id}")
    if action == ACTION_MAP:
        if not concept_id:
            raise ValueError(f"concept_id is required for action=map: {observed_item_id}")
    if action == ACTION_DIFFERENT_SCOPE:
        if not concept_id:
            raise ValueError(f"concept_id (reference concept) is required for action=different_scope: {observed_item_id}")
    if action == ACTION_NEW_CONCEPT:
        new_concept = decision.get("new_concept")
        if not isinstance(new_concept, dict) or not new_concept.get("concept_name_ja"):
            raise ValueError(f"new_concept dict with concept_name_ja is required for action=new_concept: {observed_item_id}")


def parse_ai_decisions(raw_parsed: Any) -> List[Dict[str, Any]]:
    """AiCallResult.parsed_result（list想定）を受け取り、各要素を検証して返す。

    不正な要素があれば ValueError を送出する（呼び出し側でstatus='parse_error'
    として扱う想定）。
    """
    if not isinstance(raw_parsed, list):
        raise ValueError("parsed AI result must be a JSON array")
    decisions: List[Dict[str, Any]] = []
    for item in raw_parsed:
        if not isinstance(item, dict):
            raise ValueError(f"each decision must be an object, got: {type(item)!r}")
        validate_ai_decision(item)
        decisions.append(item)
    return decisions


# ---------------------------------------------------------------------------
# AI提案 -> concept_mappings 行への変換
# ---------------------------------------------------------------------------


def mapping_row_from_ai_decision(decision: Dict[str, Any], model: str, call_id: str) -> Dict[str, Any]:
    """AI判断を concept_mappings への書き込み行に変換する。

    絶対に status='proposed' 固定。'confirmed' にはしない。
    """
    concept_id = decision.get("concept_id") or ""
    action = str(decision.get("action") or "")
    observed_item_id = str(decision.get("observed_item_id") or "")
    decided_by = f"ai:{model}"
    return {
        "mapping_id": semantics_backfill.mapping_id(observed_item_id, str(concept_id), action, decided_by),
        "observed_item_id": observed_item_id,
        "concept_id": concept_id,
        "action": action,
        "status": "proposed",  # 絶対に "confirmed" にしない
        "decided_by": decided_by,
        "confidence": decision.get("confidence"),
        "evidence": {
            "rationale": decision.get("rationale"),
            "new_concept": decision.get("new_concept"),
            "ai_call_id": call_id,
        },
    }


# ---------------------------------------------------------------------------
# budget guard
# ---------------------------------------------------------------------------


def _check_budget(conn: sqlite3.Connection, run_started_utc: str, max_calls: int) -> None:
    """今回のrun開始以降のai_calls件数がmax_calls以上なら停止する。

    呼び出し前にチェックすることで「1回だけ超過してしまう」事態を防ぐ
    （呼び出し後にカウントすると超過を許してしまう）。
    """
    count = conn.execute(
        "select count(*) from ai_calls where created_at_utc >= ?", (run_started_utc,)
    ).fetchone()[0]
    if count >= max_calls:
        raise BudgetExceeded(f"ai_calls this run reached {count} >= budget.max_calls_per_run={max_calls}")


# ---------------------------------------------------------------------------
# メインエントリ: run_ai_mapping_batch
# ---------------------------------------------------------------------------


def run_ai_mapping_batch(
    root: Path,
    runner: Optional[AiRunner],
    tier: str = "bulk",
    limit: int = 50,
    dry_run: bool = True,
    taxonomy_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """未マップobserved_itemsをAIにマッピング提案させ、proposedとして書き込む。

    dry_run=True の場合はカード生成のみでrunner.call()を一切呼ばない（コスト0）。
    dry_run=False の場合は runner が必須（Noneならエラー）。

    戻り値: {observed_items_targeted, chunks, ai_calls_made, proposals_written,
             parse_errors, dry_run}
    """
    if not dry_run and runner is None:
        raise ValueError("runner is required when dry_run=False")

    ai_config = load_ai_config(root)
    tiers_cfg = ai_config.get("tiers") or {}
    tier_cfg = tiers_cfg.get(tier) or {}
    model = tier_cfg.get("model")
    if not model:
        raise ValueError(f"config/ai.yml has no model configured for tier={tier!r}")
    chunk_size = int(tier_cfg.get("chunk_size") or ai_config.get("batch_size") or 12)
    timeout_seconds = int(ai_config.get("timeout_seconds") or 120)
    max_calls = int((ai_config.get("budget") or {}).get("max_calls_per_run") or 50)
    max_retries = int(ai_config.get("max_retries") or 0)

    run_started_utc = _now_utc_iso()

    conn = semantics_store.connect(root)
    try:
        observed_items = select_unmapped_xbrl_observed_items(conn, taxonomy_kind=taxonomy_kind, limit=limit)
        concepts = list(semantics_store.fetch_canonical_concepts(conn).values())
        cards = build_mapping_cards(conn, observed_items)
        chunks = write_mapping_card_chunks(root, observed_items, cards, concepts, chunk_size)

        result: Dict[str, Any] = {
            "observed_items_targeted": len(observed_items),
            "chunks": len(chunks),
            "ai_calls_made": 0,
            "proposals_written": 0,
            "parse_errors": 0,
            "dry_run": dry_run,
            "tier": tier,
            "model": model,
        }

        if dry_run:
            return result

        assert runner is not None  # for type checkers; validated above
        purpose = f"ai_mapping_{tier}"

        for chunk in chunks:
            # parse_error/status異常時は max_retries 回まで再試行する。
            # 各試行前にbudgetを確認し、再試行もai_callsに記録・カウントする
            # （リトライ暴走を防ぐ）。全試行失敗時のみ parse_errors を計上。
            decisions = None
            last_call_result: Optional[AiCallResult] = None
            attempts = 0
            while attempts <= max_retries:
                _check_budget(conn, run_started_utc, max_calls)
                call_result: AiCallResult = runner.call(
                    prompt=chunk["prompt"],
                    model=model,
                    purpose=purpose,
                    tier=tier,
                    input_ref=chunk["input_ref"],
                    timeout_seconds=timeout_seconds,
                )
                result["ai_calls_made"] += 1
                semantics_store.insert_ai_call(conn, ai_call_result_to_ai_calls_record(call_result))
                last_call_result = call_result
                if call_result.status == "ok" and call_result.parsed_result is not None:
                    try:
                        decisions = parse_ai_decisions(call_result.parsed_result)
                        break
                    except ValueError:
                        decisions = None
                attempts += 1

            if decisions is None or last_call_result is None:
                result["parse_errors"] += 1
                continue

            rows = [mapping_row_from_ai_decision(d, model, last_call_result.call_id) for d in decisions]
            if rows:
                # delete_first=False: 既存の human/deterministic confirmed mapping を
                # 上書きしない。追記モードで書く（これがP5の最重要注意点）。
                semantics_store.replace_concept_mappings(conn, rows, delete_first=False)
                result["proposals_written"] += len(rows)

        semantics_store.write_csv_mirrors(root, conn)
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ai-verify-mappings 用: 照合済み件数の集計のみ（confirmed書き込みはしない）
# ---------------------------------------------------------------------------


def verify_ai_proposals_against_corroboration(root: Path) -> Dict[str, Any]:
    """AI提案(status='proposed', decided_by like 'ai:%')のうち、既に
    corroborations/cell_resolutions側で確定条件を満たしているものを集計する。

    実際の concept_mappings 更新（confirmedへの昇格）は行わない
    （既存の semantics_corroborate.py の責務のまま変更しない。責務分離を守る）。
    """
    conn = semantics_store.connect(root)
    try:
        proposed_rows = [
            row
            for row in semantics_store.fetch_concept_mappings(conn)
            if row.get("status") == "proposed" and str(row.get("decided_by") or "").startswith("ai:")
        ]
        cell_resolutions = semantics_store.fetch_cell_resolutions(conn)
        auto_confirmed_concepts = {concept_id for (_, concept_id), row in cell_resolutions.items() if row.get("resolution") == "auto_confirmed"}
        likely_confirmable = [row for row in proposed_rows if row.get("concept_id") in auto_confirmed_concepts]
        return {
            "ai_proposed_total": len(proposed_rows),
            "likely_confirmable_via_corroboration": len(likely_confirmable),
        }
    finally:
        conn.close()
