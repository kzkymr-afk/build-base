from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


def fiscal_year_from_period_end(period_end: Any, fiscal_year_end_month: int) -> Optional[int]:
    if not period_end:
        return None
    text = str(period_end)[:10]
    try:
        dt = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    if fiscal_year_end_month == 12:
        return dt.year
    return dt.year - 1


def is_correction(doc: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    desc = str(doc.get("docDescription") or "")
    correction_words = cfg.get("correction_doc_description_include", ["訂正"])
    return any(word in desc for word in correction_words) or str(doc.get("formCode", "")).endswith("001")


def is_target_securities_report(doc: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    desc = str(doc.get("docDescription") or "")
    includes = cfg.get("doc_description_include", [])
    excludes = cfg.get("doc_description_exclude", [])
    if includes and not any(word in desc for word in includes):
        return False
    if any(word in desc for word in excludes):
        return False
    ordinance_candidates = set(str(x) for x in cfg.get("ordinance_code_candidates", []))
    form_candidates = set(str(x) for x in cfg.get("form_code_candidates", []))
    if ordinance_candidates and str(doc.get("ordinanceCode", "")) not in ordinance_candidates:
        return False
    if form_candidates and str(doc.get("formCode", "")) not in form_candidates:
        return False
    return str(doc.get("withdrawalStatus", "0")) != "1" and str(doc.get("disclosureStatus", "0")) != "1"


def resolve_target_documents(
    document_index: Iterable[Dict[str, Any]],
    company_master: Iterable[Dict[str, Any]],
    company_year_master: Iterable[Dict[str, Any]],
    document_filter: Dict[str, Any],
    fiscal_years: Optional[Iterable[int]] = None,
) -> List[Dict[str, Any]]:
    docs = list(document_index)
    securities_cfg = document_filter.get("securities_report", document_filter)
    requested_years = set(int(year) for year in fiscal_years) if fiscal_years else None
    companies = {str(row["operating_company_id"]): row for row in company_master}
    outputs: List[Dict[str, Any]] = []
    for company_year in company_year_master:
        company_year_id = str(company_year["company_year_id"])
        fiscal_year = int(company_year["fiscal_year"])
        if requested_years and fiscal_year not in requested_years:
            continue
        company = companies.get(str(company_year["operating_company_id"]))
        if not company:
            outputs.append(_failure(company_year, "company_not_found"))
            continue
        reporting_entity_id = str(company_year.get("reporting_entity_id") or company_year["operating_company_id"])
        reporting_company = companies.get(reporting_entity_id, company)
        candidates = [
            doc
            for doc in docs
            if str(doc.get("edinetCode", "")) == str(reporting_company.get("edinet_code", ""))
            and is_target_securities_report(doc, securities_cfg)
            and fiscal_year_from_period_end(doc.get("periodEnd"), int(reporting_company.get("fiscal_year_end_month") or 3)) == fiscal_year
        ]
        if not candidates:
            outputs.append(_failure(company_year, "target_document_not_found"))
            continue
        chosen = choose_effective_document(candidates, securities_cfg)
        output = dict(chosen)
        output.update(
            {
                "company_year_id": company_year_id,
                "operating_company_id": company_year["operating_company_id"],
                "operating_company_name": company.get("operating_company_name"),
                "fiscal_year": fiscal_year,
                "fiscal_year_end": company_year.get("fiscal_year_end"),
                "reporting_entity_id": reporting_entity_id,
                "reporting_entity_name": reporting_company.get("operating_company_name"),
                "reporting_entity_edinet_code": reporting_company.get("edinet_code"),
                "transition_year_flag": company_year.get("transition_year_flag"),
                "analysis_treatment": company_year.get("analysis_treatment"),
                "candidate_doc_ids": ";".join(str(doc.get("docID", "")) for doc in candidates),
                "resolution_status": "resolved",
                "is_correction": is_correction(chosen, securities_cfg),
            }
        )
        outputs.append(output)
    return outputs


def choose_effective_document(candidates: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    prefer_correction = bool(cfg.get("prefer_latest_correction", True))

    def sort_key(doc: Dict[str, Any]) -> tuple:
        submit = str(doc.get("submitDateTime") or "")
        correction_rank = 1 if prefer_correction and is_correction(doc, cfg) else 0
        return (correction_rank, submit, str(doc.get("docID") or ""))

    return sorted(candidates, key=sort_key)[-1]


def _failure(company_year: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {
        "company_year_id": company_year.get("company_year_id"),
        "operating_company_id": company_year.get("operating_company_id"),
        "fiscal_year": company_year.get("fiscal_year"),
        "fiscal_year_end": company_year.get("fiscal_year_end"),
        "reporting_entity_id": company_year.get("reporting_entity_id"),
        "transition_year_flag": company_year.get("transition_year_flag"),
        "analysis_treatment": company_year.get("analysis_treatment"),
        "resolution_status": "failed",
        "failure_reason": reason,
    }
