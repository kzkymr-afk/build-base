"""BuildBase S2: 主要項目カバレッジ可視化。

目的:
  「主要項目」（config/core_fields.yml で管理）について、会社×年度の
  充足状況をマトリクスとして返す。ホーム画面の「主要項目の充足マップ」
  パネルが本モジュールの build_core_coverage_matrix を叩く。

対象データ（annualのみ）:
  - config/company_year_master.csv の period_type == "annual" の行を
    会社年度の母集団とする（プローブ実測: 205行中204行がannual）。
  - data/final/final_master_long.csv の value_normalized（無ければ value）
    が空でない行を「充足」とみなす。

除外の扱い:
  config/company_field_exclusions.csv に (company_id, field_id) の除外
  登録がある場合、その会社×年度は「対象外 (excluded)」として扱い、
  「空白 (blank)」とは区別する。start_year/end_year が空の場合は
  全期間が対象、値が入っている場合はその年度範囲のみを除外とする。

自動回復可能フラグ（S1a連携）:
  data/reports/source_inference_dry_run.json が存在し、かつ対象 field_id が
  そのレポートの対象項目（受注3項目）に含まれる場合、
  services.source_inference.estimate_recovery を呼んで会社年度単位の
  high_confidence 分類を取得し、空白セルに recoverable フラグを付与する。
  レポートファイルが無い場合、または対象外の field_id の場合は
  recoverable 判定をスキップする（このモジュールは読み取り専用）。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..io_utils import read_table, read_yaml

CORE_FIELDS_CONFIG_RELATIVE_PATH = Path("config") / "core_fields.yml"
COMPANY_YEAR_MASTER_RELATIVE_PATH = Path("config") / "company_year_master.csv"
COMPANY_FIELD_EXCLUSIONS_RELATIVE_PATH = Path("config") / "company_field_exclusions.csv"
FINAL_MASTER_LONG_RELATIVE_PATH = Path("data") / "final" / "final_master_long.csv"
FIELD_DEFINITION_RELATIVE_PATH = Path("config") / "field_definition.csv"
SOURCE_INFERENCE_DRY_RUN_RELATIVE_PATH = Path("data") / "reports" / "source_inference_dry_run.json"

_BLANKISH_STRINGS = {"", "nan", "NaN", "None", "null", "<NA>"}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return text not in _BLANKISH_STRINGS


def load_core_field_ids(root: Path) -> List[str]:
    """config/core_fields.yml から主要項目リストを読む。"""
    path = root / CORE_FIELDS_CONFIG_RELATIVE_PATH
    if not path.exists():
        return []
    data = read_yaml(path)
    fields = data.get("core_fields") or []
    return [str(f) for f in fields if f]


def _load_annual_company_years(root: Path) -> List[Dict[str, Any]]:
    path = root / COMPANY_YEAR_MASTER_RELATIVE_PATH
    if not path.exists():
        return []
    rows = read_table(path)
    return [row for row in rows if str(row.get("period_type") or "").strip() == "annual"]


def _load_field_name_map(root: Path) -> Dict[str, str]:
    path = root / FIELD_DEFINITION_RELATIVE_PATH
    if not path.exists():
        return {}
    rows = read_table(path)
    return {str(row.get("field_id") or ""): str(row.get("field_name_ja") or "") for row in rows}


def _load_exclusion_ranges(root: Path) -> Dict[Tuple[str, str], List[Tuple[Optional[int], Optional[int]]]]:
    """(company_id, field_id) -> [(start_year, end_year), ...] の除外年度範囲を返す。

    start_year/end_year が空の場合は全期間を意味する (None, None) として扱う。
    """
    path = root / COMPANY_FIELD_EXCLUSIONS_RELATIVE_PATH
    ranges: Dict[Tuple[str, str], List[Tuple[Optional[int], Optional[int]]]] = defaultdict(list)
    if not path.exists():
        return ranges
    for row in read_table(path):
        company_id = str(row.get("company_id") or "").strip()
        field_id = str(row.get("field_id") or "").strip()
        if not company_id or not field_id:
            continue
        start_raw = row.get("start_year")
        end_raw = row.get("end_year")
        start_year = int(str(start_raw)) if _has_value(start_raw) else None
        end_year = int(str(end_raw)) if _has_value(end_raw) else None
        ranges[(company_id, field_id)].append((start_year, end_year))
    return ranges


def _is_excluded(
    ranges: Dict[Tuple[str, str], List[Tuple[Optional[int], Optional[int]]]],
    company_id: str,
    field_id: str,
    fiscal_year: int,
) -> bool:
    for start_year, end_year in ranges.get((company_id, field_id), []):
        if start_year is None and end_year is None:
            return True
        lower_ok = start_year is None or fiscal_year >= start_year
        upper_ok = end_year is None or fiscal_year <= end_year
        if lower_ok and upper_ok:
            return True
    return False


def _load_filled_keys(root: Path, field_ids: Sequence[str]) -> Set[Tuple[str, str]]:
    """(company_year_id, field_id) の充足済みキー集合を返す。"""
    path = root / FINAL_MASTER_LONG_RELATIVE_PATH
    if not path.exists():
        return set()
    field_id_set = set(field_ids)
    filled: Set[Tuple[str, str]] = set()
    for row in read_table(path):
        field_id = str(row.get("field_id") or "")
        if field_id not in field_id_set:
            continue
        raw_value = row.get("value_normalized")
        if not _has_value(raw_value):
            raw_value = row.get("value")
        if not _has_value(raw_value):
            continue
        filled.add((str(row.get("company_year_id") or ""), field_id))
    return filled


def _load_recoverable_keys(root: Path, field_ids: Sequence[str]) -> Set[Tuple[str, str]]:
    """source_inference_dry_run.json があり対象fieldがカバー範囲内であれば、
    company_year × field 単位の high_confidence 判定を実測して返す。

    レポートファイルが存在しない場合、または対象fieldがレポートの
    対象範囲（受注3項目）に含まれない場合は空集合を返す（読み取り専用・
    副作用なし。estimate_recovery自体もDB読み取り専用）。
    """
    import json as _json

    report_path = root / SOURCE_INFERENCE_DRY_RUN_RELATIVE_PATH
    if not report_path.exists():
        return set()
    try:
        report = _json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    report_field_ids = set(report.get("field_ids") or [])
    target_field_ids = [f for f in field_ids if f in report_field_ids]
    if not target_field_ids:
        return set()

    try:
        from . import source_inference
    except ImportError:
        return set()

    try:
        recovery = source_inference.estimate_recovery(root, field_ids=target_field_ids)
    except Exception:
        # dry-run照会はベストエフォート。失敗してもカバレッジ本体は返す。
        return set()

    classification = recovery.get("classification") or {}
    recoverable: Set[Tuple[str, str]] = set()
    for company_year_id, fields in classification.items():
        for field_id, status in fields.items():
            if status == "high_confidence":
                recoverable.add((str(company_year_id), str(field_id)))
    return recoverable


def build_core_coverage_matrix(root: Path, field_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """主要項目×会社×年度の充足マトリクスを構築する（読み取り専用）。

    戻り値:
      {
        "fields": [{"field_id", "field_name_ja"}, ...],
        "companies": [company_id, ...],
        "matrix": {
          field_id: {
            company_id: {
              "filled_years": int,
              "total_years": int,
              "blank_years": [fiscal_year, ...],
              "excluded_years": [fiscal_year, ...],
              "recoverable_years": [fiscal_year, ...],
            }
          }
        },
        "summary": {
          field_id: {"filled": int, "total": int, "rate": float, "recoverable": int}
        },
      }
    """
    root = Path(root)
    resolved_field_ids = list(field_ids) if field_ids is not None else load_core_field_ids(root)
    field_name_map = _load_field_name_map(root)
    annual_company_years = _load_annual_company_years(root)
    exclusion_ranges = _load_exclusion_ranges(root)
    filled_keys = _load_filled_keys(root, resolved_field_ids)
    recoverable_keys = _load_recoverable_keys(root, resolved_field_ids)

    companies: List[str] = sorted({str(row.get("operating_company_id") or "") for row in annual_company_years if row.get("operating_company_id")})

    matrix: Dict[str, Dict[str, Dict[str, Any]]] = {}
    summary: Dict[str, Dict[str, Any]] = {}

    for field_id in resolved_field_ids:
        field_matrix: Dict[str, Dict[str, Any]] = {}
        total_filled = 0
        total_applicable = 0
        total_recoverable = 0
        for company_id in companies:
            company_years = [row for row in annual_company_years if str(row.get("operating_company_id") or "") == company_id]
            filled_years: List[int] = []
            blank_years: List[int] = []
            excluded_years: List[int] = []
            recoverable_years: List[int] = []
            for row in company_years:
                company_year_id = str(row.get("company_year_id") or "")
                fiscal_year_raw = row.get("fiscal_year")
                try:
                    fiscal_year = int(str(fiscal_year_raw))
                except (TypeError, ValueError):
                    continue
                if _is_excluded(exclusion_ranges, company_id, field_id, fiscal_year):
                    excluded_years.append(fiscal_year)
                    continue
                if (company_year_id, field_id) in filled_keys:
                    filled_years.append(fiscal_year)
                else:
                    blank_years.append(fiscal_year)
                    if (company_year_id, field_id) in recoverable_keys:
                        recoverable_years.append(fiscal_year)
            field_matrix[company_id] = {
                "filled_years": len(filled_years),
                "total_years": len(filled_years) + len(blank_years),
                "blank_years": sorted(blank_years),
                "excluded_years": sorted(excluded_years),
                "recoverable_years": sorted(recoverable_years),
            }
            total_filled += len(filled_years)
            total_applicable += len(filled_years) + len(blank_years)
            total_recoverable += len(recoverable_years)
        matrix[field_id] = field_matrix
        summary[field_id] = {
            "filled": total_filled,
            "total": total_applicable,
            "rate": round(total_filled / total_applicable, 4) if total_applicable else 0.0,
            "recoverable": total_recoverable,
        }

    return {
        "fields": [{"field_id": fid, "field_name_ja": field_name_map.get(fid, "")} for fid in resolved_field_ids],
        "companies": companies,
        "matrix": matrix,
        "summary": summary,
    }
