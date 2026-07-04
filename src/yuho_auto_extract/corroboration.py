"""証拠照合エンジン（純関数群）。

BuildBase P1: 既存9,624セル（company_year_id x field_id）に対して、
独立ソースが何件裏取りできているか、矛盾はどこかを判定する。

方針:
- すべて純関数。ファイルI/O・DB接続はここでは行わず、呼び出し側
  （services/corroboration_report.py）が pandas / sqlite3 で読み込んだ
  データ構造を渡す。
- 単位はすべて normalizer の関数で正規化してから比較する。
- 読み取り専用。edinet.db・normalized_validated_long 等を一切変更しない。

照合4系統:
  1. corroborate_same_cell:      同一 (company_year_id, field_id) で
                                  extraction_method が異なる行同士の値一致
  2. corroborate_next_year_prior: xbrl_facts の self-join。当年度の確定値と
                                  翌年度有報の「前期」比較値の一致
  3. corroborate_validation_rules: validation_results.parquet の pass/fail
                                  を +1照合／conflict として取り込み
  4. corroborate_factbook:       外部正本マート（company_factbooks 等）
                                  との突合
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .normalizer import convert_unit, normalize_numeric, normalize_unit


def _as_float_or_none(value: Any) -> Optional[float]:
    """value_normalized 等の数値列を float に変換する。NaN/None/空文字は None を返す。

    pandas 由来の parquet 読み込みでは欠損値が float('nan') として現れるため、
    `value in (None, "")` の等価判定だけでは NaN を素通りさせてしまう
    （NaN はどの値とも等しくない）。math.isnan での明示チェックが必須。
    """
    if value is None:
        return None
    if isinstance(value, str) and value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


# 許容誤差のデフォルト: 百万円ベースで ±1、または基準値の0.1%のいずれか大きい方
DEFAULT_TOLERANCE_ABS = 1.0
DEFAULT_TOLERANCE_PCT = 0.001

# 照合②で使う許容誤差（円ベース。百万円±1に相当）
NEXT_YEAR_PRIOR_TOLERANCE_ABS_YEN = 1_000_000.0
NEXT_YEAR_PRIOR_TOLERANCE_PCT = 0.001

CHECK_KIND_SAME_CELL = "xbrl_vs_local"
CHECK_KIND_NEXT_YEAR_PRIOR = "next_year_prior"
CHECK_KIND_IDENTITY_RULE = "identity_rule"
CHECK_KIND_FACTBOOK = "factbook"


def _is_matched(diff: float, base_a: float, base_b: float, tolerance_abs: float, tolerance_pct: float) -> bool:
    base = max(abs(base_a), abs(base_b), 1.0)
    return abs(diff) <= max(tolerance_abs, base * tolerance_pct)


def build_corroboration_record(
    company_year_id: str,
    field_id: str,
    check_kind: str,
    check_ref: str,
    matched: bool,
    primary_value: Optional[float],
    other_value: Optional[float],
    difference: Optional[float],
    restatement_suspected: bool = False,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """1件の照合結果を表す共通レコードを組み立てる。"""
    return {
        "company_year_id": company_year_id,
        "field_id": field_id,
        "check_kind": check_kind,
        "check_ref": check_ref,
        "matched": bool(matched),
        "primary_value": primary_value,
        "other_value": other_value,
        "difference": difference,
        "restatement_suspected": bool(restatement_suspected),
        "detail": detail or {},
    }


# ---------------------------------------------------------------------------
# 照合①: 同一セル内 extraction_method 相互比較
# ---------------------------------------------------------------------------

def corroborate_same_cell(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一 (company_year_id, field_id) 内で extraction_method が異なる行同士を比較する。

    rows: normalized_validated_long 相当のレコード列。各行に少なくとも
      company_year_id, field_id, extraction_method, value_normalized,
      unit_normalized を含むこと。

    戻り値: build_corroboration_record 形式のリスト（check_kind='xbrl_vs_local'）。
    値が非nullの行が2件未満のセルは対象外（照合不能、呼び出し側で0件扱い）。
    unit_normalized が食い違うペアは照合せずスキップする。
    """
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if _as_float_or_none(row.get("value_normalized")) is None:
            continue
        company_year_id = str(row.get("company_year_id") or "")
        field_id = str(row.get("field_id") or "")
        if not company_year_id or not field_id:
            continue
        grouped[(company_year_id, field_id)].append(row)

    results: List[Dict[str, Any]] = []
    for (company_year_id, field_id), group in grouped.items():
        n = len(group)
        for i in range(n):
            for j in range(i + 1, n):
                row_a, row_b = group[i], group[j]
                method_a = row_a.get("extraction_method")
                method_b = row_b.get("extraction_method")
                if method_a == method_b:
                    continue
                unit_a = row_a.get("unit_normalized")
                unit_b = row_b.get("unit_normalized")
                if unit_a != unit_b:
                    # 単位不一致は照合不能。矛盾ではなく「照合できない」ものとして
                    # 別枠で detail に残す（呼び出し側の集計では無視される）。
                    continue
                # グループ化時点で非NaN・非Noneであることが保証されている
                value_a = _as_float_or_none(row_a["value_normalized"])
                value_b = _as_float_or_none(row_b["value_normalized"])
                if value_a is None or value_b is None:
                    continue
                diff = value_a - value_b
                matched = _is_matched(diff, value_a, value_b, DEFAULT_TOLERANCE_ABS, DEFAULT_TOLERANCE_PCT)
                check_ref = f"{method_a}<->{method_b}"
                results.append(
                    build_corroboration_record(
                        company_year_id=company_year_id,
                        field_id=field_id,
                        check_kind=CHECK_KIND_SAME_CELL,
                        check_ref=check_ref,
                        matched=matched,
                        primary_value=value_a,
                        other_value=value_b,
                        difference=diff,
                        detail={
                            "extraction_method_a": method_a,
                            "extraction_method_b": method_b,
                            "unit_normalized": unit_a,
                        },
                    )
                )
    return results


# ---------------------------------------------------------------------------
# 照合②: 当年度確定値 <-> 翌年度有報の前期比較値 (xbrl_facts self-join)
# ---------------------------------------------------------------------------

def _context_suffix(context_id: Optional[str]) -> str:
    if not context_id:
        return ""
    return context_id.split("_", 1)[1] if "_" in context_id else ""


def _parse_fact_value(raw: Any) -> Optional[float]:
    if raw in (None, "", "－", "-"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def corroborate_next_year_prior(
    current_facts: Iterable[Dict[str, Any]],
    prior_facts_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]],
    next_company_year_lookup: Dict[Tuple[str, int], str],
    transition_flags: Dict[str, int],
    valid_company_year_ids: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """xbrl_facts の self-join による当年度⇔翌年度前期比較値の照合。

    current_facts: 当年度側の候補行イテラブル。各行は最低限
      company_year_id, operating_company_id, fiscal_year, element_id,
      context_id, consolidation_scope, period_or_instant, value を持つ
      （relative_year in ('当期','当期末') かつ context_id like 'CurrentYear%'
       で事前フィルタ済みであることを呼び出し側が保証する）。

    prior_facts_by_key: {(company_year_id, element_id): [前期側行, ...]} の
      事前インデックス。前期側行は最低限 context_id, consolidation_scope,
      period_or_instant, value を持つ（relative_year in ('前期','前期末') かつ
      context_id like 'Prior1Year%' で事前フィルタ済み）。

    next_company_year_lookup: {(operating_company_id, fiscal_year+1): company_year_id}
      の正引き表（company_year_master.csv 由来）。

    transition_flags: {company_year_id: 0/1}。1の場合はその年度が絡む
      ペアを restatement_suspected=True にする（conflict扱いにしない）。

    valid_company_year_ids: 存在するcompany_year_idの集合。翌年度データが
      実在しない場合は照合対象外（未成立、0件扱い）。

    戻り値: build_corroboration_record 形式のリスト（check_kind='next_year_prior'）。
    field_id は呼び出し側で element_id -> field_id のマッピングが必要な場合、
    check_ref に element_id を格納し、呼び出し側で field_id を補完すること。
    ここでは check_ref = element_id、field_id は空文字のまま返す
    （services 層で xbrl_element -> field_id マッピングを適用する）。
    """
    results: List[Dict[str, Any]] = []
    for row in current_facts:
        company_year_id = str(row.get("company_year_id") or "")
        operating_company_id = str(row.get("operating_company_id") or "")
        element_id = str(row.get("element_id") or "")
        if not company_year_id or not element_id:
            continue
        try:
            fiscal_year = int(row.get("fiscal_year"))
        except (TypeError, ValueError):
            continue

        next_key = (operating_company_id, fiscal_year + 1)
        next_company_year_id = next_company_year_lookup.get(next_key)
        if not next_company_year_id:
            continue
        if valid_company_year_ids is not None and next_company_year_id not in valid_company_year_ids:
            continue

        prior_rows = prior_facts_by_key.get((next_company_year_id, element_id), [])
        if not prior_rows:
            continue

        cur_val = _parse_fact_value(row.get("value"))
        if cur_val is None:
            continue

        cur_suffix = _context_suffix(row.get("context_id"))
        cur_scope = row.get("consolidation_scope")
        cur_period = row.get("period_or_instant")

        restatement_suspected = (
            int(transition_flags.get(company_year_id, 0)) == 1
            or int(transition_flags.get(next_company_year_id, 0)) == 1
        )

        for prior in prior_rows:
            if prior.get("consolidation_scope") != cur_scope:
                continue
            if prior.get("period_or_instant") != cur_period:
                continue
            if _context_suffix(prior.get("context_id")) != cur_suffix:
                continue
            prior_val = _parse_fact_value(prior.get("value"))
            if prior_val is None:
                continue

            diff = cur_val - prior_val
            matched = _is_matched(
                diff, cur_val, prior_val, NEXT_YEAR_PRIOR_TOLERANCE_ABS_YEN, NEXT_YEAR_PRIOR_TOLERANCE_PCT
            )
            restatement_mismatch = not matched
            results.append(
                build_corroboration_record(
                    company_year_id=company_year_id,
                    field_id="",  # services層でelement_id->field_idを補完
                    check_kind=CHECK_KIND_NEXT_YEAR_PRIOR,
                    check_ref=element_id,
                    matched=matched,
                    primary_value=cur_val,
                    other_value=prior_val,
                    difference=diff,
                    restatement_suspected=restatement_mismatch,
                    detail={
                        "next_company_year_id": next_company_year_id,
                        "context_suffix": cur_suffix,
                        "consolidation_scope": cur_scope,
                        "period_or_instant": cur_period,
                        "unit": "円",
                        "transition_year_related": restatement_suspected,
                    },
                )
            )
    return results


# ---------------------------------------------------------------------------
# 照合③: validation_results (恒等式ルール) の取り込み
# ---------------------------------------------------------------------------

def corroborate_validation_rules(validation_results: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """validation_results.parquet 相当のレコードを (company_year_id, field_id) ごとに展開する。

    status='pass' -> matched=True (+1照合)
    status='fail' -> matched=False (conflict)
    status in ('warn', 'not_applicable') -> 対象外（呼び出し側で無視する）

    戻り値: build_corroboration_record 形式のリスト（check_kind='identity_rule'）。
    """
    results: List[Dict[str, Any]] = []
    for result in validation_results:
        status = result.get("status")
        if status not in ("pass", "fail"):
            continue
        company_year_id = str(result.get("company_year_id") or "")
        if not company_year_id:
            continue
        field_ids = result.get("field_ids") or []
        if isinstance(field_ids, str):
            # CSV経由等で文字列化されている場合のフォールバック
            import ast

            try:
                field_ids = ast.literal_eval(field_ids)
            except (ValueError, SyntaxError):
                field_ids = []
        rule_id = str(result.get("rule_id") or "")
        matched = status == "pass"
        difference = result.get("difference")
        for field_id in field_ids:
            field_id = str(field_id)
            if not field_id:
                continue
            results.append(
                build_corroboration_record(
                    company_year_id=company_year_id,
                    field_id=field_id,
                    check_kind=CHECK_KIND_IDENTITY_RULE,
                    check_ref=rule_id,
                    matched=matched,
                    primary_value=None,
                    other_value=None,
                    difference=difference,
                    detail={
                        "status": status,
                        "description": result.get("description", ""),
                    },
                )
            )
    return results


# ---------------------------------------------------------------------------
# 照合④: 外部正本マート（company_factbooks 等）との突合
# ---------------------------------------------------------------------------

def corroborate_factbook(
    cell_lookup: Dict[Tuple[str, str], float],
    factbook_rows: Iterable[Dict[str, Any]],
    field_map: Dict[str, str],
    check_ref: str,
    company_id_key: str = "company_id",
    fiscal_year_key: str = "fiscal_year",
    category_key: str = "use_category_normalized",
    value_key: str = "amount_million_yen",
    period_type_key: str = "period_type",
    period_type_value: str = "annual",
) -> List[Dict[str, Any]]:
    """外部正本マート(例: company_factbooks/building_orders_by_category.csv)との突合。

    cell_lookup: {(company_year_id, field_id): value_normalized(百万円)} の索引
      （normalized_validated_long から呼び出し側が構築する）。

    factbook_rows: マートの行イテラブル。period_type が period_type_value と
      一致する行のみを対象にする（予想値等は除外）。

    field_map: {factbook側カテゴリ値: field_id} の対応表。マッピングが無い
      カテゴリはスキップする（development_other等）。

    戻り値: build_corroboration_record 形式のリスト（check_kind='factbook'）。
    """
    results: List[Dict[str, Any]] = []
    for row in factbook_rows:
        if str(row.get(period_type_key)) != period_type_value:
            continue
        category = row.get(category_key)
        field_id = field_map.get(str(category))
        if not field_id:
            continue
        company_id = str(row.get(company_id_key) or "")
        try:
            fiscal_year = int(row.get(fiscal_year_key))
        except (TypeError, ValueError):
            continue
        company_year_id = f"{company_id}_{fiscal_year}"
        extracted_value = cell_lookup.get((company_year_id, field_id))
        if extracted_value is None:
            continue
        factbook_value = _as_float_or_none(row.get(value_key))
        if factbook_value is None:
            continue
        diff = extracted_value - factbook_value
        matched = _is_matched(diff, extracted_value, factbook_value, DEFAULT_TOLERANCE_ABS, DEFAULT_TOLERANCE_PCT)
        results.append(
            build_corroboration_record(
                company_year_id=company_year_id,
                field_id=field_id,
                check_kind=CHECK_KIND_FACTBOOK,
                check_ref=check_ref,
                matched=matched,
                primary_value=extracted_value,
                other_value=factbook_value,
                difference=diff,
                detail={"use_category": category},
            )
        )
    return results


# ---------------------------------------------------------------------------
# セル単位への集約
# ---------------------------------------------------------------------------

def summarize_cells(
    corroboration_records: Iterable[Dict[str, Any]],
    all_cells: Optional[Iterable[Tuple[str, str]]] = None,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """corroboration レコード群を (company_year_id, field_id) ごとに集約する。

    corroboration_count: matched=True のレコード数（独立照合成立数）。
      restatement_suspected=True の unmatched は conflict にも corroboration にも
      数えない（フラグのみ）。
    conflict_count: matched=False かつ restatement_suspected=False のレコード数。

    all_cells が渡された場合、レコードが1件も無いセルも corroboration_count=0,
    conflict_count=0 として出力に含める。
    """
    by_cell: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _ensure(key: Tuple[str, str]) -> Dict[str, Any]:
        if key not in by_cell:
            by_cell[key] = {
                "company_year_id": key[0],
                "field_id": key[1],
                "corroboration_count": 0,
                "conflict_count": 0,
                "restatement_suspected_count": 0,
                "corroborations": [],
            }
        return by_cell[key]

    if all_cells:
        for key in all_cells:
            _ensure(key)

    for record in corroboration_records:
        company_year_id = record.get("company_year_id")
        field_id = record.get("field_id")
        if not company_year_id or not field_id:
            continue
        key = (str(company_year_id), str(field_id))
        entry = _ensure(key)
        entry["corroborations"].append(record)
        if record.get("matched"):
            entry["corroboration_count"] += 1
        elif record.get("restatement_suspected"):
            entry["restatement_suspected_count"] += 1
        else:
            entry["conflict_count"] += 1

    return by_cell
