import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_jsonl, write_table, write_yaml
from yuho_auto_extract.services.major_financial_evidence import (
    build_major_financial_evidence_pack,
    compare_major_financial_ai_decisions,
)


class MajorFinancialEvidenceTests(unittest.TestCase):
    def test_build_major_financial_evidence_pack_writes_candidates_groups_and_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            _write_target_documents(root)
            _write_fact_store(root)
            _write_current_outputs(root)

            result = build_major_financial_evidence_pack(root, chunk_size=1)
            candidates = read_table(root / "data" / "ai_evidence" / "major_financial" / "candidate_facts.jsonl")
            groups = read_table(root / "data" / "ai_evidence" / "major_financial" / "candidate_groups.jsonl")

            self.assertEqual(result["fields"], 2)
            self.assertEqual(result["company_years"], 1)
            self.assertEqual(result["groups"], 2)
            self.assertEqual(result["match_type_counts"]["exact"], 2)
            self.assertEqual(result["match_type_counts"]["weak"], 1)
            self.assertTrue((root / "data" / "ai_evidence" / "major_financial" / "AI_REVIEW_INSTRUCTIONS.md").exists())
            self.assertTrue((root / "data" / "ai_evidence" / "major_financial" / "prompt_chunks" / "chunk_001.md").exists())

            net_sales_group = next(row for row in groups if row["field_id"] == "net_sales_consolidated")
            self.assertEqual(net_sales_group["status"], "candidate_found")
            self.assertEqual(net_sales_group["current_selected"]["value_normalized"], "1000")
            self.assertEqual(len(net_sales_group["candidate_ids"]), 1)

            gross_candidates = [row for row in candidates if row["field_id"] == "gross_profit_consolidated"]
            self.assertEqual({row["match_type"] for row in gross_candidates}, {"exact", "weak"})
            self.assertTrue(all(row["candidate_id"].startswith("xbrl_A_2024_") for row in gross_candidates))

    def test_compare_major_financial_ai_decisions_reports_match_without_mutating_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            _write_target_documents(root)
            _write_fact_store(root)
            _write_current_outputs(root)
            build_major_financial_evidence_pack(root)
            candidates = read_table(root / "data" / "ai_evidence" / "major_financial" / "candidate_facts.jsonl")
            net_sales_candidate = next(row for row in candidates if row["field_id"] == "net_sales_consolidated")

            write_jsonl(
                root / "data" / "ai_evidence" / "major_financial" / "ai_decisions.jsonl",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "net_sales_consolidated",
                        "decision": "select",
                        "selected_candidate_id": net_sales_candidate["candidate_id"],
                        "reason": "連結・当期・売上高に一致",
                    },
                    {
                        "company_year_id": "A_2024",
                        "field_id": "gross_profit_consolidated",
                        "decision": "keep_current",
                        "selected_candidate_id": "",
                        "reason": "現行値維持",
                    },
                ],
            )

            result = compare_major_financial_ai_decisions(root)
            rows = read_table(root / "data" / "reports" / "major_financial_ai_decision_compare.csv")
            statuses = {(row["field_id"], row["match_status"]) for row in rows}

            self.assertEqual(result["compared"], 2)
            self.assertIn(("net_sales_consolidated", "match"), statuses)
            self.assertIn(("gross_profit_consolidated", "keep_current"), statuses)

    def test_compare_major_financial_ai_decisions_rejects_unknown_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            _write_target_documents(root)
            _write_fact_store(root)
            build_major_financial_evidence_pack(root)
            write_jsonl(
                root / "data" / "ai_evidence" / "major_financial" / "ai_decisions.jsonl",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "net_sales_consolidated",
                        "decision": "select",
                        "selected_candidate_id": "missing",
                        "reason": "",
                    }
                ],
            )

            with self.assertRaises(ValueError):
                compare_major_financial_ai_decisions(root)


def _write_config(root: Path) -> None:
    write_table(
        root / "config" / "company_master.csv",
        [
            {
                "operating_company_id": "A",
                "operating_company_name": "A社",
                "edinet_code": "E00001",
                "fiscal_year_end_month": "3",
                "default_data_scope": "consolidated",
            }
        ],
    )
    write_table(
        root / "config" / "company_year_master.csv",
        [
            {
                "company_year_id": "A_2024",
                "fiscal_year": "2024",
                "fiscal_year_end": "2025-03-31",
                "operating_company_id": "A",
                "reporting_entity_id": "A",
                "parent_group_id_at_year_end": "A",
                "current_parent_group_id": "A",
                "data_scope_allowed": "consolidated;standalone",
                "transition_year_flag": "false",
                "analysis_treatment": "normal",
            }
        ],
    )
    write_table(
        root / "config" / "field_definition.csv",
        [
            {
                "field_id": "net_sales_consolidated",
                "field_name_ja": "売上高_連結",
                "category": "performance",
                "target_unit": "百万円",
                "data_scope_required": "consolidated",
                "period_type": "current_year",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "NetSales",
                "context_filters": "CurrentYearDuration;ConsolidatedMember",
            },
            {
                "field_id": "gross_profit_consolidated",
                "field_name_ja": "売上総利益_連結",
                "category": "performance",
                "target_unit": "百万円",
                "data_scope_required": "consolidated",
                "period_type": "current_year",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "GrossProfit",
                "context_filters": "CurrentYearDuration;ConsolidatedMember",
            },
            {
                "field_id": "average_salary",
                "field_name_ja": "平均給与",
                "category": "human_capital",
                "target_unit": "円",
                "data_scope_required": "standalone",
                "period_type": "current_year",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "AverageAnnualSalary",
                "context_filters": "CurrentYearInstant",
            },
        ],
    )
    write_yaml(root / "config" / "document_filter.yml", {})
    write_yaml(root / "config" / "extraction_sections.yml", {})
    write_yaml(root / "config" / "validation_rules.yml", {"rules": []})
    write_yaml(root / "config" / "model_config.yml", {})


def _write_target_documents(root: Path) -> None:
    write_table(
        root / "data" / "intermediate" / "target_documents.csv",
        [
            {
                "company_year_id": "A_2024",
                "operating_company_id": "A",
                "fiscal_year": "2024",
                "docID": "DOC001",
                "resolution_status": "resolved",
            }
        ],
    )


def _write_fact_store(root: Path) -> None:
    base = {
        "company_year_id": "A_2024",
        "operating_company_id": "A",
        "fiscal_year": "2024",
        "source_doc_id": "DOC001",
        "source_file": "data/raw/documents/DOC001/csv.zip",
        "csv_file": "XBRL_TO_CSV/sample.csv",
        "relative_year": "当期",
        "consolidation_scope": "連結",
        "normalized_scope": "consolidated",
        "period_or_instant": "期間",
        "unit": "百万円",
        "value_type": "numeric",
        "is_text_block": "no",
    }
    rows = [
        {
            **base,
            "csv_row": "1",
            "element_id": "jpcrp_cor:NetSales",
            "element_local_name": "NetSales",
            "item_name": "売上高",
            "context_id": "CurrentYearDuration",
            "value": "1000",
            "value_numeric": 1000,
            "source_quote": "売上高: 1000",
        },
        {
            **base,
            "csv_row": "2",
            "element_id": "jppfs_cor:GrossProfit",
            "element_local_name": "GrossProfit",
            "item_name": "売上総利益",
            "context_id": "CurrentYearDuration",
            "value": "200",
            "value_numeric": 200,
            "source_quote": "売上総利益: 200",
        },
        {
            **base,
            "csv_row": "3",
            "element_id": "jppfs_cor:GrossProfitLoss",
            "element_local_name": "GrossProfitLoss",
            "item_name": "売上総利益",
            "context_id": "CurrentYearDuration",
            "value": "201",
            "value_numeric": 201,
            "source_quote": "売上総利益: 201",
        },
        {
            **base,
            "csv_row": "4",
            "element_id": "jpcrp_cor:PriorYearNetSales",
            "element_local_name": "PriorYearNetSales",
            "item_name": "前期売上高",
            "context_id": "PriorYearDuration",
            "value": "900",
            "value_numeric": 900,
            "source_quote": "前期売上高: 900",
        },
        {
            **base,
            "csv_row": "5",
            "element_id": "jpcrp_cor:NetSalesTextBlock",
            "element_local_name": "NetSalesTextBlock",
            "item_name": "売上高 [テキストブロック]",
            "context_id": "CurrentYearDuration",
            "value": "売上高は1000百万円です。",
            "value_numeric": "",
            "value_type": "text",
            "is_text_block": "yes",
            "source_quote": "売上高は1000百万円です。",
        },
    ]
    write_table(root / "data" / "marts" / "xbrl_fact_store" / "facts.csv", rows)


def _write_current_outputs(root: Path) -> None:
    write_table(
        root / "data" / "final" / "final_master_long.csv",
        [
            {
                "company_year_id": "A_2024",
                "field_id": "net_sales_consolidated",
                "value_normalized": "1000",
                "value_raw": "1000",
                "unit_normalized": "百万円",
                "source_quote": "売上高: 1000",
                "xbrl_element": "jpcrp_cor:NetSales",
                "context_ref": "CurrentYearDuration",
                "validation_status": "pass",
                "review_status": "auto_accepted",
            }
        ],
    )
    write_table(
        root / "data" / "review" / "review_queue.csv",
        [
            {
                "company_year_id": "A_2024",
                "field_id": "gross_profit_consolidated",
                "extracted_value": "200",
                "review_reason": "candidate_check",
                "validation_status": "warn",
                "confidence": "0.7",
            }
        ],
    )


if __name__ == "__main__":
    unittest.main()
