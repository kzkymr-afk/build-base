from __future__ import annotations

from typing import Any, Dict


ORDER_KEYWORDS = ("受注", "受注高", "契約高", "orders")
COMPLETION_KEYWORDS = ("完工", "完成工事", "完成高", "売上", "revenue")
USE_KEYWORDS = ("用途", "建築", "事務所", "工場", "住宅", "商業", "医療", "物流", "倉庫", "教育")


def classify_table(table_text: str, source: Dict[str, Any]) -> Dict[str, str]:
    """Classify a factbook table boundary.

    R4 intentionally keeps value extraction deterministic. This function is the
    AI decision seam: callers may later route ambiguous tables through ai_runner,
    while the default implementation remains a zero-cost keyword classifier.
    """
    text = f"{table_text} {source.get('source_table_title', '')} {source.get('source_metric_id', '')}".lower()
    if not _has_any(text, USE_KEYWORDS):
        return {"accept": "false", "metric_id": "", "category_type": "", "reason": "no_use_category_keywords"}
    if _has_any(text, ORDER_KEYWORDS):
        return {"accept": "true", "metric_id": "building_orders_by_use", "category_type": "use", "reason": "order_keywords"}
    if _has_any(text, COMPLETION_KEYWORDS):
        return {"accept": "true", "metric_id": "completed_building_by_use", "category_type": "use", "reason": "completion_keywords"}
    metric_id = str(source.get("source_metric_id") or "")
    if metric_id in {"building_orders_by_use", "completed_building_by_use", "building_use_metrics"}:
        return {"accept": "true", "metric_id": metric_id, "category_type": str(source.get("category_type") or "use"), "reason": "source_metric_hint"}
    return {"accept": "false", "metric_id": "", "category_type": "", "reason": "no_metric_keywords"}


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
