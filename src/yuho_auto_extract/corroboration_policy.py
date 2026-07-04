"""BuildBase P2: 証拠ベース自動確定ポリシー層（純関数群）。

P1の corroboration.py / services/corroboration_report.py が算出した
by_cell 集約結果（summarize_cells の戻り値）を入力に、セルごとの
resolution（auto_confirmed / single_source / conflicted / needs_review /
needs_reconciliation / no_value）を決める。

方針:
- すべて純関数。ファイルI/O・DB接続はここでは行わない。
- corroboration.py のロジックは一切変更せず、import して読むだけ。
- 「ソース種別の独立性」で重み付けする（ペア数の単純カウントはしない）。
  同じ extraction_method 同士のペア（例: XBRL同士）は1バケットとして畳む。
- validation_status=='fail' のセルは絶対に auto_confirmed にしない。
- conflict（値レベルの不一致）は絶対に auto_confirmed にしない。

安全弁（監査者向けメモ）:
  この層の判定を誤って「危険側」に倒すと、誤った値が無レビューで
  最終成果物に混入する。判定に迷う分岐は必ず needs_review 側に倒すこと。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# resolution 値
# ---------------------------------------------------------------------------

RESOLUTION_NO_VALUE = "no_value"
RESOLUTION_AUTO_CONFIRMED = "auto_confirmed"
RESOLUTION_SINGLE_SOURCE = "single_source"
RESOLUTION_CONFLICTED = "conflicted"
RESOLUTION_NEEDS_REVIEW = "needs_review"
RESOLUTION_NEEDS_RECONCILIATION = "needs_reconciliation"

# ---------------------------------------------------------------------------
# ソース種別バケット
# ---------------------------------------------------------------------------

BUCKET_XBRL = "xbrl"
BUCKET_LOCAL_TABLE = "local_table"
BUCKET_MANUAL = "manual"
BUCKET_CROSS_YEAR = "cross_year"
BUCKET_IDENTITY = "identity"
BUCKET_EXTERNAL = "external"

# extraction_method -> バケット名。XBRL由来の複数手法は同一バケットに畳む
# （P1監査知見3: ペア単位カウントは真の独立ソース数ではないため）。
_EXTRACTION_METHOD_BUCKET = {
    "XBRL_CSV": BUCKET_XBRL,
    "XBRL_COST_TEXTBLOCK": BUCKET_XBRL,
    "XBRL_SEGMENT_CONTEXT": BUCKET_XBRL,
    "LOCAL_RULE_TABLE": BUCKET_LOCAL_TABLE,
    "MANUAL_OBSIDIAN": BUCKET_MANUAL,
    "MANUAL": BUCKET_MANUAL,
}

DEFAULT_AUTO_CONFIRM_MIN_INDEPENDENT = 2
DEFAULT_SINGLE_SOURCE_OK_METHODS = ["MANUAL_OBSIDIAN"]


def _method_bucket(method: str) -> Optional[str]:
    return _EXTRACTION_METHOD_BUCKET.get(str(method or ""))


def _evidence_supports_cell_value(evidence_value: Optional[float], cell_value: Optional[float]) -> bool:
    """照合レコードの証拠値が、このセルの保持値と実際に一致するかを判定する。

    P1監査知見5への対策: 照合②(next_year_prior)は field のタグ候補に部分一致する
    「別のelement」の値を拾いうる（例: sga_expense が連結SGAと別集計SGAの両方に
    マッチ）。その場合レコードは matched=True でも「セルが実際に持つ値」を裏付けて
    いない。証拠値がセル値と一致しない cross_year は独立バケットに数えてはならない。

    単位対応: 照合②の primary_value は xbrl_facts 由来で「円」単位。セルの
    value_normalized は target_unit（百万円 / 円 / 人 / 歳 等）。円→百万円の 1e6 と、
    等倍(円/人/歳)の両スケールを許容し、いずれかで一致すれば裏付けとみなす。
    どちらも一致しなければ False（＝安全側でバケットに数えない）。
    """
    if cell_value is None or evidence_value is None:
        return False
    tol = max(1.0, abs(cell_value) * 0.005)
    for scaled in (evidence_value, evidence_value / 1_000_000.0):
        if abs(cell_value - scaled) <= tol:
            return True
    return False


def independent_source_buckets(
    corroborations: List[Dict[str, Any]],
    cell_value: Optional[float] = None,
) -> Set[str]:
    """matched=True のレコードのみから、ソース種別として独立なバケット集合を求める。

    - check_kind='xbrl_vs_local': detail.extraction_method_a / _b をそれぞれ
      バケットへマップし、両方追加する（同一バケット同士のペアは1バケットにしか
      ならないため、実質的に「2つのXBRL手法が一致しただけ」を2独立源と扱わない）。
    - check_kind='next_year_prior' matched: 'cross_year' バケット。ただし証拠値が
      このセルの保持値(cell_value)と一致する場合のみ数える（監査知見5対策。別element
      由来の一致を誤って独立源に数えない）。
    - check_kind='identity_rule' matched(pass): 'identity' バケット。
    - check_kind='factbook' matched: 'external' バケット。
    """
    buckets: Set[str] = set()
    for record in corroborations:
        if not record.get("matched"):
            continue
        check_kind = record.get("check_kind")
        detail = record.get("detail") or {}
        if check_kind == "xbrl_vs_local":
            method_a = detail.get("extraction_method_a", "")
            method_b = detail.get("extraction_method_b", "")
            bucket_a = _method_bucket(method_a)
            bucket_b = _method_bucket(method_b)
            if bucket_a:
                buckets.add(bucket_a)
            if bucket_b:
                buckets.add(bucket_b)
        elif check_kind == "next_year_prior":
            # 証拠値がセル値を実際に裏付ける場合のみ独立源に数える。
            if _evidence_supports_cell_value(record.get("primary_value"), cell_value):
                buckets.add(BUCKET_CROSS_YEAR)
        elif check_kind == "identity_rule":
            buckets.add(BUCKET_IDENTITY)
        elif check_kind == "factbook":
            buckets.add(BUCKET_EXTERNAL)
    return buckets


def _conflict_breakdown(corroborations: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """matched=False（restatement_suspectedでない=真のconflict）のレコードを check_kind別に仕分ける。"""
    out: Dict[str, List[Dict[str, Any]]] = {
        "xbrl_vs_local": [],
        "next_year_prior": [],
        "identity_rule": [],
        "factbook": [],
    }
    for record in corroborations:
        if record.get("matched"):
            continue
        if record.get("restatement_suspected"):
            continue
        check_kind = record.get("check_kind")
        if check_kind in out:
            out[check_kind].append(record)
    return out


def resolve_cell(
    entry: Dict[str, Any],
    field_id: str,
    extraction_method: str,
    validation_status: Optional[str],
    has_value: bool,
    policy: Optional[Dict[str, Any]] = None,
    cell_value: Optional[float] = None,
) -> Dict[str, Any]:
    """1セル分の by_cell entry (summarize_cells の値) から resolution を決める。

    entry: {"corroboration_count", "conflict_count", "restatement_suspected_count",
            "corroborations": [...]}  — corroboration.summarize_cells() の戻り値要素。
    field_id: このセルの field_id（important_fields判定用）。
    extraction_method: このセルの代表 extraction_method（single_source_ok_methods判定用）。
    validation_status: normalized_validated_long 側のこのセルの validation_status。
    has_value: このセルに値が存在するか（無ければ問答無用で no_value）。
    policy: config/validation_rules.yml の corroboration 節（dict）。None なら既定値。

    戻り値: {
        "resolution": str,
        "review_reason": str,
        "independent_bucket_count": int,
        "buckets": List[str],
    }
    """
    policy = policy or {}
    corroborations = entry.get("corroborations", [])
    conflict_count = int(entry.get("conflict_count", 0))

    if not has_value:
        return {
            "resolution": RESOLUTION_NO_VALUE,
            "review_reason": "",
            "independent_bucket_count": 0,
            "buckets": [],
        }

    buckets = independent_source_buckets(corroborations, cell_value=cell_value)
    independent_bucket_count = len(buckets)

    min_required = int(
        (policy.get("critical_fields_require_two") and field_id in policy.get("critical_fields_require_two", []))
        and 2
        or policy.get("auto_confirm_min_independent", DEFAULT_AUTO_CONFIRM_MIN_INDEPENDENT)
    )
    # critical_fields_require_two に載っているフィールドは常に2以上を要求する
    # （auto_confirm_min_independent が1に緩められていても2を下回らせない）
    if field_id in (policy.get("critical_fields_require_two") or []):
        min_required = max(min_required, 2)

    single_source_ok_methods = set(policy.get("single_source_ok_methods") or DEFAULT_SINGLE_SOURCE_OK_METHODS)

    # --- validation_status=='fail' は絶対に auto_confirmed にしない ---
    validation_failed = str(validation_status or "") == "fail"

    # --- conflict_count > 0: 矛盾の内訳で分岐 ---
    if conflict_count > 0:
        breakdown = _conflict_breakdown(corroborations)
        has_value_level_conflict = bool(breakdown["xbrl_vs_local"]) or bool(breakdown["factbook"])
        has_identity_conflict = bool(breakdown["identity_rule"])
        has_cross_year_conflict = bool(breakdown["next_year_prior"])

        if has_value_level_conflict:
            return {
                "resolution": RESOLUTION_CONFLICTED,
                "review_reason": "value_disagreement",
                "independent_bucket_count": independent_bucket_count,
                "buckets": sorted(buckets),
            }

        if has_identity_conflict and not has_cross_year_conflict:
            rule_ids = sorted({str(r.get("check_ref") or "") for r in breakdown["identity_rule"]})
            rule_ref = rule_ids[0] if rule_ids else ""
            return {
                "resolution": RESOLUTION_NEEDS_RECONCILIATION,
                "review_reason": f"identity_group_mismatch:{rule_ref}",
                "independent_bucket_count": independent_bucket_count,
                "buckets": sorted(buckets),
            }

        if has_cross_year_conflict:
            # next_year_prior の不一致が矛盾の主因。同年のmatchedバケットが
            # 2つ以上（xbrl/local_table/manual/external等）なら遡及修正の疑い
            # として conflict にしない。1未満ならhard conflictにせずneeds_review。
            same_year_value_buckets = buckets & {BUCKET_XBRL, BUCKET_LOCAL_TABLE, BUCKET_MANUAL, BUCKET_EXTERNAL}
            if len(same_year_value_buckets) >= 2:
                # cross_year矛盾を無視し、下の通常判定へフォールスルーする。
                # ただしreview_reasonに注記を残すため、通常判定結果に上書きマージする。
                fallthrough = _resolve_non_conflict(
                    independent_bucket_count=independent_bucket_count,
                    buckets=buckets,
                    field_id=field_id,
                    extraction_method=extraction_method,
                    validation_failed=validation_failed,
                    min_required=min_required,
                    single_source_ok_methods=single_source_ok_methods,
                    entry=entry,
                )
                if fallthrough["resolution"] == RESOLUTION_AUTO_CONFIRMED:
                    fallthrough["review_reason"] = "cross_year_divergence_likely_restatement"
                return fallthrough
            return {
                "resolution": RESOLUTION_NEEDS_REVIEW,
                "review_reason": "cross_year_mismatch",
                "independent_bucket_count": independent_bucket_count,
                "buckets": sorted(buckets),
            }

        # conflict_count>0だが上記いずれにも該当しない未知パターン: 安全側でneeds_review
        return {
            "resolution": RESOLUTION_NEEDS_REVIEW,
            "review_reason": "unclassified_conflict",
            "independent_bucket_count": independent_bucket_count,
            "buckets": sorted(buckets),
        }

    # --- conflict_count == 0 ---
    return _resolve_non_conflict(
        independent_bucket_count=independent_bucket_count,
        buckets=buckets,
        field_id=field_id,
        extraction_method=extraction_method,
        validation_failed=validation_failed,
        min_required=min_required,
        single_source_ok_methods=single_source_ok_methods,
        entry=entry,
    )


def _resolve_non_conflict(
    *,
    independent_bucket_count: int,
    buckets: Set[str],
    field_id: str,
    extraction_method: str,
    validation_failed: bool,
    min_required: int,
    single_source_ok_methods: Set[str],
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    if validation_failed:
        return {
            "resolution": RESOLUTION_NEEDS_REVIEW,
            "review_reason": "validation_fail_blocks_auto_confirm",
            "independent_bucket_count": independent_bucket_count,
            "buckets": sorted(buckets),
        }

    if independent_bucket_count >= min_required:
        return {
            "resolution": RESOLUTION_AUTO_CONFIRMED,
            "review_reason": "",
            "independent_bucket_count": independent_bucket_count,
            "buckets": sorted(buckets),
        }

    if independent_bucket_count == 1:
        if str(extraction_method or "") in single_source_ok_methods:
            return {
                "resolution": RESOLUTION_AUTO_CONFIRMED,
                "review_reason": "",
                "independent_bucket_count": independent_bucket_count,
                "buckets": sorted(buckets),
            }
        return {
            "resolution": RESOLUTION_SINGLE_SOURCE,
            "review_reason": "single_source_only",
            "independent_bucket_count": independent_bucket_count,
            "buckets": sorted(buckets),
        }

    return {
        "resolution": RESOLUTION_NEEDS_REVIEW,
        "review_reason": "insufficient_corroboration",
        "independent_bucket_count": independent_bucket_count,
        "buckets": sorted(buckets),
    }
