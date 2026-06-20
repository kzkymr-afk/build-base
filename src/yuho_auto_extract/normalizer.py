from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, Optional


NULL_MARKS = {"", "-", "－", "—", "―", "–", "N/A", "n/a", "None", "null"}
AMOUNT_FACTORS_TO_MILLION = {
    "円": 1 / 1_000_000,
    "千円": 1 / 1_000,
    "百万円": 1,
    "億円": 100,
}


def normalize_numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if text in NULL_MARKS:
        return None
    negative = False
    if text.startswith(("△", "▲")):
        negative = True
        text = text[1:]
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = text.replace(",", "").replace(" ", "").replace("%", "")
    if text in NULL_MARKS:
        return None
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
        return None
    number = float(text)
    return -abs(number) if negative else number


def normalize_unit(unit_raw: Any) -> Optional[str]:
    if unit_raw is None:
        return None
    text = unicodedata.normalize("NFKC", str(unit_raw)).strip()
    text = text.replace("単位:", "").replace("単位：", "").strip()
    if not text:
        return None
    if "%" in text or "％" in text:
        return "%"
    if "歳" in text or "才" in text:
        return "歳"
    for unit in ("百万円", "千円", "億円", "円", "人", "年"):
        if unit in text:
            return unit
    return None


def convert_unit(value: Optional[float], unit_raw: Any, target_unit: str) -> Optional[float]:
    if value is None:
        return None
    source_unit = normalize_unit(unit_raw)
    if source_unit == target_unit:
        return value
    if source_unit in {"年", "歳"} and target_unit in {"年", "歳"}:
        return value
    if source_unit in AMOUNT_FACTORS_TO_MILLION and target_unit in AMOUNT_FACTORS_TO_MILLION:
        return value * AMOUNT_FACTORS_TO_MILLION[source_unit] / AMOUNT_FACTORS_TO_MILLION[target_unit]
    if target_unit == "%" and source_unit == "%":
        return value
    if target_unit in ("人", "年") and source_unit == target_unit:
        return value
    return None


def normalize_extraction(row: Dict[str, Any], field_def: Dict[str, Any]) -> Dict[str, Any]:
    value_raw = row.get("value_raw", row.get("value"))
    unit_raw = row.get("unit_raw")
    target_unit = str(field_def.get("target_unit", "")).strip()
    parsed = normalize_numeric(value_raw)
    normalized = convert_unit(parsed, unit_raw, target_unit)
    out = dict(row)
    out["field_id"] = row.get("field_id") or field_def.get("field_id")
    out["value_raw"] = value_raw
    out["value"] = normalized
    out["value_normalized"] = normalized
    out["unit_raw"] = unit_raw
    out["unit_normalized"] = target_unit if normalized is not None else normalize_unit(unit_raw)
    out.setdefault("review_required", False)
    reasons = _existing_reasons(out)
    if parsed is None and value_raw not in (None, ""):
        reasons.append("number_parse_failed")
    if normalized is None and parsed is not None:
        reasons.append("unit_conversion_failed")
    if out.get("unit_normalized") is None and parsed is not None:
        reasons.append("unit_unknown")
    if out.get("data_scope") not in (None, "", field_def.get("data_scope_required")):
        reasons.append("data_scope_mismatch")
    if reasons:
        out["review_required"] = True
        out["review_reason"] = ";".join(dict.fromkeys(reasons))
    return out


def _existing_reasons(row: Dict[str, Any]) -> list:
    reason = row.get("review_reason")
    if not reason:
        return []
    if isinstance(reason, list):
        return [str(item) for item in reason if item]
    return [item for item in str(reason).split(";") if item]
