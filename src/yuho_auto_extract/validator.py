from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple


def validate_records(rows: Iterable[Dict[str, Any]], rules_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    records = list(rows)
    results: List[Dict[str, Any]] = []
    rules = rules_config.get("rules", {})
    for rule_id, rule in rules.items():
        if "right_sum" in rule:
            results.extend(_validate_sum(records, rule_id, rule))
        elif "upper_bound" in rule:
            results.extend(_validate_upper_bound(records, rule_id, rule))
        elif rule_id == "yoy_anomaly":
            results.extend(_validate_yoy(records, rule_id, rule))
        elif rule_id == "backlog_equation":
            results.extend(_validate_backlog(records, rule_id, rule))
    return results


def attach_validation_status(rows: Iterable[Dict[str, Any]], validation_results: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_cell: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for result in validation_results:
        company_year_id = str(result.get("company_year_id", ""))
        for field_id in result.get("field_ids", []):
            by_cell[(company_year_id, str(field_id))].append(result)

    out: List[Dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        cell_results = by_cell.get((str(row.get("company_year_id", "")), str(row.get("field_id", ""))), [])
        statuses = [result.get("status") for result in cell_results]
        if "fail" in statuses:
            copied["validation_status"] = "fail"
            copied["review_required"] = True
        elif "warn" in statuses:
            copied["validation_status"] = "warn"
            copied["review_required"] = True
        elif "pass" in statuses:
            copied["validation_status"] = "pass"
        else:
            copied.setdefault("validation_status", "not_applicable")
        if copied.get("validation_status") in {"fail", "warn"}:
            reason = copied.get("review_reason", "")
            extra = "validation_" + str(copied["validation_status"])
            copied["review_reason"] = ";".join([x for x in [reason, extra] if x])
        out.append(copied)
    return out


def _validate_sum(records: List[Dict[str, Any]], rule_id: str, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_year = _index_by_company_year(records)
    results: List[Dict[str, Any]] = []
    left_field = rule["left"]
    right_fields = list(rule.get("right_sum", []))
    for company_year_id, fields in by_year.items():
        left = _value(fields.get(left_field))
        rights = [_value(fields.get(field)) for field in right_fields]
        missing = [field for field, value in zip(right_fields, rights) if value is None]
        if left is None or missing:
            status = "not_applicable"
            diff = None
        else:
            right_sum = sum(value for value in rights if value is not None)
            diff = left - right_sum
            status = "pass" if _within_tolerance(diff, left, rule) else "fail"
        results.append(
            {
                "company_year_id": company_year_id,
                "rule_id": rule_id,
                "status": status,
                "left": left_field,
                "right_fields": right_fields,
                "field_ids": [left_field] + right_fields,
                "difference": diff,
                "missing_fields": missing if left is not None else [left_field] + missing,
                "description": rule.get("description", ""),
            }
        )
    return results


def _validate_upper_bound(records: List[Dict[str, Any]], rule_id: str, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_year = _index_by_company_year(records)
    results: List[Dict[str, Any]] = []
    upper_field = rule["upper_bound"]
    for company_year_id, fields in by_year.items():
        upper_value = _value(fields.get(upper_field))
        for field in rule.get("fields", []):
            value = _value(fields.get(field))
            if upper_value is None or value is None:
                status = "not_applicable"
            else:
                status = "pass" if value <= upper_value else "fail"
            results.append(
                {
                    "company_year_id": company_year_id,
                    "rule_id": rule_id,
                    "status": status,
                    "field_ids": [field, upper_field],
                    "value": value,
                    "upper_bound": upper_value,
                    "description": rule.get("description", ""),
                }
            )
    return results


def _validate_yoy(records: List[Dict[str, Any]], rule_id: str, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    threshold = float(rule.get("threshold_pct", 0.5))
    fields = set(rule.get("fields_apply_to", []))
    by_company_field: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in records:
        field_id = str(row.get("field_id", ""))
        if field_id not in fields:
            continue
        by_company_field[(str(row.get("operating_company_id", "")), field_id)].append(row)
    results: List[Dict[str, Any]] = []
    for (_company, field_id), field_rows in by_company_field.items():
        field_rows.sort(key=lambda row: int(row.get("fiscal_year") or 0))
        prev: Optional[Dict[str, Any]] = None
        for row in field_rows:
            value = _value(row)
            prev_value = _value(prev)
            if prev is None or value is None or prev_value in (None, 0):
                status = "not_applicable"
                pct = None
            else:
                pct = value / prev_value - 1
                status = "warn" if abs(pct) > threshold else "pass"
            results.append(
                {
                    "company_year_id": row.get("company_year_id"),
                    "rule_id": rule_id,
                    "status": status,
                    "field_ids": [field_id],
                    "difference_pct": pct,
                    "description": rule.get("description", ""),
                }
            )
            prev = row
    return results


def check_backlog_equation_single(
    prev_backlog: Optional[float],
    orders: Optional[float],
    completed: Optional[float],
    next_backlog: Optional[float],
    tolerance_rule: Dict[str, Any],
) -> Tuple[str, Optional[float]]:
    """受注工事高恒等式（前期繰越+当期受注-当期完成=次期繰越）の単一セル検算。

    S1a/S1bの逆引き・学習パターン適用が、1セル単位で「推定値が確からしいか」
    を検算するために使う（S1a′で`_validate_backlog`から切り出し）。

    戻り値: (status, difference)。status は "pass" / "fail" / "not_applicable"。
    いずれかがNoneなら "not_applicable" で diff=None。
    """
    if None in (prev_backlog, orders, completed, next_backlog):
        return "not_applicable", None
    expected = prev_backlog + orders - completed
    diff = expected - next_backlog
    status = "pass" if _within_tolerance(diff, next_backlog, tolerance_rule) else "fail"
    return status, diff


def _validate_backlog(records: List[Dict[str, Any]], rule_id: str, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_company: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in records:
        by_company[str(row.get("operating_company_id", ""))].append(row)
    results: List[Dict[str, Any]] = []
    for _company, company_rows in by_company.items():
        by_year = _index_by_company_year(company_rows)
        sorted_years = sorted(by_year.items(), key=lambda item: int(next(iter(item[1].values())).get("fiscal_year") or 0))
        prev_fields: Optional[Dict[str, Dict[str, Any]]] = None
        for company_year_id, fields in sorted_years:
            prev_backlog = _value(prev_fields.get("backlog_building_next")) if prev_fields else None
            orders = _value(fields.get("building_orders_total"))
            completed = _value(fields.get("completed_building"))
            next_backlog = _value(fields.get("backlog_building_next"))
            status, diff = check_backlog_equation_single(prev_backlog, orders, completed, next_backlog, rule)
            results.append(
                {
                    "company_year_id": company_year_id,
                    "rule_id": rule_id,
                    "status": status,
                    "field_ids": ["backlog_building_next", "building_orders_total", "completed_building"],
                    "difference": diff,
                    "description": rule.get("description", ""),
                }
            )
            prev_fields = fields
    return results


def _index_by_company_year(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    by_year: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for row in records:
        company_year_id = str(row.get("company_year_id", ""))
        field_id = str(row.get("field_id", ""))
        if company_year_id and field_id:
            by_year[company_year_id][field_id] = row
    return by_year


def _value(row: Optional[Dict[str, Any]]) -> Optional[float]:
    if not row:
        return None
    value = row.get("value_normalized", row.get("value"))
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _within_tolerance(diff: Optional[float], base: Optional[float], rule: Dict[str, Any]) -> bool:
    if diff is None or base is None:
        return False
    abs_diff = abs(diff)
    tolerance_abs = float(rule.get("tolerance_abs", 0))
    tolerance_pct = float(rule.get("tolerance_pct", 0))
    return abs_diff <= max(tolerance_abs, abs(base) * tolerance_pct)
