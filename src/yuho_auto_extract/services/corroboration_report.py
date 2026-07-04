"""P1: 証拠照合レポートのビルダー。

既存の抽出結果（normalized_validated_long.parquet, edinet.db, validation_results.parquet,
外部正本マート）を読み取り専用で突合し、セルごとの独立照合件数・矛盾件数を集計する。

出力:
  data/reports/corroboration_cells.csv    — セル別詳細
  data/reports/corroboration_summary.json — 集計サマリ（機械可読）
  data/reports/corroboration_summary.md   — 集計サマリ（人間可読）

このモジュール自身は既存パイプラインの挙動・出力ファイルを一切変更しない
（読み取り専用）。edinet.db は PRAGMA query_only=ON で開く。
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..corroboration import (
    _as_float_or_none,
    corroborate_factbook,
    corroborate_next_year_prior,
    corroborate_same_cell,
    corroborate_validation_rules,
    summarize_cells,
)
from ..io_utils import ensure_parent, prefer_existing_table, read_table, write_table


REPORTS_DIR = Path("data") / "reports"
CELLS_FILENAME = "corroboration_cells.csv"
SUMMARY_JSON_FILENAME = "corroboration_summary.json"
SUMMARY_MD_FILENAME = "corroboration_summary.md"

NORMALIZED_LONG_PATH = Path("data") / "intermediate" / "normalized_validated_long.parquet"
VALIDATION_RESULTS_PATH = Path("data") / "intermediate" / "validation_results.parquet"
EDINET_DB_PATH = Path("data") / "intermediate" / "edinet.db"
COMPANY_YEAR_MASTER_PATH = Path("config") / "company_year_master.csv"
FACTBOOK_ORDERS_PATH = Path("data") / "marts" / "company_factbooks" / "building_orders_by_category.csv"

# 照合④: company_factbooks/building_orders_by_category.csv のカテゴリ -> field_id
# 現状データは company_id=SHIMIZU（清水建設）単独。development_other は対応する
# field_id が存在しないため突合対象外。
FACTBOOK_ORDERS_FIELD_MAP = {
    "domestic_building": "domestic_building_orders_total",
    "domestic_civil": "segment_orders_domestic_civil",
    "overseas_building": "segment_orders_overseas_building",
    "overseas_civil": "segment_orders_overseas_civil",
}
FACTBOOK_ORDERS_CHECK_REF = "shimizu_segment_orders"

# manual_technicians (architecture_engineers_*) は normalized_validated_long 内の
# extraction_method=MANUAL_OBSIDIAN 400行と同一データであり、独立証拠にならない
# ため照合④の対象から意図的に除外する。
EXCLUDED_FACTBOOK_FIELD_IDS = {
    "architecture_engineers_1st_class",
    "architecture_engineers_1st_class_training",
}


def build_corroboration_report(root: Path) -> Dict[str, Any]:
    """P1証拠照合レポートを構築し、data/reports/ 配下に書き出す。読み取り専用。"""
    normalized_rows = _read_normalized_long(root)
    all_cells = _cell_keys(normalized_rows)

    records: List[Dict[str, Any]] = []

    # 照合①
    records.extend(corroborate_same_cell(normalized_rows))

    # 照合②
    records.extend(_run_next_year_prior_check(root, normalized_rows))

    # 照合③
    validation_results = _read_validation_results(root)
    records.extend(corroborate_validation_rules(validation_results))

    # 照合④
    records.extend(_run_factbook_check(root, normalized_rows))

    by_cell = summarize_cells(records, all_cells=all_cells)

    cell_rows = _build_cell_rows(normalized_rows, by_cell)
    cells_path = root / REPORTS_DIR / CELLS_FILENAME
    write_table(cells_path, cell_rows)

    summary = _build_summary(normalized_rows, by_cell, validation_results)
    summary_json_path = root / REPORTS_DIR / SUMMARY_JSON_FILENAME
    ensure_parent(summary_json_path)
    summary_json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    summary_md_path = root / REPORTS_DIR / SUMMARY_MD_FILENAME
    summary_md_path.write_text(_render_summary_markdown(summary), encoding="utf-8")

    return {
        "cells_path": str(cells_path),
        "summary_json_path": str(summary_json_path),
        "summary_md_path": str(summary_md_path),
        "summary": summary,
    }


def read_summary(root: Path) -> Dict[str, Any]:
    """GET APIから使う軽量読み出し。未生成なら status: not_built を返す。"""
    summary_path = root / REPORTS_DIR / SUMMARY_JSON_FILENAME
    if not summary_path.exists():
        return {"status": "not_built"}
    return json.loads(summary_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# データ読み込みヘルパ
# ---------------------------------------------------------------------------

def _read_normalized_long(root: Path) -> List[Dict[str, Any]]:
    path = prefer_existing_table(root / NORMALIZED_LONG_PATH)
    if not path.exists():
        return []
    rows = read_table(path)
    out: List[Dict[str, Any]] = []
    for row in rows:
        # parquet読み込みでは欠損値が float('nan') になりうるため、
        # 単純な `in (None, "")` 判定では漏れる（NaNはどの値とも非等価）。
        if _as_float_or_none(row.get("value_normalized")) is None:
            continue
        out.append(row)
    return out


def _read_validation_results(root: Path) -> List[Dict[str, Any]]:
    path = prefer_existing_table(root / VALIDATION_RESULTS_PATH)
    if not path.exists():
        return []
    return read_table(path)


def _cell_keys(rows: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    seen = set()
    for row in rows:
        company_year_id = str(row.get("company_year_id") or "")
        field_id = str(row.get("field_id") or "")
        if company_year_id and field_id:
            seen.add((company_year_id, field_id))
    return sorted(seen)


# ---------------------------------------------------------------------------
# 照合②: xbrl_facts self-join
# ---------------------------------------------------------------------------

def _run_next_year_prior_check(root: Path, normalized_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    db_path = root / EDINET_DB_PATH
    if not db_path.exists():
        return []

    master_rows = _read_company_year_master(root)
    if not master_rows:
        return []

    valid_company_year_ids = {str(r.get("company_year_id")) for r in master_rows if r.get("company_year_id")}
    transition_flags = {
        str(r.get("company_year_id")): _to_int(r.get("transition_year_flag"))
        for r in master_rows
        if r.get("company_year_id")
    }
    next_lookup: Dict[Tuple[str, int], str] = {}
    for r in master_rows:
        company_year_id = r.get("company_year_id")
        operating_company_id = r.get("operating_company_id")
        fiscal_year = r.get("fiscal_year")
        if not company_year_id or not operating_company_id or fiscal_year in (None, ""):
            continue
        try:
            fiscal_year_int = int(fiscal_year)
        except (TypeError, ValueError):
            continue
        next_lookup[(str(operating_company_id), fiscal_year_int)] = str(company_year_id)

    # field_id -> タグ候補[] の対応表（xbrl_tag_candidates 由来。部分文字列マッチ用）。
    field_tag_candidates = _field_tag_candidates(root)
    if not field_tag_candidates:
        return []
    all_tags = sorted({tag for tags in field_tag_candidates.values() for tag in tags})

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row

        # element_id にタグ候補文字列が含まれる行のみ抽出（LIKE OR）
        like_clause = " or ".join(["element_id like ?"] * len(all_tags))
        current_facts = [
            dict(row)
            for row in conn.execute(
                f"""
                select company_year_id, operating_company_id, fiscal_year, element_id, context_id,
                       consolidation_scope, period_or_instant, value
                from xbrl_facts
                where relative_year in ('当期', '当期末')
                  and context_id like 'CurrentYear%'
                  and ({like_clause})
                """,
                [f"%{tag}%" for tag in all_tags],
            )
        ]
        if not current_facts:
            return []

        # 実際に出現したelement_id -> field_id[] を、タグ部分一致で解決する
        element_to_field: Dict[str, List[str]] = defaultdict(list)
        observed_elements = {str(row["element_id"]) for row in current_facts}
        for element_id in observed_elements:
            for field_id, tags in field_tag_candidates.items():
                if any(tag in element_id for tag in tags):
                    element_to_field[element_id].append(field_id)

        prior_facts_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        # 翌年度側として参照されうる company_year_id を先に絞り込む
        candidate_next_ids = {
            next_lookup.get((str(row.get("operating_company_id")), _safe_year(row.get("fiscal_year")) + 1))
            for row in current_facts
        }
        candidate_next_ids.discard(None)
        if not candidate_next_ids:
            return []

        placeholders = ",".join(["?"] * len(candidate_next_ids))
        for prior_row in conn.execute(
            f"""
            select company_year_id, element_id, context_id, consolidation_scope, period_or_instant, value
            from xbrl_facts
            where relative_year in ('前期', '前期末')
              and context_id like 'Prior1Year%'
              and company_year_id in ({placeholders})
            """,
            list(candidate_next_ids),
        ):
            key = (str(prior_row["company_year_id"]), str(prior_row["element_id"]))
            prior_facts_by_key[key].append(dict(prior_row))
    finally:
        conn.close()

    raw_records = corroborate_next_year_prior(
        current_facts=current_facts,
        prior_facts_by_key=prior_facts_by_key,
        next_company_year_lookup=next_lookup,
        transition_flags=transition_flags,
        valid_company_year_ids=valid_company_year_ids,
    )

    # element_id (check_ref) -> field_id を補完。1つのelement_idが複数field_idに
    # 対応しうる場合はそれぞれにレコードを複製する。
    resolved: List[Dict[str, Any]] = []
    normalized_cells = _cell_keys(normalized_rows)
    normalized_cell_set = set(normalized_cells)
    for record in raw_records:
        element_id = record["check_ref"]
        field_ids = element_to_field.get(element_id, [])
        for field_id in field_ids:
            key = (record["company_year_id"], field_id)
            if key not in normalized_cell_set:
                continue
            new_record = dict(record)
            new_record["field_id"] = field_id
            resolved.append(new_record)
    return resolved


def _read_company_year_master(root: Path) -> List[Dict[str, Any]]:
    path = root / COMPANY_YEAR_MASTER_PATH
    if not path.exists():
        return []
    return read_table(path)


def _field_tag_candidates(root: Path) -> Dict[str, List[str]]:
    """field_definition.csv から field_id -> [xbrl_tag_candidates] を取り出す。

    xbrl_tag_candidates はセミコロン区切りの部分文字列候補（element_id に対する
    部分一致で使う。例: タグ候補 'OperatingIncome' は element_id
    'jppfs_cor:OperatingIncome' にマッチする）。
    """
    field_def_path = prefer_existing_table(root / "config" / "field_definition.csv")
    if not field_def_path.exists():
        return {}
    rows = read_table(field_def_path)

    field_tags: Dict[str, List[str]] = {}
    for row in rows:
        field_id = row.get("field_id")
        tag_str = row.get("xbrl_tag_candidates") or ""
        if not field_id or not tag_str:
            continue
        tags = [tag.strip() for tag in str(tag_str).split(";") if tag.strip()]
        if tags:
            field_tags[str(field_id)] = tags
    return field_tags


def _safe_year(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -999999


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 照合④: 外部正本マート
# ---------------------------------------------------------------------------

def _run_factbook_check(root: Path, normalized_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    factbook_path = root / FACTBOOK_ORDERS_PATH
    if not factbook_path.exists():
        return []
    factbook_rows = read_table(factbook_path)

    cell_lookup: Dict[Tuple[str, str], float] = {}
    for row in normalized_rows:
        field_id = str(row.get("field_id") or "")
        if field_id in EXCLUDED_FACTBOOK_FIELD_IDS:
            continue
        company_year_id = str(row.get("company_year_id") or "")
        value = _as_float_or_none(row.get("value_normalized"))
        if not company_year_id or not field_id or value is None:
            continue
        cell_lookup[(company_year_id, field_id)] = value

    return corroborate_factbook(
        cell_lookup=cell_lookup,
        factbook_rows=factbook_rows,
        field_map=FACTBOOK_ORDERS_FIELD_MAP,
        check_ref=FACTBOOK_ORDERS_CHECK_REF,
    )


# ---------------------------------------------------------------------------
# 出力整形
# ---------------------------------------------------------------------------

def _build_cell_rows(
    normalized_rows: List[Dict[str, Any]],
    by_cell: Dict[Tuple[str, str], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    # セルの代表値・review_required・extraction_method一覧を拾う
    cell_meta: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in normalized_rows:
        company_year_id = str(row.get("company_year_id") or "")
        field_id = str(row.get("field_id") or "")
        if not company_year_id or not field_id:
            continue
        key = (company_year_id, field_id)
        meta = cell_meta.setdefault(
            key,
            {
                "value_normalized": row.get("value_normalized"),
                "unit_normalized": row.get("unit_normalized"),
                "extraction_methods": set(),
                "review_required": False,
                "validation_status": row.get("validation_status"),
            },
        )
        meta["extraction_methods"].add(str(row.get("extraction_method") or ""))
        if _truthy(row.get("review_required")):
            meta["review_required"] = True

    out: List[Dict[str, Any]] = []
    for key, entry in sorted(by_cell.items()):
        company_year_id, field_id = key
        meta = cell_meta.get(key, {})
        corroboration_count = entry["corroboration_count"]
        conflict_count = entry["conflict_count"]
        resolution_hint = _resolution_hint(corroboration_count, conflict_count)
        refs = sorted({f"{c['check_kind']}:{c['check_ref']}" for c in entry["corroborations"]})
        out.append(
            {
                "company_year_id": company_year_id,
                "field_id": field_id,
                "value_normalized": meta.get("value_normalized"),
                "unit_normalized": meta.get("unit_normalized"),
                "extraction_methods": ";".join(sorted(m for m in meta.get("extraction_methods", set()) if m)),
                "review_required": meta.get("review_required", False),
                "validation_status": meta.get("validation_status"),
                "corroboration_count": corroboration_count,
                "conflict_count": conflict_count,
                "restatement_suspected_count": entry.get("restatement_suspected_count", 0),
                "resolution_hint": resolution_hint,
                "corroboration_refs": ";".join(refs),
            }
        )
    return out


def _resolution_hint(corroboration_count: int, conflict_count: int) -> str:
    if conflict_count > 0:
        return "conflicted"
    if corroboration_count >= 2:
        return "corroborated_2plus"
    if corroboration_count == 1:
        return "corroborated_1"
    return "corroborated_0"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _build_summary(
    normalized_rows: List[Dict[str, Any]],
    by_cell: Dict[Tuple[str, str], Dict[str, Any]],
    validation_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    total_cells = len(by_cell)
    hint_counts: Dict[str, int] = defaultdict(int)
    auto_accepted_zero = 0

    cell_meta_review: Dict[Tuple[str, str], bool] = {}
    for row in normalized_rows:
        company_year_id = str(row.get("company_year_id") or "")
        field_id = str(row.get("field_id") or "")
        if not company_year_id or not field_id:
            continue
        key = (company_year_id, field_id)
        if _truthy(row.get("review_required")):
            cell_meta_review[key] = True
        else:
            cell_meta_review.setdefault(key, False)

    for key, entry in by_cell.items():
        hint = _resolution_hint(entry["corroboration_count"], entry["conflict_count"])
        hint_counts[hint] += 1
        review_required = cell_meta_review.get(key, False)
        if not review_required and entry["corroboration_count"] == 0 and entry["conflict_count"] == 0:
            auto_accepted_zero += 1

    extraction_method_counts: Dict[str, int] = defaultdict(int)
    for row in normalized_rows:
        method = str(row.get("extraction_method") or "")
        if method:
            extraction_method_counts[method] += 1

    rule_id_status_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for result in validation_results:
        rule_id = str(result.get("rule_id") or "")
        status = str(result.get("status") or "")
        if rule_id and status:
            rule_id_status_counts[rule_id][status] += 1

    return {
        "cells_total": total_cells,
        "corroborated_2plus": hint_counts.get("corroborated_2plus", 0),
        "corroborated_1": hint_counts.get("corroborated_1", 0),
        "corroborated_0": hint_counts.get("corroborated_0", 0),
        "conflicts": hint_counts.get("conflicted", 0),
        "auto_accepted_with_zero_corroboration": auto_accepted_zero,
        "extraction_method_counts": dict(extraction_method_counts),
        "validation_rule_status_counts": {k: dict(v) for k, v in rule_id_status_counts.items()},
        "notes": [
            "照合④(factbook)は company_id=SHIMIZU（清水建設）限定。development_other カテゴリは対応field_idが無いため突合対象外。",
            "architecture_engineers_1st_class / architecture_engineers_1st_class_training は "
            "manual_technicians マートが normalized_validated_long 内の MANUAL_OBSIDIAN 400行と同一データのため、"
            "独立証拠として照合④から除外している。",
            "粗利恒等式(gross_profit_identity_standalone)を validation_rules.yml に追加済み。"
            "反映するには本レポート生成前に `python -m yuho_auto_extract validate` を再実行して "
            "validation_results.parquet を更新する必要がある（本コマンド自体はvalidateを呼ばない）。",
        ],
    }


def _render_summary_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        "# 証拠照合レポート サマリ (P1)",
        "",
        f"- 総セル数: {summary.get('cells_total', 0)}",
        f"- 照合≥2件（自動確定候補）: {summary.get('corroborated_2plus', 0)}",
        f"- 照合=1件: {summary.get('corroborated_1', 0)}",
        f"- 照合=0件: {summary.get('corroborated_0', 0)}",
        f"- 矛盾（conflict）: {summary.get('conflicts', 0)}",
        f"- auto_accepted かつ照合0件: {summary.get('auto_accepted_with_zero_corroboration', 0)}",
        "",
        "## extraction_method 別件数",
        "",
    ]
    for method, count in sorted(summary.get("extraction_method_counts", {}).items()):
        lines.append(f"- {method}: {count}")

    lines.append("")
    lines.append("## validation_results rule_id別 status内訳")
    lines.append("")
    for rule_id, statuses in sorted(summary.get("validation_rule_status_counts", {}).items()):
        parts = ", ".join(f"{status}={count}" for status, count in sorted(statuses.items()))
        lines.append(f"- {rule_id}: {parts}")

    lines.append("")
    lines.append("## 注意事項")
    lines.append("")
    for note in summary.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"
