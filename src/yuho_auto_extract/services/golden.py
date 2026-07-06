"""BuildBase P3: ゴールデン回帰基盤。

freeze_golden(): review_resolved.csv（人手判断） + cell_resolutions(auto_confirmed)
    （証拠2件以上の機械的自動確定） + normalized_validated_long(MANUAL_OBSIDIAN)
    （人間がObsidianノートから直接入力した値）の3源からgolden集合を凍結し
    semantics.db の golden_values / golden_negative テーブルへ永続化する。

run_regression(): 一時shadow_root上でnormalize以降（既定・軽量モード）または
    build_edinet_dbから（フルモード）再実行し、golden_valuesとdiffして
    data/reports/regression_diff.csv / regression_summary.json を書く。
    本物の data/intermediate/*・data/final/* は一切変更しない。

抽出コード（xbrl_csv_parser.py, xbrl_fact_store.py, local_table_extractor.py,
edinet_db.py）・corroboration.py・apply_review_decisions は一切変更しない。
本モジュールはそれらをimportして（あるいはCLIコマンド経由で）使うのみ。
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..io_utils import is_blankish, prefer_existing_table, read_table, write_table
from . import semantics_store

GOLDEN_ORIGIN_HUMAN_CORRECT = "human_correct"
GOLDEN_ORIGIN_HUMAN_ACCEPT = "human_accept"
GOLDEN_ORIGIN_CORROBORATED_2PLUS = "corroborated_2plus"
GOLDEN_ORIGIN_MANUAL_MASTER = "manual_master"
GOLDEN_ORIGIN_NEGATIVE = "human_not_applicable"
# S1b: reviewer列が'source_inference'の行は機械由来（恒等式フィッティングによる
# 自動promote）であり、人間の判断ではない。human_correct/human_acceptに分類すると
# 回帰網の保護(gated)から誤って外れてしまう（run_regressionのシャドウ再導出は
# レビュー非適用のため、人間ロック値は原理的に非再現でinformational扱いになる仕様
# だが、機械由来の値は本来再現可能でなければならず、regressionのゲート対象に
# 含めるべき）。そのためreviewer=='source_inference'は専用originに分類する。
GOLDEN_ORIGIN_SOURCE_INFERENCE = "source_inference"

REGRESSION_MODE_LIGHT = "light"
REGRESSION_MODE_FULL = "full"

REGRESSION_DIFF_CSV = Path("data") / "reports" / "regression_diff.csv"
REGRESSION_SUMMARY_JSON = Path("data") / "reports" / "regression_summary.json"

# load_pipeline_config / cmd_normalize / cmd_validate / cmd_corroborate /
# cmd_export_final が読む config 一式（実コード確認済み: config_loader.py,
# semantics_corroborate.run_corroboration）。shadow_root にはこれら全てが
# 揃っていないと load_pipeline_config が例外を投げる。
_SHADOW_CONFIG_FILES = [
    "company_master.csv",
    "company_master.xlsx",
    "company_year_master.csv",
    "company_year_master.xlsx",
    "field_definition.csv",
    "field_definition.xlsx",
    "document_filter.yml",
    "extraction_sections.yml",
    "validation_rules.yml",
    "model_config.yml",
    "company_field_exclusions.csv",
]

# normalize の入力3種（実体を実行確認済み: extract_from_edinet_db が
# xbrl_extracted_long.parquet ではなく .csv を書くため prefer_existing_table
# が .csv にフォールバックする。db_extracted_long.csv はnormalizeの直接入力
# ではないが将来の依存変化に備えコピーしておく）。
_SHADOW_INTERMEDIATE_FILES_LIGHT = [
    "db_extracted_long.csv",
    "xbrl_extracted_long.csv",
    "xbrl_extracted_long.parquet",
    "ai_extracted_long.jsonl",
    "manual_technician_extracted_long.csv",
]

_SHADOW_INTERMEDIATE_FILES_FULL = [
    "document_index.csv",
    "document_index.parquet",
    "target_documents.csv",
    "target_documents.parquet",
    "candidate_blocks.jsonl",
    "manual_technician_extracted_long.csv",
]


# ---------------------------------------------------------------------------
# freeze_golden
# ---------------------------------------------------------------------------

def freeze_golden(root: Path) -> Dict[str, Any]:
    """golden_values / golden_negative テーブルを最新のgolden集合で完全置換する。

    優先順位（低い方から積み、後勝ちで上書きする）:
        3. manual_master        (extraction_method == 'MANUAL_OBSIDIAN')
        2. corroborated_2plus   (cell_resolutions.resolution == 'auto_confirmed')
        1. human_correct/accept (review_resolved.csv, applied_status == 'applied')  最優先

    not_applicable（review_decision）はネガティブゴールデンとして別枠に格納し、
    通常のgolden集合からは除去する。

    毎回明示的なCLI実行時のみ呼ばれる想定（run_regression からは自動起動しない
    設計。直近の抽出改善で偶然一致した誤値までgoldenに固定してしまうリスクを
    避けるため）。
    """
    run_id = uuid.uuid4().hex
    golden: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for key, value in _manual_obsidian_cells(root).items():
        golden[key] = {"value": value, "origin": GOLDEN_ORIGIN_MANUAL_MASTER, "locked": False}

    for key, value in _auto_confirmed_cells(root).items():
        golden[key] = {"value": value, "origin": GOLDEN_ORIGIN_CORROBORATED_2PLUS, "locked": False}

    negative_golden: Set[Tuple[str, str]] = set()
    for row in _read_review_resolved(root):
        company_year_id = str(row.get("company_year_id") or "")
        field_id = str(row.get("field_id") or "")
        if not company_year_id or not field_id:
            continue
        key = (company_year_id, field_id)
        decision = str(row.get("review_decision") or "")
        if decision == "not_applicable":
            negative_golden.add(key)
            golden.pop(key, None)
            continue
        if decision in {"correct", "accept"} and str(row.get("applied_status") or "") == "applied":
            applied_value = row.get("applied_value")
            if is_blankish(applied_value):
                continue
            if str(row.get("reviewer") or "") == "source_inference":
                # 機械由来（恒等式フィッティングで確定した値）: 人間ロックにはせず、
                # 回帰網の保護(gated)対象になるoriginを別枠で付与する。
                origin = GOLDEN_ORIGIN_SOURCE_INFERENCE
                locked = False
            else:
                origin = GOLDEN_ORIGIN_HUMAN_CORRECT if decision == "correct" else GOLDEN_ORIGIN_HUMAN_ACCEPT
                locked = True
            try:
                value = float(applied_value)
            except (TypeError, ValueError):
                continue
            golden[key] = {"value": value, "origin": origin, "locked": locked}

    semantics_store.backup_semantics_db(root)
    conn = semantics_store.connect(root)
    try:
        golden_entries = [
            {
                "company_year_id": key[0],
                "concept_id": key[1],
                "value": entry["value"],
                "origin": entry["origin"],
                "locked": entry["locked"],
                "evidence": {},
            }
            for key, entry in golden.items()
        ]
        negative_entries = [
            {
                "company_year_id": key[0],
                "concept_id": key[1],
                "origin": GOLDEN_ORIGIN_NEGATIVE,
                "evidence": {},
            }
            for key in negative_golden
        ]
        semantics_store.replace_golden_values(conn, golden_entries, run_id=run_id)
        semantics_store.replace_golden_negative(conn, negative_entries, run_id=run_id)
        conn.commit()
        semantics_store.write_csv_mirrors(root, conn)
    finally:
        conn.close()

    return {
        "run_id": run_id,
        "golden_cell_count": len(golden),
        "negative_golden_count": len(negative_golden),
        "by_origin": _count_by_origin(golden),
    }


def _count_by_origin(golden: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for entry in golden.values():
        origin = str(entry.get("origin") or "")
        counts[origin] = counts.get(origin, 0) + 1
    return counts


# --- golden集合の3源 ---------------------------------------------------

def _read_review_resolved(root: Path) -> List[Dict[str, Any]]:
    path = root / "data" / "review" / "review_resolved.csv"
    return read_table(path) if path.exists() else []


def _auto_confirmed_cells(root: Path) -> Dict[Tuple[str, str], float]:
    db_path = semantics_store.semantics_db_path(root)
    if not db_path.exists():
        return {}
    conn = semantics_store.connect(root)
    try:
        rows = conn.execute(
            "select company_year_id, concept_id, value from cell_resolutions where resolution = 'auto_confirmed'"
        ).fetchall()
    finally:
        conn.close()
    out: Dict[Tuple[str, str], float] = {}
    for row in rows:
        value = row["value"]
        if value is None:
            continue
        out[(str(row["company_year_id"]), str(row["concept_id"]))] = float(value)
    return out


def _manual_obsidian_cells(root: Path) -> Dict[Tuple[str, str], float]:
    path = prefer_existing_table(root / "data" / "intermediate" / "normalized_validated_long.parquet")
    if not path.exists():
        return {}
    rows = read_table(path)
    out: Dict[Tuple[str, str], float] = {}
    for row in rows:
        if str(row.get("extraction_method") or "") != "MANUAL_OBSIDIAN":
            continue
        value = row.get("value_normalized")
        if is_blankish(value):
            continue
        try:
            out[(str(row["company_year_id"]), str(row["field_id"]))] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _load_golden(root: Path) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], Set[Tuple[str, str]]]:
    """semantics.db から golden_values / golden_negative を読み出す。"""
    db_path = semantics_store.semantics_db_path(root)
    if not db_path.exists():
        return {}, set()
    conn = semantics_store.connect(root)
    try:
        golden_rows = semantics_store.fetch_golden_values(conn)
        negative_rows = semantics_store.fetch_golden_negative(conn)
    finally:
        conn.close()
    golden = {
        key: {"value": row.get("value"), "origin": row.get("origin")}
        for key, row in golden_rows.items()
        if row.get("value") is not None
    }
    negative = set(negative_rows.keys())
    return golden, negative


# ---------------------------------------------------------------------------
# run_regression
# ---------------------------------------------------------------------------

def run_regression(root: Path, *, mode: str = REGRESSION_MODE_LIGHT) -> Dict[str, Any]:
    """一時shadow_root上で再抽出し、golden_valuesとdiffする。

    既存 data/final・data/intermediate・data/marts/semantics は一切変更しない
    （一時ディレクトリで完結し、終了時に自動削除される）。
    """
    if mode not in (REGRESSION_MODE_LIGHT, REGRESSION_MODE_FULL):
        raise ValueError(f"unknown regression mode: {mode!r}")

    golden, negative_golden = _load_golden(root)

    with tempfile.TemporaryDirectory(prefix="buildbase_regression_") as tmp:
        shadow_root = Path(tmp)
        _prepare_shadow_root(root, shadow_root, mode=mode)
        _run_shadow_pipeline(shadow_root, mode=mode)
        actual_cells = _read_shadow_final_long(shadow_root)

    diff_rows, summary = _diff(golden, negative_golden, actual_cells)
    summary["mode"] = mode
    summary["run_id"] = uuid.uuid4().hex

    reports_dir = root / "data" / "reports"
    write_table(reports_dir / "regression_diff.csv", diff_rows)
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "regression_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def read_regression_summary(root: Path) -> Dict[str, Any]:
    """GET APIから使う軽量読み出し。未生成なら status: not_built を返す。"""
    summary_path = root / REGRESSION_SUMMARY_JSON
    if not summary_path.exists():
        return {"status": "not_built"}
    return json.loads(summary_path.read_text(encoding="utf-8"))


def read_golden_summary(root: Path) -> Dict[str, Any]:
    """GET APIから使うgolden集合の軽量サマリー。"""
    db_path = semantics_store.semantics_db_path(root)
    if not db_path.exists():
        return {"status": "not_built", "golden_cell_count": 0, "negative_golden_count": 0, "by_origin": {}}
    conn = semantics_store.connect(root)
    try:
        golden_rows = semantics_store.fetch_golden_values(conn)
        negative_rows = semantics_store.fetch_golden_negative(conn)
    finally:
        conn.close()

    by_origin: Dict[str, int] = {}
    latest_decided_at = ""
    for row in golden_rows.values():
        origin = str(row.get("origin") or "")
        by_origin[origin] = by_origin.get(origin, 0) + 1
        decided_at = str(row.get("decided_at_utc") or "")
        if decided_at > latest_decided_at:
            latest_decided_at = decided_at
    for row in negative_rows.values():
        origin = str(row.get("origin") or GOLDEN_ORIGIN_NEGATIVE)
        by_origin[origin] = by_origin.get(origin, 0) + 1
        decided_at = str(row.get("decided_at_utc") or "")
        if decided_at > latest_decided_at:
            latest_decided_at = decided_at

    return {
        "status": "ready",
        "golden_cell_count": len(golden_rows),
        "negative_golden_count": len(negative_rows),
        "by_origin": by_origin,
        "latest_decided_at_utc": latest_decided_at,
    }


def _prepare_shadow_root(root: Path, shadow_root: Path, *, mode: str) -> None:
    (shadow_root / "config").mkdir(parents=True, exist_ok=True)
    (shadow_root / "data" / "intermediate").mkdir(parents=True, exist_ok=True)
    (shadow_root / "data" / "review").mkdir(parents=True, exist_ok=True)

    for name in _SHADOW_CONFIG_FILES:
        src = root / "config" / name
        if src.exists():
            shutil.copy2(src, shadow_root / "config" / name)

    if mode == REGRESSION_MODE_LIGHT:
        for name in _SHADOW_INTERMEDIATE_FILES_LIGHT:
            src = root / "data" / "intermediate" / name
            if src.exists():
                shutil.copy2(src, shadow_root / "data" / "intermediate" / name)
    else:
        for name in _SHADOW_INTERMEDIATE_FILES_FULL:
            src = root / "data" / "intermediate" / name
            if src.exists():
                _link_or_copy(src, shadow_root / "data" / "intermediate" / name)
        raw_src = root / "data" / "raw"
        if raw_src.exists():
            _link_or_copy(raw_src, shadow_root / "data" / "raw")
        # フルモード: build_edinet_db をshadow_root内の一時DBパスに対して呼ぶ。
        # edinet_db.py:22 の db_path.unlink() 挙動を考慮し、本物の
        # data/intermediate/edinet.db は絶対に渡さない（このパスは
        # shadow_root配下のみを指す）。
        from ..edinet_db import build_edinet_db, extract_from_edinet_db

        db_path = shadow_root / "data" / "intermediate" / "edinet.db"
        build_edinet_db(shadow_root, db_path)
        extract_from_edinet_db(
            shadow_root,
            db_path,
            shadow_root / "data" / "intermediate" / "db_extracted_long.csv",
            write_pipeline=True,
        )


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
    except OSError:
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def _run_shadow_pipeline(shadow_root: Path, *, mode: str) -> None:
    # cmd_* はモジュールレベルの重い依存を持つため、golden.py の import 時では
    # なく呼び出し時に遅延importする（既存 __main__.py の cmd_corroborate 等の
    # 慣習に倣う）。
    from .. import __main__ as cli

    empty_args = argparse.Namespace()
    cli.cmd_normalize(shadow_root, empty_args)
    cli.cmd_validate(shadow_root, empty_args)
    cli.cmd_corroborate(shadow_root, empty_args)
    # reviewed="" : shadow_root配下には review_resolved.csv を置かないため
    # apply_review_decisions は「人手適用なし」で走り、diff対象は素の抽出結果
    # そのものになる（goldenは既にhuman判断込みで別途凍結済みのため、regression
    # 側で人手適用を重ねる必要はない）。
    cli.cmd_export_final(shadow_root, argparse.Namespace(reviewed="data/review/__no_such_file__.csv"))


def _read_shadow_final_long(shadow_root: Path) -> Dict[Tuple[str, str], Any]:
    """final_master_long.csv（long形式）から (company_year_id, field_id) -> value を作る。

    wide形式はメタ列とfield_id列が混在し field_definition との突合が必要になる
    ため、long形式を使う（company_year_id, field_id, value 列を直接持つ）。
    """
    path = prefer_existing_table(shadow_root / "data" / "final" / "final_master_long.parquet")
    if not path.exists():
        return {}
    rows = read_table(path)
    out: Dict[Tuple[str, str], Any] = {}
    for row in rows:
        company_year_id = str(row.get("company_year_id") or "")
        field_id = str(row.get("field_id") or "")
        if not company_year_id or not field_id:
            continue
        value = row.get("value", row.get("value_normalized"))
        if is_blankish(value):
            continue
        out[(company_year_id, field_id)] = value
    return out


# --- diff（純関数・DB/ファイルI/Oなし） ------------------------------------

# 機械的に再導出できるgolden起源（設定・ルール変更で値が変わりうる＝回帰ゲート対象）。
# human_correct / human_accept は「素のパイプラインが誤るからこそ人間が是正/採用した」
# ロックされた上書き値であり、レビュー非適用のシャドウ再導出では原理的に再現不能。
# よってpass/failゲートには含めず、informationalとして別集計する。
_GATED_GOLDEN_ORIGINS = frozenset(
    {GOLDEN_ORIGIN_CORROBORATED_2PLUS, GOLDEN_ORIGIN_MANUAL_MASTER, GOLDEN_ORIGIN_SOURCE_INFERENCE}
)


def _diff(
    golden: Dict[Tuple[str, str], Dict[str, Any]],
    negative_golden: Set[Tuple[str, str]],
    actual: Dict[Tuple[str, str], Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    diff_rows: List[Dict[str, Any]] = []
    mismatch = 0          # 機械起源goldenの値不一致（ゲート対象）
    missing = 0           # 機械起源goldenの値消失（ゲート対象）
    neg_violation = 0     # ネガティブgolden違反（ゲート対象）
    human_unreproduced = 0  # 人間ロック値の非再現（informational・非ゲート）

    for key, entry in golden.items():
        company_year_id, field_id = key
        expected = entry.get("value")
        origin = str(entry.get("origin") or "")
        gated = origin in _GATED_GOLDEN_ORIGINS
        got = actual.get(key)
        if got is None or is_blankish(got):
            if gated:
                missing += 1
                kind = "missing_in_actual"
            else:
                human_unreproduced += 1
                kind = "human_locked_unreproduced"
            diff_rows.append(
                {
                    "company_year_id": company_year_id,
                    "field_id": field_id,
                    "kind": kind,
                    "expected": expected,
                    "actual": "",
                    "origin": origin,
                }
            )
            continue
        if not _values_match(expected, got):
            if gated:
                mismatch += 1
                kind = "value_mismatch"
            else:
                human_unreproduced += 1
                kind = "human_locked_unreproduced"
            diff_rows.append(
                {
                    "company_year_id": company_year_id,
                    "field_id": field_id,
                    "kind": kind,
                    "expected": expected,
                    "actual": got,
                    "origin": origin,
                }
            )

    for key in negative_golden:
        company_year_id, field_id = key
        got = actual.get(key)
        if got is not None and not is_blankish(got):
            neg_violation += 1
            diff_rows.append(
                {
                    "company_year_id": company_year_id,
                    "field_id": field_id,
                    "kind": "negative_golden_violation",
                    "expected": "",
                    "actual": got,
                    "origin": GOLDEN_ORIGIN_NEGATIVE,
                }
            )

    gated_golden = sum(1 for e in golden.values() if str(e.get("origin") or "") in _GATED_GOLDEN_ORIGINS)
    gate_mismatch = mismatch + missing + neg_violation
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "golden_cell_count": len(golden),
        "gated_golden_count": gated_golden,
        "negative_golden_count": len(negative_golden),
        "checked_cell_count": len(golden) + len(negative_golden),
        "value_mismatch_count": mismatch,
        "missing_in_actual_count": missing,
        "negative_golden_violations": neg_violation,
        # 人間ロック値の非再現はゲートに含めない（是正の存在自体が期待挙動）。
        "human_locked_unreproduced_count": human_unreproduced,
        "mismatch_count": gate_mismatch,
        "pass": gate_mismatch == 0,
    }
    return diff_rows, summary


def _values_match(expected: Any, actual: Any, tol: float = 1.0) -> bool:
    """百万円単位の許容誤差±1、または相対誤差0.1%のいずれか緩い方を満たせば一致とみなす。"""
    try:
        expected_f = float(expected)
        actual_f = float(actual)
    except (TypeError, ValueError):
        return str(expected) == str(actual)
    diff = abs(expected_f - actual_f)
    allowed = max(tol, abs(expected_f) * 0.001)
    return diff <= allowed
