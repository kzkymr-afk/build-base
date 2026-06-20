from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List


def build_analysis_dataset(wide_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [dict(row) for row in wide_rows]
    for row in rows:
        row["government_order_ratio"] = _ratio(row.get("building_orders_government"), row.get("building_orders_total"))
        row["backlog_to_orders_ratio"] = _ratio(row.get("backlog_building_next"), row.get("building_orders_total"))
        row["rd_to_orders_ratio"] = _ratio(row.get("rd_expense"), row.get("building_orders_total"))
        row["ad_to_orders_ratio"] = _ratio(row.get("advertising_expense"), row.get("building_orders_total"))
        row["entertainment_to_orders_ratio"] = _ratio(row.get("entertainment_expense"), row.get("building_orders_total"))
    _attach_lag_columns(rows)
    return rows


def _attach_lag_columns(rows: List[Dict[str, Any]]) -> None:
    by_company: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_company[str(row.get("operating_company_id", ""))].append(row)
    for company_rows in by_company.values():
        company_rows.sort(key=lambda row: int(row.get("fiscal_year") or 0))
        for idx, row in enumerate(company_rows):
            next_row = company_rows[idx + 1] if idx + 1 < len(company_rows) else None
            next_orders = _float(next_row.get("building_orders_total")) if next_row else None
            current_orders = _float(row.get("building_orders_total"))
            row["next_year_building_orders"] = next_orders
            row["next_year_order_growth"] = _ratio(next_orders - current_orders, current_orders) if next_orders is not None and current_orders is not None else None


def _ratio(numerator: Any, denominator: Any) -> Any:
    n = _float(numerator)
    d = _float(denominator)
    if n is None or d in (None, 0):
        return None
    return n / d


def _float(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
