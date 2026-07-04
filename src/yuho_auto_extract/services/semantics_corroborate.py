"""BuildBase P2: run_corroboration 本体。

P1の照合4系統（corroboration_report.py の内部ヘルパー経由）を実行し、
corroboration_policy.py でセルごとの resolution を判定、semantics_store.py
経由で data/marts/semantics/semantics.db に永続化する。さらに
normalized_validated_long(parquet優先) へ corroboration_count /
conflict_count / corroboration_status / corroboration_refs 列を付与して
書き戻す。

このモジュールはオーケストレーションのみを担当し、照合ロジック自体は
corroboration.py（変更禁止・import専用）、resolution判定ロジックは
corroboration_policy.py（純関数）に委譲する。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .. import corroboration_policy
from ..config_loader import load_pipeline_config
from ..corroboration import corroborate_same_cell, corroborate_validation_rules, summarize_cells
from ..io_utils import prefer_existing_table, read_table, write_table
from . import semantics_store
from .corroboration_report import (
    NORMALIZED_LONG_PATH,
    VALIDATION_RESULTS_PATH,
    _read_normalized_long,
    _read_validation_results,
    _run_factbook_check,
    _run_next_year_prior_check,
)


def run_corroboration(root: Path) -> Dict[str, Any]:
    """照合4系統を実行し、semantics.dbへ永続化、normalized_validated_longへ書き戻す。

    戻り値: {"summary": {...}, "semantics_db_path": str, "normalized_long_path": str}
    """
    run_id = uuid.uuid4().hex

    cfg = load_pipeline_config(root)
    policy = cfg.validation_rules.get("corroboration_policy") or cfg.validation_rules.get("corroboration") or {}

    normalized_rows = _read_normalized_long(root)
    all_cells = _cell_keys(normalized_rows)

    records: List[Dict[str, Any]] = []
    records.extend(corroborate_same_cell(normalized_rows))
    records.extend(_run_next_year_prior_check(root, normalized_rows))
    validation_results = _read_validation_results(root)
    records.extend(corroborate_validation_rules(validation_results))
    records.extend(_run_factbook_check(root, normalized_rows))

    by_cell = summarize_cells(records, all_cells=all_cells)

    cell_meta = _build_cell_meta(normalized_rows)

    resolutions: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for key, entry in by_cell.items():
        company_year_id, field_id = key
        meta = cell_meta.get(key, {})
        has_value = meta.get("has_value", False)
        extraction_method = meta.get("extraction_method", "")
        validation_status = meta.get("validation_status")

        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id=field_id,
            extraction_method=extraction_method,
            validation_status=validation_status,
            has_value=has_value,
            policy=policy,
            cell_value=meta.get("value_normalized"),
        )
        refs = sorted({f"{c['check_kind']}:{c['check_ref']}" for c in entry.get("corroborations", [])})
        resolutions[key] = {
            "company_year_id": company_year_id,
            "concept_id": field_id,
            "value": meta.get("value_normalized"),
            "corroboration_count": entry.get("corroboration_count", 0),
            "conflict_count": entry.get("conflict_count", 0),
            "independent_bucket_count": decision["independent_bucket_count"],
            "buckets": decision["buckets"],
            "resolution": decision["resolution"],
            "review_reason": decision["review_reason"],
            "sources": refs,
        }

    # --- semantics.db への永続化 ---
    semantics_store.backup_semantics_db(root)
    conn = semantics_store.connect(root)
    try:
        semantics_store.replace_corroborations(conn, records, run_id=run_id)
        semantics_store.replace_cell_resolutions(conn, resolutions.values(), run_id=run_id)
        semantics_store.write_csv_mirrors(root, conn)
    finally:
        conn.close()

    # --- normalized_validated_long への書き戻し ---
    normalized_long_path = _write_back_normalized_long(root, resolutions)

    summary = _build_summary(resolutions)

    return {
        "summary": summary,
        "semantics_db_path": str(semantics_store.semantics_db_path(root)),
        "normalized_long_path": str(normalized_long_path),
        "run_id": run_id,
    }


def _cell_keys(rows: List[Dict[str, Any]]):
    seen = set()
    for row in rows:
        company_year_id = str(row.get("company_year_id") or "")
        field_id = str(row.get("field_id") or "")
        if company_year_id and field_id:
            seen.add((company_year_id, field_id))
    return sorted(seen)


def _build_cell_meta(normalized_rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """セルごとの代表値・extraction_method・validation_statusを拾う。

    複数行が同一セルに存在する場合（extraction_method違い）は最後に見た行を
    代表とする。has_value は value_normalized が数値として存在するかで判定。
    """
    from ..corroboration import _as_float_or_none

    meta: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in normalized_rows:
        company_year_id = str(row.get("company_year_id") or "")
        field_id = str(row.get("field_id") or "")
        if not company_year_id or not field_id:
            continue
        key = (company_year_id, field_id)
        value = _as_float_or_none(row.get("value_normalized"))
        entry = meta.setdefault(
            key,
            {
                "has_value": False,
                "value_normalized": None,
                "extraction_method": "",
                "validation_status": None,
            },
        )
        if value is not None:
            entry["has_value"] = True
            entry["value_normalized"] = value
            entry["extraction_method"] = row.get("extraction_method") or entry["extraction_method"]
        if row.get("validation_status"):
            entry["validation_status"] = row.get("validation_status")
    return meta


def _write_back_normalized_long(root: Path, resolutions: Dict[Tuple[str, str], Dict[str, Any]]) -> Path:
    """normalized_validated_long に照合列を付与して同じパスへ書き戻す。

    prefer_existing_table() で実在パス（parquet優先、無ければcsv/jsonl）を
    特定し、そのまま同一パスへ書き戻す。normalized_validated_long が無い
    環境（validateを未実行）では normalized_long にはフォールバックしない
    （P1知見: cmd_corroborateはvalidate後段で動く前提のため）。
    """
    path = prefer_existing_table(root / NORMALIZED_LONG_PATH)
    if not path.exists():
        return path
    rows = read_table(path)
    out_rows: List[Dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("company_year_id") or ""), str(row.get("field_id") or ""))
        resolution = resolutions.get(key)
        copied = dict(row)
        if resolution:
            copied["corroboration_count"] = resolution.get("corroboration_count", 0)
            copied["conflict_count"] = resolution.get("conflict_count", 0)
            copied["corroboration_status"] = resolution.get("resolution", "")
            copied["corroboration_refs"] = ";".join(resolution.get("sources") or [])
        else:
            copied.setdefault("corroboration_count", 0)
            copied.setdefault("conflict_count", 0)
            copied.setdefault("corroboration_status", "")
            copied.setdefault("corroboration_refs", "")
        out_rows.append(copied)
    write_table(path, out_rows)
    # pandas/pyarrow不在のテスト・CI環境向け保険としてcsvミラーも書く
    if path.suffix.lower() == ".parquet":
        csv_path = path.with_suffix(".csv")
        if csv_path.exists():
            write_table(csv_path, out_rows)
    return path


def _build_summary(resolutions: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for entry in resolutions.values():
        resolution = str(entry.get("resolution") or "")
        summary[resolution] = summary.get(resolution, 0) + 1
    return summary
