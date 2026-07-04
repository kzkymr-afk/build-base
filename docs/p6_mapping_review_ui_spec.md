# P6 実装仕様書 — マッピングレビューUI

対象: `concept_mappings` の `status='proposed'` を非エンジニアが画面で承認/却下する。
実claude不使用。既存の `update_concept_mapping_status`（P5c実装済・二重ガード）を呼ぶだけで、AI呼び出しは一切発生しない。

対象データ実測（2026-07-04時点）:

| decided_by パターン | action | 件数 |
|---|---|---|
| `ai:*` | map | 37（うち19は既に`ai:*+corroboration`へ確定済み。proposed残りは18＝skip分） |
| `ai:*` | different_scope | 39 |
| `ai:*` | ignore | 1,013 |
| `ai:*` | new_concept | 0（27件は`ai:*+human_adopt`で確定済み） |
| `deterministic:xbrl_tag_candidates_match` | map | 221 |

**触ってはいけないもの**: `status='confirmed'` の既存行（human 1,327 / deterministic 751 / `ai:*+corroboration` 19 / `ai:*+human_adopt` 30）。UPDATE対象は必ず `status='proposed'` のみ。

---

## 1. 提案データの取得（DTO設計）

### 1.1 既存の戻り値構造（そのまま使う）

`src/yuho_auto_extract/services/semantics_store.py`:

- `fetch_concept_mappings(conn) -> List[Dict]`（721行目）: `concept_mappings`全件を`dict`のリストで返す。列は `mapping_id, observed_item_id, concept_id, action, status, decided_by, confidence, evidence_json, valid_from_year, valid_to_year, company_scope, superseded_by, created_at_utc, updated_at_utc`。
- `fetch_observed_items(conn) -> Dict[str, Dict]`（583行目）: `observed_item_id -> row` の辞書。列は `observed_item_id, item_kind, element_id, element_local_name, normalized_scope, period_bucket, taxonomy_kind, section_name, row_label, company_scope, label_ja, unit, first_fiscal_year, last_fiscal_year, sample_values_json, source`。
- `fetch_canonical_concepts(conn) -> Dict[str, Dict]`（645行目）: `concept_id -> row` の辞書。列は `concept_id, concept_name_ja, category, data_scope, target_unit, period_type, definition_ja, calculation_formula, status, merged_into_concept_id`。

`evidence_json` の実データ構造（decided_by別）:

- `ai:*` 提案（`ai_mapping.py:365 mapping_row_from_ai_decision`）: `{"rationale": "...", "new_concept": {...} or null, "ai_call_id": "..."}`
- `deterministic:xbrl_tag_candidates_match`（`semantics_backfill.py:531`）: `{"matched_via": "matched_field_ids"}`

`sample_values_json`（observed_items, xbrl系。`semantics_backfill.py:174`）: `{"sample_company_year_id", "sample_source_doc_id", "sample_context_id", "sample_value", "sample_value_display", "sample_source_quote", "value_count", "company_count", "year_count", "company_year_count", "matched_field_ids"}`。

### 1.2 新規: `services/mapping_review.py`（新設ファイル）

既存モジュールを変更せず、新規サービス層でjoinする。`corroboration_report.py`や`ai_mapping.py`と同じ「semantics_store越しにconn取得→dict組み立て→JSON安全化」の作法を踏襲する。

```python
"""BuildBase P6: マッピング提案レビュー用のDTO組み立て。

concept_mappings(status='proposed') に observed_items / canonical_concepts を
joinして1件=1レビュー対象のDTOを返す。DBへの書き込みは行わない（読み取り専用）。
更新は semantics_store.update_concept_mapping_status に一本化する
（このモジュールは新規update関数を作らない）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import semantics_store


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


def read_mapping_proposals(
    root: Path,
    *,
    action: str = "",
    decided_by_kind: str = "",
    min_confidence: Optional[float] = None,
    limit: int = 200,
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

    total = len(proposed)
    action_counts: Dict[str, int] = {}
    for row in proposed:
        key = str(row.get("action") or "")
        action_counts[key] = action_counts.get(key, 0) + 1

    dtos = [_proposal_dto(row, observed_items, concepts) for row in proposed[:limit]]
    return {"total": total, "action_counts": action_counts, "proposals": dtos}
```

**設計判断**: `fetch_concept_mappings`/`fetch_observed_items`/`fetch_canonical_concepts`は全件ロード方式（インメモリ辞書）。observed_items 3,169件・concept_mappings 2,299件規模なら全件ロードで十分高速（既存の`ai_mapping.py`や`mapping_promotion.py`も同じ全件ロード方式を使っている）。ページングはDTO化後にPython側でスライスする（既存`datasets.paginate`と同型でも良いが、まずはlimit直書きで十分）。

---

## 2. confirm/reject API（`update_concept_mapping_status` を使う）

### 2.1 実シグネチャ（`semantics_store.py:726-793`）

```python
def update_concept_mapping_status(
    conn: sqlite3.Connection,
    mapping_id: str,
    *,
    new_status: str,
    new_decided_by: str,
    evidence_patch: Optional[Dict[str, Any]] = None,
    new_action: Optional[str] = None,
    new_concept_id: Optional[str] = None,
    expected_current_status: str = "proposed",
) -> bool:
```

- **安全ガード**: SQLの`where mapping_id = ? and status = ?`（`expected_current_status`、既定`'proposed'`）に一致する行のみ更新。人間/deterministicの`confirmed`行はこの条件に一致しないため更新されない（`mapping_promotion.py`のdocstringが明記する「二重防御」の一つ）。
- 戻り値: 実際に更新したら`True`、対象なし/status不一致なら`False`（冪等判定に使える）。
- `evidence_patch`は既存`evidence_json`へのマージ（キー追加・上書き）。

実際の呼び出し例（`mapping_promotion.py:281-291`, confirm実装の実例）:

```python
updated = semantics_store.update_concept_mapping_status(
    conn,
    str(mapping_row["mapping_id"]),
    new_status="confirmed",
    new_decided_by=f"ai:{model}+corroboration",
    evidence_patch={"corroboration": {...}},
)
```

### 2.2 P6での呼び出し方針

新規 `services/mapping_review.py` に以下2関数を追加する（1.2のDTO関数と同じファイル）:

```python
def confirm_mapping_proposal(root: Path, mapping_id: str, *, reviewer: str = "") -> Dict[str, Any]:
    """人間レビューによる承認。status='proposed'の行のみ対象（ガードはstore側）。"""
    conn = semantics_store.connect(root)
    try:
        mapping_row = next(
            (r for r in semantics_store.fetch_concept_mappings(conn) if r.get("mapping_id") == mapping_id), None
        )
        if mapping_row is None:
            return {"updated": False, "reason": "not_found"}
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
        return {"updated": updated, "mapping_id": mapping_id, "new_status": "confirmed"}
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
            return {"updated": False, "reason": "not_found"}
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
        return {"updated": updated, "mapping_id": mapping_id, "new_status": "rejected"}
    finally:
        conn.close()
```

**`decided_by`の付し方**: 計画書の「decided_by に '+human_review' 等を付す」に従い、元の`decided_by`（`ai:sonnet-4...`や`deterministic:xbrl_tag_candidates_match`）を保持したまま`+human_review`をサフィックス追加する（`mapping_promotion.py`が`+corroboration`/`+human_adopt`を追加するのと同じ命名規則）。これにより「誰の提案を人間がどう判断したか」が`decided_by`列だけで追跡できる。

**`write_csv_mirrors`呼び出し**: 他の書き込み系関数（`backfill_semantics`, `promote_verified_map_proposals`）は更新後に必ず`semantics_store.write_csv_mirrors(root, conn)`を呼んでCSVミラーを同期している。P6のconfirm/rejectも同様に呼ぶ（Git追跡・目視確認用のCSVを最新に保つ）。

**却下時の`new_action`/`new_concept_id`は使わない**: 却下は「この提案は採用しない」であって「別の対応に付け替える」ではないため、`new_action`/`new_concept_id`引数は渡さない（`status='rejected'`にするだけ）。将来「却下時に別concept_idへ付け替えたい」要望が出たら`new_concept_id`を使えるが、今回のスコープ外。

---

## 3. `web_api/app.py` エンドポイント配線

### 3.1 既存の同期GETパターン（`_start_job`を使わない例）

```python
@app.get("/api/corroboration/summary")
def corroboration_summary() -> Dict[str, Any]:
    return corroboration_report.read_summary(PROJECT_ROOT)
```

```python
@app.get("/api/datasets/cell-detail")
def cell_detail(company_year_id: str, field_id: str) -> Dict[str, Any]:
    if not company_year_id or not field_id:
        raise HTTPException(status_code=400, detail="company_year_id and field_id are required")
    return datasets.read_cell_detail(PROJECT_ROOT, company_year_id=company_year_id, field_id=field_id)
```

### 3.2 既存の同期POST（reviews系。ValueErrorを400に変換する型）

```python
@app.post("/api/reviews/resolved")
def save_resolved_reviews(request: ReviewSaveRequest) -> Dict[str, Any]:
    try:
        return reviews.upsert_resolved_reviews(PROJECT_ROOT, request.reviews)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

### 3.3 P6追加分（`app.py`への差分）

import行に `mapping_review` を追加（16行目のimport文にモジュール追加）:

```python
from yuho_auto_extract.services import ai_prompt, algorithm_audit, automation, company_factbooks, corroboration_report, datasets, field_admin, golden, market, mapping_review, pipeline, reviews, rule_candidates
```

Pydanticモデル追加（既存`ReviewSaveRequest`等と同じ並びに追加）:

```python
class MappingReviewDecisionRequest(BaseModel):
    reviewer: str = ""
    note: str = ""
```

エンドポイント追加（`/api/reviews/rule-candidates`系の直後あたりに配置。既存クエリパラメータの書式`Query(...)`を踏襲）:

```python
@app.get("/api/mappings/proposals")
def mapping_proposals(
    action: str = "",
    decided_by_kind: str = "",
    min_confidence: Optional[float] = Query(default=None),
    limit: int = Query(200, ge=1, le=2000),
) -> Dict[str, Any]:
    return mapping_review.read_mapping_proposals(
        PROJECT_ROOT,
        action=action,
        decided_by_kind=decided_by_kind,
        min_confidence=min_confidence,
        limit=limit,
    )


@app.post("/api/mappings/{mapping_id}/confirm")
def confirm_mapping(mapping_id: str, request: MappingReviewDecisionRequest = Body(default=MappingReviewDecisionRequest())) -> Dict[str, Any]:
    result = mapping_review.confirm_mapping_proposal(PROJECT_ROOT, mapping_id, reviewer=request.reviewer)
    if not result.get("updated"):
        raise HTTPException(status_code=409, detail=f"mapping {mapping_id} is not in 'proposed' status or not found")
    return result


@app.post("/api/mappings/{mapping_id}/reject")
def reject_mapping(mapping_id: str, request: MappingReviewDecisionRequest = Body(default=MappingReviewDecisionRequest())) -> Dict[str, Any]:
    result = mapping_review.reject_mapping_proposal(PROJECT_ROOT, mapping_id, reviewer=request.reviewer, note=request.note)
    if not result.get("updated"):
        raise HTTPException(status_code=409, detail=f"mapping {mapping_id} is not in 'proposed' status or not found")
    return result
```

**409の意図**: 既にconfirmed/rejectedになった提案（画面を2つ開いていた、他人が先に処理した等）への二重操作をUIに伝える。`update_concept_mapping_status`が`False`を返すケース＝「対象がない or ガード不一致」を全部409にまとめる（`JobAlreadyRunning`を409にする既存`_start_job`と同じ発想）。

CORS/認証: 既存の`app.add_middleware(CORSMiddleware, ...)`（29-40行目）をそのまま使う。新規オリジン追加・認証追加は不要（ローカルWebアプリのため既存踏襲でスコープ外）。

### 3.4 任意: cell_resolutionsサマリ

`semantics_store.fetch_cell_resolutions(conn)`（385行目）は`(company_year_id, concept_id) -> row`の辞書を返す。`row["resolution"]`が`'conflicted'`/`'needs_reconciliation'`等（P2実データ分布: auto_confirmed 1,182 / needs_reconciliation 958 / conflicted 60 / needs_review 3,411 / single_source 4,674 / no_value 28）。

サマリ専用に新規エンドポイントを作らず、`mapping_review.py`に集計関数を追加して`/api/mappings/proposals`のレスポンスに`conflict_summary`キーとして同梱するのが最小差分（別エンドポイントにしてもよいが、任意スコープなので実装コストを下げる）:

```python
def read_conflict_summary(root: Path) -> Dict[str, int]:
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
```

---

## 4. `main.tsx` パネル/タブ配線

### 4.1 tabs配列（730-741行目）

```tsx
const tabs = [
  ['run', '実行'],
  ['results', '結果'],
  ['fields', '項目整理'],
  ['stocks', '株価'],
  ['factbooks', 'ファクトブック'],
  ['charts', 'グラフ'],
  ['audit', '根拠'],
  ['review', 'レビュー'],
  ['ai', 'AI分析'],
  ['report', 'レポート']
] as const;
```

**新タブの追加位置**: `'review'`（既存の値レビュー）の直後に`'mapping_review'`（マッピングレビュー）を挿入する。既存の`'review'`タブは「セル単位の抽出値レビュー」、新タブは「観測項目→概念のマッピング提案レビュー」で役割が異なるため独立タブにする（既存`ReviewPanel`への追記ではなく新規パネル。ファイル`main.tsx`は該当パネル追加のみで分割リファクタしない、という制約とも整合）。

```tsx
const tabs = [
  ['run', '実行'],
  ['results', '結果'],
  ['fields', '項目整理'],
  ['stocks', '株価'],
  ['factbooks', 'ファクトブック'],
  ['charts', 'グラフ'],
  ['audit', '根拠'],
  ['review', 'レビュー'],
  ['mapping_review', 'マッピングレビュー'],
  ['ai', 'AI分析'],
  ['report', 'レポート']
] as const;
```

### 4.2 `App()`内のレンダー分岐（994-996行目付近に追記）

```tsx
{tab === 'review' && <ReviewPanel initialTarget={reviewTarget} job={job} onJob={setJob} onError={setError} refreshToken={dataRefreshToken} />}
{tab === 'mapping_review' && <MappingReviewPanel onError={setError} refreshToken={dataRefreshToken} />}
{tab === 'ai' && <AiPanel onError={setError} />}
```

`job`/`onJob`は不要（このパネルはジョブを起動しない。同期APIのみ）。既存の`onError`（`App()`の`setError`）とタブ間共有の`refreshToken`（他パネルの更新完了通知）だけ受け取る、最小props構成にする。

### 4.3 `api<T>()`ヘルパー（844-854行目、既存のまま流用）

```tsx
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}
```

409応答時は`response.ok`がfalseなので`throw new Error(text)`される。呼び出し側の`catch`でエラーメッセージ表示すればよい（既存の全パネルと同じエラーハンドリング作法）。

### 4.4 型定義の追加（既存の型群の近くに追記。`interface CellDetail`等が定義されている箇所を探して追加）

```tsx
interface MappingProposal {
  mapping_id: string;
  action: string;
  status: string;
  decided_by: string;
  decided_by_kind: string;
  confidence: number | null;
  rationale: string;
  new_concept_proposal: { concept_name_ja?: string; category?: string; definition_ja?: string } | null;
  observed_item: {
    observed_item_id: string;
    item_kind: string;
    element_id: string;
    element_local_name: string;
    label_ja: string;
    normalized_scope: string;
    unit: string;
    taxonomy_kind: string;
    section_name: string;
    sample_values: Record<string, unknown>;
  };
  concept: { concept_id: string; concept_name_ja: string; category: string; data_scope: string; target_unit: string } | null;
}

interface MappingProposalsResult {
  total: number;
  action_counts: Record<string, number>;
  proposals: MappingProposal[];
}
```

### 4.5 新規パネル本体: `MappingReviewPanel`

構造は既存`XbrlDiscoveredMetricsPanel`（サマリカード。1446-1502行目）＋既存`ReviewPanel`の1件選択・承認/却下パターン（save/deleteReviewの作法）を組み合わせる。**カード内容**は「観測項目（element_id/label_ja/scope/unit/サンプル値）／AIまたは決定的の判断根拠（rationale）／マッピング先concept（concept_name_ja）を並置し、承認・却下ボタンを置く」構成:

```tsx
function MappingReviewPanel({ onError, refreshToken }: { onError: (message: string) => void; refreshToken: number }) {
  const [actionFilter, setActionFilter] = React.useState('');
  const [kindFilter, setKindFilter] = React.useState('');
  const [data, setData] = React.useState<MappingProposalsResult | null>(null);
  const [busyId, setBusyId] = React.useState('');
  const [message, setMessage] = React.useState('');

  const load = React.useCallback(() => {
    const params = new URLSearchParams();
    if (actionFilter) params.set('action', actionFilter);
    if (kindFilter) params.set('decided_by_kind', kindFilter);
    api<MappingProposalsResult>(`/api/mappings/proposals?${params}`)
      .then(setData)
      .catch((err) => onError(String(err)));
  }, [actionFilter, kindFilter, onError]);

  React.useEffect(() => { load(); }, [load, refreshToken]);

  async function decide(mappingId: string, decision: 'confirm' | 'reject') {
    setBusyId(mappingId);
    setMessage('');
    try {
      await api(`/api/mappings/${encodeURIComponent(mappingId)}/${decision}`, {
        method: 'POST',
        body: JSON.stringify({ reviewer: 'web_ui' })
      });
      setMessage(decision === 'confirm' ? '承認しました。' : '却下しました。');
      load();
    } catch (err) {
      onError(String(err));
    } finally {
      setBusyId('');
    }
  }

  const counts = data?.action_counts || {};

  return (
    <section className="stack">
      <div className="panel automation-panel">
        <div className="panel-head">
          <div>
            <h2>マッピング提案レビュー</h2>
            <p className="muted">status=proposed のマッピング提案のみを表示します。承認済み・却下済みは変更されません。</p>
          </div>
        </div>
        <div className="automation-grid">
          <div className="metric"><small>提案合計</small><strong>{data?.total ?? '-'}</strong></div>
          <div className="metric"><small>map</small><strong>{counts.map ?? 0}</strong></div>
          <div className="metric"><small>different_scope</small><strong>{counts.different_scope ?? 0}</strong></div>
          <div className="metric"><small>ignore</small><strong>{counts.ignore ?? 0}</strong></div>
          <div className="metric"><small>new_concept</small><strong>{counts.new_concept ?? 0}</strong></div>
        </div>
        <div className="toolbar">
          <select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
            <option value="">action: すべて</option>
            <option value="map">map</option>
            <option value="different_scope">different_scope</option>
            <option value="ignore">ignore</option>
            <option value="new_concept">new_concept</option>
          </select>
          <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
            <option value="">判断主体: すべて</option>
            <option value="ai">AI提案</option>
            <option value="deterministic">決定的一致</option>
          </select>
        </div>
      </div>
      {message && <p className="hint">{message}</p>}
      <div className="stack">
        {(data?.proposals || []).map((p) => (
          <div className="panel" key={p.mapping_id}>
            <div className="panel-head">
              <div>
                <h3>{p.observed_item.label_ja || p.observed_item.element_local_name || p.observed_item.observed_item_id}</h3>
                <p className="muted">
                  {p.observed_item.element_id} / scope={p.observed_item.normalized_scope || '-'} / unit={p.observed_item.unit || '-'} / {p.observed_item.taxonomy_kind}
                </p>
              </div>
              <span className={`badge ${p.decided_by_kind === 'ai' ? 'pending' : 'succeeded'}`}>
                {p.decided_by_kind === 'ai' ? 'AI提案' : '決定的一致'}{p.confidence != null ? `(${p.confidence})` : ''}
              </span>
            </div>
            <div className="grid">
              <div>
                <small>action</small>
                <strong>{p.action}</strong>
              </div>
              <div>
                <small>マッピング先概念</small>
                <strong>{p.concept?.concept_name_ja || p.new_concept_proposal?.concept_name_ja || '(なし/新概念)'}</strong>
              </div>
            </div>
            <p className="hint">根拠: {p.rationale || '(記録なし)'}</p>
            <div className="toolbar">
              <button onClick={() => decide(p.mapping_id, 'confirm')} disabled={busyId === p.mapping_id}>承認</button>
              <button className="ghost" onClick={() => decide(p.mapping_id, 'reject')} disabled={busyId === p.mapping_id}>却下</button>
            </div>
          </div>
        ))}
        {data && data.proposals.length === 0 && <Empty message="該当する提案はありません。" />}
      </div>
    </section>
  );
}
```

既存流儀との対応:
- 承認ボタンの構造は`XbrlDiscoveredMetricsPanel.saveMapping`（`api<...>(path, {method:'POST', body: JSON.stringify(...)})` → 成功時`setMessage`＋`refresh()`、失敗時`setError`）と同型。
- サマリカード（`automation-grid`/`metric`クラス）は`CorroborationSummaryPanel`と同じCSSクラスをそのまま再利用（新規CSS追加不要）。
- 却下時に`window.confirm`を挟むかは任意。既存`markSelectedCompanyFieldNotApplicable`（2992行目）は不可逆操作に`confirm`を使っているが、`reject`はいつでも別提案が来るわけではなく再現性のある操作ではないため、既存`ReviewPanel.deleteReview`（3035行目）に倣い`confirm`を入れる方が安全（UIコードでは省略したが、実装時に追加推奨）。

---

## 5. テスト方針（既存パターン踏襲）

`tests/test_web_services.py`のセットアップ作法（`tempfile.TemporaryDirectory()` → `Path(tmp)`をroot代わりに使う）を踏襲し、新規`tests/test_mapping_review.py`を作成する。

参考にする既存テスト:
- `tests/test_mapping_promotion.py:262 test_human_confirmed_row_untouched_by_update_function` — `semantics_store.replace_concept_mappings(conn, [...], delete_first=False)`で1行セットアップし、`update_concept_mapping_status`実行後にstatus/decided_byを確認する型。P6の`confirm_mapping_proposal`/`reject_mapping_proposal`のテストもこの型で書く：
  - proposed行を1件セットアップ→`confirm_mapping_proposal`実行→`status='confirmed'`かつ`decided_by`に`+human_review`サフィックスが付くことを確認。
  - confirmed行（human/deterministic）を1件セットアップ→`confirm_mapping_proposal`/`reject_mapping_proposal`実行→`updated=False`を確認し、行が変化しないことをSQLで再検証（このテストが「人間confirmed非変更」の直接的保証になる）。
- `tests/test_web_services.py:222 test_review_upsert_writes_resolved_without_touching_queue` — 「対象外のファイル・データが変化しないこと」を明示的に確認する型（`before = path.read_text()` → 実行 → `assertEqual`）。P6では`data/final/*`や`confirmed`行のCSVミラー内容が不変であることの確認に応用できる。

DTO関数（`read_mapping_proposals`）のテストは、`semantics_store.replace_observed_items`/`upsert_canonical_concepts`/`replace_concept_mappings`で最小データをセットアップし、action/decided_by_kind/min_confidenceの絞り込みが正しく効くことを確認する（`tests/test_semantics_backfill.py`のセットアップ方式と同型）。

APIレイヤ（`app.py`の3エンドポイント）はFastAPIの`TestClient`を使うテストが既存に無い場合、サービス層（`mapping_review.py`）のユニットテストで実質カバーし、E2E確認は`./yuho web-start`後の手動疎通で十分（既存`corroboration_report`API等も同様にサービス層テストのみで、`app.py`自体の専用テストは無い）。

---

## 実装チェックリスト

1. 新規 `src/yuho_auto_extract/services/mapping_review.py`（§1.2, §2.2）
2. `src/yuho_auto_extract/web_api/app.py` に import追加＋3エンドポイント追加（§3.3）
3. `web/src/main.tsx`: tabs配列に`mapping_review`追加（§4.1）、`App()`にレンダー分岐追加（§4.2）、型定義追加（§4.4）、`MappingReviewPanel`関数追加（§4.5）
4. 新規 `tests/test_mapping_review.py`（§5）
5. `cd web && npm run build` でビルド確認、`. .venv/bin/activate && python -m pytest tests/ -q` で既存回帰ゼロ確認
6. 手動確認: `./yuho web-stop && ./yuho web-start` 後、`http://localhost:8765` でマッピングレビュータブを開き、1件confirm→`data/marts/semantics/concept_mappings.csv`のstatus列が更新されること、既存confirmed行（例: `cmap_0015e377af177014`）が変化しないことを目視確認
