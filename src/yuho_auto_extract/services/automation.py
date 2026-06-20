from __future__ import annotations

import calendar
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from yuho_auto_extract.io_utils import is_blankish, prefer_existing_table, read_table, read_yaml, write_table
from yuho_auto_extract.services.datasets import parse_run_report


DEFAULT_AUTOMATION_CONFIG: Dict[str, Any] = {
    "annual_refresh": {
        "enabled": True,
        "default_fiscal_year_end_month": 3,
        "filing_windows": [
            {
                "name": "march_end_annual",
                "fiscal_year_end_month": 3,
                "start_month_day": "06-01",
                "end_month_day": "08-15",
            }
        ],
        "review_gate": {
            "enabled": True,
            "max_active_review_items": 0,
            "max_saved_unapplied_reviews": 0,
            "require_final_outputs": True,
            "require_algorithm_audit": False,
        },
    },
    "roll_forward": {
        "enabled": True,
        "copy_latest_company_year_per_company": True,
        "reset_transition_year_flag": True,
        "clear_event_fields": True,
    },
}


def load_automation_config(root: Path) -> Dict[str, Any]:
    path = root / "config" / "automation.yml"
    cfg = deepcopy(DEFAULT_AUTOMATION_CONFIG)
    if path.exists():
        cfg = _deep_merge(cfg, read_yaml(path))
    return cfg


def automation_status(root: Path, fiscal_year: Optional[int] = None, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    cfg = load_automation_config(root)
    window = annual_window_status(cfg, today)
    target_fiscal_year = fiscal_year or window.get("target_fiscal_year")
    review_gate = review_gate_status(root, cfg)
    roll_forward = roll_forward_plan(root, int(target_fiscal_year)) if target_fiscal_year else _empty_roll_forward_plan()
    source_registry = source_registry_status(root)
    return {
        "config_path": str(root / "config" / "automation.yml"),
        "as_of": today.isoformat(),
        "enabled": bool(cfg.get("annual_refresh", {}).get("enabled", True)),
        "target_fiscal_year": target_fiscal_year,
        "annual_window": window,
        "review_gate": review_gate,
        "company_year_roll_forward": roll_forward,
        "sources": source_registry,
    }


def annual_window_status(cfg: Dict[str, Any], today: date) -> Dict[str, Any]:
    windows = cfg.get("annual_refresh", {}).get("filing_windows", []) or []
    candidates: List[Dict[str, Any]] = []
    for filing_year in range(today.year - 1, today.year + 3):
        for item in windows:
            start = _date_from_month_day(filing_year, str(item.get("start_month_day", "06-01")))
            end = _date_from_month_day(filing_year, str(item.get("end_month_day", "08-15")))
            if end < start:
                end = _date_from_month_day(filing_year + 1, str(item.get("end_month_day", "08-15")))
            target_fiscal_year = filing_year - 1
            candidates.append(
                {
                    "name": str(item.get("name") or f"window_{filing_year}"),
                    "fiscal_year_end_month": int(item.get("fiscal_year_end_month") or 3),
                    "target_fiscal_year": target_fiscal_year,
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                    "in_window": start <= today <= end,
                }
            )

    active = next((row for row in candidates if row["in_window"]), None)
    if active:
        return {
            **active,
            "next_window_start": "",
            "next_window_target_fiscal_year": None,
            "message": f"{active['target_fiscal_year']}年度の年次取得ウィンドウ内です。",
        }

    future = sorted((row for row in candidates if date.fromisoformat(row["window_start"]) > today), key=lambda row: row["window_start"])
    next_window = future[0] if future else None
    return {
        "name": "",
        "fiscal_year_end_month": cfg.get("annual_refresh", {}).get("default_fiscal_year_end_month", 3),
        "target_fiscal_year": None,
        "window_start": "",
        "window_end": "",
        "in_window": False,
        "next_window_start": next_window.get("window_start") if next_window else "",
        "next_window_target_fiscal_year": next_window.get("target_fiscal_year") if next_window else None,
        "message": "現在は年次取得ウィンドウ外です。",
    }


def review_gate_status(root: Path, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_automation_config(root)
    gate_cfg = cfg.get("annual_refresh", {}).get("review_gate", {}) or {}
    queue_rows = _read_optional(root / "data" / "review" / "review_queue.csv")
    resolved_rows = _read_optional(root / "data" / "review" / "review_resolved.csv")
    resolved = {
        (str(row.get("company_year_id", "")), str(row.get("field_id", ""))): row
        for row in resolved_rows
        if row.get("company_year_id") and row.get("field_id")
    }

    active = []
    saved_unapplied = []
    for row in queue_rows:
        key = (str(row.get("company_year_id", "")), str(row.get("field_id", "")))
        resolved_row = resolved.get(key)
        if not _is_resolved_done(resolved_row):
            active.append(row)
        if resolved_row and not _is_resolved_done(resolved_row):
            saved_unapplied.append(resolved_row)

    final_outputs = {
        "final_master_wide": (root / "data" / "final" / "final_master_wide.csv").exists(),
        "source_audit": (root / "data" / "final" / "source_audit.csv").exists(),
        "field_coverage": (root / "data" / "final" / "field_coverage.csv").exists(),
    }
    report = parse_run_report(root / "data" / "final" / "run_report.md")
    algorithm_audit_exists = (root / "data" / "algorithm_audit" / "manifest.json").exists()
    max_active = int(gate_cfg.get("max_active_review_items", 0))
    max_saved_unapplied = int(gate_cfg.get("max_saved_unapplied_reviews", 0))
    blocking: List[str] = []
    if bool(gate_cfg.get("enabled", True)) and len(active) > max_active:
        blocking.append(f"active_review_items={len(active)} exceeds {max_active}")
    if len(saved_unapplied) > max_saved_unapplied:
        blocking.append(f"saved_unapplied_reviews={len(saved_unapplied)} exceeds {max_saved_unapplied}")
    if bool(gate_cfg.get("require_final_outputs", True)):
        missing_outputs = [name for name, ok in final_outputs.items() if not ok]
        if missing_outputs:
            blocking.append(f"missing_final_outputs={','.join(missing_outputs)}")
    if bool(gate_cfg.get("require_algorithm_audit", False)) and not algorithm_audit_exists:
        blocking.append("algorithm_audit_missing")

    return {
        "ready": not blocking,
        "blocking_reasons": blocking,
        "active_review_items": len(active),
        "saved_unapplied_reviews": len(saved_unapplied),
        "review_queue_items": len(queue_rows),
        "resolved_reviews": len(resolved_rows),
        "final_outputs": final_outputs,
        "algorithm_audit_exists": algorithm_audit_exists,
        "run_report_summary": report.get("summary", {}),
    }


def roll_forward_plan(root: Path, fiscal_year: int) -> Dict[str, Any]:
    rows = _read_optional(root / "config" / "company_year_master.csv")
    existing_ids = {str(row.get("company_year_id", "")) for row in rows}
    latest = _latest_company_year_by_company(rows)
    planned = []
    existing = []
    for company_id, row in latest.items():
        next_id = f"{company_id}_{fiscal_year}"
        if next_id in existing_ids:
            existing.append(next_id)
        else:
            planned.append(_roll_forward_row(root, row, fiscal_year))
    return {
        "target_fiscal_year": fiscal_year,
        "existing_rows": len(existing),
        "planned_rows": len(planned),
        "existing_company_year_ids": existing,
        "planned_company_year_ids": [str(row.get("company_year_id", "")) for row in planned],
        "companies": sorted(latest.keys()),
    }


def roll_forward_company_years(root: Path, fiscal_year: int, dry_run: bool = False) -> Dict[str, Any]:
    rows = _read_optional(root / "config" / "company_year_master.csv")
    latest = _latest_company_year_by_company(rows)
    existing_ids = {str(row.get("company_year_id", "")) for row in rows}
    new_rows = [
        _roll_forward_row(root, row, fiscal_year)
        for company_id, row in latest.items()
        if f"{company_id}_{fiscal_year}" not in existing_ids
    ]
    result = {
        "target_fiscal_year": fiscal_year,
        "added_rows": len(new_rows),
        "existing_rows": len(latest) - len(new_rows),
        "company_year_ids": [str(row.get("company_year_id", "")) for row in new_rows],
        "dry_run": dry_run,
    }
    if dry_run or not new_rows:
        return result
    merged = rows + new_rows
    write_table(root / "config" / "company_year_master.csv", merged)
    write_table(root / "config" / "company_year_master.xlsx", merged)
    return result


def merge_table_by_key(root: Path, rel_path: str, new_rows: Sequence[Dict[str, Any]], key_fields: Sequence[str]) -> Dict[str, Any]:
    path = root / rel_path
    existing_path = prefer_existing_table(path)
    existing_rows = read_table(existing_path) if existing_path.exists() else []
    merged = merge_records_by_key(existing_rows, new_rows, key_fields)
    write_table(path, merged)
    return {
        "path": str(path),
        "existing_rows": len(existing_rows),
        "new_rows": len(new_rows),
        "merged_rows": len(merged),
    }


def merge_records_by_key(
    existing_rows: Iterable[Dict[str, Any]],
    new_rows: Iterable[Dict[str, Any]],
    key_fields: Sequence[str],
) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    order: List[Tuple[str, ...]] = []
    for row in list(existing_rows) + list(new_rows):
        key = _record_key(row, key_fields)
        if key not in merged:
            order.append(key)
        merged[key] = dict(row)
    return [merged[key] for key in order]


def source_registry_status(root: Path) -> Dict[str, Any]:
    path = root / "config" / "source_registry.yml"
    if not path.exists():
        return {"path": str(path), "total": 0, "enabled": 0, "planned": 0, "sources": []}
    cfg = read_yaml(path)
    sources = cfg.get("sources", []) or []
    return {
        "path": str(path),
        "total": len(sources),
        "enabled": sum(1 for row in sources if bool(row.get("enabled", False))),
        "planned": sum(1 for row in sources if str(row.get("status", "")) == "planned"),
        "sources": [
            {
                "id": row.get("id", ""),
                "name": row.get("name", ""),
                "status": row.get("status", ""),
                "enabled": bool(row.get("enabled", False)),
                "canonical_store": row.get("canonical_store", ""),
            }
            for row in sources
        ],
    }


def write_annual_refresh_summary(root: Path, summary: Dict[str, Any]) -> Path:
    out_dir = root / "data" / "automation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "annual_refresh_last.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    history_path = out_dir / "annual_refresh_runs.jsonl"
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _latest_company_year_by_company(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        company_id = str(row.get("operating_company_id", "")).strip()
        if not company_id:
            continue
        fiscal_year = _safe_int(row.get("fiscal_year"), -1)
        current_year = _safe_int(latest.get(company_id, {}).get("fiscal_year"), -1)
        if fiscal_year > current_year:
            latest[company_id] = dict(row)
    return latest


def _roll_forward_row(root: Path, row: Dict[str, Any], fiscal_year: int) -> Dict[str, Any]:
    company_id = str(row.get("operating_company_id", "")).strip()
    companies = {
        str(item.get("operating_company_id", "")): item
        for item in _read_optional(root / "config" / "company_master.csv")
        if item.get("operating_company_id")
    }
    company = companies.get(company_id, {})
    fiscal_month = int(company.get("fiscal_year_end_month") or row.get("fiscal_year_end_month") or 3)
    next_row = dict(row)
    next_row["company_year_id"] = f"{company_id}_{fiscal_year}"
    next_row["fiscal_year"] = str(fiscal_year)
    next_row["fiscal_year_end"] = _fiscal_year_end(fiscal_year, fiscal_month)
    next_row["transition_year_flag"] = "0"
    next_row["reorg_event_type"] = ""
    next_row["event_date"] = ""
    next_row["notes"] = _roll_forward_note(row)
    return next_row


def _roll_forward_note(row: Dict[str, Any]) -> str:
    source_id = row.get("company_year_id", "")
    source_note = str(row.get("notes", "") or "").strip()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    note = f"auto_roll_forward from {source_id} at {stamp}; reorg/listing changes must be checked manually"
    if source_note:
        return f"{source_note} / {note}"
    return note


def _fiscal_year_end(fiscal_year: int, fiscal_month: int) -> str:
    year = fiscal_year if fiscal_month == 12 else fiscal_year + 1
    day = calendar.monthrange(year, fiscal_month)[1]
    return f"{year:04d}-{fiscal_month:02d}-{day:02d}"


def _is_resolved_done(row: Optional[Dict[str, Any]]) -> bool:
    if not row:
        return False
    status = str(row.get("applied_status", "") or "").strip().lower()
    return status in {"applied", "rejected"}


def _record_key(row: Dict[str, Any], key_fields: Sequence[str]) -> Tuple[str, ...]:
    values = tuple(str(row.get(field, "") or "") for field in key_fields)
    if any(values):
        return values
    return (json.dumps(row, ensure_ascii=False, sort_keys=True),)


def _read_optional(path: Path) -> List[Dict[str, Any]]:
    actual = prefer_existing_table(path)
    return read_table(actual) if actual.exists() else []


def _date_from_month_day(year: int, value: str) -> date:
    month, day = value.split("-", 1)
    return date(year, int(month), int(day))


def _safe_int(value: Any, default: int = 0) -> int:
    if is_blankish(value):
        return default
    try:
        return int(float(str(value)))
    except ValueError:
        return default


def _empty_roll_forward_plan() -> Dict[str, Any]:
    return {
        "target_fiscal_year": None,
        "existing_rows": 0,
        "planned_rows": 0,
        "existing_company_year_ids": [],
        "planned_company_year_ids": [],
        "companies": [],
    }


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base
