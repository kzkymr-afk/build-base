from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from yuho_auto_extract.config_loader import load_pipeline_config
from yuho_auto_extract.io_utils import ensure_parent, prefer_existing_table, read_table, write_jsonl, write_table
from yuho_auto_extract.normalizer import normalize_numeric
from yuho_auto_extract.xbrl_fact_store import (
    FACT_STORE_DIR,
    _candidate_priority,
    _fact_matches_candidate,
    _fact_matches_context,
    _fact_sort_key,
    _prefer_primary_facts,
)


EVIDENCE_DIR = Path("data") / "ai_evidence" / "major_financial"
PROMPT_CHUNK_DIRNAME = "prompt_chunks"
MAJOR_CATEGORIES = {"performance", "financial_position", "construction"}
VALID_DECISIONS = {"select", "keep_current", "unresolved", "not_applicable"}
DEFAULT_CHUNK_SIZE = 80
EXACT_LIMIT = 8
WEAK_LIMIT = 4


def build_major_financial_evidence_pack(root: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Dict[str, Any]:
    cfg = load_pipeline_config(root)
    fields = _major_financial_fields(cfg.field_definition)
    targets = _resolved_targets(root)
    facts = _read_fact_store(root)
    facts_by_company_year: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        facts_by_company_year[str(fact.get("company_year_id") or "")].append(fact)

    current_by_key = _current_values(root)
    review_by_key = _review_queue_rows(root)

    candidate_rows: List[Dict[str, Any]] = []
    group_rows: List[Dict[str, Any]] = []
    for target in targets:
        company_year_id = str(target.get("company_year_id") or "")
        target_facts = facts_by_company_year.get(company_year_id, [])
        for field in fields:
            group_candidates = _field_candidates(target_facts, field)
            candidate_ids: List[str] = []
            group_candidate_rows: List[Dict[str, Any]] = []
            for rank, (match_type, fact) in enumerate(group_candidates, start=1):
                row = _candidate_row(target, field, fact, match_type, rank)
                candidate_rows.append(row)
                group_candidate_rows.append(row)
                candidate_ids.append(str(row["candidate_id"]))
            key = (company_year_id, str(field.get("field_id") or ""))
            group_rows.append(
                _group_row(
                    target=target,
                    field=field,
                    candidate_ids=candidate_ids,
                    candidates=[_prompt_candidate(row) for row in group_candidate_rows],
                    current=current_by_key.get(key, {}),
                    review=review_by_key.get(key, {}),
                )
            )

    out_dir = root / EVIDENCE_DIR
    _clear_generated_files(out_dir)
    ensure_parent(out_dir / "manifest.json")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "candidate_facts.jsonl", candidate_rows)
    write_jsonl(out_dir / "candidate_groups.jsonl", group_rows)
    instructions_path = out_dir / "AI_REVIEW_INSTRUCTIONS.md"
    instructions_path.write_text(_instructions(), encoding="utf-8")
    chunk_files = _write_prompt_chunks(out_dir, group_rows, chunk_size=max(1, int(chunk_size or DEFAULT_CHUNK_SIZE)))

    status_counts = Counter(str(row.get("status") or "") for row in group_rows)
    match_counts = Counter(str(row.get("match_type") or "") for row in candidate_rows)
    manifest = {
        "generated_at_utc": _now_utc(),
        "evidence_dir": str(EVIDENCE_DIR),
        "target_categories": sorted(MAJOR_CATEGORIES),
        "fields": len(fields),
        "company_years": len(targets),
        "groups": len(group_rows),
        "candidate_facts": len(candidate_rows),
        "status_counts": dict(sorted(status_counts.items())),
        "match_type_counts": dict(sorted(match_counts.items())),
        "candidate_facts_path": str(EVIDENCE_DIR / "candidate_facts.jsonl"),
        "candidate_groups_path": str(EVIDENCE_DIR / "candidate_groups.jsonl"),
        "instructions_path": str(EVIDENCE_DIR / "AI_REVIEW_INSTRUCTIONS.md"),
        "prompt_chunks": [str(EVIDENCE_DIR / PROMPT_CHUNK_DIRNAME / path.name) for path in chunk_files],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def compare_major_financial_ai_decisions(
    root: Path,
    decisions_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    out_dir = root / EVIDENCE_DIR
    actual_decisions_path = decisions_path or out_dir / "ai_decisions.jsonl"
    if not actual_decisions_path.exists():
        raise FileNotFoundError(f"AI decisions file is missing: {actual_decisions_path}")
    decisions = read_table(actual_decisions_path)
    candidate_rows = read_table(out_dir / "candidate_facts.jsonl")
    group_rows = read_table(out_dir / "candidate_groups.jsonl")
    candidates_by_id = {str(row.get("candidate_id") or ""): row for row in candidate_rows}
    groups_by_key = {(str(row.get("company_year_id") or ""), str(row.get("field_id") or "")): row for row in group_rows}

    out: List[Dict[str, Any]] = []
    for decision in decisions:
        normalized = _validate_decision(decision, groups_by_key, candidates_by_id)
        key = (str(normalized["company_year_id"]), str(normalized["field_id"]))
        group = groups_by_key[key]
        selected = candidates_by_id.get(str(normalized.get("selected_candidate_id") or ""), {})
        current = group.get("current_selected") if isinstance(group.get("current_selected"), dict) else {}
        current_value = _first_value(current, ["value_normalized", "value", "value_raw"])
        selected_value = _first_value(selected, ["value_numeric", "value"])
        out.append(
            {
                "company_year_id": normalized["company_year_id"],
                "field_id": normalized["field_id"],
                "field_name_ja": group.get("field_name_ja", ""),
                "decision": normalized["decision"],
                "selected_candidate_id": normalized.get("selected_candidate_id", ""),
                "current_value": current_value,
                "selected_value": selected_value,
                "difference": _difference(current_value, selected_value) if selected else "",
                "match_status": _decision_match_status(normalized["decision"], current_value, selected_value, bool(selected)),
                "reason": normalized.get("reason", ""),
                "current_source_quote": _first_value(current, ["source_quote"]),
                "selected_source_quote": selected.get("source_quote", ""),
                "selected_element_id": selected.get("element_id", ""),
                "selected_context_id": selected.get("context_id", ""),
                "selected_match_type": selected.get("match_type", ""),
            }
        )

    output = output_path or (root / "data" / "reports" / "major_financial_ai_decision_compare.csv")
    written = write_table(output, out)
    counts = Counter(str(row.get("match_status") or "") for row in out)
    return {
        "decisions": len(decisions),
        "compared": len(out),
        "output": str(written.relative_to(root)) if written.is_relative_to(root) else str(written),
        "status_counts": dict(sorted(counts.items())),
    }


def _major_financial_fields(fields: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected = [
        dict(field)
        for field in fields
        if str(field.get("category") or "") in MAJOR_CATEGORIES
        and str(field.get("preferred_method") or "") == "XBRL_CSV"
        and str(field.get("field_id") or "")
    ]
    return sorted(selected, key=lambda row: (str(row.get("category") or ""), str(row.get("field_id") or "")))


def _resolved_targets(root: Path) -> List[Dict[str, Any]]:
    path = prefer_existing_table(root / "data" / "intermediate" / "target_documents.parquet")
    rows = read_table(path) if path.exists() else []
    return [
        row
        for row in rows
        if str(row.get("resolution_status") or "") in {"", "resolved"}
        and str(row.get("company_year_id") or "")
    ]


def _read_fact_store(root: Path) -> List[Dict[str, Any]]:
    path = prefer_existing_table(root / FACT_STORE_DIR / "facts.parquet")
    if not path.exists():
        raise FileNotFoundError(f"XBRL Fact Store is missing: {path}")
    return read_table(path)


def _field_candidates(facts: Sequence[Dict[str, Any]], field: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    numeric_facts = [fact for fact in facts if _is_numeric_fact(fact)]
    tag_candidates = _split_values(field.get("xbrl_tag_candidates"))
    context_filters = _split_values(field.get("context_filters"))
    exact = [
        fact
        for fact in numeric_facts
        if tag_candidates
        and _fact_matches_candidate(fact, tag_candidates)
        and _context_matches(fact, context_filters)
    ]
    exact = _sort_facts(_prefer_primary_facts(exact), tag_candidates)[:EXACT_LIMIT]
    exact_ids = {_fact_signature(fact) for fact in exact}
    weak = [
        fact
        for fact in numeric_facts
        if _fact_signature(fact) not in exact_ids
        and _context_matches(fact, context_filters)
        and _weak_matches_field(fact, field, tag_candidates)
    ]
    weak = _sort_facts(_prefer_primary_facts(weak), tag_candidates)[:WEAK_LIMIT]
    return [("exact", fact) for fact in exact] + [("weak", fact) for fact in weak]


def _context_matches(fact: Dict[str, Any], filters: Sequence[str]) -> bool:
    return not filters or _fact_matches_context(fact, filters)


def _sort_facts(facts: List[Dict[str, Any]], tag_candidates: Sequence[str]) -> List[Dict[str, Any]]:
    return sorted(facts, key=lambda row: (_candidate_priority(row, tag_candidates), _fact_sort_key(row)))


def _is_numeric_fact(fact: Dict[str, Any]) -> bool:
    if _truthy(fact.get("is_text_block")):
        return False
    value_type = str(fact.get("value_type") or "")
    if value_type and value_type != "numeric":
        return False
    return normalize_numeric(fact.get("value_numeric")) is not None or normalize_numeric(fact.get("value")) is not None


def _weak_matches_field(fact: Dict[str, Any], field: Dict[str, Any], tag_candidates: Sequence[str]) -> bool:
    local_name = str(fact.get("element_local_name") or "").lower()
    label = str(fact.get("item_name") or "")
    for token in tag_candidates:
        token_text = str(token or "").strip()
        if not token_text:
            continue
        if _is_ascii(token_text) and token_text.lower() in local_name:
            return True
    for term in _label_terms(field):
        if term and term in label:
            return True
    return False


def _label_terms(field: Dict[str, Any]) -> List[str]:
    raw = [str(field.get("field_name_ja") or "")] + _split_values(field.get("synonyms_ja"))
    terms: List[str] = []
    stop = {"連結", "単独", "個別", "合計", "当期", "純利益"}
    for value in raw:
        for token in value.replace("_", ";").replace("・", ";").split(";"):
            token = token.strip()
            if len(token) >= 2 and token not in stop and token not in terms:
                terms.append(token)
    return terms


def _candidate_row(target: Dict[str, Any], field: Dict[str, Any], fact: Dict[str, Any], match_type: str, rank: int) -> Dict[str, Any]:
    company_year_id = str(target.get("company_year_id") or fact.get("company_year_id") or "")
    field_id = str(field.get("field_id") or "")
    candidate_id = _candidate_id(company_year_id, field_id, fact)
    return _json_safe(
        {
            "candidate_id": candidate_id,
            "company_year_id": company_year_id,
            "operating_company_id": target.get("operating_company_id") or fact.get("operating_company_id") or "",
            "fiscal_year": target.get("fiscal_year") or fact.get("fiscal_year") or "",
            "field_id": field_id,
            "field_name_ja": field.get("field_name_ja", ""),
            "target_unit": field.get("target_unit", ""),
            "required_scope": field.get("data_scope_required", ""),
            "period_type": field.get("period_type", ""),
            "match_type": match_type,
            "rank": rank,
            "source_type": "xbrl_fact_store",
            "source_doc_id": fact.get("source_doc_id", ""),
            "source_file": fact.get("source_file", ""),
            "csv_file": fact.get("csv_file", ""),
            "csv_row": fact.get("csv_row", ""),
            "element_id": fact.get("element_id", ""),
            "element_local_name": fact.get("element_local_name", ""),
            "item_name": fact.get("item_name", ""),
            "context_id": fact.get("context_id", ""),
            "relative_year": fact.get("relative_year", ""),
            "normalized_scope": fact.get("normalized_scope", ""),
            "period_or_instant": fact.get("period_or_instant", ""),
            "unit": fact.get("unit", ""),
            "value": fact.get("value", ""),
            "value_numeric": normalize_numeric(fact.get("value_numeric")) if normalize_numeric(fact.get("value_numeric")) is not None else normalize_numeric(fact.get("value")),
            "source_quote": fact.get("source_quote", ""),
        }
    )


def _group_row(
    target: Dict[str, Any],
    field: Dict[str, Any],
    candidate_ids: Sequence[str],
    candidates: Sequence[Dict[str, Any]],
    current: Dict[str, Any],
    review: Dict[str, Any],
) -> Dict[str, Any]:
    status = "candidate_found" if candidate_ids else "no_candidate"
    return _json_safe(
        {
            "company_year_id": target.get("company_year_id", ""),
            "operating_company_id": target.get("operating_company_id", ""),
            "fiscal_year": target.get("fiscal_year", ""),
            "source_doc_id": target.get("docID") or target.get("source_doc_id") or "",
            "field_id": field.get("field_id", ""),
            "field_name_ja": field.get("field_name_ja", ""),
            "target_unit": field.get("target_unit", ""),
            "required_scope": field.get("data_scope_required", ""),
            "period_type": field.get("period_type", ""),
            "xbrl_tag_candidates": field.get("xbrl_tag_candidates", ""),
            "context_filters": field.get("context_filters", ""),
            "status": status,
            "candidate_count": len(candidate_ids),
            "candidate_ids": list(candidate_ids),
            "candidates": list(candidates),
            "current_selected": _current_summary(current),
            "review_queue": _review_summary(review),
            "validation_status": current.get("validation_status", ""),
            "review_status": current.get("review_status", ""),
        }
    )


def _prompt_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "candidate_id",
        "match_type",
        "rank",
        "value_numeric",
        "unit",
        "element_id",
        "item_name",
        "context_id",
        "normalized_scope",
        "period_or_instant",
        "source_quote",
    ]
    return {key: row.get(key, "") for key in keys}


def _current_values(root: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    path = prefer_existing_table(root / "data" / "final" / "final_master_long.parquet")
    rows = read_table(path) if path.exists() else []
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("company_year_id") or ""), str(row.get("field_id") or ""))
        if key[0] and key[1] and key not in out:
            out[key] = row
    return out


def _review_queue_rows(root: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    path = root / "data" / "review" / "review_queue.csv"
    rows = read_table(path) if path.exists() else []
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("company_year_id") or ""), str(row.get("field_id") or ""))
        if key[0] and key[1] and key not in out:
            out[key] = row
    return out


def _current_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    keys = [
        "value",
        "value_raw",
        "value_normalized",
        "unit_normalized",
        "data_scope",
        "xbrl_element",
        "context_ref",
        "source_quote",
        "extraction_method",
        "confidence",
        "validation_status",
        "review_status",
    ]
    return {key: row.get(key, "") for key in keys}


def _review_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    if not row:
        return {}
    keys = ["existing_value", "extracted_value", "difference", "difference_pct", "review_reason", "validation_status", "confidence"]
    return {key: row.get(key, "") for key in keys}


def _write_prompt_chunks(out_dir: Path, groups: Sequence[Dict[str, Any]], chunk_size: int) -> List[Path]:
    chunk_dir = out_dir / PROMPT_CHUNK_DIRNAME
    chunk_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for chunk_index, start in enumerate(range(0, len(groups), chunk_size), start=1):
        chunk = list(groups[start : start + chunk_size])
        path = chunk_dir / f"chunk_{chunk_index:03d}.md"
        path.write_text(_prompt_chunk(chunk_index, chunk), encoding="utf-8")
        paths.append(path)
    return paths


def _instructions() -> str:
    return """# Major Financial Evidence Review

BuildBaseが抽出した有報主要財務項目について、XBRL Fact Storeの候補から採用すべき candidate_id を選ぶためのレビュー用パックです。

## 固定ルール

- 値を推定・補完しないでください。
- `decision=select` の場合は、必ず提示された `candidate_id` から1つだけ選んでください。
- 現在値が妥当で候補変更が不要なら `decision=keep_current` としてください。
- 候補が足りない、定義が合わない、単位やスコープが怪しい場合は `decision=unresolved` としてください。
- 会社・年度・項目として対象外なら `decision=not_applicable` としてください。
- 出力はJSONLだけにしてください。

## 出力形式

```jsonl
{"company_year_id":"A_2024","field_id":"net_sales_consolidated","decision":"select","selected_candidate_id":"xbrl_...","reason":"連結・当期・売上高の定義に一致"}
{"company_year_id":"A_2024","field_id":"gross_profit_consolidated","decision":"unresolved","selected_candidate_id":"","reason":"売上総利益と完成工事総利益の候補が混在している"}
```
"""


def _prompt_chunk(chunk_index: int, groups: Sequence[Dict[str, Any]]) -> str:
    lines = [
        f"# Major Financial Evidence Prompt Chunk {chunk_index:03d}",
        "",
        "以下の各グループについて、出力形式に従ってJSONLを返してください。",
        "値は作らず、候補から選ぶか、keep_current / unresolved / not_applicable を返してください。",
        "",
        "```jsonl",
    ]
    for group in groups:
        lines.append(json.dumps(_prompt_group(group), ensure_ascii=False, sort_keys=True))
    lines.extend(["```", ""])
    return "\n".join(lines)


def _prompt_group(group: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "company_year_id": group.get("company_year_id", ""),
        "field_id": group.get("field_id", ""),
        "field_name_ja": group.get("field_name_ja", ""),
        "target_unit": group.get("target_unit", ""),
        "required_scope": group.get("required_scope", ""),
        "period_type": group.get("period_type", ""),
        "xbrl_tag_candidates": group.get("xbrl_tag_candidates", ""),
        "context_filters": group.get("context_filters", ""),
        "current_selected": group.get("current_selected", {}),
        "review_queue": group.get("review_queue", {}),
        "candidates": group.get("candidates", []),
    }


def _validate_decision(
    row: Dict[str, Any],
    groups_by_key: Dict[Tuple[str, str], Dict[str, Any]],
    candidates_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, str]:
    company_year_id = str(row.get("company_year_id") or "").strip()
    field_id = str(row.get("field_id") or "").strip()
    decision = str(row.get("decision") or "").strip()
    selected_candidate_id = str(row.get("selected_candidate_id") or "").strip()
    reason = str(row.get("reason") or "").strip()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"invalid decision: {decision}")
    if (company_year_id, field_id) not in groups_by_key:
        raise ValueError(f"decision target is not in candidate_groups: {company_year_id} / {field_id}")
    if decision == "select":
        if not selected_candidate_id:
            raise ValueError(f"selected_candidate_id is required for select: {company_year_id} / {field_id}")
        candidate = candidates_by_id.get(selected_candidate_id)
        if not candidate:
            raise ValueError(f"candidate_id not found: {selected_candidate_id}")
        if str(candidate.get("company_year_id") or "") != company_year_id or str(candidate.get("field_id") or "") != field_id:
            raise ValueError(f"candidate_id does not match decision target: {selected_candidate_id}")
    return {
        "company_year_id": company_year_id,
        "field_id": field_id,
        "decision": decision,
        "selected_candidate_id": selected_candidate_id,
        "reason": reason,
    }


def _decision_match_status(decision: str, current_value: Any, selected_value: Any, has_selected: bool) -> str:
    if decision in {"unresolved", "not_applicable", "keep_current"}:
        return decision
    if not has_selected:
        return "selected_missing"
    current_num = normalize_numeric(current_value)
    selected_num = normalize_numeric(selected_value)
    if current_num is not None and selected_num is not None:
        return "match" if math.isclose(current_num, selected_num, rel_tol=0, abs_tol=0.0001) else "mismatch"
    return "match" if str(current_value).strip() == str(selected_value).strip() else "mismatch"


def _difference(current_value: Any, selected_value: Any) -> Any:
    current_num = normalize_numeric(current_value)
    selected_num = normalize_numeric(selected_value)
    if current_num is None or selected_num is None:
        return ""
    return selected_num - current_num


def _first_value(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return ""


def _candidate_id(company_year_id: str, field_id: str, fact: Dict[str, Any]) -> str:
    digest = hashlib.sha1(_fact_signature(fact).encode("utf-8")).hexdigest()[:16]
    return f"xbrl_{company_year_id}_{field_id}_{digest}"


def _fact_signature(fact: Dict[str, Any]) -> str:
    return "|".join(
        str(fact.get(key) or "")
        for key in ["company_year_id", "source_doc_id", "csv_file", "csv_row", "element_id", "context_id"]
    )


def _split_values(value: Any) -> List[str]:
    return [part.strip() for part in str(value or "").replace(",", ";").split(";") if part.strip()]


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _is_ascii(value: str) -> bool:
    try:
        value.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and math.isnan(value):
        return ""
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            return str(value)
    return value


def _clear_generated_files(out_dir: Path) -> None:
    if not out_dir.exists():
        return
    for name in ["candidate_facts.jsonl", "candidate_groups.jsonl", "AI_REVIEW_INSTRUCTIONS.md", "manifest.json"]:
        path = out_dir / name
        if path.exists():
            path.unlink()
    chunk_dir = out_dir / PROMPT_CHUNK_DIRNAME
    if chunk_dir.exists():
        shutil.rmtree(chunk_dir)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
