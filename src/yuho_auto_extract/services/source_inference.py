"""BuildBase S1a: 出典逆引きエンジン + 一括dry-runレポート。

背景（Opus監査済み確定計画より）:
  受注3項目（building_orders_total / completed_building / backlog_building_next）は
  XBRLに存在しない（実証済み、逆引き0件）。唯一の源泉は本文の「受注工事高、完成工事高
  及び次期繰越工事高」表（業界標準様式: 行=土木/建築/計 × 列=前期繰越/当期受注/計/
  当期完成/次期繰越）。raw_table_markdown は空のことが多く、raw_text（非構造化全文）
  から読む必要がある。

中核アルゴリズム（恒等式フィッティングを一次手段にする）:
  行ラベル直後に連続する数値トークン列から5個組 (a, b, c, d, e) を走査し、
    a + b ≈ c   （前期繰越 + 当期受注 ＝ 計）
    c - d ≈ e   （計 − 当期完成 ＝ 次期繰越）
  を同時に満たす組を探す。2本の恒等式を同時に満たす5個組が偶然成立する確率は
  極小なので、列見出しの表記揺れやレイアウト差に依存せず
  (a=前期繰越, b=当期受注, d=当期完成, e=次期繰越) を自己検証つきで確定できる。
  4個組（計列なし: a + b − d ≈ e）へのフォールバックも用意する。
  ラベル近傍窓のスコアリング（非標準表向けのフォールバック）は最後の手段とする。

このモジュールは読み取り専用（S1aの絶対制約）:
  - final/review/config/semantics.db/edinet.db への書き込みは一切行わない。
  - edinet.db は mapping_promotion.open_edinet_db_readonly（PRAGMA query_only）で開く。
  - 出力は data/reports/source_inference_dry_run.json / .md のみ。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import local_table_extractor as lte
from ..io_utils import ensure_parent, is_blankish, read_table
from ..validator import check_backlog_equation_single
from . import semantics_store
from .mapping_promotion import open_edinet_db_readonly

REPORTS_DIR = Path("data") / "reports"
DRY_RUN_JSON_FILENAME = "source_inference_dry_run.json"
DRY_RUN_MD_FILENAME = "source_inference_dry_run.md"

FINAL_MASTER_LONG_RELATIVE_PATH = Path("data") / "final" / "final_master_long.csv"

DEFAULT_FIELD_IDS = ["building_orders_total", "completed_building", "backlog_building_next"]

# fieldごとの5個組内オフセット（0=前期繰越, 1=当期受注, 2=計, 3=当期完成, 4=次期繰越）
FIELD_ROLE_BY_ID = {
    "backlog_building_prev": 0,
    "building_orders_total": 1,
    "building_orders_calc_total": 2,
    "completed_building": 3,
    "backlog_building_next": 4,
}
ROLE_LABELS = {0: "前期繰越", 1: "当期受注", 2: "計", 3: "当期完成", 4: "次期繰越"}

# 行ラベル語彙（建築/土木/計）。既存 SEGMENT_ORDER_LABELS のキー（xx事業/xx部門）に加え、
# 表の実データで頻出する素の「建築工事」「土木工事」「建築」「土木」「計」「合計」も含める。
# 大林組等、組版の都合で行ラベルの字間に空白/改行が挟まる（「建　築」「土　木」
# 「合　計」）表記があるため、各文字の間に \s* を許容する。
ROW_LABEL_PATTERNS: Dict[str, str] = {
    "building": r"建\s*築(?:\s*(?:事業|工事|部門))?",
    "civil": r"土\s*木(?:\s*(?:事業|工事|部門))?",
    "total": r"(?:合\s*)?計",
}

_ALL_ROW_LABEL_RE = re.compile(
    "|".join(f"(?P<{key}>{pattern})" for key, pattern in ROW_LABEL_PATTERNS.items())
)

# 対象とする candidate_blocks のセクション（orders_backlog系・review_*系に絞る）
TARGET_SECTION_PREFIXES = ("orders_backlog", "review_")

_NEGATIVE_PREFIXES = ("△", "▲", "-", "－", "−")
_UNIT_SCALE = {
    "円": 1.0,
    "千円": 1_000.0,
    "百万円": 1_000_000.0,
    "億円": 100_000_000.0,
}

_TOLERANCE_RULE = {"tolerance_abs": 2.0, "tolerance_pct": 0.001}


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------


@dataclass
class NumberToken:
    """raw_text 中で検出した数値トークン。"""

    value: float
    start: int
    end: int
    raw_text: str
    is_parenthetical: bool = False
    digit_count: int = 0


@dataclass
class FittedTuple:
    """恒等式フィッティングで確定した5個組（または4個組）。"""

    values: Tuple[Optional[float], ...]  # (prev_backlog, orders, calc_total, completed, next_backlog)
    positions: Tuple[int, ...]
    row_label: str
    row_label_key: str
    has_total_column: bool
    equation_ok: bool
    anchor_hit: bool = False
    period: str = "unknown"  # "current" / "previous" / "unknown"


# ---------------------------------------------------------------------------
# 1. トークン化
# ---------------------------------------------------------------------------


def tokenize_numbers(raw_text: str, min_digits_for_isolated: int = 3) -> List[NumberToken]:
    """raw_text から数値トークン列を位置付きで抽出する。

    - カンマ区切り（12,345）を1つの数値として解釈する。
    - 全角数字・全角記号はNFKC正規化してから解釈する。
    - △・▲・先頭マイナス・全角マイナスは負号として扱う。
    - 丸括弧で囲まれた数値 `(12,345)` は「参考値（前期末訂正値等）」として
      is_parenthetical=True を立てる。恒等式フィッティングでは既定で除外する。
    - 3桁未満（=100未満）の裸の数値は、桁区切りカンマも小数点も伴わない場合、
      箇条書き番号等のノイズである可能性が高いため既定で除外する
      （MATSUI_2018: 正解データに紛れ込んだ`4`はこのパターン）。
      ただし小数（比率等）はこのフィルタの対象にしない。
    """
    if not raw_text:
        return []
    text = unicodedata.normalize("NFKC", raw_text)
    tokens: List[NumberToken] = []

    # 括弧付き数値（全角/半角の丸括弧）を先に検出し、対応する半角括弧を含む区間を記録する。
    paren_spans: List[Tuple[int, int]] = []
    for match in re.finditer(r"\(([^()]{1,20})\)", text):
        paren_spans.append((match.start(), match.end()))

    def _inside_paren(pos: int) -> bool:
        return any(start <= pos < end for start, end in paren_spans)

    number_re = re.compile(r"[△▲\-−]?\d[\d,]*(?:\.\d+)?")
    for match in number_re.finditer(text):
        raw = match.group(0)
        digits_only = re.sub(r"[^\d]", "", raw)
        if not digits_only:
            continue
        negative = raw[0] in _NEGATIVE_PREFIXES
        numeric_part = raw.lstrip("".join(_NEGATIVE_PREFIXES))
        has_comma = "," in numeric_part
        has_decimal = "." in numeric_part
        digit_count = len(re.sub(r"[^\d]", "", numeric_part.split(".")[0]))
        if not has_comma and not has_decimal and digit_count < min_digits_for_isolated:
            # 孤立した短い整数（箇条書き番号・年号の一部等）は除外する。
            continue
        try:
            value = float(numeric_part.replace(",", ""))
        except ValueError:
            continue
        if negative:
            value = -value
        tokens.append(
            NumberToken(
                value=value,
                start=match.start(),
                end=match.end(),
                raw_text=raw,
                is_parenthetical=_inside_paren(match.start()),
                digit_count=digit_count,
            )
        )
    return tokens


# ---------------------------------------------------------------------------
# 2. 行ラベル位置の検出
# ---------------------------------------------------------------------------


def find_row_label_positions(raw_text: str) -> List[Tuple[int, int, str, str]]:
    """raw_text 中の行ラベル（建築/土木/計）の出現位置を列挙する。

    戻り値: [(start, end, label_key, matched_text), ...]（出現順）
    label_key は "building" / "civil" / "total"。
    """
    if not raw_text:
        return []
    text = unicodedata.normalize("NFKC", raw_text)
    out: List[Tuple[int, int, str, str]] = []
    for match in _ALL_ROW_LABEL_RE.finditer(text):
        key = match.lastgroup
        if key is None:
            continue
        out.append((match.start(), match.end(), key, match.group(0)))
    return out


# ---------------------------------------------------------------------------
# 3. 恒等式フィッティング（一次手段）
# ---------------------------------------------------------------------------

_CURRENT_PERIOD_RE = re.compile(r"当事業年度|当連結会計年度")
_PREVIOUS_PERIOD_RE = re.compile(r"前事業年度|前連結会計年度")
# 単体の「前期」「当期」（事業年度接尾辞を伴わない短縮形）。列見出し
# （前期繰越高/当期受注高等）との衝突があるため、_COLUMN_HEADER_RE に
# 一致する区間は後段で除外してから使う。
_CURRENT_PERIOD_SHORT_RE = re.compile(r"当期")
_PREVIOUS_PERIOD_SHORT_RE = re.compile(r"前期")

# 表の列見出しに現れる「前期○○」「当期○○」（○○=繰越/受注/完成/売上/施工）。
# 見出し語のあいだに空白・改行・単位表記が挟まる組版（例:「当期\n受注\n工事高」）
# にも対応するため、「前期/当期」の直後に0〜6文字の空白・改行を許容してから
# 対象語が続くかを見る。マッチした「前期/当期」トークンは期区分マーカーの
# 候補から除外する（列見出しは年度区分ではなく列の意味を表すラベルであり、
# これを年度マーカーとして扱うと当年度・前年度の行がどちらも current 等の
# 同一区分に誤判定され、恒等式フィットが複数に割れて low_confidence に
# 落ちてしまう）。
_COLUMN_HEADER_RE = re.compile(r"(?:前期|当期)[\s\n]{0,6}(?:繰越|受注|完成|売上|施工)")

# 「第113期」等の期番号マーカー。事業年度表記が無い（前期/当期しか
# 出てこない）業界標準表で、当該書類の年度を一意に特定する一次情報源。
_NTH_PERIOD_RE = re.compile(r"第\s*(\d+)\s*期")


def _column_header_spans(text: str) -> List[Tuple[int, int]]:
    return [(match.start(), match.end()) for match in _COLUMN_HEADER_RE.finditer(text)]


def _inside_spans(pos: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def _find_period_markers(text: str) -> List[Tuple[int, str]]:
    """テキスト中の期区分マーカー出現位置を列挙する。

    優先順位:
      1. 「第N期」形式。2つ以上検出できた場合はこれを唯一の情報源として使う
         （数値が最大のものを current、それ以外を previous とする）。これは
         多くの業界標準「受注高、売上高及び繰越高」表で当事業年度/前事業年度
         という表記が使われず「前期」「当期」という短縮語のみが列見出しとして
         現れるケース（SHIMIZU/TAISEI/TAKENAKA/TOA/PENTA/NISHIMATSU/OBAYASHI等）
         に対応するための一次マーカー。
      2. 「当事業年度」「前事業年度」（連結含む）。
      3. 単体の「前期」「当期」。ただし列見出し（前期繰越/当期受注等）に
         含まれる出現は _COLUMN_HEADER_RE で検出して除外する。
    第N期マーカーが2つ以上取れた場合は、行単位の位置合わせを崩さないよう
    2.・3. のマーカーとマージせず単独で採用する（同じテキスト中で「前期/当期」
    が列見出し以外の注記等に出現し、誤って別の区分を割り込ませることを防ぐ）。
    """
    nth_matches = [(match.start(), int(match.group(1))) for match in _NTH_PERIOD_RE.finditer(text)]
    if len(nth_matches) >= 2:
        max_value = max(value for _pos, value in nth_matches)
        markers = [
            (pos, "current" if value == max_value else "previous") for pos, value in nth_matches
        ]
        markers.sort(key=lambda item: item[0])
        return markers

    header_spans = _column_header_spans(text)
    markers: List[Tuple[int, str]] = []
    for match in _CURRENT_PERIOD_RE.finditer(text):
        markers.append((match.start(), "current"))
    for match in _PREVIOUS_PERIOD_RE.finditer(text):
        markers.append((match.start(), "previous"))
    for match in _CURRENT_PERIOD_SHORT_RE.finditer(text):
        if _inside_spans(match.start(), header_spans):
            continue
        markers.append((match.start(), "current"))
    for match in _PREVIOUS_PERIOD_SHORT_RE.finditer(text):
        if _inside_spans(match.start(), header_spans):
            continue
        markers.append((match.start(), "previous"))
    markers.sort(key=lambda item: item[0])
    return markers


def _period_at(markers: Sequence[Tuple[int, str]], position: int) -> str:
    period = "unknown"
    for marker_pos, marker_period in markers:
        if marker_pos <= position:
            period = marker_period
        else:
            break
    return period


def _within_tolerance(diff: float, base: float) -> bool:
    return abs(diff) <= max(1.0, abs(base) * 0.005)


def _numbers_after(
    tokens: Sequence[NumberToken],
    after_pos: int,
    max_count: int = 8,
    exclude_parenthetical: bool = True,
    before_pos: Optional[int] = None,
) -> List[NumberToken]:
    out: List[NumberToken] = []
    for token in tokens:
        if token.start < after_pos:
            continue
        if before_pos is not None and token.start >= before_pos:
            break
        if exclude_parenthetical and token.is_parenthetical:
            continue
        out.append(token)
        if len(out) >= max_count:
            break
    return out


def _try_five_tuple(window: Sequence[NumberToken]) -> Optional[FittedTuple]:
    if len(window) < 5:
        return None
    a, b, c, d, e = (t.value for t in window[:5])
    positions = tuple(t.start for t in window[:5])
    sum_ok = _within_tolerance(a + b - c, c)
    diff_ok = _within_tolerance(c - d - e, e)
    if sum_ok and diff_ok:
        return FittedTuple(
            values=(a, b, c, d, e),
            positions=positions,
            row_label="",
            row_label_key="",
            has_total_column=True,
            equation_ok=True,
        )
    return None


def _try_four_tuple(window: Sequence[NumberToken]) -> Optional[FittedTuple]:
    """計列がない表: a(前期繰越) + b(当期受注) - d(当期完成) ≈ e(次期繰越)。"""
    if len(window) < 4:
        return None
    a, b, d, e = (t.value for t in window[:4])
    positions = tuple(t.start for t in window[:4])
    status, diff = check_backlog_equation_single(a, b, d, e, _TOLERANCE_RULE)
    if status == "pass":
        return FittedTuple(
            values=(a, b, None, d, e),
            positions=positions,
            row_label="",
            row_label_key="",
            has_total_column=False,
            equation_ok=True,
        )
    return None


def fit_backlog_tuples(
    tokens: Sequence[NumberToken],
    row_label_positions: Sequence[Tuple[int, int, str, str]],
    max_window: int = 8,
    text: Optional[str] = None,
) -> List[FittedTuple]:
    """行ラベル直後の数値トークン列から5個組/4個組を全列挙する。

    各行ラベル出現位置ごとに、その直後の数値トークンをスライド窓で走査し、
    恒等式を満たす組を候補として列挙する。5個組（計列あり）を優先し、
    見つからない場合のみ4個組（計列なし）にフォールバックする。
    `text` を渡すと「当事業年度/前事業年度」マーカーを検出し、各フィット組の
    `period` フィールドに反映する（複数年度が並記される表で当年度行を
    優先的に選別するために使う）。
    """
    period_markers = _find_period_markers(text) if text else []
    results: List[FittedTuple] = []
    sorted_labels = sorted(row_label_positions, key=lambda item: item[0])
    for idx, (label_start, label_end, label_key, matched_text) in enumerate(sorted_labels):
        # 次の行ラベルの開始位置までに窓を限定する（隣接行の数値列を誤って
        # 取り込まないため）。最後のラベルの場合は制限しない。
        next_label_start = sorted_labels[idx + 1][0] if idx + 1 < len(sorted_labels) else None
        following = _numbers_after(tokens, label_end, max_count=max_window + 5, before_pos=next_label_start)
        if len(following) < 4:
            continue
        period = _period_at(period_markers, label_start) if period_markers else "unknown"
        found_five = False
        for offset in range(0, max(1, len(following) - 4)):
            window = following[offset : offset + 5]
            fitted = _try_five_tuple(window)
            if fitted is not None:
                fitted.row_label = matched_text
                fitted.row_label_key = label_key
                fitted.period = period
                results.append(fitted)
                found_five = True
        if found_five:
            continue
        for offset in range(0, max(1, len(following) - 3)):
            window = following[offset : offset + 4]
            fitted = _try_four_tuple(window)
            if fitted is not None:
                fitted.row_label = matched_text
                fitted.row_label_key = label_key
                fitted.period = period
                results.append(fitted)
    return results


def extract_table_segment(raw_text: str) -> str:
    """raw_text から「受注工事高、完成工事高及び次期繰越工事高」表の区間だけを取り出す。

    既存 local_table_extractor._orders_backlog_segment を再利用する
    （行ラベル語彙とテーブル境界検出のロジックを重複させない）。
    見出しが見つからない場合は raw_text 全体を返す（非標準表向けフォールバック）。
    """
    normalized = lte._normalize_text(raw_text)
    segment = lte._orders_backlog_segment(normalized)
    return segment if segment else normalized


def _check_total_row_consistency(fitted_tuples: Sequence[FittedTuple]) -> Dict[int, bool]:
    """計行 ≈ 建築行 + 土木行 の整合を確認し、各フィット組にフラグを付ける（インデックス→整合可否）。

    業界標準様式では表の各期区分ごとに「土木工事 → 建築工事 → 計」の順で3行が
    連続して現れる。この出現順の制約を使い、building行の直前に現れる civil行と
    直後に現れる total行を同一表インスタンスとみなして合算が一致するかを確認する。
    civil/total行が見つからない、または3行が期待順で隣接していない場合は
    判定不能として True（中立）を返す。
    """
    ok: Dict[int, bool] = {idx: True for idx in range(len(fitted_tuples))}
    ordered = sorted(range(len(fitted_tuples)), key=lambda idx: fitted_tuples[idx].positions[0])

    for pos in range(len(ordered)):
        b_idx = ordered[pos]
        b_fit = fitted_tuples[b_idx]
        if b_fit.row_label_key != "building":
            continue
        if pos == 0 or pos + 1 >= len(ordered):
            continue
        c_idx = ordered[pos - 1]
        t_idx = ordered[pos + 1]
        c_fit = fitted_tuples[c_idx]
        t_fit = fitted_tuples[t_idx]
        if c_fit.row_label_key != "civil" or t_fit.row_label_key != "total":
            continue
        consistent = True
        has_any_check = False
        for role_idx in range(5):
            bv = b_fit.values[role_idx]
            cv = c_fit.values[role_idx]
            tv = t_fit.values[role_idx]
            if bv is None or cv is None or tv is None:
                continue
            has_any_check = True
            if not _within_tolerance(bv + cv - tv, tv):
                consistent = False
                break
        if not has_any_check:
            continue
        ok[b_idx] = consistent
        ok[c_idx] = consistent
        ok[t_idx] = consistent
    return ok


# ---------------------------------------------------------------------------
# 4. 単一セルの出典逆引き
# ---------------------------------------------------------------------------


def _unit_scale(unit: str) -> float:
    return _UNIT_SCALE.get(str(unit or "").strip(), 1.0)


def _fetch_candidate_blocks(conn, company_year_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "select row_json from candidate_blocks where company_year_id = ?",
        (company_year_id,),
    ).fetchall()
    blocks: List[Dict[str, Any]] = []
    for row in rows:
        try:
            block = json.loads(row["row_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        section_name = str(block.get("section_name") or "")
        if section_name.startswith(TARGET_SECTION_PREFIXES):
            blocks.append(block)
    return blocks


def _value_matches(candidate: Optional[float], target: float, tolerance_abs: float = 1.0) -> bool:
    if candidate is None:
        return False
    return abs(candidate - target) <= max(tolerance_abs, abs(target) * 0.001)


def infer_source_for_cell(
    root: Path,
    company_year_id: str,
    field_id: str,
    value: float,
    unit: str = "百万円",
    conn: Any = None,
) -> Dict[str, Any]:
    """指定セルの値を candidate_blocks 内で恒等式フィッティングして逆引きする。

    戻り値: {
        "company_year_id", "field_id", "value", "matched": bool,
        "candidates": [
            {candidate_block_id, section_name, fitted_tuple, role, confidence, snippet}, ...
        ],
    }
    曖昧（複数候補が拮抗）な場合は複数候補を保持し自動選択しない。
    """
    owns_conn = conn is None
    if conn is None:
        conn = open_edinet_db_readonly(root)
    try:
        blocks = _fetch_candidate_blocks(conn, company_year_id)
    finally:
        if owns_conn:
            conn.close()

    role_offset = FIELD_ROLE_BY_ID.get(field_id)
    candidates: List[Dict[str, Any]] = []
    for block in blocks:
        raw_text = str(block.get("raw_text") or "")
        if not raw_text:
            continue
        segment = extract_table_segment(raw_text)
        tokens = tokenize_numbers(segment)
        label_positions = find_row_label_positions(segment)
        fitted_tuples = fit_backlog_tuples(tokens, label_positions, text=segment)
        consistency = _check_total_row_consistency(fitted_tuples)
        for idx, fitted in enumerate(fitted_tuples):
            if role_offset is None:
                continue
            candidate_value = fitted.values[role_offset] if role_offset < len(fitted.values) else None
            if not _value_matches(candidate_value, value):
                continue
            confidence = 0.95 if fitted.has_total_column else 0.8
            if not consistency.get(idx, True):
                confidence -= 0.2
            snippet_start = max(0, fitted.positions[0] - 40)
            snippet_end = min(len(segment), fitted.positions[-1] + 40)
            candidates.append(
                {
                    "candidate_block_id": block.get("candidate_block_id"),
                    "section_name": block.get("section_name"),
                    "fitted_tuple": fitted.values,
                    "row_label": fitted.row_label,
                    "row_label_key": fitted.row_label_key,
                    "role": ROLE_LABELS.get(role_offset, str(role_offset)),
                    "has_total_column": fitted.has_total_column,
                    "row_equation_consistent": consistency.get(idx, True),
                    "period": fitted.period,
                    "confidence": round(max(0.0, min(1.0, confidence)), 3),
                    "snippet": segment[snippet_start:snippet_end],
                }
            )

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return {
        "company_year_id": company_year_id,
        "field_id": field_id,
        "value": value,
        "matched": bool(candidates),
        "candidates": candidates,
    }


# ---------------------------------------------------------------------------
# 5. 会社レイアウトの学習（既知セルからの逆引き集計）
# ---------------------------------------------------------------------------


def learn_company_layouts(root: Path, field_ids: Sequence[str] = DEFAULT_FIELD_IDS) -> Dict[str, Any]:
    """既知セル（final_master_longの非欠損）でフィットし、会社別レイアウトを集計する。

    戻り値: {
        "by_company_year": {company_year_id: {field_id: infer_source_for_cell結果}},
        "reproduction": {"total": N, "matched_high_confidence": M, "rate": M/N},
    }
    """
    final_rows = read_table(root / FINAL_MASTER_LONG_RELATIVE_PATH)
    known_cells: List[Dict[str, Any]] = []
    for row in final_rows:
        field_id = str(row.get("field_id") or "")
        if field_id not in field_ids:
            continue
        raw_value = row.get("value_normalized") or row.get("value")
        if raw_value in (None, ""):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        known_cells.append(
            {
                "company_year_id": str(row.get("company_year_id") or ""),
                "field_id": field_id,
                "value": value,
                "unit": str(row.get("unit_normalized") or row.get("unit_raw") or "百万円"),
            }
        )

    conn = open_edinet_db_readonly(root)
    by_company_year: Dict[str, Dict[str, Any]] = defaultdict(dict)
    matched_high_confidence = 0
    try:
        for cell in known_cells:
            result = infer_source_for_cell(
                root,
                cell["company_year_id"],
                cell["field_id"],
                cell["value"],
                unit=cell["unit"],
                conn=conn,
            )
            by_company_year[cell["company_year_id"]][cell["field_id"]] = result
            if result["candidates"] and result["candidates"][0]["confidence"] >= 0.9:
                matched_high_confidence += 1
    finally:
        conn.close()

    total = len(known_cells)
    return {
        "by_company_year": dict(by_company_year),
        "reproduction": {
            "total": total,
            "matched_high_confidence": matched_high_confidence,
            "rate": (matched_high_confidence / total) if total else 0.0,
        },
    }


# ---------------------------------------------------------------------------
# 6. 欠落セルへの回復見込み推定
# ---------------------------------------------------------------------------


def _company_year_ids_with_candidate_blocks(conn) -> List[str]:
    rows = conn.execute("select distinct company_year_id from candidate_blocks").fetchall()
    return [str(row["company_year_id"]) for row in rows if row["company_year_id"]]


def _fit_all_for_company_year(conn, company_year_id: str) -> List[Tuple[Dict[str, Any], "FittedTuple", bool]]:
    """company_year_idの全candidate_blocksを恒等式フィッティングし、
    (block, fitted, is_consistent) のリストを返す（estimate_recovery/
    build_promotion_planで共有する内部ヘルパー）。
    """
    blocks = _fetch_candidate_blocks(conn, company_year_id)
    all_fitted: List[Tuple[Dict[str, Any], FittedTuple, bool]] = []
    for block in blocks:
        raw_text = str(block.get("raw_text") or "")
        if not raw_text:
            continue
        segment = extract_table_segment(raw_text)
        tokens = tokenize_numbers(segment)
        label_positions = find_row_label_positions(segment)
        fitted_tuples = fit_backlog_tuples(tokens, label_positions, text=segment)
        consistency = _check_total_row_consistency(fitted_tuples)
        for idx, fitted in enumerate(fitted_tuples):
            all_fitted.append((block, fitted, consistency.get(idx, True)))
    return all_fitted


def _building_role_values_for_field(
    all_fitted: Sequence[Tuple[Dict[str, Any], "FittedTuple", bool]],
    field_id: str,
) -> List[Dict[str, Any]]:
    """all_fittedからbuilding行・当該年度のみを対象に、field_idのroleに対応する
    値の候補一覧を返す（estimate_recovery/build_promotion_planで共有）。
    """
    role_offset = FIELD_ROLE_BY_ID.get(field_id)
    if role_offset is None:
        return []
    has_current_period = any(fitted.period == "current" for _b, fitted, _c in all_fitted)
    found_values: List[Dict[str, Any]] = []
    for block, fitted, is_consistent in all_fitted:
        if fitted.row_label_key != "building":
            continue
        if has_current_period and fitted.period == "previous":
            continue
        candidate_value = fitted.values[role_offset] if role_offset < len(fitted.values) else None
        if candidate_value is None:
            continue
        found_values.append(
            {
                "value": candidate_value,
                "candidate_block_id": block.get("candidate_block_id"),
                "section_name": block.get("section_name"),
                "has_total_column": fitted.has_total_column,
                "row_equation_consistent": is_consistent,
                "period": fitted.period,
            }
        )
    return found_values


def _classify_found_values(found_values: Sequence[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """found_valuesを (status, details) に分類する（"high_confidence"/"low_confidence"/"not_found"）。

    5個組（計列あり・恒等式整合）由来の値を最優先の証拠として扱う。4個組フォールバックは
    計列を伴わない非標準表のみが対象のはずだが、実データでは同一表内の別セクションから
    桁数の合わない断片（年号の一部等）を拾うことがあるため、5個組由来のユニーク値が
    存在する場合はそれを採用し、4個組由来の値は無視する。
    """
    if not found_values:
        return "not_found", []
    high_conf = [v for v in found_values if v["has_total_column"] and v["row_equation_consistent"]]
    if high_conf:
        distinct_high_conf_values = {round(v["value"], 1) for v in high_conf}
        if len(distinct_high_conf_values) == 1:
            return "high_confidence", high_conf
        return "low_confidence", found_values
    return "low_confidence", found_values


def estimate_recovery(root: Path, field_ids: Sequence[str] = DEFAULT_FIELD_IDS) -> Dict[str, Any]:
    """学習済みレイアウト＋恒等式フィットを欠落セル側に適用し、回復見込みを分類する。

    会社×年度×field を以下に分類する:
      - already_filled: final_master_long に既に値がある
      - high_confidence: 恒等式フィットがユニークに1本成立（表から5個組を発見）
      - low_confidence: 複数候補が拮抗、または4個組フォールバックのみ成立
      - not_found: 候補ブロックはあるが恒等式フィットが1本も成立しない
    """
    final_rows = read_table(root / FINAL_MASTER_LONG_RELATIVE_PATH)
    filled_keys = set()
    for row in final_rows:
        field_id = str(row.get("field_id") or "")
        if field_id not in field_ids:
            continue
        raw_value = row.get("value_normalized") or row.get("value")
        if raw_value in (None, ""):
            continue
        filled_keys.add((str(row.get("company_year_id") or ""), field_id))

    conn = open_edinet_db_readonly(root)
    try:
        company_year_ids = _company_year_ids_with_candidate_blocks(conn)
        classification: Dict[str, Dict[str, str]] = defaultdict(dict)
        details: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(dict)

        for company_year_id in company_year_ids:
            blocks = _fetch_candidate_blocks(conn, company_year_id)
            # 会社年度単位で一度だけフィットし、各fieldのroleに割り当てる。
            all_fitted: List[Tuple[Dict[str, Any], FittedTuple, bool]] = []
            for block in blocks:
                raw_text = str(block.get("raw_text") or "")
                if not raw_text:
                    continue
                segment = extract_table_segment(raw_text)
                tokens = tokenize_numbers(segment)
                label_positions = find_row_label_positions(segment)
                fitted_tuples = fit_backlog_tuples(tokens, label_positions, text=segment)
                consistency = _check_total_row_consistency(fitted_tuples)
                for idx, fitted in enumerate(fitted_tuples):
                    all_fitted.append((block, fitted, consistency.get(idx, True)))

            # 表に複数年度（当期・前期）が並記される場合は、書類が報告対象とする
            # 当該年度の値のみを欠落セル推定に使う（前期繰越の値を誤って
            # 当期の値として埋めてしまうことを防ぐ）。期区分マーカーが
            # 検出できない場合は period="unknown" のままとし、無条件に候補にする。
            has_current_period = any(fitted.period == "current" for _b, fitted, _c in all_fitted)

            for field_id in field_ids:
                key = (company_year_id, field_id)
                if key in filled_keys:
                    classification[company_year_id][field_id] = "already_filled"
                    continue
                role_offset = FIELD_ROLE_BY_ID.get(field_id)
                if role_offset is None:
                    classification[company_year_id][field_id] = "not_found"
                    continue
                found_values: List[Dict[str, Any]] = []
                for block, fitted, is_consistent in all_fitted:
                    if fitted.row_label_key != "building":
                        continue
                    if has_current_period and fitted.period == "previous":
                        continue
                    candidate_value = fitted.values[role_offset] if role_offset < len(fitted.values) else None
                    if candidate_value is None:
                        continue
                    found_values.append(
                        {
                            "value": candidate_value,
                            "candidate_block_id": block.get("candidate_block_id"),
                            "section_name": block.get("section_name"),
                            "has_total_column": fitted.has_total_column,
                            "row_equation_consistent": is_consistent,
                            "period": fitted.period,
                        }
                    )
                if not found_values:
                    classification[company_year_id][field_id] = "not_found"
                    continue
                # 5個組（計列あり・恒等式整合）由来の値を最優先の証拠として扱う。
                # 4個組フォールバックは計列を伴わない非標準表のみが対象のはずだが、
                # 実データでは同一表内の別セクションから桁数の合わない断片
                # （年号の一部等）を拾うことがあるため、5個組由来のユニーク値が
                # 存在する場合はそれを採用し、4個組由来の値は無視する。
                high_conf = [v for v in found_values if v["has_total_column"] and v["row_equation_consistent"]]
                if high_conf:
                    distinct_high_conf_values = {round(v["value"], 1) for v in high_conf}
                    if len(distinct_high_conf_values) == 1:
                        classification[company_year_id][field_id] = "high_confidence"
                        details[company_year_id][field_id] = high_conf
                    else:
                        classification[company_year_id][field_id] = "low_confidence"
                        details[company_year_id][field_id] = found_values
                else:
                    classification[company_year_id][field_id] = "low_confidence"
                    details[company_year_id][field_id] = found_values
    finally:
        conn.close()

    return {"classification": dict(classification), "details": dict(details)}


# ---------------------------------------------------------------------------
# 7. dry-runレポート生成
# ---------------------------------------------------------------------------


def build_dry_run_report(root: Path, field_ids: Sequence[str] = DEFAULT_FIELD_IDS) -> Dict[str, Any]:
    """learn_company_layouts + estimate_recovery を実行し、レポートを生成する。

    出力は data/reports/source_inference_dry_run.json / .md のみ（読み取り専用フェーズ）。
    """
    layout_result = learn_company_layouts(root, field_ids)
    recovery_result = estimate_recovery(root, field_ids)

    classification = recovery_result["classification"]
    summary_counts: Dict[str, int] = defaultdict(int)
    by_field_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for company_year_id, fields in classification.items():
        for field_id, status in fields.items():
            summary_counts[status] += 1
            by_field_counts[field_id][status] += 1

    by_company: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for company_year_id, fields in classification.items():
        company_id = company_year_id.rsplit("_", 1)[0] if "_" in company_year_id else company_year_id
        for field_id, status in fields.items():
            by_company[company_id][status] += 1

    report = {
        "reproduction": layout_result["reproduction"],
        "summary": dict(summary_counts),
        "by_field": {field_id: dict(counts) for field_id, counts in by_field_counts.items()},
        "by_company": {company_id: dict(counts) for company_id, counts in by_company.items()},
        "field_ids": list(field_ids),
        # セル別分類（company_year_id -> field_id -> status）。
        # coverage 等の下流がレポートを読むだけで recoverable 判定できるように
        # 永続化する（毎リクエストの estimate_recovery 再計算は重く、
        # /api/coverage/core が27秒かかる実測問題への対策）。
        "classification": {cy: dict(fields) for cy, fields in classification.items()},
    }

    reports_dir = root / REPORTS_DIR
    ensure_parent(reports_dir / "placeholder")
    json_path = reports_dir / DRY_RUN_JSON_FILENAME
    md_path = reports_dir / DRY_RUN_MD_FILENAME

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_dry_run_markdown(report), encoding="utf-8")

    return {
        "report": report,
        "report_json_path": json_path,
        "report_md_path": md_path,
    }


def _dry_run_markdown(report: Dict[str, Any]) -> str:
    lines = ["# source inference dry-run report", ""]
    repro = report.get("reproduction", {})
    lines.append("## 43セル再現率（既知セルの逆引き）")
    lines.append(f"- total: {repro.get('total', 0)}")
    lines.append(f"- matched_high_confidence: {repro.get('matched_high_confidence', 0)}")
    lines.append(f"- rate: {repro.get('rate', 0.0):.3f}")
    lines.append("")
    lines.append("## 欠落セル回復見込み（サマリ）")
    for status, count in sorted((report.get("summary") or {}).items()):
        lines.append(f"- {status}: {count}")
    lines.append("")
    lines.append("## field別内訳")
    for field_id, counts in sorted((report.get("by_field") or {}).items()):
        lines.append(f"### {field_id}")
        for status, count in sorted(counts.items()):
            lines.append(f"- {status}: {count}")
        lines.append("")
    lines.append("## 会社別内訳")
    for company_id, counts in sorted((report.get("by_company") or {}).items()):
        lines.append(f"- {company_id}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# S1b: 学習パターン適用経路（会社スコープ・検算ゲート）
# ---------------------------------------------------------------------------
#
# 絶対制約（確定計画S1b節より）:
#   1. 書き込み経路は reviews.upsert_resolved_reviews のみ。
#   2. 既存の値があるセルには一切書かない（空白セルの充填のみ）。
#      MATSUI_2018/TODA_2016等の誤値上書きもしない — レポートに出すのみ。
#   3. 自動promote条件: high_confidence（恒等式ユニーク成立）かつ、当該会社で
#      複数年度の恒等式が成立していること。単年度のみはcandidate止まり。
#   4. promoteする行は reviewer='source_inference' を必ず設定する
#      （golden.freeze_goldenがgated originに分類するための必須フック）。
#   5. dry-run既定。本適用は apply_promotion_plan(..., dry_run=False) 明示時のみ。

SUSPECT_VALIDATION_STATUSES = {"fail"}


def _pattern_id(company_id: str, field_id: str, section_name: str, row_label_key: str) -> str:
    raw = "|".join([company_id or "", field_id or "", section_name or "", row_label_key or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _company_id_from_company_year(company_year_id: str) -> str:
    if "_" not in company_year_id:
        return company_year_id
    return company_year_id.rsplit("_", 1)[0]


def build_promotion_plan(root: Path, field_ids: Sequence[str] = DEFAULT_FIELD_IDS) -> Dict[str, Any]:
    """estimate_recoveryのhigh_confidenceセルから、会社ごとに複数年度成立を確認し、
    promote対象・candidate止まり・疑わしい既存値を分類したプランを返す。

    戻り値: {
        "promote": [{"company_year_id", "field_id", "value", "unit", "evidence": {...}}, ...],
        "candidate_single_year": [同上（promoteしない理由=単年度のみ）],
        "suspect_existing_values": [
            {"company_year_id", "field_id", "existing_value", "recovered_value",
             "validation_status", "evidence": {...}}, ...
        ],
    }

    「既存の値があるセル」（already_filled）には一切書き込まない（絶対制約2）。
    ここでの suspect_existing_values は検出のみ・上書きしない。
    """
    final_rows = read_table(root / FINAL_MASTER_LONG_RELATIVE_PATH)
    existing_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in final_rows:
        field_id = str(row.get("field_id") or "")
        if field_id not in field_ids:
            continue
        company_year_id = str(row.get("company_year_id") or "")
        if not company_year_id:
            continue
        existing_by_key[(company_year_id, field_id)] = row

    conn = open_edinet_db_readonly(root)
    try:
        company_year_ids = _company_year_ids_with_candidate_blocks(conn)
        # まず各company_year x field を high_confidence/low_confidence/not_found/
        # already_filled(既存値との整合状況込み)に分類する。
        per_cell: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for company_year_id in company_year_ids:
            all_fitted = _fit_all_for_company_year(conn, company_year_id)
            for field_id in field_ids:
                found_values = _building_role_values_for_field(all_fitted, field_id)
                status, details = _classify_found_values(found_values)
                per_cell[(company_year_id, field_id)] = {"status": status, "details": details}
    finally:
        conn.close()

    # 会社×fieldごとに、恒等式が成立(high_confidence)した年度数を数える
    # （絶対制約3: 単年度のみの会社はcandidate止まり）。
    high_conf_years_by_company_field: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for (company_year_id, field_id), info in per_cell.items():
        if info["status"] == "high_confidence":
            company_id = _company_id_from_company_year(company_year_id)
            high_conf_years_by_company_field[(company_id, field_id)].append(company_year_id)

    promote: List[Dict[str, Any]] = []
    candidate_single_year: List[Dict[str, Any]] = []
    suspect_existing_values: List[Dict[str, Any]] = []

    for (company_year_id, field_id), info in sorted(per_cell.items()):
        if info["status"] != "high_confidence":
            continue
        existing_row = existing_by_key.get((company_year_id, field_id))
        details = info["details"]
        recovered_value = details[0]["value"] if details else None
        company_id = _company_id_from_company_year(company_year_id)
        multi_year_ok = len(high_conf_years_by_company_field.get((company_id, field_id), [])) >= 2

        if existing_row is not None:
            # 絶対制約2: 既存値があるセルには一切書かない。ただし推定値と既存値が
            # 乖離し、かつ既存値がvalidation失敗（疑わしい）場合はレポートに出す。
            existing_value = existing_row.get("value_normalized") or existing_row.get("value")
            if is_blankish(existing_value):
                continue
            try:
                existing_value_f = float(existing_value)
            except (TypeError, ValueError):
                continue
            validation_status = str(existing_row.get("validation_status") or "")
            if validation_status in SUSPECT_VALIDATION_STATUSES and recovered_value is not None:
                if not _within_tolerance(existing_value_f - recovered_value, recovered_value):
                    suspect_existing_values.append(
                        {
                            "company_year_id": company_year_id,
                            "field_id": field_id,
                            "existing_value": existing_value_f,
                            "recovered_value": recovered_value,
                            "validation_status": validation_status,
                            "multi_year_confirmed": multi_year_ok,
                            "evidence": _evidence_from_details(details),
                        }
                    )
            continue

        if recovered_value is None:
            continue
        entry = {
            "company_year_id": company_year_id,
            "field_id": field_id,
            "value": recovered_value,
            "unit": "百万円",
            "evidence": _evidence_from_details(details),
        }
        if multi_year_ok:
            promote.append(entry)
        else:
            candidate_single_year.append(entry)

    return {
        "promote": promote,
        "candidate_single_year": candidate_single_year,
        "suspect_existing_values": suspect_existing_values,
        "field_ids": list(field_ids),
    }


def _evidence_from_details(details: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not details:
        return {}
    top = details[0]
    return {
        "candidate_block_id": top.get("candidate_block_id"),
        "section_name": top.get("section_name"),
        "row_label_key": "building",
        "has_total_column": top.get("has_total_column"),
        "row_equation_consistent": top.get("row_equation_consistent"),
        "period": top.get("period"),
        "value": top.get("value"),
        "n_corroborating_blocks": len(details),
    }


def _format_reviewer_note(field_id: str, evidence: Dict[str, Any]) -> str:
    parts = [
        f"source_inference: field={field_id}",
        f"candidate_block_id={evidence.get('candidate_block_id')}",
        f"section={evidence.get('section_name')}",
        f"tuple5(role={evidence.get('row_label_key')})",
        f"has_total_column={evidence.get('has_total_column')}",
        f"equation_consistent={evidence.get('row_equation_consistent')}",
        f"period={evidence.get('period')}",
        f"n_corroborating_blocks={evidence.get('n_corroborating_blocks')}",
    ]
    return " | ".join(parts)


def apply_promotion_plan(root: Path, plan: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
    """build_promotion_planの結果を適用する。

    dry_run=True（既定）の場合は何も書き込まず、適用予定件数のみ返す。
    dry_run=False の場合:
      - plan["promote"] の各行を reviews.upsert_resolved_reviews に
        review_decision='correct', corrected_value=値, reviewer='source_inference',
        reviewer_note=出典 で一括書込みする（絶対制約1: 書き込み経路はここのみ）。
      - 既存レビュー行があるセル（review_queue.csvに無いセルも含め、既にreview_resolved.csv
        に確定済みの行があるセル）はスキップする（上書き禁止）。
      - learned_label_patterns に promoted として記録する。
      - 書込み後、pipeline.apply_review 相当（export-final --reviewed
        data/review/review_resolved.csv）を呼び final に反映する。
    """
    from . import cells as cells_service  # 遅延import: _synthetic_review_rowパターンを再利用
    from . import reviews as reviews_service

    to_promote = list(plan.get("promote") or [])
    if dry_run:
        return {
            "dry_run": True,
            "planned": len(to_promote),
            "skipped_existing_review": 0,
            "applied": 0,
        }

    resolved_path = root / "data" / "review" / "review_resolved.csv"
    existing_resolved = read_table(resolved_path) if resolved_path.exists() else []
    existing_review_keys = {
        (str(row.get("company_year_id") or ""), str(row.get("field_id") or ""))
        for row in existing_resolved
    }

    rows_to_write: List[Dict[str, Any]] = []
    skipped_existing_review = 0
    now = _now_utc_iso()
    patterns_to_record: List[Dict[str, Any]] = []

    for entry in to_promote:
        key = (str(entry["company_year_id"]), str(entry["field_id"]))
        if key in existing_review_keys:
            # 上書き禁止（絶対制約2の一環: 既にレビュー確定済みのセルには書かない）。
            skipped_existing_review += 1
            continue
        evidence = entry.get("evidence") or {}
        base = cells_service._synthetic_review_row(root, key[0], key[1])
        base.update(
            {
                "review_decision": "correct",
                "corrected_value": entry["value"],
                "reviewer_note": _format_reviewer_note(key[1], evidence),
                "reviewer": "source_inference",
                "reviewed_at": now,
                "applied_status": "",
                "applied_value": "",
                "applied_at": "",
            }
        )
        rows_to_write.append(base)

        company_id = _company_id_from_company_year(key[0])
        patterns_to_record.append(
            {
                "pattern_id": _pattern_id(company_id, key[1], str(evidence.get("section_name") or ""), "building"),
                "company_id": company_id,
                "field_id": key[1],
                "section_name": evidence.get("section_name"),
                "row_label_key": "building",
                "layout": "tuple5" if evidence.get("has_total_column") else "tuple4",
                "evidence": evidence,
                "status": "promoted",
                "created_at_utc": now,
            }
        )

    write_result: Dict[str, Any] = {"changed": 0, "total": len(existing_resolved)}
    if rows_to_write:
        write_result = reviews_service.upsert_resolved_reviews(root, rows_to_write)

    if patterns_to_record:
        conn = semantics_store.connect(root)
        try:
            semantics_store.upsert_learned_label_patterns(conn, patterns_to_record)
        finally:
            conn.close()

    _write_candidate_patterns(root, plan)
    _write_suspect_existing_values_report(root, plan)

    apply_result: Dict[str, Any] = {"ran": False}
    if rows_to_write:
        from . import pipeline as pipeline_service  # 遅延import: 循環import回避

        exit_code = pipeline_service.apply_review(root, reviewed="data/review/review_resolved.csv")
        apply_result = {"ran": True, "exit_code": exit_code}

    return {
        "dry_run": False,
        "planned": len(to_promote),
        "skipped_existing_review": skipped_existing_review,
        "applied": len(rows_to_write),
        "write_result": write_result,
        "apply_review": apply_result,
    }


def _write_candidate_patterns(root: Path, plan: Dict[str, Any]) -> None:
    """candidate_single_year（単年度のみ・promoteしない）をlearned_label_patternsに
    status='candidate'として記録する（レポート・将来の複数年度成立確認用）。
    """
    candidates = plan.get("candidate_single_year") or []
    if not candidates:
        return
    now = _now_utc_iso()
    patterns_to_record: List[Dict[str, Any]] = []
    for entry in candidates:
        evidence = entry.get("evidence") or {}
        company_id = _company_id_from_company_year(str(entry["company_year_id"]))
        field_id = str(entry["field_id"])
        patterns_to_record.append(
            {
                "pattern_id": _pattern_id(company_id, field_id, str(evidence.get("section_name") or ""), "building"),
                "company_id": company_id,
                "field_id": field_id,
                "section_name": evidence.get("section_name"),
                "row_label_key": "building",
                "layout": "tuple5" if evidence.get("has_total_column") else "tuple4",
                "evidence": evidence,
                "status": "candidate",
                "created_at_utc": now,
            }
        )
    conn = semantics_store.connect(root)
    try:
        semantics_store.upsert_learned_label_patterns(conn, patterns_to_record)
    finally:
        conn.close()


def _write_suspect_existing_values_report(root: Path, plan: Dict[str, Any]) -> Optional[Path]:
    """疑わしい既存値レポートを data/reports/suspect_existing_values.md に書く（上書きはしない）。"""
    suspects = plan.get("suspect_existing_values") or []
    reports_dir = root / REPORTS_DIR
    ensure_parent(reports_dir / "placeholder")
    path = reports_dir / "suspect_existing_values.md"
    lines = ["# 疑わしい既存値レポート（source_inference）", "", "既存の値は一切上書きしていません。人手でのレビューを推奨します。", ""]
    if not suspects:
        lines.append("該当なし。")
    for item in suspects:
        lines.append(
            f"- {item['company_year_id']} / {item['field_id']}: "
            f"existing={item['existing_value']} recovered={item['recovered_value']} "
            f"validation_status={item['validation_status']} "
            f"multi_year_confirmed={item.get('multi_year_confirmed')}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
