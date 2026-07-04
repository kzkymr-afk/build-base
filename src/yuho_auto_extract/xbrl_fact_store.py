from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .config_loader import load_pipeline_config
from .io_utils import ensure_parent, is_blankish, prefer_existing_table, read_table, write_table
from .normalizer import normalize_numeric
from .xbrl_csv_parser import _not_found_record, _record_from_csv_row


FACT_STORE_DIR = Path("data") / "marts" / "xbrl_fact_store"
ENCODINGS = ("utf-16", "utf-8-sig", "cp932")


def build_xbrl_fact_store(root: Path, doc_id: str = "", company_year_id: str = "", merge_existing: bool = False) -> Dict[str, Any]:
    targets = _target_documents(root)
    selected_targets = list(_select_targets(targets, doc_id=doc_id, company_year_id=company_year_id))
    new_fact_rows: List[Dict[str, Any]] = []
    missing_documents: List[Dict[str, str]] = []
    for target in selected_targets:
        source_doc_id = str(target.get("docID") or target.get("source_doc_id") or "")
        csv_zip = root / "data" / "raw" / "documents" / source_doc_id / "csv.zip"
        if not source_doc_id or not csv_zip.exists():
            missing_documents.append(
                {
                    "company_year_id": str(target.get("company_year_id") or ""),
                    "source_doc_id": source_doc_id,
                    "reason": "csv_zip_not_found",
                }
            )
            continue
        new_fact_rows.extend(_iter_fact_rows(csv_zip, target))

    fact_rows = new_fact_rows
    if merge_existing:
        selected_company_years = {str(target.get("company_year_id") or "") for target in selected_targets}
        selected_doc_ids = {str(target.get("docID") or target.get("source_doc_id") or "") for target in selected_targets}
        fact_rows = [
            row
            for row in _read_fact_store(root)
            if str(row.get("company_year_id") or "") not in selected_company_years
            and str(row.get("source_doc_id") or "") not in selected_doc_ids
        ] + new_fact_rows

    out_dir = root / FACT_STORE_DIR
    facts_csv = write_table(out_dir / "facts.csv", fact_rows)
    facts_json = write_table(out_dir / "facts.json", fact_rows)
    facts_parquet = write_table(out_dir / "facts.parquet", fact_rows)
    context_rows = _context_index(fact_rows)
    context_csv = write_table(out_dir / "context_index.csv", context_rows)
    context_json = write_table(out_dir / "context_index.json", context_rows)
    context_parquet = write_table(out_dir / "context_index.parquet", context_rows)
    digest_path = out_dir / "document_digest.md"
    ensure_parent(digest_path)
    digest_path.write_text(_document_digest(fact_rows, missing_documents), encoding="utf-8")
    manifest = {
        "store_dir": str(FACT_STORE_DIR),
        "facts_csv": str(facts_csv.relative_to(root)),
        "facts_json": str(facts_json.relative_to(root)),
        "facts_parquet": str(facts_parquet.relative_to(root)),
        "context_csv": str(context_csv.relative_to(root)),
        "context_json": str(context_json.relative_to(root)),
        "context_parquet": str(context_parquet.relative_to(root)),
        "document_digest": str(digest_path.relative_to(root)),
        "targets": len(selected_targets),
        "documents_missing_csv": len(missing_documents),
        "facts": len(fact_rows),
        "facts_updated": len(new_fact_rows),
        "contexts": len(context_rows),
        "text_blocks": sum(1 for row in fact_rows if _truthy(row.get("is_text_block"))),
        "merge_existing": merge_existing,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def extract_from_xbrl_fact_store(
    root: Path,
    output_path: Path,
    write_pipeline: bool = False,
    company_year_id: str = "",
) -> Dict[str, int]:
    cfg = load_pipeline_config(root)
    facts = _read_fact_store(root)
    facts_by_company_year: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_company_year[str(fact.get("company_year_id") or "")].append(fact)

    rows: List[Dict[str, Any]] = []
    run_id = "xbrl-fact-store"
    targets = list(_select_targets(_target_documents(root), company_year_id=company_year_id))
    if company_year_id and not targets:
        targets = _targets_from_facts(facts_by_company_year.get(company_year_id, []))
    xbrl_fields = [field for field in cfg.field_definition if str(field.get("preferred_method") or "") == "XBRL_CSV"]
    for target in targets:
        company_year_id = str(target.get("company_year_id") or "")
        target_facts = facts_by_company_year.get(company_year_id, [])
        for field in xbrl_fields:
            candidates = _match_fact_candidates(target_facts, field, target)
            source_file = Path("xbrl_fact_store:facts")
            if not candidates:
                rows.append(_not_found_record(field, _target_for_record(target), run_id, source_file))
                continue
            chosen = candidates[0]
            rows.append(
                _record_from_csv_row(
                    _fact_to_csv_row(chosen),
                    field,
                    _target_for_record(target),
                    run_id,
                    source_file,
                    _effective_candidate_count(candidates),
                )
            )

    written = write_table(output_path, rows)
    if write_pipeline:
        write_table(root / "data" / "intermediate" / "xbrl_extracted_long.csv", rows)
        write_table(root / "data" / "intermediate" / "xbrl_extracted_long.parquet", rows)
    return {
        "facts": len(facts),
        "targets": len(targets),
        "fields": len(xbrl_fields),
        "rows": len(rows),
        "output_written": 1 if written.exists() else 0,
    }


def compare_xbrl_fact_store(
    root: Path,
    old_path: Path,
    new_path: Path,
    output_path: Path,
) -> Dict[str, int]:
    old_rows = read_table(prefer_existing_table(old_path)) if prefer_existing_table(old_path).exists() else []
    new_rows = read_table(prefer_existing_table(new_path)) if prefer_existing_table(new_path).exists() else []
    old_by_key = _first_by_key(old_rows)
    new_by_key = _first_by_key(new_rows)
    keys = sorted(set(old_by_key) | set(new_by_key))
    out: List[Dict[str, Any]] = []
    for company_year_id, field_id in keys:
        old = old_by_key.get((company_year_id, field_id), {})
        new = new_by_key.get((company_year_id, field_id), {})
        old_value = _record_value(old)
        new_value = _record_value(new)
        out.append(
            {
                "company_year_id": company_year_id,
                "field_id": field_id,
                "match_status": _match_status(old, new, old_value, new_value),
                "old_value": old_value,
                "new_value": new_value,
                "old_method": old.get("extraction_method", ""),
                "new_method": new.get("extraction_method", ""),
                "old_source_quote": old.get("source_quote", ""),
                "new_source_quote": new.get("source_quote", ""),
                "source_quote": new.get("source_quote") or old.get("source_quote", ""),
            }
        )
    written = write_table(output_path, out)
    counts = Counter(str(row["match_status"]) for row in out)
    return {
        "old_rows": len(old_rows),
        "new_rows": len(new_rows),
        "compared": len(out),
        "output_written": 1 if written.exists() else 0,
        **{f"status_{key}": value for key, value in sorted(counts.items())},
    }


def _target_documents(root: Path) -> List[Dict[str, Any]]:
    path = prefer_existing_table(root / "data" / "intermediate" / "target_documents.parquet")
    return read_table(path) if path.exists() else []


def _select_targets(
    targets: Sequence[Dict[str, Any]],
    doc_id: str = "",
    company_year_id: str = "",
) -> Iterator[Dict[str, Any]]:
    for target in targets:
        status = str(target.get("resolution_status") or "")
        if status and status != "resolved":
            continue
        target_doc_id = str(target.get("docID") or target.get("source_doc_id") or "")
        target_company_year_id = str(target.get("company_year_id") or "")
        if doc_id and target_doc_id != doc_id:
            continue
        if company_year_id and target_company_year_id != company_year_id:
            continue
        yield target


def _targets_from_facts(facts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not facts:
        return []
    first = facts[0]
    return [
        {
            "company_year_id": first.get("company_year_id", ""),
            "operating_company_id": first.get("operating_company_id", ""),
            "fiscal_year": first.get("fiscal_year", ""),
            "docID": first.get("source_doc_id", ""),
            "source_doc_id": first.get("source_doc_id", ""),
            "period_type": "semiannual_h1" if str(first.get("company_year_id") or "").endswith("H1") else "annual",
            "resolution_status": "resolved",
        }
    ]


def _iter_fact_rows(csv_zip_path: Path, target: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    with zipfile.ZipFile(csv_zip_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            text = _decode(zf.read(name))
            sample = text[:4096]
            delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            for row_index, row in enumerate(reader, start=1):
                fact = _fact_row(row, target, csv_zip_path, name, row_index)
                if fact:
                    yield fact


def _fact_row(
    row: Dict[str, Any],
    target: Dict[str, Any],
    csv_zip_path: Path,
    csv_file: str,
    row_index: int,
) -> Dict[str, Any]:
    element = _first_present(row, ["要素ID", "element_id", "element", "xbrl_element", "タグ", "tag"])
    item_name = _first_present(row, ["項目名", "item_name", "label", "名称"])
    value = _first_present(row, ["値", "value", "Value", "金額"])
    context_id = _first_present(row, ["コンテキストID", "contextRef", "context_ref", "コンテキスト"])
    relative_year = _first_present(row, ["相対年度", "relative_year"])
    consolidation_scope = _first_present(row, ["連結・個別", "consolidation_scope"])
    period = _first_present(row, ["期間・時点", "period_or_instant"])
    unit_id = _first_present(row, ["ユニットID", "unit_id"])
    unit = _first_present(row, ["単位", "unit", "Unit"])
    element_text = str(element or "")
    local_name = element_text.split(":")[-1]
    is_text_block = _is_text_block(element_text, local_name)
    value_numeric = normalize_numeric(value) if not is_text_block else None
    return {
        "company_year_id": target.get("company_year_id", ""),
        "operating_company_id": target.get("operating_company_id", ""),
        "fiscal_year": target.get("fiscal_year", ""),
        "source_doc_id": target.get("docID") or target.get("source_doc_id") or "",
        "source_file": str(csv_zip_path),
        "csv_file": csv_file,
        "csv_row": row_index,
        "element_id": element_text,
        "element_local_name": local_name,
        "item_name": item_name or "",
        "context_id": context_id or "",
        "relative_year": relative_year or "",
        "consolidation_scope": consolidation_scope or "",
        "normalized_scope": _normalized_scope(consolidation_scope, context_id),
        "period_or_instant": period or "",
        "unit_id": unit_id or "",
        "unit": unit or "",
        "value": "" if value is None else value,
        "value_numeric": value_numeric,
        "value_type": _value_type(value, value_numeric, is_text_block),
        "is_text_block": "yes" if is_text_block else "no",
        "source_quote": _source_quote(item_name, value),
    }


def _context_index(facts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for fact in facts:
        key = (str(fact.get("company_year_id") or ""), str(fact.get("context_id") or ""))
        if not key[0] or not key[1] or key in seen:
            continue
        seen[key] = {
            "company_year_id": fact.get("company_year_id", ""),
            "operating_company_id": fact.get("operating_company_id", ""),
            "fiscal_year": fact.get("fiscal_year", ""),
            "source_doc_id": fact.get("source_doc_id", ""),
            "context_id": fact.get("context_id", ""),
            "relative_year": fact.get("relative_year", ""),
            "consolidation_scope": fact.get("consolidation_scope", ""),
            "normalized_scope": fact.get("normalized_scope", ""),
            "period_or_instant": fact.get("period_or_instant", ""),
            "segment_suffix": _segment_suffix(str(fact.get("context_id") or "")),
        }
    return list(seen.values())


def _document_digest(facts: Sequence[Dict[str, Any]], missing_documents: Sequence[Dict[str, str]]) -> str:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        grouped[str(fact.get("company_year_id") or "")].append(fact)
    lines = ["# XBRL Fact Store Digest", ""]
    if missing_documents:
        lines.extend(["## Missing CSV", ""])
        for row in missing_documents:
            lines.append(f"- `{row['company_year_id']}` / `{row['source_doc_id']}`: {row['reason']}")
        lines.append("")
    for company_year_id in sorted(grouped):
        rows = grouped[company_year_id]
        source_doc_id = str(rows[0].get("source_doc_id") or "")
        numeric_rows = [row for row in rows if row.get("value_type") == "numeric"]
        text_blocks = [row for row in rows if _truthy(row.get("is_text_block"))]
        scopes = Counter(str(row.get("normalized_scope") or "unknown") for row in rows)
        lines.extend(
            [
                f"## {company_year_id}",
                "",
                f"- source_doc_id: `{source_doc_id}`",
                f"- facts: {len(rows)}",
                f"- numeric_facts: {len(numeric_rows)}",
                f"- text_blocks: {len(text_blocks)}",
                f"- scopes: {', '.join(f'{key}={value}' for key, value in sorted(scopes.items()))}",
                "",
                "### Numeric Facts Sample",
                "",
                "| element | label | context | unit | value |",
                "|---|---|---|---|---|",
            ]
        )
        for row in numeric_rows[:40]:
            lines.append(
                "| "
                + " | ".join(
                    _md_cell(row.get(key, ""))
                    for key in ["element_local_name", "item_name", "context_id", "unit", "value"]
                )
                + " |"
            )
        if text_blocks:
            lines.extend(["", "### Text Blocks Sample", ""])
            for row in text_blocks[:10]:
                lines.append(f"- `{row.get('element_local_name', '')}`: {_md_inline(str(row.get('source_quote') or ''))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _read_fact_store(root: Path) -> List[Dict[str, Any]]:
    path = prefer_existing_table(root / FACT_STORE_DIR / "facts.parquet")
    return read_table(path) if path.exists() else []


def _match_fact_candidates(facts: Sequence[Dict[str, Any]], field: Dict[str, Any], target: Dict[str, Any]) -> List[Dict[str, Any]]:
    tag_candidates = _split_values(field.get("xbrl_tag_candidates"))
    context_filters = _period_context_filters(_split_values(field.get("context_filters")), str(target.get("period_type") or "annual"))
    if not tag_candidates:
        return []
    rows = [fact for fact in facts if _fact_matches_candidate(fact, tag_candidates)]
    if context_filters:
        rows = [fact for fact in rows if _fact_matches_context(fact, context_filters)]
    rows = _prefer_primary_facts(rows)
    rows = _prefer_best_candidate_priority(rows, tag_candidates)
    rows.sort(key=_fact_sort_key)
    return rows


def _fact_matches_candidate(fact: Dict[str, Any], candidates: Sequence[str]) -> bool:
    local_name = str(fact.get("element_local_name") or "")
    item_name = str(fact.get("item_name") or "")
    return any(_candidate_token_matches(local_name, item_name, token) for token in candidates)


def _fact_matches_context(fact: Dict[str, Any], filters: Sequence[str]) -> bool:
    context = str(fact.get("context_id") or "")
    relative_year = str(fact.get("relative_year") or "")
    period = str(fact.get("period_or_instant") or "")
    scope = str(fact.get("normalized_scope") or "")
    period_token = next((token for token in filters if token in _PERIOD_CONTEXT_TOKENS), "")
    if period_token and "NonConsolidatedMember" in filters:
        if context != f"{period_token}_NonConsolidatedMember":
            return False
    elif period_token and "ConsolidatedMember" in filters:
        if context not in {period_token, f"{period_token}_ConsolidatedMember"}:
            return False
    elif period_token and not context.startswith(period_token):
        if not (_is_duration_period_token(period_token) and _is_current_relative_year(relative_year) and "期間" in period):
            if not (_is_instant_period_token(period_token) and _is_current_relative_year(relative_year) and "時点" in period):
                return False
    for token in filters:
        if token in context:
            continue
        if _is_duration_period_token(token) and _is_current_relative_year(relative_year) and "期間" in period:
            continue
        if _is_instant_period_token(token) and _is_current_relative_year(relative_year) and "時点" in period:
            continue
        if token == "ConsolidatedMember" and (scope == "consolidated" or (period_token and context == period_token)):
            continue
        if token in {"NonConsolidatedMember", "StandaloneMember"} and scope == "standalone":
            continue
        return False
    return True


_PERIOD_CONTEXT_TOKENS = {
    "CurrentYearDuration",
    "CurrentYearInstant",
    "CurrentYTDDuration",
    "CurrentQuarterInstant",
}


def _period_context_filters(filters: Sequence[str], period_type: str) -> List[str]:
    if period_type != "semiannual_h1":
        return list(filters)
    mapped = []
    for token in filters:
        if token == "CurrentYearDuration":
            mapped.append("CurrentYTDDuration")
        elif token == "CurrentYearInstant":
            mapped.append("CurrentQuarterInstant")
        else:
            mapped.append(token)
    return mapped


def _is_current_relative_year(relative_year: str) -> bool:
    return any(token in relative_year for token in ("当期", "当年度", "当四半期", "当中間"))


def _is_duration_period_token(token: str) -> bool:
    return token in {"CurrentYearDuration", "CurrentYTDDuration"}


def _is_instant_period_token(token: str) -> bool:
    return token in {"CurrentYearInstant", "CurrentQuarterInstant"}


def _prefer_primary_facts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    non_text = [row for row in rows if not _truthy(row.get("is_text_block"))]
    if non_text:
        rows = non_text
    non_summary = [row for row in rows if "SummaryOfBusinessResults" not in str(row.get("element_id") or "")]
    return non_summary or rows


def _prefer_best_candidate_priority(rows: List[Dict[str, Any]], candidates: Sequence[str]) -> List[Dict[str, Any]]:
    if not rows or not candidates:
        return rows
    ranked = [(_candidate_priority(row, candidates), row) for row in rows]
    best = min(priority for priority, _row in ranked)
    if best >= len(candidates):
        return rows
    return [row for priority, row in ranked if priority == best]


def _candidate_priority(row: Dict[str, Any], candidates: Sequence[str]) -> int:
    local_name = str(row.get("element_local_name") or "")
    label = str(row.get("item_name") or "")
    for index, token in enumerate(candidates):
        if _candidate_token_matches(local_name, label, token):
            return index
    return len(candidates)


def _candidate_token_matches(local_name: str, label: str, token: str) -> bool:
    if _is_ascii_token(token):
        return local_name.lower() == token.lower()
    return token in label


def _fact_sort_key(row: Dict[str, Any]) -> Tuple[int, str, int]:
    unit = str(row.get("unit") or "")
    return (0 if unit in {"円", "千円", "百万円", "億円", "%"} else 1, str(row.get("csv_file") or ""), _safe_int(row.get("csv_row")))


def _fact_to_csv_row(fact: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "要素ID": fact.get("element_id"),
        "項目名": fact.get("item_name"),
        "コンテキストID": fact.get("context_id"),
        "相対年度": fact.get("relative_year"),
        "連結・個別": fact.get("consolidation_scope"),
        "期間・時点": fact.get("period_or_instant"),
        "ユニットID": fact.get("unit_id"),
        "単位": fact.get("unit"),
        "値": fact.get("value"),
        "_source_csv": fact.get("csv_file"),
    }


def _effective_candidate_count(rows: Sequence[Dict[str, Any]]) -> int:
    values = {str(row.get("value") or "").strip() for row in rows}
    values.discard("")
    return len(values) if values else len(rows)


def _first_by_key(rows: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("company_year_id") or ""), str(row.get("field_id") or ""))
        if not key[0] or not key[1] or key in out:
            continue
        out[key] = row
    return out


def _record_value(row: Dict[str, Any]) -> str:
    for key in ("value", "value_normalized", "value_raw"):
        value = row.get(key)
        if not is_blankish(value):
            return str(value)
    return ""


def _match_status(old: Dict[str, Any], new: Dict[str, Any], old_value: str, new_value: str) -> str:
    if not old and new:
        return "new_only"
    if old and not new:
        return "old_only"
    if not old_value and not new_value:
        return "both_blank"
    old_num = normalize_numeric(old_value)
    new_num = normalize_numeric(new_value)
    if old_num is not None and new_num is not None:
        return "match" if abs(old_num - new_num) <= 0.000001 else "mismatch"
    return "match" if old_value == new_value else "mismatch"


def _target_for_record(target: Dict[str, Any]) -> Dict[str, Any]:
    copied = dict(target)
    copied["docID"] = target.get("docID") or target.get("source_doc_id")
    return copied


def _normalized_scope(scope: Any, context_id: Any) -> str:
    scope_text = str(scope or "")
    context = str(context_id or "")
    if "個別" in scope_text or "単独" in scope_text or "NonConsolidated" in context or "Standalone" in context:
        return "standalone"
    if "連結" in scope_text or "ConsolidatedMember" in context:
        return "consolidated"
    if "Segment" in context or "Member" in context:
        return "segment"
    return ""


def _segment_suffix(context_id: str) -> str:
    if "_" not in context_id:
        return ""
    return context_id.split("_")[-1]


def _value_type(value: Any, value_numeric: Optional[float], is_text_block: bool) -> str:
    if is_text_block:
        return "text"
    if is_blankish(value):
        return "blank"
    if value_numeric is not None:
        return "numeric"
    return "string"


def _source_quote(item_name: Any, value: Any, limit: int = 500) -> str:
    if is_blankish(value):
        text = str(item_name or "")
    elif item_name:
        text = f"{item_name}: {value}"
    else:
        text = str(value)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _is_text_block(element_id: str, local_name: str) -> bool:
    return "TextBlock" in element_id or local_name.endswith("TextBlock")


def _first_present(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key in row and not is_blankish(row[key]):
            return row[key]
        if key.lower() in lowered and not is_blankish(lowered[key.lower()]):
            return lowered[key.lower()]
    return None


def _split_values(value: Any) -> List[str]:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return []
    return [part.strip() for part in re.split(r"[;\n]+", text) if part.strip() and part.strip().lower() != "nan"]


def _decode(content: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _is_ascii_token(token: str) -> bool:
    try:
        token.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value or "0")))
    except ValueError:
        return 0


def _md_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")[:120]


def _md_inline(value: str) -> str:
    return value.replace("\n", " ")[:240]
