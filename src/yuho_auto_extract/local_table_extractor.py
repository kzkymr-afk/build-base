from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple


ORDER_FIELDS = {
    "building_orders_total": 1,
    "completed_building": 3,
    "backlog_building_next": 4,
}

DOMESTIC_BUILDING_ORDER_FIELDS = {
    "domestic_building_orders_total": 1,
    "domestic_completed_building": 3,
    "domestic_backlog_building_next": 4,
}

SEGMENT_ORDER_LABELS = {
    "建築事業": "segment_orders_building",
    "建築部門": "segment_orders_building",
    "土木事業": "segment_orders_civil",
    "土木部門": "segment_orders_civil",
    "国内建築事業": "segment_orders_domestic_building",
    "国内建築": "segment_orders_domestic_building",
    "国内土木事業": "segment_orders_domestic_civil",
    "国内土木": "segment_orders_domestic_civil",
    "海外建築事業": "segment_orders_overseas_building",
    "海外建築": "segment_orders_overseas_building",
    "海外土木事業": "segment_orders_overseas_civil",
    "海外土木": "segment_orders_overseas_civil",
    "海外建設事業": "segment_orders_overseas_construction",
    "海外建設": "segment_orders_overseas_construction",
}

REVIEW_HINT_HEADER_FIELDS = [
    "employees_standalone",
    "average_age",
    "average_tenure",
    "average_salary",
]

REVIEW_HINT_LABEL_PATTERNS = {
    "employees_standalone": r"従業員\s*数\s*(?:\(?\s*人\s*\)?)?",
    "average_age": r"平均\s*年[齢令]\s*(?:\(?\s*(?:歳|才)\s*\)?)?",
    "average_tenure": r"平均\s*勤続\s*年数\s*(?:\(?\s*年\s*\)?)?",
    "average_salary": r"平均\s*(?:年間)?\s*給与\s*(?:\(?\s*(?:円|千円|百万円)\s*\)?)?",
}


def extract_local_table_rows(candidate_blocks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    blocks = sorted(candidate_blocks, key=lambda block: float(block.get("locator_score") or 0), reverse=True)
    for block in blocks:
        section_name = str(block.get("section_name") or "")
        if section_name.startswith("review_"):
            extracted = _extract_review_hint_rows(block)
        elif section_name in {"orders_backlog", "sales_style_orders", "segment_orders"}:
            extracted = _extract_sales_style_ratio(block)
            extracted += _extract_segment_order_rows(block)
            if section_name == "orders_backlog":
                extracted += (
                    _extract_building_order_breakdown(block)
                    + _extract_completed_building_breakdown(block)
                    + _extract_backlog_building_breakdown(block)
                    + _extract_domestic_building_orders_backlog(block)
                    + _extract_orders_backlog(block)
                )
        else:
            continue
        for row in extracted:
            key = (str(row.get("company_year_id")), str(row.get("field_id")))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return sorted(rows, key=lambda row: (str(row.get("company_year_id")), str(row.get("field_id"))))


def _extract_review_hint_rows(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    target_fields = [str(field_id) for field_id in block.get("target_fields", []) if field_id]
    if not target_fields:
        return []

    text = _normalize_text(str(block.get("raw_table_markdown") or block.get("raw_text") or ""))
    segment = _review_hint_segment(text, block)
    lines = _lines(segment)
    if not lines:
        return []

    values = _review_table_order_values(lines, target_fields)
    for field_id, value in _review_direct_values(lines, target_fields).items():
        values.setdefault(field_id, value)
    for field_id, value in _review_generic_values(block, lines, target_fields).items():
        values.setdefault(field_id, value)

    out: List[Dict[str, Any]] = []
    scope = "standalone" if "提出会社" in segment else str(block.get("scope_hint") or "standalone")
    for field_id in target_fields:
        value = values.get(field_id)
        if value is None:
            continue
        number, row_index, unit = value
        out.append(
            _row(
                block,
                field_id,
                number,
                lines,
                row_index,
                confidence=0.92,
                review_reason="",
                notes="Extracted by review-derived local table parser.",
                unit=unit,
                review_required=False,
                data_scope=scope,
            )
        )
    return out


def _review_hint_segment(text: str, block: Dict[str, Any]) -> str:
    if not text:
        return ""
    preferred = [word for word in ["提出会社の状況", "提出会社"] if word in text]
    if preferred:
        start = min(text.find(word) for word in preferred if text.find(word) >= 0)
    else:
        keywords = list(block.get("heading_keywords") or []) + list(block.get("table_keywords") or [])
        positions = [text.find(str(word)) for word in keywords if word and text.find(str(word)) >= 0]
        start = min(positions) if positions else 0

    end_markers = [
        "労働組合の状況",
        "管理職に占める女性労働者",
        "多様性に関する指標",
        "事業等のリスク",
        "経営方針",
    ]
    positions = [text.find(marker, start + 80) for marker in end_markers]
    positives = [pos for pos in positions if pos > start]
    end = min(positives) if positives else min(len(text), start + 3500)
    return text[start:end]


def _review_table_order_values(lines: List[str], target_fields: List[str]) -> Dict[str, Tuple[float, int, str]]:
    known_fields = _review_known_fields(target_fields)
    header_hits: List[Dict[str, Any]] = []
    seen_fields = set()
    for line_index, line in enumerate(lines):
        for match in _review_label_matches(line, known_fields):
            field_id = str(match["field_id"])
            if field_id in seen_fields:
                continue
            seen_fields.add(field_id)
            header_hits.append({**match, "line_index": line_index})

    if not header_hits:
        return {}

    last_header_index = max(int(hit["line_index"]) for hit in header_hits)
    values: List[Tuple[float, int]] = []
    skipping_bracket_block = False
    for line_index in range(last_header_index + 1, min(len(lines), last_header_index + 20)):
        if skipping_bracket_block:
            if _review_is_bracket_close_line(lines[line_index]):
                skipping_bracket_block = False
            continue
        if _review_is_bracket_open_line(lines[line_index]):
            skipping_bracket_block = True
            continue
        line_values = _review_numeric_values(lines[line_index])
        if not line_values:
            continue
        values.extend((value, line_index) for value in line_values)
        if len(values) >= len(header_hits):
            break

    if len(header_hits) == 1 and len(values) > 1:
        return {}

    out: Dict[str, Tuple[float, int, str]] = {}
    for header_index, hit in enumerate(header_hits):
        field_id = str(hit["field_id"])
        if field_id not in target_fields or header_index >= len(values):
            continue
        value, row_index = values[header_index]
        unit = _review_unit_for_field(field_id, str(hit.get("label") or ""))
        if not _review_value_plausible(field_id, value, unit):
            continue
        out[field_id] = (value, row_index, unit)
    return out


def _review_direct_values(lines: List[str], target_fields: List[str]) -> Dict[str, Tuple[float, int, str]]:
    known_fields = _review_known_fields(target_fields)
    out: Dict[str, Tuple[float, int, str]] = {}
    for line_index, line in enumerate(lines):
        normalized = unicodedata.normalize("NFKC", line)
        matches = _review_label_matches(line, known_fields)
        if not matches:
            continue
        for match in matches:
            field_id = str(match["field_id"])
            if field_id not in target_fields or field_id in out:
                continue
            next_label_start = min(
                [int(item["start"]) for item in matches if int(item["start"]) > int(match["end"])] or [len(normalized)]
            )
            tail = normalized[int(match["end"]) : next_label_start]
            values = _review_numeric_values(tail)
            if not values:
                continue
            value = values[0]
            unit = _review_unit_for_field(field_id, str(match.get("label") or ""))
            if _review_value_plausible(field_id, value, unit):
                out[field_id] = (value, line_index, unit)
    return out


def _review_generic_values(
    block: Dict[str, Any],
    lines: List[str],
    target_fields: List[str],
) -> Dict[str, Tuple[float, int, str]]:
    labels_by_field = _review_generic_labels_by_field(block, target_fields)
    if not labels_by_field:
        return {}
    out: Dict[str, Tuple[float, int, str]] = {}
    for line_index, line in enumerate(lines):
        matches = _review_generic_label_matches(line, labels_by_field)
        if not matches:
            continue
        for match in matches:
            field_id = str(match["field_id"])
            if field_id in out:
                continue
            next_label_start = min(
                [int(item["start"]) for item in matches if int(item["start"]) > int(match["end"])] or [len(line)]
            )
            unit = _review_generic_unit(block, field_id, line)
            value = _review_value_after_label(lines, line_index, int(match["end"]), next_label_start, unit)
            if value is None or not _review_value_plausible(field_id, value, unit):
                continue
            out[field_id] = (value, line_index, unit)
    return out


def _review_generic_labels_by_field(block: Dict[str, Any], target_fields: List[str]) -> Dict[str, List[str]]:
    by_field_raw = block.get("review_row_labels_by_field", {})
    by_field = by_field_raw if isinstance(by_field_raw, dict) else {}
    shared_labels = _review_generic_label_values(block.get("review_row_labels", []))
    if not shared_labels and len(target_fields) == 1:
        shared_labels = _review_generic_label_values(block.get("table_keywords", []))

    out: Dict[str, List[str]] = {}
    section_name = str(block.get("section_name") or "")
    for field_id in target_fields:
        labels: List[str] = []
        _extend_unique(labels, _review_generic_label_values(by_field.get(field_id, [])))
        if len(target_fields) == 1 or section_name == f"review_{field_id}":
            _extend_unique(labels, shared_labels)
        if labels:
            out[field_id] = labels
    return out


def _review_generic_label_values(value: Any) -> List[str]:
    if isinstance(value, list):
        raw_values = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_values = [part.strip() for part in re.split(r"[;|｜、,]", str(value or "")) if part.strip()]
    return [item for item in raw_values if _looks_like_review_row_label(item)]


def _looks_like_review_row_label(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or len(normalized) > 40:
        return False
    blocked_exact = {
        "提出会社の状況",
        "従業員の状況",
        "生産、受注及び販売の状況",
        "事業の状況",
        "完成工事原価報告書",
        "完成工事原価明細書",
    }
    if normalized in blocked_exact:
        return False
    if re.search(r"\d", normalized):
        return False
    return bool(re.search(r"[一-龯ぁ-んァ-ンA-Za-z]", normalized))


def _review_generic_label_matches(line: str, labels_by_field: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    normalized = unicodedata.normalize("NFKC", line)
    matches: List[Dict[str, Any]] = []
    for field_id, labels in labels_by_field.items():
        for label in sorted(labels, key=len, reverse=True):
            pattern = re.escape(unicodedata.normalize("NFKC", label))
            for match in re.finditer(pattern, normalized):
                matches.append(
                    {
                        "field_id": field_id,
                        "label": label,
                        "start": match.start(),
                        "end": match.end(),
                    }
                )
    matches.sort(key=lambda item: (int(item["start"]), -(int(item["end"]) - int(item["start"]))))
    deduped: List[Dict[str, Any]] = []
    occupied: List[Tuple[int, int]] = []
    for match in matches:
        span = (int(match["start"]), int(match["end"]))
        if any(not (span[1] <= used[0] or span[0] >= used[1]) for used in occupied):
            continue
        occupied.append(span)
        deduped.append(match)
    return deduped


def _review_generic_unit(block: Dict[str, Any], field_id: str, line: str) -> str:
    units_by_field = block.get("review_units_by_field", {})
    if isinstance(units_by_field, dict):
        unit = str(units_by_field.get(field_id) or "").strip()
        if unit:
            return unit
    for unit in ("百万円", "千円", "億円", "円", "%", "人", "歳", "年"):
        if unit in unicodedata.normalize("NFKC", line):
            return unit
    return str(block.get("unit_hint") or "")


def _review_value_after_label(
    lines: List[str],
    line_index: int,
    label_end: int,
    next_label_start: int,
    unit: str,
) -> Optional[float]:
    line = unicodedata.normalize("NFKC", lines[line_index])
    chunks = [line[label_end:next_label_start]]
    if not _review_numeric_values(chunks[0]):
        for next_index in range(line_index + 1, min(len(lines), line_index + 5)):
            if _review_generic_label_values([lines[next_index]]):
                break
            chunks.append(lines[next_index])
            if _review_numeric_values(" ".join(chunks)):
                break
    values = _review_numeric_values(" ".join(chunks))
    return _select_review_current_value(values, unit)


def _select_review_current_value(values: List[float], unit: str) -> Optional[float]:
    if not values:
        return None
    if unit == "%":
        percent_values = [value for value in values if abs(value) <= 1000]
        return percent_values[-1] if percent_values else values[-1]
    if len(values) >= 4 and _looks_like_amount_ratio_pair(values):
        return values[2]
    if len(values) >= 2:
        return values[-1]
    return values[0]


def _looks_like_amount_ratio_pair(values: List[float]) -> bool:
    if len(values) < 4:
        return False
    return abs(values[1]) <= 100 and abs(values[3]) <= 100 and (abs(values[0]) > 100 or abs(values[2]) > 100)


def _review_known_fields(target_fields: List[str]) -> List[str]:
    fields = list(REVIEW_HINT_HEADER_FIELDS)
    for field_id in target_fields:
        if field_id not in fields and field_id in REVIEW_HINT_LABEL_PATTERNS:
            fields.append(field_id)
    return fields


def _review_label_matches(line: str, known_fields: List[str]) -> List[Dict[str, Any]]:
    normalized = unicodedata.normalize("NFKC", line)
    matches: List[Dict[str, Any]] = []
    for field_id in known_fields:
        pattern = REVIEW_HINT_LABEL_PATTERNS.get(field_id)
        if not pattern:
            continue
        for match in re.finditer(pattern, normalized):
            label = match.group(0).strip()
            if not label:
                continue
            matches.append(
                {
                    "field_id": field_id,
                    "label": label,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    matches.sort(key=lambda item: (int(item["start"]), -(int(item["end"]) - int(item["start"]))))
    deduped: List[Dict[str, Any]] = []
    used_spans = set()
    for match in matches:
        span = (int(match["start"]), int(match["end"]))
        if span in used_spans:
            continue
        used_spans.add(span)
        deduped.append(match)
    return deduped


def _review_numeric_values(text: str) -> List[float]:
    normalized = unicodedata.normalize("NFKC", text).replace("，", ",")
    normalized = re.sub(r"[\[［【〔]\s*[+-]?\d[\d,\s.]*(?:\.\d+)?\s*[\]］】〕]", " ", normalized)
    normalized = re.sub(r"[（(]\s*\d[\d,\s.]*(?:\.\d+)?\s*[）)]", " ", normalized)
    values: List[float] = []
    amount_pattern = r"(?:△|▲)?\(?\d{1,3}(?:,\s?\d{3})+(?:\.\d+)?\)?|(?:△|▲)?\(?\d+(?:\.\d+)?\)?"
    for match in re.finditer(amount_pattern, normalized):
        value = _parse_amount_token(match.group(0))
        if value is not None:
            values.append(value)
    return values


def _review_is_bracket_open_line(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).strip()
    return normalized in {"[", "〔", "【", "(", "（"}


def _review_is_bracket_close_line(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).strip()
    return normalized in {"]", "〕", "】", ")", "）"}


def _review_unit_for_field(field_id: str, label: str) -> str:
    normalized = unicodedata.normalize("NFKC", label)
    if field_id == "average_salary":
        for unit in ("百万円", "千円", "億円", "円"):
            if unit in normalized:
                return unit
        return "円"
    if field_id == "average_age":
        return "歳"
    if field_id == "average_tenure":
        return "年"
    if field_id == "employees_standalone":
        return "人"
    return ""


def _review_value_plausible(field_id: str, value: float, unit: str) -> bool:
    if field_id == "average_age":
        return 10.0 <= value <= 100.0
    if field_id == "average_tenure":
        return 0.0 <= value <= 80.0
    if field_id == "average_salary":
        return value >= (1.0 if unit in {"百万円", "億円"} else 100.0)
    if field_id == "employees_standalone":
        return value >= 0.0
    return True


def _extract_orders_backlog(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = _normalize_text(str(block.get("raw_table_markdown") or block.get("raw_text") or ""))
    segment = _orders_backlog_segment(text)
    lines = _lines(segment)
    unit = _table_amount_unit(segment, block)
    row_index, values = _find_last_building_row(lines)
    if row_index is None or values is None:
        return []
    values = _canonical_order_values(values)
    row_equation_pass = _orders_row_equation_pass(values)
    out: List[Dict[str, Any]] = []
    for field_id, offset in ORDER_FIELDS.items():
        value = _order_field_value(values, field_id, offset)
        if value is None:
            continue
        out.append(
            _row(
                block,
                field_id,
                value,
                lines,
                row_index,
                confidence=0.90 if row_equation_pass else 0.78,
                review_reason="" if row_equation_pass else "local_rule_review_required",
                notes=(
                    "Extracted by deterministic local table parser: orders/completed/backlog row equation matched."
                    if row_equation_pass
                    else "Extracted by deterministic local table parser. Human review required unless validation passes."
                ),
                unit=unit,
                review_required=not row_equation_pass,
            )
        )
    return out


def _extract_domestic_building_orders_backlog(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = _normalize_text(str(block.get("raw_table_markdown") or block.get("raw_text") or ""))
    segment = _orders_backlog_segment(text)
    lines = _lines(segment)
    unit = _table_amount_unit(segment, block)
    row_index, values = _find_last_domestic_building_row(lines)
    if row_index is None or values is None:
        return []
    values = _canonical_order_values(values)
    row_equation_pass = _orders_row_equation_pass(values)
    out: List[Dict[str, Any]] = []
    for field_id, offset in DOMESTIC_BUILDING_ORDER_FIELDS.items():
        value = _order_field_value(values, field_id, offset)
        if value is None:
            continue
        out.append(
            _row(
                block,
                field_id,
                value,
                lines,
                row_index,
                confidence=0.90 if row_equation_pass else 0.78,
                review_reason="" if row_equation_pass else "local_rule_review_required",
                notes=(
                    "Extracted by deterministic local table parser: domestic building row equation matched."
                    if row_equation_pass
                    else "Extracted by deterministic local table parser: domestic building row. Human review required unless validation passes."
                ),
                unit=unit,
                review_required=not row_equation_pass,
            )
        )
    return out


def _extract_building_order_breakdown(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = _normalize_text(str(block.get("raw_table_markdown") or block.get("raw_text") or ""))
    segment = _order_breakdown_segment(text)
    if not _looks_like_order_breakdown_table(segment):
        return []
    lines = _lines(segment)
    unit = _table_amount_unit(segment, block)
    row_index, values = _find_current_building_breakdown_row(lines, min_values=4)
    if row_index is None or values is None:
        return []

    mapped_values = _map_order_breakdown_values(values)
    out: List[Dict[str, Any]] = []
    for field_id in [
        "building_orders_government",
        "building_orders_private",
        "building_orders_overseas",
        "building_orders_total",
    ]:
        if field_id not in mapped_values:
            continue
        value = mapped_values[field_id]
        out.append(
            _row(
                block,
                field_id,
                value,
                lines,
                row_index,
                confidence=0.82,
                review_reason="local_company_pattern_review_required",
                notes="Extracted by local company table pattern: building orders by government/private/overseas.",
                unit=unit,
            )
        )
    return out


def _extract_completed_building_breakdown(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = _normalize_text(str(block.get("raw_table_markdown") or block.get("raw_text") or ""))
    segment = _completed_breakdown_segment(text)
    if not _looks_like_customer_breakdown_table(segment):
        return []
    return _extract_customer_breakdown_fields(
        block,
        segment,
        {
            "government": "completed_building_government",
            "private": "completed_building_private",
            "overseas": "completed_building_overseas",
        },
        "completed building by customer type",
    )


def _extract_backlog_building_breakdown(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = _normalize_text(str(block.get("raw_table_markdown") or block.get("raw_text") or ""))
    segment = _backlog_breakdown_segment(text)
    if not _looks_like_customer_breakdown_table(segment):
        return []
    return _extract_customer_breakdown_fields(
        block,
        segment,
        {
            "government": "backlog_building_government",
            "private": "backlog_building_private",
            "overseas": "backlog_building_overseas",
        },
        "building backlog by customer type",
    )


def _extract_customer_breakdown_fields(
    block: Dict[str, Any],
    segment: str,
    field_ids: Dict[str, str],
    pattern_name: str,
) -> List[Dict[str, Any]]:
    lines = _lines(segment)
    unit = _table_amount_unit(segment, block)
    row_index, values = _find_current_building_breakdown_row(lines)
    if row_index is None or values is None:
        return []

    mapped_values = _map_customer_breakdown_values(values, segment)
    out: List[Dict[str, Any]] = []
    for value_key, field_id in field_ids.items():
        if value_key not in mapped_values:
            continue
        out.append(
            _row(
                block,
                field_id,
                mapped_values[value_key],
                lines,
                row_index,
                confidence=0.90,
                review_reason="",
                notes=f"Extracted by local company table pattern: {pattern_name}.",
                unit=unit,
                review_required=False,
            )
        )
    return out


def _extract_sales_style_ratio(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = _normalize_text(str(block.get("raw_table_markdown") or block.get("raw_text") or ""))
    segment = _sales_style_segment(text)
    if not _looks_like_sales_style_table(segment):
        return []
    lines = _lines(segment)
    row_index, values = _find_current_sales_style_building_row(lines)
    if row_index is None or values is None or len(values) < 3:
        return []
    special, competitive, total = values[0], values[1], values[2]
    if special is None or competitive is None or total is None:
        return []
    if abs(total - 100.0) > 0.2 or abs((special + competitive) - total) > 0.2:
        return []
    return [
        _row(
            block,
            "building_orders_special_contract_ratio",
            special,
            lines,
            row_index,
            confidence=0.90,
            review_reason="",
            notes="Extracted by deterministic local table parser: building order special-contract ratio.",
            unit="%",
            review_required=False,
        ),
        _row(
            block,
            "building_orders_competitive_ratio",
            competitive,
            lines,
            row_index,
            confidence=0.90,
            review_reason="",
            notes="Extracted by deterministic local table parser: building order competitive ratio.",
            unit="%",
            review_required=False,
        ),
    ]


def _extract_segment_order_rows(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = _normalize_text(str(block.get("raw_table_markdown") or block.get("raw_text") or ""))
    rows = _extract_segment_order_table_rows(block, text)
    rows += _extract_segment_order_narrative_rows(block, text)
    return rows


def _extract_segment_order_table_rows(block: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
    segment = _segment_order_segment(text)
    if not segment or "受注実績" not in segment or "セグメントの名称" not in segment:
        return []
    lines = _lines(segment)
    unit = _table_amount_unit(segment, block)
    out: List[Dict[str, Any]] = []
    current_values_by_field: Dict[str, float] = {}
    row_index_by_field: Dict[str, int] = {}
    label_by_field: Dict[str, str] = {}
    for row_index, line in enumerate(lines):
        label = _segment_order_label_for_line(line)
        if not label:
            continue
        field_id = SEGMENT_ORDER_LABELS[label]
        values = _amount_numbers_in_text(line)
        if len(values) < 2:
            values = _amount_numbers_in_text(" ".join(lines[row_index : min(len(lines), row_index + 4)]))
        if len(values) < 2:
            continue
        current_values_by_field[field_id] = values[1]
        row_index_by_field[field_id] = row_index
        label_by_field[field_id] = label

    for field_id, value in current_values_by_field.items():
        out.append(
            _row(
                block,
                field_id,
                value,
                lines,
                row_index_by_field[field_id],
                confidence=0.88,
                review_reason="",
                notes="Extracted by deterministic local table parser: segment order table.",
                unit=unit,
                review_required=False,
                data_scope="segment",
                **_common_segment_metadata(field_id, label_by_field.get(field_id, "")),
            )
        )
    return out


def _extract_segment_order_narrative_rows(block: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
    compact_text = re.sub(r"\s+", " ", text)
    lines = _lines(text)
    out: List[Dict[str, Any]] = []
    for label, field_id in SEGMENT_ORDER_LABELS.items():
        if field_id in {"segment_orders_domestic_building", "segment_orders_domestic_civil"}:
            continue
        pattern = rf"(?:\(|（){re.escape(label)}(?:\)|）)[^。]{{0,80}}?受注高は\s*([0-9０-９,，\s]+)\s*億円"
        match = re.search(pattern, compact_text)
        if not match:
            continue
        value = _parse_amount_token(match.group(1))
        if value is None:
            continue
        row_index = _find_line_index_containing(lines, label)
        out.append(
            _row(
                block,
                field_id,
                value,
                lines,
                row_index,
                confidence=0.86,
                review_reason="",
                notes="Extracted by deterministic local text parser: segment order narrative.",
                unit="億円",
                review_required=False,
                data_scope="segment",
                **_common_segment_metadata(field_id, label),
            )
        )
    return out


def _segment_order_segment(text: str) -> str:
    starts = []
    for pattern in [
        r"受注実績",
        r"受注高は",
    ]:
        match = re.search(pattern, text)
        if match:
            starts.append(match.start())
    if not starts:
        return ""
    start = min(starts)
    markers = [
        "売上実績",
        "販売実績",
        "完成工事高",
        "キャッシュ・フロー",
        "事業等のリスク",
        "経営成績に重要な影響",
        "③",
    ]
    positions = [text.find(marker, start + 80) for marker in markers]
    positives = [pos for pos in positions if pos > start]
    end = min(positives) if positives else min(len(text), start + 5000)
    return text[start:end]


def _segment_order_field_for_line(line: str) -> Optional[str]:
    label = _segment_order_label_for_line(line)
    return SEGMENT_ORDER_LABELS[label] if label else None


def _segment_order_label_for_line(line: str) -> Optional[str]:
    compact = _compact_label(line)
    for label, field_id in sorted(SEGMENT_ORDER_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        if compact.startswith(_compact_label(label)):
            return label
    return None


def _common_segment_metadata(field_id: str, source_label: str) -> Dict[str, Any]:
    return {
        "source_segment_label": source_label,
        "normalized_segment_key": field_id.replace("segment_orders_", "", 1),
        "segment_taxonomy_status": "common",
        "applies_to_company_id": "",
        "field_creation_reason": "",
    }


def _amount_numbers_in_text(text: str) -> List[float]:
    values: List[float] = []
    normalized = unicodedata.normalize("NFKC", text).replace("，", ",")
    amount_pattern = r"(?:△|▲)?\(?\d{1,3}(?:,\s?\d{3})+(?:\.\d+)?\)?|(?:△|▲)?\(?\d+(?:\.\d+)?\)?"
    for match in re.finditer(amount_pattern, normalized):
        token = match.group(0)
        value = _parse_amount_token(token)
        if value is None:
            continue
        if abs(value) >= 1000:
            values.append(value)
    return values


def _parse_amount_token(text: str) -> Optional[float]:
    compact = unicodedata.normalize("NFKC", text).replace("，", ",").replace(",", "").strip()
    compact = re.sub(r"(?<=\d)\s+(?=\d)", "", compact)
    negative = False
    if compact.startswith(("△", "▲")):
        negative = True
        compact = compact[1:].strip()
    if compact.startswith("(") and compact.endswith(")"):
        negative = True
        compact = compact[1:-1].strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", compact):
        value = float(compact)
        return -abs(value) if negative else value
    return None


def _find_line_index_containing(lines: List[str], needle: str) -> int:
    for idx, line in enumerate(lines):
        if needle in line:
            return idx
    return 0


def _sales_style_segment(text: str) -> str:
    starts = []
    for pattern in [
        r"受注工事高の受注方法別比率",
        r"受注高の受注方法別比率",
        r"事業会社別受注工事高の受注方法別比率",
        r"受注方法別比率",
    ]:
        match = re.search(pattern, text)
        if match:
            starts.append(match.start())
    if not starts:
        return ""
    start = min(starts)
    markers = [
        "完成工事高",
        "売上高",
        "手持工事",
        "繰越工事",
        "次期繰越",
        "(3)",
        "（3）",
        "c.",
        "c ",
        "ハ ",
        "ニ ",
    ]
    positions = [text.find(marker, start + 30) for marker in markers]
    positives = [pos for pos in positions if pos > start]
    end = min(positives) if positives else len(text)
    return text[start:end]


def _looks_like_sales_style_table(context: str) -> bool:
    if not context:
        return False
    compact = context.replace(" ", "").replace("\n", "")
    return "特命" in compact and "競争" in compact and "建築" in compact and "100" in compact


def _find_current_sales_style_building_row(lines: List[str]) -> Tuple[Optional[int], Optional[List[Optional[float]]]]:
    current_best: Tuple[Optional[int], Optional[List[Optional[float]]]] = (None, None)
    fallback_best: Tuple[Optional[int], Optional[List[Optional[float]]]] = (None, None)
    period: Optional[str] = None
    for row_idx, line in enumerate(lines):
        if _is_current_period_marker(line):
            period = "current"
        elif _is_previous_period_marker(line):
            period = "previous"
        if not _is_building_row_label(line):
            continue
        values = _collect_following_numbers(lines, row_idx + 1)
        if len(values) < 3:
            continue
        if values[0] is None or values[1] is None or values[2] is None:
            continue
        if abs((values[0] + values[1]) - values[2]) > 0.2:
            continue
        fallback_best = (row_idx, values)
        if period == "current":
            current_best = (row_idx, values)
    return current_best if current_best[0] is not None else fallback_best


def _map_order_breakdown_values(values: List[Optional[float]]) -> Dict[str, float]:
    if len(values) < 4 or values[0] is None or values[1] is None:
        return {}
    government = values[0]
    private = values[1]
    overseas = 0.0 if values[2] is None else values[2]
    expected_total = government + private + overseas
    mapped = {
        "building_orders_government": government,
        "building_orders_private": private,
        "building_orders_overseas": overseas,
    }
    for candidate in [value for value in values[3:6] if value is not None]:
        if _within_amount_tolerance(candidate - expected_total, expected_total):
            mapped["building_orders_total"] = candidate
            break
    if "building_orders_total" not in mapped and values[3] is not None:
        mapped["building_orders_total"] = values[3]
    return mapped


def _map_customer_breakdown_values(values: List[Optional[float]], segment: str) -> Dict[str, float]:
    if len(values) < 3 or values[0] is None or values[1] is None:
        return {}

    government = values[0]
    private = values[1]
    mapped = {
        "government": government,
        "private": private,
    }
    has_overseas = "海外" in segment
    if has_overseas and len(values) >= 4 and values[2] is not None:
        overseas = values[2]
        expected_total = government + private + overseas
        for candidate in [value for value in values[3:6] if value is not None]:
            if _within_amount_tolerance(candidate - expected_total, expected_total):
                mapped["overseas"] = overseas
                return mapped
        return {}

    expected_total = government + private
    for candidate in [value for value in values[2:5] if value is not None]:
        if _within_amount_tolerance(candidate - expected_total, expected_total):
            return mapped
    return {}


def _orders_row_equation_pass(values: List[Optional[float]]) -> bool:
    return _orders_row_equation_pass_raw(_canonical_order_values(values))


def _orders_row_equation_pass_raw(values: List[Optional[float]]) -> bool:
    if len(values) < 5:
        return False
    previous_backlog = values[0]
    current_orders = values[1]
    completed_work = values[3]
    next_backlog = values[4]
    if next_backlog is not None and next_backlog < 0 and len(values) > 5 and values[5] is not None and values[5] >= 0:
        next_backlog = values[5]
    if None in {previous_backlog, current_orders, completed_work, next_backlog}:
        return False
    expected = float(previous_backlog) + float(current_orders) - float(completed_work)
    return _within_amount_tolerance(expected - float(next_backlog), float(next_backlog))


def _canonical_order_values(values: List[Optional[float]]) -> List[Optional[float]]:
    if len(values) >= 6 and values[0] is not None and values[0] < 0 and values[1] is not None and values[1] >= 0:
        shifted = values[1:]
        if _orders_row_equation_pass_raw(shifted):
            return shifted
    return values


def _order_field_value(values: List[Optional[float]], field_id: str, offset: int) -> Optional[float]:
    if len(values) <= offset:
        return None
    value = values[offset]
    if "backlog" in field_id and value is not None and value < 0 and len(values) > offset + 1:
        next_value = values[offset + 1]
        if next_value is not None and next_value >= 0:
            return next_value
    return value


def _within_amount_tolerance(diff: float, base: float) -> bool:
    return abs(diff) <= max(2.0, abs(base) * 0.001)


def _orders_backlog_segment(text: str) -> str:
    start = _find_orders_backlog_start(text)
    if start < 0:
        return text
    end = _find_orders_backlog_end(text, start)
    return text[start:end]


def _find_orders_backlog_start(text: str) -> int:
    patterns = [
        r"受注工事高[、,\s]*(?:完成工事高|売上高)[、,\s]*(?:及び|および)[、,\s]*(?:次期繰越工事高|次期繰越高|繰越工事高)",
        r"受注工事高[、,\s]*(?:完成工事高|売上高)[、,\s]*(?:次期繰越工事高|次期繰越高|繰越工事高)",
        r"受注高[、,\s]*売上高[、,\s]*(?:及び|および)?[、,\s]*繰越高",
        r"受注(?:\(|（)契約(?:\)|）)高[、,\s]*売上高[、,\s]*(?:及び|および)?[、,\s]*次期繰越高",
        r"受注高(?:\(|（)契約高(?:\)|）)[、,\s]*(?:及び|および)[、,\s]*売上高",
        r"受注高[、,\s]*売上高[、,\s]*(?:及び|および)[、,\s]*次期繰越高",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.DOTALL)
        if match:
            return match.start()
    return -1


def _find_orders_backlog_end(text: str, start: int) -> int:
    markers = [
        "受注工事高の受注方法",
        "受注方法別",
        "② 受注工事高",
        "2 受注工事高",
        "② 受注",
        "2 受注",
        "②受注",
        "2受注",
        "(2) 受注工事高",
        "(2)受注工事高",
        "b. 受注工事高",
        "b.受注工事高",
        "③ 完成工事高",
        "3 完成工事高",
        "③ 売上高",
        "3 売上高",
        "c. 完成工事高",
        "c.完成工事高",
        "c. 売上高",
    ]
    positions = [text.find(marker, start + 100) for marker in markers]
    regex_markers = [
        r"(?:②|2)\s+受注工事高",
        r"(?:②|2)\s+受注高",
        r"(?:\(|（)2(?:\)|）)\s+受注工事高",
        r"(?:\(|（)2(?:\)|）)\s+受注高",
        r"(?:③|3)\s+完成工事高",
        r"(?:③|3)\s+売上高",
    ]
    for pattern in regex_markers:
        match = re.search(pattern, text[start + 100 :], flags=re.IGNORECASE)
        if match:
            positions.append(start + 100 + match.start())
    positives = [pos for pos in positions if pos > start]
    return min(positives) if positives else len(text)


def _order_breakdown_segment(text: str) -> str:
    starts = []
    for pattern in [
        r"(?:②|2)\s*受注工事高",
        r"(?:②|2)\s*受注高",
        r"(?:\(|（)2(?:\)|）)\s*受注工事高",
        r"(?:\(|（)2(?:\)|）)\s*受注高",
        r"\bb\.\s*受注工事高",
        r"\bb\.\s*受注高",
    ]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            starts.append(match.start())
    if not starts:
        return ""

    start = min(starts)
    markers = [
        "受注工事高の受注方法",
        "受注方法別",
        "③",
        "3 ",
        "(3)",
        "（3）",
        "c. 完成工事高",
        "c.完成工事高",
        "③ 完成工事高",
        "③ 売上高",
        "完成工事高",
        "売上高",
        "繰越工事高",
        "手持工事高",
    ]
    positions = [text.find(marker, start + 20) for marker in markers]
    positives = [pos for pos in positions if pos > start]
    end = min(positives) if positives else len(text)
    return text[start:end]


def _completed_breakdown_segment(text: str) -> str:
    starts = []
    for pattern in [
        r"(?:③|3)\s*完成工事高",
        r"(?:\(|（)3(?:\)|）)\s*完成工事高",
        r"\bc\.\s*完成工事高",
        r"完成工事高",
    ]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            starts.append(match.start())
    if not starts:
        return ""
    start = min(starts)
    markers = [
        "手持工事高",
        "次期繰越工事高",
        "次期繰越高",
        "繰越工事高",
        "(4)",
        "（4）",
        "④",
        "4 ",
        "d.",
        "d ",
        "３【",
        "3 【",
        "対処すべき課題",
    ]
    positions = [text.find(marker, start + 30) for marker in markers]
    positives = [pos for pos in positions if pos > start]
    end = min(positives) if positives else len(text)
    return text[start:end]


def _backlog_breakdown_segment(text: str) -> str:
    starts = []
    for pattern in [
        r"手持工事高",
        r"次期繰越工事高",
        r"次期繰越高",
    ]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            starts.append(match.start())
    if not starts:
        return ""
    start = min(starts)
    markers = [
        "３【",
        "3 【",
        "対処すべき課題",
        "経営方針",
        "事業等のリスク",
        "(5)",
        "（5）",
        "⑤",
        "5 ",
        "e.",
        "e ",
    ]
    positions = [text.find(marker, start + 30) for marker in markers]
    positives = [pos for pos in positions if pos > start]
    end = min(positives) if positives else len(text)
    return text[start:end]


def _find_last_building_row(lines: List[str]) -> Tuple[Optional[int], Optional[List[Optional[float]]]]:
    if not _looks_like_orders_backlog_table("\n".join(lines[:80])):
        return None, None
    best: Tuple[Optional[int], Optional[List[Optional[float]]]] = (None, None)
    for row_idx, line in enumerate(lines):
        if not _is_building_row_label(line):
            continue
        values = _collect_following_numbers(lines, row_idx + 1)
        if len(values) >= 5:
            best = (row_idx, values)
    return best


def _find_last_domestic_building_row(lines: List[str]) -> Tuple[Optional[int], Optional[List[Optional[float]]]]:
    if not _looks_like_orders_backlog_table("\n".join(lines[:80])):
        return None, None
    best: Tuple[Optional[int], Optional[List[Optional[float]]]] = (None, None)
    for row_idx, line in enumerate(lines):
        if not _is_domestic_building_row_label(line):
            continue
        values = _collect_following_numbers(lines, row_idx + 1)
        if len(values) >= 5:
            best = (row_idx, values)
    return best


def _find_current_building_breakdown_row(
    lines: List[str],
    min_values: int = 3,
) -> Tuple[Optional[int], Optional[List[Optional[float]]]]:
    current_best: Tuple[Optional[int], Optional[List[Optional[float]]]] = (None, None)
    fallback_best: Tuple[Optional[int], Optional[List[Optional[float]]]] = (None, None)
    period: Optional[str] = None
    for row_idx, line in enumerate(lines):
        if _is_current_period_marker(line):
            period = "current"
        elif _is_previous_period_marker(line):
            period = "previous"
        if not _is_building_row_label(line):
            continue
        values = _collect_following_numbers(lines, row_idx + 1)
        if len(values) < min_values:
            continue
        fallback_best = (row_idx, values)
        if period == "current":
            current_best = (row_idx, values)
    return current_best if current_best[0] is not None else fallback_best


def _looks_like_orders_backlog_table(context: str) -> bool:
    return ("受注" in context and ("売上高" in context or "完成工事高" in context)) and ("繰越" in context or "手持" in context)


def _looks_like_order_breakdown_table(context: str) -> bool:
    if not context:
        return False
    compact = context.replace(" ", "").replace("\n", "")
    has_customer_columns = ("官公庁" in compact or "官庁" in compact or "公共" in compact) and "民間" in compact
    return "受注" in compact and has_customer_columns and "海外" in compact and ("建築工事" in compact or "建築事業" in compact or "建築" in compact)


def _looks_like_customer_breakdown_table(context: str) -> bool:
    if not context:
        return False
    compact = context.replace(" ", "").replace("\n", "")
    has_customer_columns = ("官公庁" in compact or "官庁" in compact or "公共" in compact) and "民間" in compact
    has_total = "計" in compact or "合計" in compact
    has_building = "建築工事" in compact or "建築事業" in compact or "建築" in compact
    return has_customer_columns and has_total and has_building


def _is_building_row_label(line: str) -> bool:
    compact = _compact_label(line)
    return compact in {"建築事業", "建築工事", "建築"} or compact.startswith("建築事業")


def _is_domestic_building_row_label(line: str) -> bool:
    compact = _compact_label(line)
    labels = {"国内建築", "国内建築事業", "国内建築工事"}
    return compact in labels or compact.startswith("国内建築")


def _is_current_period_marker(line: str) -> bool:
    compact = _compact_label(line)
    return any(token in compact for token in ["当事業年度", "当連結会計年度", "当期"])


def _is_previous_period_marker(line: str) -> bool:
    compact = _compact_label(line)
    return any(token in compact for token in ["前事業年度", "前連結会計年度", "前期"])


def _collect_following_numbers(lines: List[str], start: int) -> List[Optional[float]]:
    values: List[Optional[float]] = []
    for line in lines[start : min(len(lines), start + 20)]:
        if _is_next_row_label(line) and values:
            break
        parsed = _parse_number_or_dash(line)
        if parsed is None and not _is_dash(line):
            if values:
                break
            continue
        values.append(parsed)
        if len(values) >= 8:
            break
    return values


def _is_next_row_label(line: str) -> bool:
    compact = _compact_label(line)
    labels = {
        "土木事業",
        "土木工事",
        "土木",
        "国内土木",
        "国内土木事業",
        "国内土木工事",
        "海外",
        "海外建設事業",
        "小計",
        "不動産事業",
        "計",
        "合計",
    }
    return compact in labels or compact.startswith(("土木事業", "土木工事", "国内土木", "海外建設"))


def _compact_label(line: str) -> str:
    normalized = unicodedata.normalize("NFKC", line)
    normalized = normalized.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", normalized)


def _table_amount_unit(segment: str, block: Dict[str, Any]) -> str:
    compact = unicodedata.normalize("NFKC", segment)
    if "千円" in compact:
        return "千円"
    if "百万円" in compact:
        return "百万円"
    if "億円" in compact:
        return "億円"
    if "円" in compact:
        return "円"
    return str(block.get("unit_hint") or "百万円")


def _parse_number_or_dash(text: str) -> Optional[float]:
    if _is_dash(text):
        return None
    compact = unicodedata.normalize("NFKC", text).replace(",", "").strip()
    compact = re.sub(r"(?<=\d)\s+(?=\d)", "", compact)
    negative = False
    if compact.startswith(("△", "▲")):
        negative = True
        compact = compact[1:].strip()
    if compact.startswith("(") and compact.endswith(")"):
        negative = True
        compact = compact[1:-1].strip()
    compact = compact.replace("−", "-")
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", compact):
        value = float(compact)
        return -abs(value) if negative else value
    return None


def _is_dash(text: str) -> bool:
    return text.strip() in {"-", "－", "―", "—", "–"}


def _row(
    block: Dict[str, Any],
    field_id: str,
    value: float,
    lines: List[str],
    row_index: int,
    confidence: float = 0.78,
    review_reason: str = "local_rule_review_required",
    notes: str = "Extracted by deterministic local table parser. Human review required unless validation passes.",
    unit: Optional[str] = None,
    review_required: bool = True,
    data_scope: Optional[str] = None,
    source_segment_label: str = "",
    normalized_segment_key: str = "",
    segment_taxonomy_status: str = "",
    applies_to_company_id: str = "",
    field_creation_reason: str = "",
) -> Dict[str, Any]:
    quote_start = max(0, row_index - 10)
    quote = " ".join(lines[quote_start : min(len(lines), row_index + 9)])
    unit = unit or block.get("unit_hint") or "百万円"
    scope = data_scope or block.get("scope_hint") or "standalone"
    return {
        "run_id": block.get("run_id"),
        "company_year_id": block.get("company_year_id"),
        "operating_company_id": block.get("operating_company_id"),
        "fiscal_year": block.get("fiscal_year"),
        "source_doc_id": block.get("source_doc_id"),
        "source_file": block.get("source_file"),
        "section_name": block.get("section_name"),
        "candidate_block_id": block.get("candidate_block_id"),
        "field_id": field_id,
        "status": "found",
        "value_raw": value,
        "value": value,
        "unit_raw": unit,
        "unit_normalized": unit,
        "data_scope": scope,
        "source_segment_label": source_segment_label,
        "normalized_segment_key": normalized_segment_key,
        "segment_taxonomy_status": segment_taxonomy_status,
        "applies_to_company_id": applies_to_company_id,
        "field_creation_reason": field_creation_reason,
        "period_label": "当事業年度",
        "source_heading": block.get("heading_text"),
        "source_quote": quote[:500],
        "confidence": confidence,
        "review_required": review_required,
        "review_reason": review_reason,
        "notes": notes,
        "extraction_method": "LOCAL_RULE_TABLE",
    }


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.replace("\r\n", "\n").replace("\r", "\n")


def _lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _extend_unique(target: List[str], values: Iterable[str]) -> None:
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in target:
            target.append(cleaned)
