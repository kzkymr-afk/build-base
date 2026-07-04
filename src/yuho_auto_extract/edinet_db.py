from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .io_utils import prefer_existing_table, read_table, write_jsonl, write_table
from .local_table_extractor import extract_local_table_rows
from .xbrl_csv_parser import _not_found_record, _record_from_csv_row


ENCODINGS = ("utf-16", "utf-8-sig", "cp932")


def build_edinet_db(root: Path, db_path: Path) -> Dict[str, int]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        _create_schema(conn)
        counts: Dict[str, int] = {}
        counts["company_master"] = _insert_named_rows(conn, "company_master", _read_optional(root / "config" / "company_master.csv"))
        counts["company_year_master"] = _insert_named_rows(conn, "company_year_master", _read_optional(root / "config" / "company_year_master.csv"))
        counts["field_definition"] = _insert_field_definition(conn, _read_optional(root / "config" / "field_definition.csv"))
        counts["document_index"] = _insert_named_rows(conn, "document_index", _read_optional(root / "data" / "intermediate" / "document_index.parquet"))
        targets = _read_optional(root / "data" / "intermediate" / "target_documents.parquet")
        counts["target_documents"] = _insert_target_documents(conn, targets)
        counts["download_manifest"] = _insert_named_rows(conn, "download_manifest", _read_optional(root / "data" / "raw" / "download_manifest.parquet"))
        counts["candidate_blocks"] = _insert_candidate_blocks(conn, _read_optional(root / "data" / "intermediate" / "candidate_blocks.jsonl"))
        counts["xbrl_extracted_long"] = _insert_named_rows(conn, "xbrl_extracted_long", _read_optional(root / "data" / "intermediate" / "xbrl_extracted_long.parquet"))
        counts["normalized_validated_long"] = _insert_named_rows(conn, "normalized_validated_long", _read_optional(root / "data" / "intermediate" / "normalized_validated_long.parquet"))
        counts["final_master_long"] = _insert_named_rows(conn, "final_master_long", _read_optional(root / "data" / "final" / "final_master_long.csv"))
        counts["xbrl_facts"] = _insert_xbrl_facts(conn, root, targets)
        _create_indexes(conn)
        conn.commit()
        return counts
    finally:
        conn.close()


def extract_from_edinet_db(
    root: Path,
    db_path: Path,
    output_path: Path,
    write_pipeline: bool = True,
    period_type: str = "annual",
) -> Dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        fields = [_json_row(row) for row in conn.execute("select row_json from field_definition order by field_id")]
        targets = [
            target
            for target in [_json_row(row) for row in conn.execute("select row_json from target_documents order by company_year_id")]
            if _target_period_matches(target, period_type)
        ]
        xbrl_rows = _extract_xbrl_from_db(conn, fields, targets)
        blocks = [_json_row(row) for row in conn.execute("select row_json from candidate_blocks")]
        local_rows = extract_local_table_rows(blocks)
        combined = xbrl_rows + local_rows
        written = write_table(output_path, combined)
        if write_pipeline:
            write_table(root / "data" / "intermediate" / "xbrl_extracted_long.csv", xbrl_rows)
            write_jsonl(root / "data" / "intermediate" / "ai_extracted_long.jsonl", local_rows)
        return {
            "combined_rows": len(combined),
            "xbrl_rows": len(xbrl_rows),
            "local_rows": len(local_rows),
            "targets": len(targets),
            "output_written": 1 if written.exists() else 0,
        }
    finally:
        conn.close()


def split_local_review_rows(root: Path) -> Dict[str, int]:
    rows = read_table(root / "data" / "review" / "review_queue.csv")
    local_rows = [row for row in rows if _is_local_review_row(row)]
    accepted = [dict(row) for row in local_rows if _can_auto_accept_local_row(row)]
    manual = [dict(row) for row in local_rows if not _can_auto_accept_local_row(row)]
    for row in accepted:
        row["review_decision"] = "accept"
        row["reviewer_note"] = "machine accepted: local table extraction and validation_status=pass"
        row["reviewer"] = "codex_local_rule"
        row["reviewed_at"] = _timestamp()
    for row in manual:
        row["reviewer_note"] = "manual check required: local table extraction did not pass validation cleanly"
    write_table(root / "data" / "review" / "review_resolved_local_pass.csv", accepted)
    write_table(root / "data" / "review" / "review_resolved_local_pass.xlsx", accepted)
    write_table(root / "data" / "review" / "review_queue_local_needs_manual.csv", manual)
    write_table(root / "data" / "review" / "review_queue_local_needs_manual.xlsx", manual)
    return {"accepted_rows": len(accepted), "manual_rows": len(manual), "local_rows": len(local_rows)}


def _is_local_review_row(row: Dict[str, Any]) -> bool:
    reason = str(row.get("review_reason") or "")
    return "local_rule_review_required" in reason or "local_company_pattern_review_required" in reason


def _can_auto_accept_local_row(row: Dict[str, Any]) -> bool:
    if str(row.get("validation_status") or "") != "pass":
        return False
    field_id = str(row.get("field_id") or "")
    auto_accept_fields = {
        "building_orders_total",
        "building_orders_private",
        "building_orders_government",
        "building_orders_overseas",
        "completed_building",
        "backlog_building_next",
    }
    return field_id in auto_accept_fields


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        pragma journal_mode = wal;
        pragma synchronous = normal;

        create table named_rows (
            table_name text not null,
            row_json text not null
        );

        create table field_definition (
            field_id text primary key,
            preferred_method text,
            target_unit text,
            data_scope_required text,
            xbrl_tag_candidates text,
            context_filters text,
            row_json text not null
        );

        create table target_documents (
            company_year_id text primary key,
            operating_company_id text,
            fiscal_year integer,
            source_doc_id text,
            resolution_status text,
            row_json text not null
        );

        create table candidate_blocks (
            candidate_block_id text primary key,
            company_year_id text,
            source_doc_id text,
            section_name text,
            locator_score real,
            heading_text text,
            row_json text not null
        );

        create table xbrl_facts (
            id integer primary key autoincrement,
            company_year_id text,
            operating_company_id text,
            fiscal_year integer,
            source_doc_id text,
            csv_file text,
            element_id text,
            item_name text,
            context_id text,
            relative_year text,
            consolidation_scope text,
            period_or_instant text,
            unit_id text,
            unit text,
            value text
        );
        """
    )


def _create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create index if not exists idx_named_rows_table on named_rows(table_name);
        create index if not exists idx_target_documents_doc on target_documents(source_doc_id);
        create index if not exists idx_candidate_blocks_year_section on candidate_blocks(company_year_id, section_name);
        create index if not exists idx_xbrl_facts_year_element on xbrl_facts(company_year_id, element_id);
        create index if not exists idx_xbrl_facts_context on xbrl_facts(company_year_id, context_id);
        create index if not exists idx_xbrl_facts_doc on xbrl_facts(source_doc_id);
        """
    )


def _insert_named_rows(conn: sqlite3.Connection, table_name: str, rows: Sequence[Dict[str, Any]]) -> int:
    payload = [(table_name, _json(row)) for row in rows]
    conn.executemany("insert into named_rows(table_name, row_json) values (?, ?)", payload)
    return len(payload)


def _insert_field_definition(conn: sqlite3.Connection, rows: Sequence[Dict[str, Any]]) -> int:
    payload = [
        (
            str(row.get("field_id") or ""),
            str(row.get("preferred_method") or ""),
            str(row.get("target_unit") or ""),
            str(row.get("data_scope_required") or ""),
            str(row.get("xbrl_tag_candidates") or ""),
            str(row.get("context_filters") or ""),
            _json(row),
        )
        for row in rows
        if row.get("field_id")
    ]
    conn.executemany(
        """
        insert into field_definition(
            field_id, preferred_method, target_unit, data_scope_required,
            xbrl_tag_candidates, context_filters, row_json
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _insert_target_documents(conn: sqlite3.Connection, rows: Sequence[Dict[str, Any]]) -> int:
    payload = [
        (
            str(row.get("company_year_id") or ""),
            str(row.get("operating_company_id") or ""),
            _int_or_none(row.get("fiscal_year")),
            str(row.get("docID") or ""),
            str(row.get("resolution_status") or ""),
            _json(row),
        )
        for row in rows
        if row.get("company_year_id")
    ]
    conn.executemany(
        """
        insert or replace into target_documents(
            company_year_id, operating_company_id, fiscal_year, source_doc_id,
            resolution_status, row_json
        ) values (?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _insert_candidate_blocks(conn: sqlite3.Connection, rows: Sequence[Dict[str, Any]]) -> int:
    payload = [
        (
            str(row.get("candidate_block_id") or ""),
            str(row.get("company_year_id") or ""),
            str(row.get("source_doc_id") or ""),
            str(row.get("section_name") or ""),
            _float_or_none(row.get("locator_score")),
            str(row.get("heading_text") or ""),
            _json(row),
        )
        for row in rows
        if row.get("candidate_block_id")
    ]
    conn.executemany(
        """
        insert or replace into candidate_blocks(
            candidate_block_id, company_year_id, source_doc_id, section_name,
            locator_score, heading_text, row_json
        ) values (?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    return len(payload)


def _insert_xbrl_facts(conn: sqlite3.Connection, root: Path, targets: Sequence[Dict[str, Any]]) -> int:
    total = 0
    for target in targets:
        if str(target.get("resolution_status") or "") != "resolved":
            continue
        doc_id = str(target.get("docID") or "")
        csv_zip = root / "data" / "raw" / "documents" / doc_id / "csv.zip"
        if not csv_zip.exists():
            continue
        rows = list(_iter_xbrl_fact_rows(csv_zip, target))
        conn.executemany(
            """
            insert into xbrl_facts(
                company_year_id, operating_company_id, fiscal_year, source_doc_id,
                csv_file, element_id, item_name, context_id, relative_year,
                consolidation_scope, period_or_instant, unit_id, unit, value
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        total += len(rows)
    return total


def _iter_xbrl_fact_rows(csv_zip_path: Path, target: Dict[str, Any]) -> Iterator[Tuple[Any, ...]]:
    with zipfile.ZipFile(csv_zip_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            text = _decode(zf.read(name))
            sample = text[:4096]
            delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            for row in reader:
                yield (
                    target.get("company_year_id"),
                    target.get("operating_company_id"),
                    _int_or_none(target.get("fiscal_year")),
                    target.get("docID"),
                    name,
                    _first_present(row, ["要素ID", "element_id", "element", "xbrl_element", "タグ", "tag"]),
                    _first_present(row, ["項目名", "item_name", "label", "名称"]),
                    _first_present(row, ["コンテキストID", "contextRef", "context_ref", "コンテキスト"]),
                    _first_present(row, ["相対年度", "relative_year"]),
                    _first_present(row, ["連結・個別", "consolidation_scope"]),
                    _first_present(row, ["期間・時点", "period_or_instant"]),
                    _first_present(row, ["ユニットID", "unit_id"]),
                    _first_present(row, ["単位", "unit", "Unit"]),
                    _first_present(row, ["値", "value", "Value", "金額"]),
                )


def _extract_xbrl_from_db(conn: sqlite3.Connection, fields: Sequence[Dict[str, Any]], targets: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    run_id = "edinet-db"
    resolved_targets = [target for target in targets if str(target.get("resolution_status") or "") == "resolved"]
    for target in resolved_targets:
        for field in fields:
            if str(field.get("preferred_method") or "") != "XBRL_CSV":
                continue
            segment_record = _extract_segment_record_from_db(conn, field, target, run_id)
            if segment_record is not None:
                rows.append(segment_record)
                continue
            cost_record = _extract_cost_detail_record_from_db(conn, field, target, run_id)
            if cost_record is not None:
                rows.append(cost_record)
                continue
            candidates = _query_fact_candidates(
                conn,
                str(target.get("company_year_id") or ""),
                field,
                period_type=str(target.get("period_type") or "annual"),
            )
            source_file = Path("edinet.db:xbrl_facts")
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
                    _effective_fact_candidate_count(candidates),
                )
            )
    return rows


SEGMENT_SALES_ELEMENTS = ("RevenuesFromExternalCustomers", "NetSales")
SEGMENT_PROFIT_ELEMENTS = ("OperatingIncome", "BusinessProfitLossIFRS")
COST_DETAIL_LABELS = {
    "cost_materials": "材料費",
    "cost_labor": "労務費",
    "cost_subcontract": "外注費",
    "cost_expense": "経費",
}
COST_DETAIL_TEXT_BLOCK_SUFFIXES = (
    "CostReportOfCompletedConstructionContractsTextBlock",
    "CostOfCompletedConstructionTextBlock",
    "ReportOfCostsOfRevenuesOnConstructionServicesTextBlock",
    "DetailedScheduleOfCostOfSalesTextBlock",
)
STACKED_COST_LABEL_ORDER = ("材料費", "労務費", "労務外注費", "外注費", "経費", "人件費")


def _extract_segment_record_from_db(
    conn: sqlite3.Connection,
    field: Dict[str, Any],
    target: Dict[str, Any],
    run_id: str,
) -> Optional[Dict[str, Any]]:
    field_id = str(field.get("field_id") or "")
    if field_id not in {"segment_sales_construction", "segment_profit_construction"}:
        return None
    elements = SEGMENT_SALES_ELEMENTS if field_id == "segment_sales_construction" else SEGMENT_PROFIT_ELEMENTS
    candidates = _query_segment_fact_candidates(conn, str(target.get("company_year_id") or ""), elements)
    source_file = Path("edinet.db:xbrl_facts")
    if not candidates:
        return _not_found_record(field, _target_for_record(target), run_id, source_file)
    selected = _select_segment_facts(candidates, elements)
    if not selected:
        return _not_found_record(field, _target_for_record(target), run_id, source_file)
    value = sum(_fact_numeric_value(row) or 0.0 for row in selected)
    unit = selected[0]["unit"] or field.get("target_unit")
    contexts = [str(row["context_id"] or "") for row in selected]
    quote_parts = [
        f"{str(row['item_name'] or row['element_id'] or '').strip()}[{_segment_suffix(str(row['context_id'] or ''))}]={row['value']}"
        for row in selected[:6]
    ]
    return {
        "run_id": run_id,
        "company_year_id": target.get("company_year_id"),
        "operating_company_id": target.get("operating_company_id"),
        "fiscal_year": target.get("fiscal_year"),
        "source_doc_id": target.get("docID") or target.get("source_doc_id"),
        "source_file": str(source_file),
        "source_heading": ";".join(_segment_suffix(context) for context in contexts[:6]),
        "source_quote": "; ".join(quote_parts),
        "field_id": field_id,
        "value_raw": value,
        "unit_raw": unit,
        "context_ref": ";".join(contexts),
        "xbrl_element": "+".join(sorted({str(row["element_id"] or "") for row in selected})),
        "data_scope": "segment",
        "extraction_method": "XBRL_SEGMENT_CONTEXT",
        "confidence": 0.90 if len(selected) == 1 else 0.86,
        "review_required": False,
        "review_reason": "",
        "candidate_count": len(selected),
    }


def _query_segment_fact_candidates(
    conn: sqlite3.Connection,
    company_year_id: str,
    element_local_names: Sequence[str],
) -> List[sqlite3.Row]:
    element_clause = " or ".join("element_id like ?" for _ in element_local_names)
    sql = f"""
        select * from xbrl_facts
        where company_year_id = ?
          and context_id like 'CurrentYearDuration%'
          and ({element_clause})
          and value not in ('', '－', '-', '—', '―')
        order by context_id, element_id, rowid
    """
    params = [company_year_id] + [f"%:{name}" for name in element_local_names]
    rows = list(conn.execute(sql, params))
    return [row for row in rows if _is_construction_segment_context(str(row["context_id"] or "")) and _fact_numeric_value(row) is not None]


def _select_segment_facts(rows: Sequence[sqlite3.Row], element_priority: Sequence[str]) -> List[sqlite3.Row]:
    for local_name in element_priority:
        element_rows = [row for row in rows if str(row["element_id"] or "").split(":")[-1] == local_name]
        if not element_rows:
            continue
        broad_rows = [row for row in element_rows if _is_broad_construction_segment_context(str(row["context_id"] or ""))]
        if broad_rows:
            return _dedupe_segment_contexts(broad_rows)
        subsegment_rows = _dedupe_segment_contexts(element_rows)
        if subsegment_rows:
            return subsegment_rows
    return []


def _dedupe_segment_contexts(rows: Sequence[sqlite3.Row]) -> List[sqlite3.Row]:
    by_context: Dict[str, sqlite3.Row] = {}
    for row in rows:
        context = str(row["context_id"] or "")
        if context not in by_context:
            by_context[context] = row
    return list(by_context.values())


def _is_construction_segment_context(context: str) -> bool:
    suffix = _segment_suffix(context)
    if "ReportableSegment" not in suffix:
        return False
    excluded = (
        "RealEstate",
        "Development",
        "ReserveForConstruction",
        "RoadConstructionAndPaving",
        "AdvertisingAgency",
        "GolfCourse",
        "HotelBusiness",
        "ServiceRelated",
        "Infrastructure",
        "Manufacturing",
        "Machinery",
        "Subsidiaries",
        "AssociateCompanies",
        "Other",
    )
    if any(token in suffix for token in excluded):
        return False
    if "CivilEngineering" not in suffix and "Engineering" in suffix:
        return False
    return any(token in suffix for token in ("Construction", "Building", "CivilEngineering"))


def _is_broad_construction_segment_context(context: str) -> bool:
    suffix = _segment_suffix(context)
    subsegment_tokens = (
        "BuildingConstruction",
        "CivilEngineeringReportable",
        "DomesticBuilding",
        "DomesticCivil",
        "DomesticConstruction",
        "OverseasBuilding",
        "OverseasCivil",
        "OverseasConstruction",
        "BuildingReportableSegment",
    )
    if "CivilEngineeringAndBuildingConstruction" not in suffix and any(token in suffix for token in subsegment_tokens):
        return False
    broad_tokens = (
        "ConstructionReportableSegment",
        "ConstructionBusinessReportableSegment",
        "ConstructionBusinessOfTheCorporationReportableSegment",
        "ConstructionRelatedBusinessReportableSegment",
        "CivilEngineeringAndBuildingConstructionReportableSegment",
        "ConstructionConstructionReportableSegment",
    )
    return any(token in suffix for token in broad_tokens)


def _segment_suffix(context: str) -> str:
    return context.split("_")[-1]


def _fact_numeric_value(row: sqlite3.Row) -> Optional[float]:
    value = str(row["value"] or "").replace(",", "").strip()
    if value in {"", "-", "－", "—", "―"}:
        return None
    if value.startswith(("△", "▲")):
        value = "-" + value[1:].strip()
    try:
        return float(value)
    except ValueError:
        return None


def _extract_cost_detail_record_from_db(
    conn: sqlite3.Connection,
    field: Dict[str, Any],
    target: Dict[str, Any],
    run_id: str,
) -> Optional[Dict[str, Any]]:
    field_id = str(field.get("field_id") or "")
    label = COST_DETAIL_LABELS.get(field_id)
    if not label:
        return None
    row = _query_cost_detail_text_block(conn, str(target.get("company_year_id") or ""))
    source_file = Path("edinet.db:xbrl_facts")
    if row is None:
        return _not_found_record(field, _target_for_record(target), run_id, source_file)
    text = str(row["value"] or "")
    parsed = _parse_cost_detail_value(text, label)
    if parsed is None:
        return _not_found_record(field, _target_for_record(target), run_id, source_file)
    value, quote = parsed
    return {
        "run_id": run_id,
        "company_year_id": target.get("company_year_id"),
        "operating_company_id": target.get("operating_company_id"),
        "fiscal_year": target.get("fiscal_year"),
        "source_doc_id": target.get("docID") or target.get("source_doc_id"),
        "source_file": str(source_file),
        "source_heading": row["element_id"],
        "source_quote": quote[:500],
        "field_id": field_id,
        "value_raw": value,
        "unit_raw": "百万円",
        "context_ref": row["context_id"],
        "xbrl_element": row["element_id"],
        "data_scope": "standalone",
        "extraction_method": "XBRL_COST_TEXTBLOCK",
        "confidence": 0.86,
        "review_required": False,
        "review_reason": "",
        "candidate_count": 1,
    }


def _query_cost_detail_text_block(conn: sqlite3.Connection, company_year_id: str) -> Optional[sqlite3.Row]:
    suffix_predicates = " or ".join("element_id like ?" for _ in COST_DETAIL_TEXT_BLOCK_SUFFIXES)
    params = [company_year_id]
    params.extend(f"%:{suffix}" for suffix in COST_DETAIL_TEXT_BLOCK_SUFFIXES)
    rows = list(
        conn.execute(
            f"""
            select * from xbrl_facts
            where company_year_id = ?
              and context_id like 'CurrentYearDuration%'
              and value like '%完成工事原価%'
              and (
                value like '%材料費%'
                or value like '%労務費%'
                or value like '%外注費%'
                or value like '%経費%'
              )
              and ({suffix_predicates})
            order by id
            """,
            tuple(params),
        )
    )
    rows.sort(key=lambda row: (_cost_text_block_priority(str(row["element_id"] or "")), int(row["id"] or 0)))
    return rows[0] if rows else None


def _cost_text_block_priority(element_id: str) -> int:
    for index, suffix in enumerate(COST_DETAIL_TEXT_BLOCK_SUFFIXES):
        if element_id.endswith(f":{suffix}"):
            return index
    return len(COST_DETAIL_TEXT_BLOCK_SUFFIXES)


def _parse_cost_detail_value(text: str, label: str) -> Optional[Tuple[float, str]]:
    normalized = _normalize_cost_text(text)
    segment = _construction_cost_segment(normalized)
    stacked = _parse_stacked_cost_detail_value(segment, label)
    if stacked is not None:
        return stacked
    match = _find_cost_label(segment, label)
    if not match:
        return None
    row_text = segment[match.start() : _next_cost_row_start(segment, match.end())]
    cleaned = re.sub(r"（[^）]*）|\([^)]*\)", "", row_text)
    paired_amount_tokens = re.findall(r"(△?\d{1,3}(?:,\d{3})+)\s*(?:100(?:\.0)?|\d{1,3}(?:\.\d)?)", cleaned)
    paired_amount_values = [_parse_cost_number(token) for token in paired_amount_tokens]
    paired_amount_values = [value for value in paired_amount_values if value is not None]
    if len(paired_amount_values) >= 2:
        return paired_amount_values[1], row_text
    if len(paired_amount_values) == 1:
        return paired_amount_values[0], row_text
    amount_tokens = re.findall(r"△?\d{1,3}(?:,\d{3})+", cleaned)
    amount_values = [_parse_cost_number(token) for token in amount_tokens]
    amount_values = [value for value in amount_values if value is not None]
    if len(amount_values) >= 2:
        return amount_values[1], row_text
    if len(amount_values) == 1:
        return amount_values[0], row_text
    ratio = r"(?:100(?:\.0)?|(?:[1-9]?\d|0)\.\d)"
    no_comma_amount_tokens = re.findall(rf"(\d+?)\s*{ratio}", cleaned)
    no_comma_values = [_parse_cost_number(token) for token in no_comma_amount_tokens]
    no_comma_values = [value for value in no_comma_values if value is not None]
    if len(no_comma_values) >= 2:
        return no_comma_values[1], row_text
    if len(no_comma_values) == 1:
        return no_comma_values[0], row_text
    return None


def _parse_stacked_cost_detail_value(segment: str, label: str) -> Optional[Tuple[float, str]]:
    required_labels = tuple(COST_DETAIL_LABELS.values())
    label_start = min([pos for item in required_labels for pos in [segment.find(item)] if pos >= 0] or [-1])
    if label_start < 0:
        return None
    first_amount = re.search(r"△?\d{1,3}(?:,\d{3})+", segment[label_start:])
    if not first_amount:
        return None
    label_block = segment[label_start : label_start + first_amount.start()]
    if not all(item in label_block for item in required_labels):
        return None

    tail = segment[label_start + first_amount.start() :]
    total_match = re.search(r"(?:合計|計)\s*△?\d", tail)
    if total_match:
        tail = tail[: total_match.start()]
    tokens = re.findall(r"[（(]?\s*△?\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*[）)]?|[（(]?\s*△?\d+(?:\.\d+)?\s*[）)]?", tail)
    if not tokens:
        return None

    prev_amounts, index = _take_cost_amount_tokens(tokens, 0)
    if len(prev_amounts) < 4:
        return None
    while index < len(tokens) and not _cost_token_is_amount(tokens[index]):
        index += 1
    current_amounts, _index = _take_cost_amount_tokens(tokens, index)
    if len(current_amounts) < 4:
        return None

    order = _stacked_cost_label_order(label_block)
    try:
        value_index = order.index(label)
    except ValueError:
        return None
    if value_index >= len(current_amounts):
        return None
    value = _parse_cost_number(current_amounts[value_index])
    if value is None:
        return None
    quote = f"{label_block.strip()} {tail.strip()}"
    return value, quote


def _take_cost_amount_tokens(tokens: Sequence[str], start: int) -> Tuple[List[str], int]:
    amounts: List[str] = []
    index = start
    while index < len(tokens):
        token = tokens[index]
        if not _cost_token_is_amount(token):
            break
        amounts.append(token)
        index += 1
    return amounts, index


def _cost_token_is_amount(token: str) -> bool:
    cleaned = token.strip().strip("()（）").replace(",", "").replace("△", "-").strip()
    if not cleaned:
        return False
    if "," in token:
        return True
    try:
        value = abs(float(cleaned))
    except ValueError:
        return False
    return value >= 100


def _stacked_cost_label_order(label_block: str) -> List[str]:
    return [label for label in STACKED_COST_LABEL_ORDER if label in label_block]


def _normalize_cost_text(text: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKC", text).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _construction_cost_segment(text: str) -> str:
    start_candidates = [pos for marker in ["【完成工事原価", "完成工事原価報告書", "完成工事原価明細書"] for pos in [text.find(marker)] if pos >= 0]
    start = min(start_candidates) if start_candidates else 0
    end_markers = ["【不動産", "【開発", "不動産事業等売上原価", "開発事業等売上原価"]
    end_positions = [text.find(marker, start + 20) for marker in end_markers]
    positives = [pos for pos in end_positions if pos > start]
    end = min(positives) if positives else len(text)
    return text[start:end]


def _find_cost_label(text: str, label: str) -> Optional[re.Match[str]]:
    pattern = rf"(?<!うち)(?<!労務){re.escape(label)}"
    return re.search(pattern, text)


def _next_cost_row_start(text: str, start: int) -> int:
    matches = list(re.finditer(r"(?<!うち)(?<!労務)(材料費|労務費|外注費|経費|合計|計)", text[start:]))
    return start + matches[0].start() if matches else len(text)


def _parse_cost_number(token: str) -> Optional[float]:
    value = token.replace(",", "").strip()
    if value.startswith("△"):
        value = "-" + value[1:]
    try:
        return float(value)
    except ValueError:
        return None


def _query_fact_candidates(
    conn: sqlite3.Connection,
    company_year_id: str,
    field: Dict[str, Any],
    period_type: str = "annual",
) -> List[sqlite3.Row]:
    tag_candidates = [part.strip() for part in str(field.get("xbrl_tag_candidates") or "").split(";") if part.strip()]
    context_filters = _period_context_filters(
        [part.strip() for part in str(field.get("context_filters") or "").split(";") if part.strip()],
        period_type,
    )
    if not tag_candidates:
        return []
    clauses = ["company_year_id = ?"]
    params: List[Any] = [company_year_id]
    tag_clause = " or ".join(["element_id like ? or item_name like ?"] * len(tag_candidates))
    clauses.append(f"({tag_clause})")
    for tag in tag_candidates:
        params.extend([f"%{tag}%", f"%{tag}%"])
    sql = f"""
        select * from xbrl_facts
        where {' and '.join(clauses)}
        order by
          case when unit in ('円', '千円', '百万円', '億円', '%') then 0 else 1 end,
          csv_file,
          id
    """
    rows = [row for row in conn.execute(sql, params) if _fact_matches_candidate(row, tag_candidates)]
    if not context_filters:
        return _prefer_best_candidate_priority(_prefer_primary_facts(rows), tag_candidates)
    return _prefer_best_candidate_priority(_prefer_primary_facts([row for row in rows if _fact_matches_context(row, context_filters)]), tag_candidates)


def _fact_to_csv_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "要素ID": row["element_id"],
        "項目名": row["item_name"],
        "コンテキストID": row["context_id"],
        "相対年度": row["relative_year"],
        "連結・個別": row["consolidation_scope"],
        "期間・時点": row["period_or_instant"],
        "ユニットID": row["unit_id"],
        "単位": row["unit"],
        "値": row["value"],
        "_source_csv": row["csv_file"],
    }


def _prefer_primary_facts(rows: List[sqlite3.Row]) -> List[sqlite3.Row]:
    if not rows:
        return rows
    non_text = [row for row in rows if "TextBlock" not in str(row["element_id"] or "")]
    if non_text:
        rows = non_text
    non_summary = [row for row in rows if "SummaryOfBusinessResults" not in str(row["element_id"] or "")]
    return non_summary or rows


def _prefer_best_candidate_priority(rows: List[sqlite3.Row], candidates: Sequence[str]) -> List[sqlite3.Row]:
    if not rows or not candidates:
        return rows
    ranked = [(_fact_candidate_priority(row, candidates), row) for row in rows]
    best = min(priority for priority, _row in ranked)
    if best >= len(candidates):
        return rows
    return [row for priority, row in ranked if priority == best]


def _fact_candidate_priority(row: sqlite3.Row, candidates: Sequence[str]) -> int:
    element = str(row["element_id"] or "")
    label = str(row["item_name"] or "")
    local_name = element.split(":")[-1]
    for index, token in enumerate(candidates):
        if _candidate_token_matches(local_name, label, token):
            return index
    return len(candidates)


def _effective_fact_candidate_count(rows: List[sqlite3.Row]) -> int:
    values = {str(row["value"] or "").strip() for row in rows}
    values.discard("")
    return len(values) if values else len(rows)


def _fact_matches_context(row: sqlite3.Row, filters: Sequence[str]) -> bool:
    context = str(row["context_id"] or "")
    relative_year = str(row["relative_year"] or "")
    period = str(row["period_or_instant"] or "")
    scope = _fact_scope(row)
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


def _fact_matches_candidate(row: sqlite3.Row, candidates: Sequence[str]) -> bool:
    element = str(row["element_id"] or "")
    label = str(row["item_name"] or "")
    local_name = element.split(":")[-1]
    for token in candidates:
        if _candidate_token_matches(local_name, label, token):
            return True
    return False


def _candidate_token_matches(local_name: str, label: str, token: str) -> bool:
    if _is_ascii_token(token):
        return local_name.lower() == token.lower()
    return token in label


def _is_ascii_token(token: str) -> bool:
    try:
        token.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _fact_scope(row: sqlite3.Row) -> Optional[str]:
    scope = str(row["consolidation_scope"] or "")
    if "連結" in scope:
        return "consolidated"
    if "個別" in scope or "単独" in scope:
        return "standalone"
    return None


def _target_for_record(target: Dict[str, Any]) -> Dict[str, Any]:
    copied = dict(target)
    copied["docID"] = target.get("docID") or target.get("source_doc_id")
    return copied


def _read_optional(path: Path) -> List[Dict[str, Any]]:
    actual = prefer_existing_table(path)
    return read_table(actual) if actual.exists() else []


def _json(row: Dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _json_row(row: sqlite3.Row) -> Dict[str, Any]:
    return json.loads(row["row_json"])


def _target_period_matches(target: Dict[str, Any], period_type: str) -> bool:
    requested = str(period_type or "annual")
    if requested == "all":
        return True
    return str(target.get("period_type") or "annual") == requested


def _decode(content: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _first_present(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
        if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
            return lowered[key.lower()]
    return None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
