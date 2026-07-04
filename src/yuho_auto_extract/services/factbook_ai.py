from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..ai_runner import AiCallResult, AiRunner


ORDER_KEYWORDS = ("受注", "受注高", "契約高", "orders")
COMPLETION_KEYWORDS = ("完工", "完成工事", "完成高", "売上", "revenue")
USE_KEYWORDS = ("用途", "建築", "事務所", "工場", "住宅", "商業", "医療", "物流", "倉庫", "教育")


def classify_table(
    table_text: str,
    source: Dict[str, Any],
    *,
    runner: Optional[AiRunner] = None,
    model: str = "",
    tier: str = "bulk",
    timeout_seconds: Optional[int] = None,
    input_ref: str = "",
    call_results: Optional[List[AiCallResult]] = None,
) -> Dict[str, str]:
    """Classify a factbook table boundary.

    R4 intentionally keeps value extraction deterministic. This function is the
    AI decision seam: callers may later route ambiguous tables through ai_runner,
    while the default implementation remains a zero-cost keyword classifier.
    """
    keyword_decision = _keyword_classify_table(table_text, source)
    if keyword_decision.get("accept") == "true" or runner is None:
        return keyword_decision
    if not model:
        return keyword_decision

    call_result = runner.call(
        prompt=_classification_prompt(table_text, source),
        model=model,
        purpose="factbook_table_classification",
        tier=tier,
        input_ref=input_ref or str(source.get("source_dataset_id") or source.get("id") or "factbook_table"),
        timeout_seconds=timeout_seconds,
    )
    if call_results is not None:
        call_results.append(call_result)
    parsed = call_result.parsed_result
    if isinstance(parsed, list) and parsed:
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        return keyword_decision
    return _normalize_ai_decision(parsed, fallback=keyword_decision)


def _keyword_classify_table(table_text: str, source: Dict[str, Any]) -> Dict[str, str]:
    text = f"{table_text} {source.get('source_table_title', '')} {source.get('title', '')}".lower()
    if not _has_any(text, USE_KEYWORDS):
        return {"accept": "false", "metric_id": "", "category_type": "", "reason": "no_use_category_keywords"}
    if _has_any(text, ORDER_KEYWORDS):
        return {"accept": "true", "metric_id": "building_orders_by_use", "category_type": "use", "reason": "order_keywords"}
    if _has_any(text, COMPLETION_KEYWORDS):
        return {"accept": "true", "metric_id": "completed_building_by_use", "category_type": "use", "reason": "completion_keywords"}
    metric_id = str(source.get("source_metric_id") or "")
    if metric_id in {"building_orders_by_use", "completed_building_by_use", "building_use_metrics"}:
        return {"accept": "false", "metric_id": "", "category_type": "", "reason": "source_metric_hint_requires_table_keywords_or_ai"}
    return {"accept": "false", "metric_id": "", "category_type": "", "reason": "no_metric_keywords"}


def _classification_prompt(table_text: str, source: Dict[str, Any]) -> str:
    payload = {
        "source": {
            "company_id": source.get("company_id"),
            "company_name": source.get("company_name"),
            "source_dataset_id": source.get("source_dataset_id") or source.get("id"),
            "source_doc_type": source.get("source_doc_type"),
            "source_metric_id": source.get("source_metric_id"),
            "target_metric_ids": source.get("target_metric_ids"),
            "title": source.get("title"),
        },
        "table_text": table_text[:6000],
    }
    return "\n".join(
        [
            "# BuildBase factbook table classification",
            "You classify whether a mechanically extracted construction-company factbook table is useful.",
            "Do not extract or invent values. Return strict JSON only.",
            "",
            "Accepted metric_id values:",
            "- building_orders_by_use",
            "- completed_building_by_use",
            "- building_orders_by_business_scope",
            "- completed_building_by_business_scope",
            "",
            'Return shape: {"accept":"true|false","metric_id":"...","category_type":"use|business_scope","reason":"short"}',
            "",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ]
    )


def _normalize_ai_decision(decision: Dict[str, Any], *, fallback: Dict[str, str]) -> Dict[str, str]:
    accept = str(decision.get("accept") or "").strip().lower()
    metric_id = str(decision.get("metric_id") or "").strip()
    category_type = str(decision.get("category_type") or "").strip()
    if accept not in {"true", "false"}:
        return fallback
    if accept == "false":
        return {"accept": "false", "metric_id": "", "category_type": "", "reason": str(decision.get("reason") or "ai_rejected")}
    if metric_id not in {
        "building_orders_by_use",
        "completed_building_by_use",
        "building_orders_by_business_scope",
        "completed_building_by_business_scope",
    }:
        return fallback
    if category_type not in {"use", "business_scope"}:
        category_type = "business_scope" if "business_scope" in metric_id else "use"
    return {
        "accept": "true",
        "metric_id": metric_id,
        "category_type": category_type,
        "reason": str(decision.get("reason") or "ai_classified"),
    }


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
