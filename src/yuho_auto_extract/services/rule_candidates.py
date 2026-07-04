from __future__ import annotations

import re
import csv
import shutil
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Sequence

from yuho_auto_extract.io_utils import is_blankish, prefer_existing_table, read_table, read_yaml, write_table, write_yaml


RULE_CANDIDATE_COLUMNS = [
    "field_id",
    "field_name_ja",
    "evidence_count",
    "company_year_ids",
    "proposed_xbrl_tags",
    "proposed_section_keywords",
    "proposed_tables",
    "proposed_row_labels",
    "proposed_scope",
    "proposed_unit",
    "generality",
    "quotes",
    "reviewed_value_examples",
    "learning_source",
    "confidence",
    "inference_notes",
    "candidate_status",
    "candidate_applied_at",
    "needs_manual_check",
    "recommended_action",
    "last_filled_delta",
    "last_review_queue_after",
    "last_auto_applied",
    "last_applied_columns",
    "last_applied_sections",
]

RULE_CANDIDATE_DECISION_COLUMNS = [
    "field_id",
    "candidate_signature",
    "candidate_status",
    "candidate_applied_at",
    "applied_columns",
    "applied_sections",
]

SIGNATURE_COLUMNS = [
    "field_id",
    "proposed_xbrl_tags",
    "proposed_section_keywords",
    "proposed_tables",
    "proposed_row_labels",
    "proposed_scope",
    "proposed_unit",
]

STRUCTURED_KEYS = {
    "LOC",
    "TABLE",
    "LABEL",
    "SCOPE",
    "UNIT",
    "QUOTE",
    "XBRL_TAG",
    "GENERALITY",
    "RULE_HINT",
}

JAPANESE_STRUCTURED_KEYS = {
    "場所": "LOC",
    "位置": "LOC",
    "表": "TABLE",
    "テーブル": "TABLE",
    "行": "LABEL",
    "行ラベル": "LABEL",
    "ラベル": "LABEL",
    "項目": "LABEL",
    "項目名": "LABEL",
    "スコープ": "SCOPE",
    "範囲": "SCOPE",
    "単位": "UNIT",
    "引用": "QUOTE",
    "根拠": "QUOTE",
    "XBRLタグ": "XBRL_TAG",
    "タグ": "XBRL_TAG",
    "汎用性": "GENERALITY",
    "一般性": "GENERALITY",
    "ルールヒント": "RULE_HINT",
}


def generate_rule_candidates(root: Path) -> Dict[str, Any]:
    rows = build_rule_candidates(root)
    rows = _annotate_rule_candidate_status(root, rows)
    rows = _attach_learning_impact(root, rows)
    path = root / "data" / "review" / "rule_candidates.csv"
    _write_rule_candidates(path, rows)
    active_rows = _filter_rule_candidates(rows, "active")
    status_counts = _rule_candidate_status_counts(rows)
    return {
        "path": str(path),
        "total": status_counts["active"],
        "all_total": status_counts["all"],
        "applied_total": status_counts["applied"],
        "status_counts": status_counts,
        "rows": active_rows,
    }


def read_rule_candidates(root: Path, candidate_status: str = "active") -> List[Dict[str, Any]]:
    path = root / "data" / "review" / "rule_candidates.csv"
    rows = read_table(path) if path.exists() else []
    rows = _annotate_rule_candidate_status(root, rows)
    rows = _attach_learning_impact(root, rows)
    return _filter_rule_candidates(rows, candidate_status)


def _attach_learning_impact(root: Path, rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    path = root / "data" / "review" / "review_learning_impact.csv"
    if not path.exists():
        return [dict(row) for row in rows]
    impact_by_field = {
        str(row.get("field_id", "") or "").strip(): row
        for row in read_table(path)
        if str(row.get("field_id", "") or "").strip()
    }
    out: List[Dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        impact = impact_by_field.get(str(copied.get("field_id", "") or "").strip(), {})
        copied["last_filled_delta"] = impact.get("filled_delta", "")
        copied["last_review_queue_after"] = impact.get("review_queue_after", "")
        copied["last_auto_applied"] = impact.get("auto_applied", "")
        copied["last_applied_columns"] = impact.get("applied_columns", "")
        copied["last_applied_sections"] = impact.get("applied_sections", "")
        out.append(copied)
    return out


def rule_candidate_status_counts(root: Path) -> Dict[str, int]:
    return _rule_candidate_status_counts(read_rule_candidates(root, candidate_status="all"))


def apply_rule_candidates(root: Path, field_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    candidates = read_rule_candidates(root, candidate_status="active")
    requested = {str(field_id).strip() for field_id in (field_ids or []) if str(field_id).strip()}
    selected = [
        row
        for row in candidates
        if not requested or str(row.get("field_id", "")).strip() in requested
    ]
    found = {str(row.get("field_id", "")).strip() for row in selected}
    warnings = [f"rule candidate not found: {field_id}" for field_id in sorted(requested - found)]
    if not selected:
        return {
            "applied_candidates": 0,
            "updated_fields": [],
            "updated_sections": [],
            "backups": [],
            "warnings": warnings,
        }

    config_dir = root / "config"
    field_csv = config_dir / "field_definition.csv"
    if not field_csv.exists():
        raise ValueError("config/field_definition.csv が見つかりません。")
    field_rows = read_table(field_csv)
    fields_by_id = {
        str(row.get("field_id", "")).strip(): row
        for row in field_rows
        if str(row.get("field_id", "")).strip()
    }

    sections_path = config_dir / "extraction_sections.yml"
    sections = read_yaml(sections_path) if sections_path.exists() else {}
    if not isinstance(sections, dict):
        raise ValueError("config/extraction_sections.yml は辞書形式である必要があります。")

    updated_fields: List[Dict[str, Any]] = []
    updated_sections: List[str] = []
    backups: List[str] = []
    applied_meta: Dict[str, Dict[str, List[str]]] = {}
    fields_changed = False
    sections_changed = False

    for candidate in selected:
        field_id = str(candidate.get("field_id", "")).strip()
        if not field_id:
            continue
        field_row = fields_by_id.get(field_id)
        if field_row is None:
            warnings.append(f"field_definition.csv に field_id={field_id} がありません。")
            continue

        changed_columns = _apply_candidate_to_field(field_row, candidate)
        applied_meta.setdefault(field_id, {"columns": [], "sections": []})
        _extend_unique(applied_meta[field_id]["columns"], changed_columns)
        if changed_columns:
            fields_changed = True
            updated_fields.append({"field_id": field_id, "columns": changed_columns})

        section_name, section_was_changed = _apply_candidate_to_sections(sections, candidate)
        if section_name:
            _extend_unique(applied_meta[field_id]["sections"], [section_name])
        if section_was_changed:
            sections_changed = True
            if section_name not in updated_sections:
                updated_sections.append(section_name)

    if fields_changed or sections_changed:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if fields_changed:
            _backup_file(field_csv, stamp, backups)
            write_table(field_csv, field_rows)
            field_xlsx = config_dir / "field_definition.xlsx"
            if field_xlsx.exists():
                _backup_file(field_xlsx, stamp, backups)
                write_table(field_xlsx, field_rows)
        if sections_changed:
            _backup_file(sections_path, stamp, backups)
            write_yaml(sections_path, sections)
    _mark_rule_candidates_applied(root, selected, applied_meta)

    return {
        "applied_candidates": len(selected),
        "updated_fields": updated_fields,
        "updated_sections": updated_sections,
        "backups": backups,
        "warnings": warnings,
    }


def build_rule_candidates(root: Path) -> List[Dict[str, Any]]:
    resolved_path = root / "data" / "review" / "review_resolved.csv"
    if not resolved_path.exists():
        return []
    field_rows = read_table(root / "config" / "field_definition.csv")
    fields = {
        str(row.get("field_id", "")): row
        for row in field_rows
        if row.get("field_id")
    }
    field_names = {field_id: str(row.get("field_name_ja", "")) for field_id, row in fields.items()}
    context_index = _build_value_context_index(root)
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in read_table(resolved_path):
        if not _is_learning_source(row):
            continue
        field_id = str(row.get("field_id", "")).strip()
        if not field_id:
            continue
        parsed = parse_review_note(str(row.get("reviewer_note", "") or ""))
        inferred = _infer_from_review_value(row, fields.get(field_id, {}), context_index)
        parsed = _merge_parsed(parsed, inferred)
        if not _has_learning_signal(parsed):
            continue
        group = grouped.setdefault(field_id, _empty_group(field_id, str(row.get("field_name_ja", "")) or field_names.get(field_id, "")))
        _add_evidence(group, row, parsed)
    candidates = [_finalize_group(group) for group in grouped.values()]
    return sorted(candidates, key=lambda row: (str(row.get("field_id", "")), str(row.get("field_name_ja", ""))))


def _rule_candidate_decisions_path(root: Path) -> Path:
    return root / "data" / "review" / "rule_candidate_decisions.csv"


def _read_rule_candidate_decisions(root: Path) -> Dict[tuple[str, str], Dict[str, Any]]:
    path = _rule_candidate_decisions_path(root)
    if not path.exists():
        return {}
    decisions: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in read_table(path):
        field_id = str(row.get("field_id", "") or "").strip()
        signature = str(row.get("candidate_signature", "") or "").strip()
        if field_id and signature:
            decisions[(field_id, signature)] = row
    return decisions


def _annotate_rule_candidate_status(root: Path, rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    decisions = _read_rule_candidate_decisions(root)
    annotated: List[Dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        field_id = str(copied.get("field_id", "") or "").strip()
        signature = _candidate_signature(copied)
        decision = decisions.get((field_id, signature), {})
        status = str(decision.get("candidate_status", "") or copied.get("candidate_status", "") or "active").strip()
        copied["candidate_status"] = status or "active"
        copied["candidate_applied_at"] = decision.get("candidate_applied_at", copied.get("candidate_applied_at", ""))
        annotated.append(copied)
    return annotated


def _filter_rule_candidates(rows: Sequence[Dict[str, Any]], candidate_status: str) -> List[Dict[str, Any]]:
    status = str(candidate_status or "active").strip().lower()
    if status in {"", "all"}:
        return list(rows)
    if status == "applied":
        return [row for row in rows if str(row.get("candidate_status", "")).strip().lower() == "applied"]
    return [row for row in rows if str(row.get("candidate_status", "")).strip().lower() != "applied"]


def _rule_candidate_status_counts(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    applied = sum(1 for row in rows if str(row.get("candidate_status", "")).strip().lower() == "applied")
    active = len(rows) - applied
    return {"active": active, "applied": applied, "all": len(rows)}


def _mark_rule_candidates_applied(
    root: Path,
    candidates: Sequence[Dict[str, Any]],
    applied_meta: Dict[str, Dict[str, List[str]]],
) -> None:
    if not candidates:
        return
    path = _rule_candidate_decisions_path(root)
    existing = list(read_table(path)) if path.exists() else []
    by_key = {
        (str(row.get("field_id", "") or "").strip(), str(row.get("candidate_signature", "") or "").strip()): dict(row)
        for row in existing
        if str(row.get("field_id", "") or "").strip() and str(row.get("candidate_signature", "") or "").strip()
    }
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for candidate in candidates:
        field_id = str(candidate.get("field_id", "") or "").strip()
        if not field_id:
            continue
        signature = _candidate_signature(candidate)
        meta = applied_meta.get(field_id, {})
        by_key[(field_id, signature)] = {
            "field_id": field_id,
            "candidate_signature": signature,
            "candidate_status": "applied",
            "candidate_applied_at": now,
            "applied_columns": ";".join(meta.get("columns", [])),
            "applied_sections": ";".join(meta.get("sections", [])),
        }
    rows = [by_key[key] for key in sorted(by_key)]
    write_table(path, rows)


def _candidate_signature(row: Dict[str, Any]) -> str:
    parts = []
    for column in SIGNATURE_COLUMNS:
        values = _candidate_values(row.get(column, ""))
        parts.append(";".join(values))
    return "||".join(parts)


def parse_review_note(note: str) -> Dict[str, List[str]]:
    parsed: Dict[str, List[str]] = defaultdict(list)
    for raw_line in note.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z_]+)\s*[:=]\s*(.+)$", line)
        if match and match.group(1).upper() in STRUCTURED_KEYS:
            _add_structured_value(parsed, match.group(1).upper(), match.group(2).strip())
            continue
        ja_match = re.match(r"^([^:=：＝]{1,20})\s*[:：=＝]\s*(.+)$", line)
        if ja_match:
            mapped_key = JAPANESE_STRUCTURED_KEYS.get(ja_match.group(1).strip())
            if mapped_key:
                _add_structured_value(parsed, mapped_key, ja_match.group(2).strip())
                continue
        for key, value in _iter_key_value_chunks(line):
            _add_structured_value(parsed, key.upper(), value)
    return {key: _unique(values) for key, values in parsed.items()}


def _merge_parsed(*items: Dict[str, List[str]]) -> Dict[str, List[str]]:
    merged: Dict[str, List[str]] = defaultdict(list)
    for item in items:
        for key, values in item.items():
            _extend_unique(merged[key], values)
    return {key: values for key, values in merged.items() if values}


def _build_value_context_index(root: Path) -> Dict[tuple[str, str], List[Dict[str, Any]]]:
    contexts: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for table_path, source_type in [
        (root / "data" / "review" / "review_queue.csv", "review_queue"),
        (root / "data" / "final" / "source_audit.csv", "source_audit"),
        (root / "data" / "intermediate" / "normalized_validated_long.parquet", "extracted"),
        (root / "data" / "intermediate" / "normalized_validated_long.csv", "extracted"),
    ]:
        if not table_path.exists():
            continue
        actual_path = prefer_existing_table(table_path) if table_path.suffix == ".parquet" else table_path
        for row in read_table(actual_path):
            key = _row_key(row)
            if not key:
                continue
            text = "\n".join(
                str(row.get(column, "") or "")
                for column in ["source_heading", "source_quote", "field_name_ja"]
                if row.get(column)
            )
            if not text.strip():
                continue
            contexts[key].append(
                {
                    "source_type": source_type,
                    "text": text,
                    "heading": row.get("source_heading", ""),
                    "quote": row.get("source_quote", ""),
                    "unit": row.get("unit_normalized", ""),
                    "scope": row.get("data_scope", ""),
                    "xbrl_element": row.get("xbrl_element", ""),
                    "value": row.get("value", row.get("value_normalized", row.get("extracted_value", ""))),
                }
            )

    blocks_path = root / "data" / "intermediate" / "candidate_blocks.jsonl"
    if blocks_path.exists():
        for block in read_table(blocks_path):
            company_year_id = str(block.get("company_year_id", "") or "").strip()
            if not company_year_id:
                continue
            target_fields = [str(field_id).strip() for field_id in block.get("target_fields", []) if str(field_id).strip()]
            section_name = str(block.get("section_name", "") or "")
            if section_name.startswith("review_"):
                inferred_field = section_name.replace("review_", "", 1)
                if inferred_field and inferred_field not in target_fields:
                    target_fields.append(inferred_field)
            text = str(block.get("raw_table_markdown") or block.get("raw_text") or "")
            if not text.strip():
                continue
            contexts[(company_year_id, "")].append(
                {
                    "source_type": "company_candidate_block",
                    "text": text,
                    "heading": "",
                    "heading_keywords": [],
                    "table_keywords": [],
                    "unit": block.get("unit_hint", ""),
                    "scope": block.get("scope_hint", ""),
                    "quote": "",
                }
            )
            for field_id in target_fields:
                contexts[(company_year_id, field_id)].append(
                    {
                        "source_type": "candidate_block",
                        "text": text,
                        "heading": block.get("heading_text", ""),
                        "heading_keywords": block.get("heading_keywords", []),
                        "table_keywords": block.get("table_keywords", []),
                        "unit": block.get("unit_hint", ""),
                        "scope": block.get("scope_hint", ""),
                        "quote": "",
                    }
                )
    return contexts


def _infer_from_review_value(
    row: Dict[str, Any],
    field: Dict[str, Any],
    context_index: Dict[tuple[str, str], List[Dict[str, Any]]],
) -> Dict[str, List[str]]:
    reviewed_value = _reviewed_value(row)
    if is_blankish(reviewed_value):
        return {}
    key = _row_key(row)
    if not key:
        return {}
    parsed: Dict[str, List[str]] = defaultdict(list)
    labels = _field_label_candidates(row, field)
    contexts = list(context_index.get(key, []))
    shared_contexts = context_index.get((key[0], ""), [])
    seen_context_keys = {(str(context.get("source_type", "")), str(context.get("text", ""))) for context in contexts}
    for context in shared_contexts:
        context_key = (str(context.get("source_type", "")), str(context.get("text", "")))
        if context_key not in seen_context_keys:
            contexts.append(context)
            seen_context_keys.add(context_key)
    inline_text = "\n".join(
        str(row.get(column, "") or "")
        for column in ["source_heading", "source_quote", "field_name_ja"]
        if row.get(column)
    )
    if inline_text.strip():
        contexts.insert(
            0,
            {
                "source_type": "saved_review",
                "text": inline_text,
                "heading": row.get("source_heading", ""),
                "quote": row.get("source_quote", ""),
                "unit": row.get("unit_normalized", ""),
                "scope": row.get("data_scope", ""),
            },
        )

    seen_snippets: List[str] = []
    for context in contexts:
        text = str(context.get("text") or "")
        match = _find_value_match(reviewed_value, text)
        if not match:
            continue
        start, end, _token = match
        snippet = _snippet_around(text, start, end)
        if snippet in seen_snippets:
            continue
        seen_snippets.append(snippet)
        source_type = str(context.get("source_type") or "value_context")
        tables, row_labels = _context_table_and_row_labels(context, snippet, labels)
        if source_type == "company_candidate_block" and not row_labels:
            continue
        _extend_unique(parsed["learning_sources"], [source_type])
        _extend_unique(parsed["quotes"], [snippet])
        _extend_unique(parsed["section_keywords"], _context_section_keywords(context, snippet, field))
        _extend_unique(parsed["tables"], tables)
        _extend_unique(parsed["row_labels"], row_labels or labels)
        _extend_unique(parsed["scopes"], _context_scopes(context, snippet, field))
        _extend_unique(parsed["units"], _context_units(context, field))
        xbrl_tag = _trusted_xbrl_tag(row, context, reviewed_value)
        if xbrl_tag:
            _extend_unique(parsed["xbrl_tags"], [xbrl_tag])
        _extend_unique(parsed["inference_notes"], [f"value matched in {source_type}"])
        parsed["confidences"].append(str(_context_confidence(source_type, bool(row_labels), bool(xbrl_tag))))

    parsed = _merge_parsed(parsed, _infer_zero_value_review_note(row, field, labels, reviewed_value))
    if not _has_learning_signal(parsed):
        return _infer_from_saved_review_only(row, field, labels, reviewed_value)
    return {key: _unique(values) for key, values in parsed.items()}


def _infer_zero_value_review_note(
    row: Dict[str, Any],
    field: Dict[str, Any],
    labels: Sequence[str],
    reviewed_value: Any,
) -> Dict[str, List[str]]:
    if _parse_numeric_value(reviewed_value) != 0:
        return {}
    note = unicodedata.normalize("NFKC", str(row.get("reviewer_note", "") or "")).strip()
    if not note:
        return {}
    zero_markers = _zero_value_markers(note)
    if not zero_markers:
        return {}
    parsed: Dict[str, List[str]] = defaultdict(list)
    _extend_unique(parsed["row_labels"], labels)
    _extend_unique(parsed["row_labels"], zero_markers)
    _extend_unique(parsed["section_keywords"], _candidate_values(field.get("section_keywords", "")))
    if "研究開発活動" in note:
        _extend_unique(parsed["section_keywords"], ["研究開発活動"])
    _extend_unique(parsed["scopes"], [str(field.get("data_scope_required") or row.get("data_scope") or "").strip()])
    _extend_unique(parsed["units"], [str(field.get("target_unit") or row.get("unit_normalized") or "").strip()])
    _extend_unique(parsed["quotes"], [_snippet_around(note, 0, min(len(note), 1))])
    _extend_unique(parsed["learning_sources"], ["zero_value_review_note"])
    _extend_unique(parsed["inference_notes"], ["zero value inferred from saved review note"])
    parsed["confidences"].append("0.60")
    return {key: _unique(values) for key, values in parsed.items()}


def _zero_value_markers(text: str) -> List[str]:
    markers: List[str] = []
    for marker in ["特記事項なし", "該当事項なし", "該当なし", "なし"]:
        if marker in text:
            markers.append(marker)
    return markers


def _infer_from_saved_review_only(
    row: Dict[str, Any],
    field: Dict[str, Any],
    labels: Sequence[str],
    reviewed_value: Any,
) -> Dict[str, List[str]]:
    if is_blankish(reviewed_value):
        return {}
    parsed: Dict[str, List[str]] = defaultdict(list)
    _extend_unique(parsed["row_labels"], labels)
    _extend_unique(parsed["section_keywords"], _candidate_values(field.get("section_keywords", "")))
    _extend_unique(parsed["scopes"], [str(field.get("data_scope_required") or row.get("data_scope") or "").strip()])
    _extend_unique(parsed["units"], [str(field.get("target_unit") or row.get("unit_normalized") or "").strip()])
    _extend_unique(parsed["learning_sources"], ["review_value_only"])
    _extend_unique(parsed["inference_notes"], ["saved review value without matched source text"])
    parsed["confidences"].append("0.40")
    return {key: _unique(values) for key, values in parsed.items()}


def _is_learning_source(row: Dict[str, Any]) -> bool:
    decision = str(row.get("review_decision", "")).strip().lower()
    return decision in {"accept", "correct"}


def _has_learning_signal(parsed: Dict[str, List[str]]) -> bool:
    return any(parsed.get(key) for key in ["xbrl_tags", "section_keywords", "tables", "row_labels", "scopes", "units"])


def _empty_group(field_id: str, field_name_ja: str) -> Dict[str, Any]:
    return {
        "field_id": field_id,
        "field_name_ja": field_name_ja,
        "company_year_ids": [],
        "xbrl_tags": [],
        "section_keywords": [],
        "tables": [],
        "row_labels": [],
        "scopes": [],
        "units": [],
        "generalities": [],
        "quotes": [],
        "reviewed_values": [],
        "learning_sources": [],
        "confidence_scores": [],
        "inference_notes": [],
        "evidence_count": 0,
    }


def _add_evidence(group: MutableMapping[str, Any], row: Dict[str, Any], parsed: Dict[str, List[str]]) -> None:
    group["evidence_count"] += 1
    _extend_unique(group["company_year_ids"], [str(row.get("company_year_id", "")).strip()])
    _extend_unique(group["xbrl_tags"], parsed.get("xbrl_tags", []))
    _extend_unique(group["section_keywords"], parsed.get("section_keywords", []))
    _extend_unique(group["tables"], parsed.get("tables", []))
    _extend_unique(group["row_labels"], parsed.get("row_labels", []))
    _extend_unique(group["scopes"], parsed.get("scopes", []))
    _extend_unique(group["units"], parsed.get("units", []))
    _extend_unique(group["generalities"], parsed.get("generalities", []))
    _extend_unique(group["quotes"], parsed.get("quotes", []))
    _extend_unique(group["learning_sources"], parsed.get("learning_sources", []))
    _extend_unique(group["inference_notes"], parsed.get("inference_notes", []))
    group["confidence_scores"].extend(_numeric_confidences(parsed.get("confidences", [])))
    value = _reviewed_value(row)
    if not is_blankish(value):
        _extend_unique(group["reviewed_values"], [value])


def _finalize_group(group: Dict[str, Any]) -> Dict[str, Any]:
    field_id = str(group["field_id"])
    section_keywords = _sanitize_candidate_values(field_id, "section_keywords", group["section_keywords"])
    tables = _sanitize_candidate_values(field_id, "tables", group["tables"])
    row_labels = _sanitize_candidate_values(field_id, "row_labels", group["row_labels"])
    evidence_count = int(group["evidence_count"])
    generality = _generality(group["generalities"], group["company_year_ids"])
    confidence = _group_confidence(group)
    needs_manual_check = "yes" if confidence != "high" or not (row_labels or group["xbrl_tags"]) else "no"
    recommended_action = _recommended_action({**group, "section_keywords": section_keywords, "tables": tables, "row_labels": row_labels})
    return {
        "field_id": field_id,
        "field_name_ja": group["field_name_ja"],
        "evidence_count": str(evidence_count),
        "company_year_ids": ";".join(group["company_year_ids"]),
        "proposed_xbrl_tags": ";".join(group["xbrl_tags"]),
        "proposed_section_keywords": ";".join(section_keywords),
        "proposed_tables": ";".join(tables),
        "proposed_row_labels": ";".join(row_labels),
        "proposed_scope": ";".join(group["scopes"]),
        "proposed_unit": ";".join(group["units"]),
        "generality": generality,
        "quotes": " || ".join(group["quotes"][:5]),
        "reviewed_value_examples": ";".join(group["reviewed_values"][:5]),
        "learning_source": ";".join(group["learning_sources"]),
        "confidence": confidence,
        "inference_notes": ";".join(group["inference_notes"][:5]),
        "needs_manual_check": needs_manual_check,
        "recommended_action": recommended_action,
    }


def _sanitize_candidate_values(field_id: str, kind: str, values: Sequence[str]) -> List[str]:
    cleaned = _unique([str(value).strip() for value in values if str(value).strip()])
    if field_id != "rd_expense":
        return cleaned
    if kind == "section_keywords":
        return [value for value in cleaned if "研究開発" in value]
    if kind in {"tables", "row_labels"}:
        return [value for value in cleaned if "研究開発費" in value]
    return cleaned


def _add_structured_value(parsed: MutableMapping[str, List[str]], key: str, value: str) -> None:
    if key == "RULE_HINT":
        _parse_rule_hint(parsed, value)
    elif key == "LOC":
        parts = _split_multi_value(value)
        _extend_unique(parsed["locations"], parts)
        _extend_unique(parsed["section_keywords"], _location_keywords(parts))
    elif key == "TABLE":
        _extend_unique(parsed["tables"], _split_multi_value(value))
    elif key == "LABEL":
        _extend_unique(parsed["row_labels"], _split_multi_value(value))
    elif key == "SCOPE":
        _extend_unique(parsed["scopes"], _split_multi_value(value))
    elif key == "UNIT":
        _extend_unique(parsed["units"], _split_multi_value(value))
    elif key == "QUOTE":
        _extend_unique(parsed["quotes"], [value])
    elif key == "XBRL_TAG":
        embedded_key, xbrl_value, embedded_value = _split_embedded_structured_value(value)
        xbrl_tags = _xbrl_tag_values(xbrl_value)
        if xbrl_tags:
            _extend_unique(parsed["xbrl_tags"], xbrl_tags)
        if embedded_key:
            _add_structured_value(parsed, embedded_key, embedded_value)
    elif key == "GENERALITY":
        _extend_unique(parsed["generalities"], _split_multi_value(value))


def _parse_rule_hint(parsed: MutableMapping[str, List[str]], value: str) -> None:
    for key, chunk_value in _iter_key_value_chunks(value):
        normalized_key = key.lower()
        values = _split_multi_value(chunk_value)
        if normalized_key in {"section_keywords", "section_keyword", "section"}:
            _extend_unique(parsed["section_keywords"], values)
        elif normalized_key in {"row_label", "row_labels", "label"}:
            _extend_unique(parsed["row_labels"], values)
        elif normalized_key in {"table", "table_name"}:
            _extend_unique(parsed["tables"], values)
        elif normalized_key in {"xbrl_tag", "xbrl_tags", "tag"}:
            _extend_unique(parsed["xbrl_tags"], values)
        elif normalized_key == "scope":
            _extend_unique(parsed["scopes"], values)
        elif normalized_key == "unit":
            _extend_unique(parsed["units"], values)


def _iter_key_value_chunks(text: str) -> Iterable[tuple[str, str]]:
    for chunk in text.split(";"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            yield key, value


def _split_embedded_structured_value(value: str) -> tuple[str, str, str]:
    pattern = r"(" + "|".join(sorted(STRUCTURED_KEYS, key=len, reverse=True)) + r")\s*[:=]\s*"
    match = re.search(pattern, value, flags=re.IGNORECASE)
    if not match:
        return "", value, ""
    return match.group(1).upper(), value[: match.start()].strip(), value[match.end() :].strip()


def _split_multi_value(value: str) -> List[str]:
    if not value:
        return []
    normalized = value.replace("｜", "|").replace("、", "|").replace(",", "|")
    parts = re.split(r"[|]", normalized)
    return [part.strip() for part in parts if part.strip()]


def _location_keywords(locations: Sequence[str]) -> List[str]:
    keywords: List[str] = []
    for location in locations:
        parts = [part.strip() for part in re.split(r">|＞", location) if part.strip()]
        if parts:
            keywords.append(parts[-1])
        else:
            keywords.append(location)
    return _unique(keywords)


def _reviewed_value(row: Dict[str, Any]) -> str:
    decision = str(row.get("review_decision", "")).strip().lower()
    if decision == "correct":
        return str(row.get("corrected_value", "") or "").strip()
    if decision == "accept":
        return str(row.get("extracted_value", "") or "").strip()
    return ""


def _row_key(row: Dict[str, Any]) -> tuple[str, str]:
    company_year_id = str(row.get("company_year_id", "") or "").strip()
    field_id = str(row.get("field_id", "") or "").strip()
    if not company_year_id or not field_id:
        return ("", "")
    return (company_year_id, field_id)


def _field_label_candidates(row: Dict[str, Any], field: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    _extend_unique(values, [str(row.get("field_name_ja") or field.get("field_name_ja") or "")])
    _extend_unique(values, _candidate_values(field.get("synonyms_ja", "")))
    return values


def _find_value_match(value: Any, text: str) -> Optional[tuple[int, int, str]]:
    normalized = unicodedata.normalize("NFKC", text or "").replace("，", ",")
    value_number = _parse_numeric_value(value)
    if value_number is None:
        needle = unicodedata.normalize("NFKC", str(value or "")).strip()
        if not needle:
            return None
        index = normalized.find(needle)
        return (index, index + len(needle), needle) if index >= 0 else None

    amount_pattern = r"(?:△|▲|-)?\(?\d{1,3}(?:,\s?\d{3})+(?:\.\d+)?\)?|(?:△|▲|-)?\(?\d+(?:\.\d+)?\)?"
    for match in re.finditer(amount_pattern, normalized):
        token_number = _parse_numeric_value(match.group(0))
        if token_number is None:
            continue
        if _numbers_equal(value_number, token_number):
            return (match.start(), match.end(), match.group(0))
    return None


def _parse_numeric_value(value: Any) -> Optional[float]:
    if is_blankish(value):
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    negative = text.startswith(("△", "▲"))
    text = text.replace("△", "").replace("▲", "").replace(",", "").replace(" ", "")
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _numbers_equal(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-9, abs(left) * 1e-9)


def _snippet_around(text: str, start: int, end: int, width: int = 220) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    left = max(0, start - width)
    right = min(len(normalized), end + width)
    snippet = re.sub(r"\s+", " ", normalized[left:right]).strip()
    return snippet[:500]


def _context_section_keywords(context: Dict[str, Any], snippet: str, field: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    _extend_unique(values, _listish(context.get("heading_keywords", [])))
    _extend_unique(values, _candidate_values(field.get("section_keywords", "")))
    heading = str(context.get("heading", "") or "").strip()
    if 0 < len(heading) <= 60:
        _extend_unique(values, [heading])
    for keyword in ["従業員の状況", "研究開発活動", "生産、受注及び販売の状況", "受注高", "完成工事高", "繰越高"]:
        if keyword in snippet:
            _extend_unique(values, [keyword])
    return values


def _context_table_and_row_labels(context: Dict[str, Any], snippet: str, labels: Sequence[str]) -> tuple[List[str], List[str]]:
    tables: List[str] = []
    row_labels: List[str] = []
    for keyword in _listish(context.get("table_keywords", [])):
        if _looks_like_table_keyword(keyword):
            _extend_unique(tables, [keyword])
        else:
            _extend_unique(row_labels, [keyword])
    for table in ["提出会社の状況", "当社における受注高及び売上高の状況", "受注工事高", "完成工事高", "手持工事高"]:
        if table in snippet:
            _extend_unique(tables, [table])
    for label in labels:
        if label and label in snippet:
            _extend_unique(row_labels, [label])
    _extend_unique(row_labels, _nearby_label_lines(snippet, labels))
    return tables, row_labels


def _nearby_label_lines(snippet: str, labels: Sequence[str]) -> List[str]:
    values: List[str] = []
    for label in labels:
        if label and label in snippet:
            _extend_unique(values, [label])
    for line in re.split(r"[|\n]", snippet):
        cleaned = line.strip()
        if not cleaned or len(cleaned) > 40:
            continue
        if re.search(r"\d", cleaned):
            continue
        if not re.search(r"[一-龯ぁ-んァ-ン]", cleaned):
            continue
        if any(blocked in cleaned for blocked in ["平成", "令和", "現在", "自", "至"]):
            continue
        if any(label and (label in cleaned or cleaned in label) for label in labels):
            _extend_unique(values, [cleaned])
    return values


def _looks_like_table_keyword(value: str) -> bool:
    return any(token in value for token in ["状況", "内訳", "一覧", "表", "セグメント", "受注高", "完成工事高", "手持工事高"])


def _context_scopes(context: Dict[str, Any], snippet: str, field: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    required = str(field.get("data_scope_required") or "").strip()
    _extend_unique(values, [required])
    if "提出会社" in snippet:
        _extend_unique(values, ["standalone"])
    elif "連結" in snippet:
        _extend_unique(values, ["consolidated"])
    elif not required:
        _extend_unique(values, [str(context.get("scope") or "").strip()])
    return values


def _context_units(context: Dict[str, Any], field: Dict[str, Any]) -> List[str]:
    target_unit = str(field.get("target_unit") or "").strip()
    context_unit = str(context.get("unit") or "").strip()
    if target_unit:
        return [target_unit]
    return _unique([context_unit])


def _trusted_xbrl_tag(row: Dict[str, Any], context: Dict[str, Any], reviewed_value: Any) -> str:
    tag = str(context.get("xbrl_element") or "").strip()
    if not tag:
        return ""
    decision = str(row.get("review_decision", "") or "").strip().lower()
    if decision == "accept":
        return tag
    context_value = context.get("value", "")
    if _value_equivalent(reviewed_value, context_value):
        return tag
    return ""


def _value_equivalent(left: Any, right: Any) -> bool:
    left_number = _parse_numeric_value(left)
    right_number = _parse_numeric_value(right)
    if left_number is not None and right_number is not None:
        return _numbers_equal(left_number, right_number)
    return str(left or "").strip() == str(right or "").strip()


def _context_confidence(source_type: str, has_row_label: bool, has_xbrl_tag: bool) -> float:
    if has_xbrl_tag:
        return 0.92
    if source_type in {"candidate_block", "company_candidate_block"} and has_row_label:
        return 0.86
    if source_type in {"source_audit", "saved_review", "review_queue"} and has_row_label:
        return 0.78
    return 0.62


def _numeric_confidences(values: Sequence[str]) -> List[float]:
    out: List[float] = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _group_confidence(group: Dict[str, Any]) -> str:
    scores = [float(score) for score in group.get("confidence_scores", []) if str(score) != ""]
    evidence_count = int(group.get("evidence_count") or 0)
    best_score = max(scores) if scores else 0.0
    if evidence_count >= 2 and best_score >= 0.78:
        return "high"
    if best_score >= 0.78 or evidence_count >= 2:
        return "medium"
    return "low"


def _listish(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return _candidate_values(value)


def _generality(generalities: Sequence[str], company_year_ids: Sequence[str]) -> str:
    if generalities:
        return ";".join(_unique(generalities))
    companies = {value.rsplit("_", 1)[0] for value in company_year_ids if "_" in value}
    if len(companies) >= 2:
        return "複数社で確認"
    if len(company_year_ids) >= 2:
        return "複数年度で確認"
    return "要確認"


def _recommended_action(group: Dict[str, Any]) -> str:
    actions: List[str] = []
    if group["xbrl_tags"]:
        actions.append("field_definition.xbrl_tag_candidates")
    if group["section_keywords"] or group["row_labels"] or group["tables"]:
        actions.append("LOCAL_TABLE rule")
    if group["scopes"]:
        actions.append("scope check")
    return ";".join(actions) if actions else "manual review"


def _apply_candidate_to_field(field_row: Dict[str, Any], candidate: Dict[str, Any]) -> List[str]:
    changed_columns: List[str] = []
    field_id = str(candidate.get("field_id", "") or "").strip()
    mapping = {
        "xbrl_tag_candidates": _xbrl_tag_values(candidate.get("proposed_xbrl_tags", "")),
        "section_keywords": _candidate_values(candidate.get("proposed_section_keywords", "")),
        "synonyms_ja": _candidate_values(candidate.get("proposed_row_labels", "")),
    }
    for column, additions in mapping.items():
        if _merge_semicolon_cell(field_row, column, additions):
            changed_columns.append(column)
    for column, kind in {"section_keywords": "section_keywords", "synonyms_ja": "row_labels"}.items():
        cleaned = _sanitize_candidate_values(field_id, kind, _candidate_values(field_row.get(column, "")))
        before = str(field_row.get(column, "") or "")
        after = ";".join(cleaned)
        if after != before:
            field_row[column] = after
            if column not in changed_columns:
                changed_columns.append(column)
    return changed_columns


def _apply_candidate_to_sections(sections: Dict[str, Any], candidate: Dict[str, Any]) -> tuple[str, bool]:
    field_id = str(candidate.get("field_id", "")).strip()
    if not field_id:
        return "", False
    section_keywords = _candidate_values(candidate.get("proposed_section_keywords", ""))
    table_names = _candidate_values(candidate.get("proposed_tables", ""))
    row_labels = _candidate_values(candidate.get("proposed_row_labels", ""))
    if not (section_keywords or table_names or row_labels):
        return "", False

    section_name = f"review_{field_id}"
    raw_section = sections.get(section_name)
    if not isinstance(raw_section, dict):
        raw_section = {}
    section = dict(raw_section)
    section.setdefault(
        "description",
        f"レビュー由来ヒント: {candidate.get('field_name_ja') or field_id}",
    )
    heading_keywords = section_keywords or table_names
    table_keywords = table_names + row_labels
    changed = False
    changed |= _merge_yaml_list(section, "heading_keywords", heading_keywords)
    changed |= _merge_yaml_list(section, "table_keywords", table_keywords)
    changed |= _merge_yaml_list(section, "review_table_keywords", table_names)
    changed |= _merge_yaml_list(section, "review_row_labels", row_labels)
    changed |= _merge_yaml_list_map(section, "review_row_labels_by_field", field_id, row_labels)
    changed |= _merge_yaml_scalar_map(section, "review_units_by_field", field_id, _candidate_values(candidate.get("proposed_unit", "")))
    changed |= _merge_yaml_list(section, "target_fields", [field_id])
    changed |= _sanitize_section_for_field(section, field_id)
    if changed or section_name not in sections:
        sections[section_name] = section
        return section_name, True
    return section_name, False


def _sanitize_section_for_field(section: Dict[str, Any], field_id: str) -> bool:
    if field_id != "rd_expense":
        return False
    changed = False
    for key, kind in {
        "heading_keywords": "section_keywords",
        "table_keywords": "row_labels",
        "review_table_keywords": "row_labels",
        "review_row_labels": "row_labels",
    }.items():
        before = section.get(key, [])
        before_list = before if isinstance(before, list) else _candidate_values(str(before))
        after = _sanitize_candidate_values(field_id, kind, before_list)
        if after != before_list:
            section[key] = after
            changed = True
    by_field = section.get("review_row_labels_by_field", {})
    if isinstance(by_field, dict):
        before = by_field.get(field_id, [])
        before_list = before if isinstance(before, list) else _candidate_values(str(before))
        after = _sanitize_candidate_values(field_id, "row_labels", before_list)
        if after != before_list:
            by_field[field_id] = after
            section["review_row_labels_by_field"] = by_field
            changed = True
    return changed


def _merge_yaml_list(section: Dict[str, Any], key: str, additions: Sequence[str]) -> bool:
    existing = section.get(key, [])
    if not isinstance(existing, list):
        existing = _candidate_values(str(existing))
    merged = [str(value).strip() for value in existing if str(value).strip()]
    before = list(merged)
    _extend_unique(merged, additions)
    section[key] = merged
    return merged != before


def _merge_yaml_list_map(section: Dict[str, Any], key: str, item_key: str, additions: Sequence[str]) -> bool:
    if not item_key or not additions:
        return False
    existing = section.get(key, {})
    if not isinstance(existing, dict):
        existing = {}
    copied = dict(existing)
    values = copied.get(item_key, [])
    if not isinstance(values, list):
        values = _candidate_values(values)
    merged = [str(value).strip() for value in values if str(value).strip()]
    before = list(merged)
    _extend_unique(merged, additions)
    copied[item_key] = merged
    section[key] = copied
    return merged != before or copied != existing


def _merge_yaml_scalar_map(section: Dict[str, Any], key: str, item_key: str, additions: Sequence[str]) -> bool:
    if not item_key or not additions:
        return False
    value = str(additions[0] or "").strip()
    if not value:
        return False
    existing = section.get(key, {})
    if not isinstance(existing, dict):
        existing = {}
    copied = dict(existing)
    before = copied.get(item_key, "")
    copied[item_key] = value
    section[key] = copied
    return before != value or copied != existing


def _merge_semicolon_cell(row: Dict[str, Any], column: str, additions: Sequence[str]) -> bool:
    if not additions:
        return False
    existing = _candidate_values(row.get(column, ""))
    merged = list(existing)
    _extend_unique(merged, additions)
    if merged == existing:
        return False
    row[column] = ";".join(merged)
    return True


def _candidate_values(value: Any) -> List[str]:
    normalized = str(value or "").replace("；", ";").replace("\n", ";").replace("||", ";")
    return _split_multi_value(normalized.replace(";", "|"))


def _xbrl_tag_values(value: Any) -> List[str]:
    blocked = {"なし", "無し", "none", "n/a", "na", "-", "未確認", "不明"}
    values = []
    for item in _candidate_values(value):
        normalized = item.strip()
        if normalized.lower() in blocked or normalized in blocked:
            continue
        if re.search(r"(" + "|".join(STRUCTURED_KEYS) + r")\s*[:=]", normalized, flags=re.IGNORECASE):
            continue
        values.append(normalized)
    return _unique(values)


def _backup_file(path: Path, stamp: str, backups: List[str]) -> None:
    if not path.exists():
        return
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    backups.append(str(backup))


def _write_rule_candidates(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RULE_CANDIDATE_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in RULE_CANDIDATE_COLUMNS})


def _extend_unique(target: List[str], values: Iterable[str]) -> None:
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in target:
            target.append(cleaned)


def _unique(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    _extend_unique(out, values)
    return out
