from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .io_utils import prefer_existing_table, read_table, read_yaml


REQUIRED_COMPANY_COLUMNS = {
    "operating_company_id",
    "operating_company_name",
    "edinet_code",
    "fiscal_year_end_month",
    "default_data_scope",
}

REQUIRED_COMPANY_YEAR_COLUMNS = {
    "company_year_id",
    "fiscal_year",
    "fiscal_year_end",
    "operating_company_id",
    "reporting_entity_id",
    "parent_group_id_at_year_end",
    "current_parent_group_id",
    "data_scope_allowed",
    "transition_year_flag",
    "analysis_treatment",
}

REQUIRED_FIELD_COLUMNS = {
    "field_id",
    "field_name_ja",
    "category",
    "target_unit",
    "data_scope_required",
    "period_type",
    "preferred_method",
}


@dataclass
class PipelineConfig:
    root: Path
    company_master: List[Dict[str, Any]]
    company_year_master: List[Dict[str, Any]]
    field_definition: List[Dict[str, Any]]
    document_filter: Dict[str, Any]
    extraction_sections: Dict[str, Any]
    validation_rules: Dict[str, Any]
    model_config: Dict[str, Any]


def load_pipeline_config(root: Path) -> PipelineConfig:
    config_dir = root / "config"
    company_master = read_table(prefer_existing_table(config_dir / "company_master.xlsx"))
    company_year_master = read_table(prefer_existing_table(config_dir / "company_year_master.xlsx"))
    field_definition = read_table(prefer_existing_table(config_dir / "field_definition.xlsx"))
    _require_columns(company_master, REQUIRED_COMPANY_COLUMNS, "company_master")
    _require_columns(company_year_master, REQUIRED_COMPANY_YEAR_COLUMNS, "company_year_master")
    _require_columns(field_definition, REQUIRED_FIELD_COLUMNS, "field_definition")
    return PipelineConfig(
        root=root,
        company_master=company_master,
        company_year_master=company_year_master,
        field_definition=field_definition,
        document_filter=read_yaml(config_dir / "document_filter.yml"),
        extraction_sections=read_yaml(config_dir / "extraction_sections.yml"),
        validation_rules=read_yaml(config_dir / "validation_rules.yml"),
        model_config=read_yaml(config_dir / "model_config.yml"),
    )


def _require_columns(rows: List[Dict[str, Any]], required: set, name: str) -> None:
    if not rows:
        raise ValueError(f"{name} is empty")
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(f"{name} missing required columns: {sorted(missing)}")


def field_map(fields: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row["field_id"]): row for row in fields}


def company_map(companies: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row["operating_company_id"]): row for row in companies}


def company_year_map(company_years: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row["company_year_id"]): row for row in company_years}
