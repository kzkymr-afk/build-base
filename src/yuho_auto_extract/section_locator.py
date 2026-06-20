from __future__ import annotations

import itertools
import re
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List


def locate_candidate_blocks(
    document_dir: Path,
    target: Dict[str, Any],
    extraction_sections: Dict[str, Any],
    run_id: str,
) -> List[Dict[str, Any]]:
    files = _candidate_text_files(document_dir)
    blocks: List[Dict[str, Any]] = []
    counter = itertools.count(1)
    for source_file in files:
        text = _clean_text(_read_text(source_file))
        if not text:
            continue
        for section_name, section in extraction_sections.items():
            for window in _candidate_windows(text, section, section_name):
                block_id = f"{run_id}:{target.get('company_year_id')}:{section_name}:{next(counter)}"
                blocks.append(
                    {
                        "run_id": run_id,
                        "candidate_block_id": block_id,
                        "source_doc_id": target.get("docID"),
                        "source_file": str(source_file),
                        "company_year_id": target.get("company_year_id"),
                        "operating_company_id": target.get("operating_company_id"),
                        "fiscal_year": target.get("fiscal_year"),
                        "section_name": section_name,
                        "heading_text": _best_heading(window, section),
                        "page_number": None,
                        "table_index": None,
                        "raw_text": window,
                        "raw_table_markdown": "",
                        "unit_hint": _unit_hint(window),
                        "scope_hint": _scope_hint(window),
                        "heading_keywords": section.get("heading_keywords", []),
                        "table_keywords": section.get("table_keywords", []),
                        "review_table_keywords": section.get("review_table_keywords", []),
                        "review_row_labels": section.get("review_row_labels", []),
                        "review_row_labels_by_field": section.get("review_row_labels_by_field", {}),
                        "review_units_by_field": section.get("review_units_by_field", {}),
                        "target_fields": section.get("target_fields", []),
                        "locator_score": _candidate_score(window, section, section_name),
                    }
                )
    return _cap_blocks(blocks)


def _candidate_text_files(document_dir: Path) -> List[Path]:
    extensions = {".html", ".htm", ".xhtml", ".txt", ".csv"}
    return [path for path in document_dir.rglob("*") if path.suffix.lower() in extensions]


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp932"):
        try:
            return path.read_text(encoding=encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _clean_text(text: str) -> str:
    text = re.sub(r"<(script|style).*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|tr|div|table|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _candidate_windows(text: str, section: Dict[str, Any], section_name: str, window_size: int = 5000) -> List[str]:
    positions = sorted(
        {
            match.start()
            for word in section.get("heading_keywords", [])
            if word
            for match in re.finditer(re.escape(word), text)
        }
    )
    spans: List[tuple] = []
    for pos in positions:
        start = max(0, pos - window_size // 5)
        end = min(len(text), pos + window_size)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    windows: List[str] = []
    seen = set()
    for start, end in spans:
        window = text[start:end].strip()
        signature = re.sub(r"\s+", "", window[:1000])
        if signature in seen:
            continue
        seen.add(signature)
        if _matches_section_window(window, section, section_name):
            windows.append(window)
    return windows


def _matches_section_window(text: str, section: Dict[str, Any], section_name: str) -> bool:
    heading_hits = sum(1 for word in section.get("heading_keywords", []) if word in text)
    table_hits = sum(1 for word in section.get("table_keywords", []) if word in text)
    if heading_hits == 0:
        return False
    if section_name == "orders_backlog":
        return table_hits >= 2 and any(word in text for word in ["受注", "完成", "繰越", "工事高"])
    if section_name == "purpose_orders":
        if any(word in text[:1500] for word in ["報酬", "役員", "株式"]):
            return False
        return table_hits >= 2 and any(word in text for word in ["受注", "工事高", "建物", "用途"])
    if section_name == "sales_style_orders":
        return table_hits >= 1 and any(word in text for word in ["受注", "工事", "売上"])
    return table_hits > 0


def _candidate_score(text: str, section: Dict[str, Any], section_name: str) -> int:
    heading_hits = sum(1 for word in section.get("heading_keywords", []) if word in text)
    table_hits = sum(1 for word in section.get("table_keywords", []) if word in text)
    score = heading_hits * 3 + table_hits * 2
    if "百万円" in text:
        score += 2
    if "当社における受注高及び売上高の状況" in text:
        score += 8
    if "受注工事高" in text:
        score += 6
    if "完成工事高" in text:
        score += 4
    if "次期繰越" in text:
        score += 4
    if section_name == "purpose_orders" and any(word in text for word in ["報酬", "役員", "株式"]):
        score -= 12
    if section_name == "orders_backlog" and any(word in text[:800] for word in ["報酬", "役員"]):
        score -= 8
    return score


def _cap_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    caps = {"orders_backlog": 5, "purpose_orders": 3, "sales_style_orders": 3}
    min_scores = {"orders_backlog": 10, "purpose_orders": 8, "sales_style_orders": 8}
    grouped: Dict[tuple, List[Dict[str, Any]]] = {}
    for block in blocks:
        grouped.setdefault((block.get("company_year_id"), block.get("section_name")), []).append(block)
    out: List[Dict[str, Any]] = []
    for (_company_year_id, section_name), group in grouped.items():
        cap = caps.get(str(section_name), 3)
        seen = set()
        kept = 0
        for block in sorted(group, key=lambda item: int(item.get("locator_score") or 0), reverse=True):
            if int(block.get("locator_score") or 0) < min_scores.get(str(section_name), 0):
                continue
            signature = re.sub(r"\s+", "", str(block.get("raw_text", ""))[:1000])
            if signature in seen:
                continue
            seen.add(signature)
            out.append(block)
            kept += 1
            if kept >= cap:
                break
    return out


def _best_heading(text: str, section: Dict[str, Any]) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if any(word in line for word in section.get("heading_keywords", [])):
            return line[:200]
    return ""


def _clip_around_keywords(text: str, section: Dict[str, Any], window: int = 5000) -> str:
    keywords = list(section.get("heading_keywords", [])) + list(section.get("table_keywords", []))
    positions = [text.find(word) for word in keywords if word and text.find(word) >= 0]
    if not positions:
        return text[:window]
    pos = min(positions)
    start = max(0, pos - window // 4)
    end = min(len(text), start + window)
    return text[start:end]


def _unit_hint(text: str) -> str:
    for unit in ("百万円", "千円", "億円", "円", "%", "人"):
        if unit in text:
            return unit
    return ""


def _scope_hint(text: str) -> str:
    if "単独" in text or "提出会社" in text or "当社" in text:
        return "standalone"
    if "連結" in text or "グループ" in text:
        return "consolidated"
    if "セグメント" in text:
        return "segment"
    return "unknown"
