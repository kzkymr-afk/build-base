import tempfile
import time
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import is_blankish, read_table, write_table
from yuho_auto_extract.review_queue import build_review_queue
from yuho_auto_extract.services.ai_prompt import build_prompt
from yuho_auto_extract.services.datasets import read_cell_detail, read_chart_data, read_options, read_review_queue, read_wide
from yuho_auto_extract.services import cells, pipeline, semantics_store
from yuho_auto_extract.services.reviews import (
    delete_resolved_reviews,
    mark_company_field_not_applicable,
    mark_resolved_reviews_applied,
    upsert_resolved_reviews,
)
from yuho_auto_extract.web_api.jobs import JobManager


class WebServiceTests(unittest.TestCase):
    def test_chart_data_filters_rows_and_converts_numeric_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "company_master.csv",
                [
                    {"operating_company_id": "A", "operating_company_name": "A社"},
                    {"operating_company_id": "B", "operating_company_name": "B社"},
                ],
            )
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {"field_id": "roe", "field_name_ja": "ROE", "category": "finance", "target_unit": "%"},
                    {"field_id": "sales", "field_name_ja": "売上高", "category": "finance", "target_unit": "百万円"},
                ],
            )
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [
                    {"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "roe": "8.2", "sales": "1,234"},
                    {"company_year_id": "A_2023", "operating_company_id": "A", "fiscal_year": "2023", "roe": "", "sales": "900"},
                    {"company_year_id": "B_2024", "operating_company_id": "B", "fiscal_year": "2024", "roe": "7.1", "sales": "2,000"},
                ],
            )

            result = read_chart_data(root, companies=["A"], fiscal_years=["2024"], fields=["roe", "sales"])

            self.assertEqual(result["total"], 1)
            self.assertEqual(result["rows"][0]["company_year_id"], "A_2024")
            self.assertEqual(result["rows"][0]["roe"], 8.2)
            self.assertEqual(result["rows"][0]["sales"], 1234.0)
            self.assertEqual(result["fields"][0]["name"], "ROE")
            self.assertEqual(result["companies"][0]["label"], "A社（A）")

    def test_chart_data_does_not_pick_default_fields_without_explicit_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE", "category": "finance", "target_unit": "%"}])
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "roe": "8.2"}])

            result = read_chart_data(root, companies=["A"], fiscal_years=["2024"], fields=[])

            self.assertEqual(result["total"], 0)
            self.assertEqual(result["rows"], [])
            self.assertEqual(result["fields"], [])

    def test_chart_data_includes_source_summary_for_selected_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(root / "config" / "field_definition.csv", [{"field_id": "sales", "field_name_ja": "売上高", "category": "finance", "target_unit": "百万円"}])
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "sales": "1,234"}])
            write_table(
                root / "data" / "final" / "source_audit.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "sales",
                        "field_name_ja": "売上高",
                        "value": "1234",
                        "unit_normalized": "百万円",
                        "data_scope": "consolidated",
                        "source_doc_id": "S100TEST",
                        "source_file": "edinet.db:xbrl_facts",
                        "source_heading": "NetSales",
                        "source_quote": "売上高: 1234000000",
                        "extraction_method": "XBRL_CSV",
                        "confidence": "0.95",
                    }
                ],
            )

            result = read_chart_data(root, companies=["A"], fiscal_years=["2024"], fields=["sales"])

            self.assertEqual(result["sources"][0]["company_name"], "A社")
            self.assertEqual(result["sources"][0]["field_name"], "売上高")
            self.assertEqual(result["sources"][0]["source_doc_id"], "S100TEST")
            self.assertEqual(result["sources"][0]["source_quote"], "売上高: 1234000000")

    def test_derived_ratio_fields_are_available_without_mutating_wide_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {"field_id": "net_sales_consolidated", "field_name_ja": "売上高_連結", "category": "performance", "target_unit": "百万円"},
                    {"field_id": "rd_expense", "field_name_ja": "研究開発費", "category": "expense", "target_unit": "百万円"},
                    {"field_id": "segment_profit_construction", "field_name_ja": "建設セグメント利益", "category": "segment", "target_unit": "百万円"},
                    {"field_id": "segment_sales_construction", "field_name_ja": "建設セグメント売上高", "category": "segment", "target_unit": "百万円"},
                    {"field_id": "cost_materials", "field_name_ja": "材料費", "category": "cost", "target_unit": "百万円"},
                    {"field_id": "cost_labor", "field_name_ja": "労務費", "category": "cost", "target_unit": "百万円"},
                    {"field_id": "cost_subcontract", "field_name_ja": "外注費", "category": "cost", "target_unit": "百万円"},
                    {"field_id": "cost_expense", "field_name_ja": "経費", "category": "cost", "target_unit": "百万円"},
                ],
            )
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "operating_company_id": "A",
                        "fiscal_year": "2024",
                        "net_sales_consolidated": "1,000",
                        "rd_expense": "10",
                        "segment_profit_construction": "40",
                        "segment_sales_construction": "800",
                        "cost_materials": "100",
                        "cost_labor": "50",
                        "cost_subcontract": "300",
                        "cost_expense": "50",
                    }
                ],
            )

            options = read_options(root)
            chart = read_chart_data(
                root,
                fields=[
                    "construction_segment_profit_margin",
                    "cost_subcontract_share",
                    "rd_expense_to_net_sales_consolidated_ratio",
                ],
            )
            wide = read_wide(root, fields=["construction_segment_profit_margin", "cost_subcontract_share"])

            self.assertIn("derived_ratios", {preset["id"] for preset in options["field_presets"]})
            self.assertEqual(chart["rows"][0]["construction_segment_profit_margin"], 5.0)
            self.assertEqual(chart["rows"][0]["cost_subcontract_share"], 60.0)
            self.assertEqual(chart["rows"][0]["rd_expense_to_net_sales_consolidated_ratio"], 1.0)
            self.assertEqual(wide["rows"][0]["construction_segment_profit_margin"], 5.0)
            self.assertFalse((root / "data" / "final" / "final_master_wide.csv").read_text(encoding="utf-8").count("construction_segment_profit_margin"))

    def test_result_field_presets_cover_all_fields_and_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(root / "config" / "company_year_master.csv", [{"company_year_id": "A_2024", "fiscal_year": "2024"}])
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {"field_id": "net_sales_consolidated", "field_name_ja": "売上高_連結", "category": "performance", "target_unit": "百万円"},
                    {"field_id": "total_assets_consolidated", "field_name_ja": "総資産_連結", "category": "financial_position", "target_unit": "百万円"},
                    {"field_id": "building_orders_overseas", "field_name_ja": "建築受注高_海外", "category": "orders", "target_unit": "百万円"},
                    {"field_id": "architecture_engineers_1st_class", "field_name_ja": "建築一式_技術職員数_一級", "category": "human_capital", "target_unit": "人"},
                ],
            )

            options = read_options(root)
            fields = {field["id"] for field in options["fields"]}
            presets = {preset["id"]: preset for preset in options["field_presets"]}

            self.assertEqual(fields, set(presets["all"]["fields"]))
            self.assertIn("building_orders_overseas", presets["orders"]["fields"])
            self.assertIn("architecture_engineers_1st_class", presets["human_capital"]["fields"])
            self.assertIn("construction_segment_profit_margin", presets["derived_ratios"]["fields"])
            self.assertEqual(presets["financial_position"]["name"], "財政状態")

    def test_wide_results_include_cell_statuses_for_direct_review_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE", "target_unit": "%"}])
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "roe": ""}],
            )
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [{"company_year_id": "A_2024", "field_id": "roe", "field_name_ja": "ROE", "extracted_value": "8.2"}],
            )

            result = read_wide(root, fields=["roe"])

            status = result["cell_statuses"]["A_2024"]["roe"]
            self.assertEqual(status["status"], "blank_with_review_candidate")
            self.assertEqual(status["candidate_count"], 1)
            self.assertTrue(result["rows"][0]["roe"] == "")

    def test_cell_detail_exposes_workbench_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE", "target_unit": "%"}])
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "roe": ""}],
            )
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [{"company_year_id": "A_2024", "field_id": "roe", "field_name_ja": "ROE", "extracted_value": "8.2", "review_reason": "confidence_below_threshold"}],
            )

            detail = read_cell_detail(root, "A_2024", "roe")

            self.assertEqual(detail["current"]["status"], "blank_with_review_candidate")
            self.assertEqual(detail["review_state"]["candidate_count"], 1)
            self.assertEqual(detail["candidates"][0]["value"], "8.2")
            self.assertIn("cell_only", detail["similar_scope_counts"])
            self.assertTrue(detail["actions_available"]["manual_correct"])

    def test_cost_composition_ratio_does_not_treat_missing_components_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {"field_id": "cost_materials", "field_name_ja": "材料費", "category": "cost", "target_unit": "百万円"},
                    {"field_id": "cost_labor", "field_name_ja": "労務費", "category": "cost", "target_unit": "百万円"},
                    {"field_id": "cost_subcontract", "field_name_ja": "外注費", "category": "cost", "target_unit": "百万円"},
                    {"field_id": "cost_expense", "field_name_ja": "経費", "category": "cost", "target_unit": "百万円"},
                ],
            )
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "operating_company_id": "A",
                        "fiscal_year": "2024",
                        "cost_materials": "100",
                        "cost_labor": "",
                        "cost_subcontract": "300",
                        "cost_expense": "50",
                    }
                ],
            )

            result = read_chart_data(root, fields=["cost_subcontract_share"])

            self.assertIsNone(result["rows"][0]["cost_subcontract_share"])

    def test_review_upsert_writes_resolved_without_touching_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "data" / "review" / "review_queue.csv"
            write_table(
                queue_path,
                [
                    {
                        "company_year_id": "A_2024",
                        "company_name": "A",
                        "fiscal_year": "2024",
                        "field_id": "roe",
                        "field_name_ja": "ROE",
                        "extracted_value": "0.12",
                        "review_decision": "",
                        "corrected_value": "",
                    }
                ],
            )
            before = queue_path.read_text(encoding="utf-8")

            result = upsert_resolved_reviews(
                root,
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "roe",
                        "review_decision": "correct",
                        "corrected_value": "0.13",
                        "reviewer_note": "source quote checked",
                    }
                ],
            )

            self.assertEqual(result["changed"], 1)
            self.assertEqual(queue_path.read_text(encoding="utf-8"), before)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0]["review_decision"], "correct")
            self.assertEqual(resolved[0]["corrected_value"], "0.13")
            self.assertEqual(resolved[0]["reviewer_note"], "source quote checked")

    def test_cell_review_saves_synthetic_resolved_row_when_queue_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE", "target_unit": "%"}])
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "roe": ""}],
            )
            write_table(root / "data" / "review" / "review_queue.csv", [])

            result = cells.save_cell_review(
                root,
                "A_2024",
                "roe",
                review_decision="correct",
                corrected_value="8.2",
                reviewer_note="filled from result table",
            )

            self.assertEqual(result["changed"], 1)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual(resolved[0]["company_year_id"], "A_2024")
            self.assertEqual(resolved[0]["field_id"], "roe")
            self.assertEqual(resolved[0]["field_name_ja"], "ROE")
            self.assertEqual(resolved[0]["review_decision"], "correct")
            self.assertEqual(resolved[0]["corrected_value"], "8.2")

    def test_cell_review_accepts_selected_review_queue_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "company_name": "A",
                        "fiscal_year": "2024",
                        "field_id": "roe",
                        "field_name_ja": "ROE",
                        "extracted_value": "0.11",
                        "source_quote": "first candidate",
                    },
                    {
                        "company_year_id": "A_2024",
                        "company_name": "A",
                        "fiscal_year": "2024",
                        "field_id": "roe",
                        "field_name_ja": "ROE",
                        "extracted_value": "0.12",
                        "source_quote": "second candidate",
                    },
                ],
            )

            result = cells.save_cell_review(
                root,
                "A_2024",
                "roe",
                review_decision="accept",
                reviewer_note="picked visible candidate",
                candidate_id="review:1",
            )

            self.assertEqual(result["changed"], 1)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual(resolved[0]["review_decision"], "accept")
            self.assertEqual(resolved[0]["extracted_value"], "0.12")
            self.assertEqual(resolved[0]["source_quote"], "second candidate")
            self.assertIn("selected_candidate=review:1", resolved[0]["reviewer_note"])

    def test_cell_review_accepts_selected_source_audit_candidate_when_queue_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE", "target_unit": "%"}])
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "roe": ""}],
            )
            write_table(root / "data" / "review" / "review_queue.csv", [])
            write_table(
                root / "data" / "final" / "source_audit.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "roe",
                        "value": "0.11",
                        "unit_normalized": "%",
                        "source_doc_id": "doc-first",
                        "source_quote": "first audit candidate",
                    },
                    {
                        "company_year_id": "A_2024",
                        "field_id": "roe",
                        "value": "0.12",
                        "unit_normalized": "%",
                        "source_doc_id": "doc-second",
                        "source_quote": "second audit candidate",
                    },
                ],
            )

            result = cells.save_cell_review(
                root,
                "A_2024",
                "roe",
                review_decision="accept",
                candidate_id="audit:1",
            )

            self.assertEqual(result["changed"], 1)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual(resolved[0]["review_decision"], "accept")
            self.assertEqual(resolved[0]["extracted_value"], "0.12")
            self.assertEqual(resolved[0]["source_doc_id"], "doc-second")
            self.assertEqual(resolved[0]["source_quote"], "second audit candidate")
            self.assertIn("selected_candidate=audit:1", resolved[0]["reviewer_note"])

    def test_apply_similar_reviews_previews_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE"}])
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [
                    {"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "roe": ""},
                    {"company_year_id": "A_2023", "operating_company_id": "A", "fiscal_year": "2023", "roe": ""},
                    {"company_year_id": "B_2024", "operating_company_id": "B", "fiscal_year": "2024", "roe": ""},
                ],
            )
            write_table(root / "data" / "review" / "review_queue.csv", [])

            preview = cells.apply_similar_reviews(
                root,
                "A_2024",
                "roe",
                scope="same_company_all_years",
                review_decision="correct",
                corrected_value="8.2",
                preview=True,
            )

            self.assertTrue(preview["preview"])
            self.assertEqual(preview["target_count"], 2)
            self.assertFalse((root / "data" / "review" / "review_resolved.csv").exists())

    def test_review_upsert_overwrites_existing_resolved_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [{"company_year_id": "A_2024", "field_id": "roe", "extracted_value": "0.12"}],
            )

            upsert_resolved_reviews(
                root,
                [{"company_year_id": "A_2024", "field_id": "roe", "review_decision": "correct", "corrected_value": "0.13"}],
            )
            result = upsert_resolved_reviews(
                root,
                [{"company_year_id": "A_2024", "field_id": "roe", "review_decision": "correct", "corrected_value": "0.14", "reviewer_note": "rechecked"}],
            )

            self.assertEqual(result["changed"], 1)
            self.assertEqual(result["total"], 1)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0]["corrected_value"], "0.14")
            self.assertEqual(resolved[0]["reviewer_note"], "rechecked")

    def test_review_upsert_accepts_not_applicable_without_corrected_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [{"company_year_id": "INFR_2024", "field_id": "cost_labor", "extracted_value": ""}],
            )

            result = upsert_resolved_reviews(
                root,
                [{"company_year_id": "INFR_2024", "field_id": "cost_labor", "review_decision": "not_applicable"}],
            )

            self.assertEqual(result["changed"], 1)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual(resolved[0]["review_decision"], "not_applicable")
            self.assertEqual(resolved[0]["corrected_value"], "")

    def test_review_upsert_clears_applied_state_after_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [{"company_year_id": "A_2024", "field_id": "roe", "extracted_value": "0.12"}],
            )
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "roe",
                        "review_decision": "correct",
                        "corrected_value": "0.13",
                        "applied_status": "applied",
                        "applied_value": "0.13",
                        "applied_at": "2026-06-17T00:00:00Z",
                    }
                ],
            )

            upsert_resolved_reviews(
                root,
                [{"company_year_id": "A_2024", "field_id": "roe", "review_decision": "correct", "corrected_value": "0.14"}],
            )

            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual(resolved[0]["corrected_value"], "0.14")
            self.assertEqual(resolved[0]["applied_status"], "")
            self.assertEqual(resolved[0]["applied_value"], "")
            self.assertEqual(resolved[0]["applied_at"], "")

    def test_delete_resolved_review_removes_only_target_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "data" / "review" / "review_queue.csv"
            write_table(
                queue_path,
                [
                    {"company_year_id": "A_2024", "field_id": "roe"},
                    {"company_year_id": "A_2024", "field_id": "average_age"},
                ],
            )
            before = queue_path.read_text(encoding="utf-8")
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {"company_year_id": "A_2024", "field_id": "roe", "review_decision": "correct", "corrected_value": "0.14"},
                    {"company_year_id": "A_2024", "field_id": "average_age", "review_decision": "correct", "corrected_value": "45.0"},
                ],
            )

            result = delete_resolved_reviews(root, [{"company_year_id": "A_2024", "field_id": "roe"}])

            self.assertEqual(result["deleted"], 1)
            self.assertEqual(result["total"], 1)
            self.assertEqual(queue_path.read_text(encoding="utf-8"), before)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0]["field_id"], "average_age")

    def test_review_queue_merges_saved_review_for_reediting_and_filtering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "fiscal_year": "2024",
                        "field_id": "roe",
                        "field_name_ja": "ROE",
                        "extracted_value": "0.12",
                        "review_reason": "validation_warn",
                    },
                    {
                        "company_year_id": "A_2024",
                        "fiscal_year": "2024",
                        "field_id": "average_age",
                        "field_name_ja": "平均年齢",
                        "extracted_value": "",
                    },
                ],
            )
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "roe",
                        "extracted_value": "old-value",
                        "review_decision": "correct",
                        "corrected_value": "0.14",
                        "reviewer_note": "保存済みメモ",
                        "reviewed_at": "2026-06-17T00:00:00Z",
                    }
                ],
            )

            all_rows = read_review_queue(root)["rows"]
            saved_rows = read_review_queue(root, review_status="saved")["rows"]
            unsaved_rows = read_review_queue(root, review_status="unsaved")["rows"]

            roe = next(row for row in all_rows if row["field_id"] == "roe")
            self.assertEqual(roe["review_saved"], "yes")
            self.assertEqual(roe["review_decision"], "correct")
            self.assertEqual(roe["corrected_value"], "0.14")
            self.assertEqual(roe["reviewer_note"], "保存済みメモ")
            self.assertEqual(roe["applied_status"], "")
            self.assertEqual(roe["extracted_value"], "0.12")
            self.assertEqual(roe["review_category"], "saved_unapplied")
            self.assertEqual(roe["review_category_label"], "保存済み未反映")
            average_age = next(row for row in all_rows if row["field_id"] == "average_age")
            self.assertEqual(average_age["review_category"], "missing")
            self.assertEqual(average_age["review_category_label"], "未取得")
            self.assertEqual(read_review_queue(root)["review_category_counts"], {"saved_unapplied": 1, "missing": 1})
            self.assertEqual(len(saved_rows), 1)
            self.assertEqual(saved_rows[0]["field_id"], "roe")
            self.assertEqual(len(unsaved_rows), 1)
            self.assertEqual(unsaved_rows[0]["field_id"], "average_age")

    def test_review_queue_splits_warning_categories_and_filters_by_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "orders",
                        "extracted_value": "100",
                        "review_reason": "validation_fail",
                    },
                    {
                        "company_year_id": "A_2024",
                        "field_id": "scope",
                        "extracted_value": "100",
                        "review_reason": "data_scope_mismatch",
                    },
                    {
                        "company_year_id": "A_2024",
                        "field_id": "confidence",
                        "extracted_value": "100",
                        "review_reason": "confidence_below_threshold",
                    },
                    {
                        "company_year_id": "A_2024",
                        "field_id": "blank",
                        "extracted_value": "",
                        "review_reason": "xbrl_tag_not_found",
                    },
                ],
            )

            all_rows = read_review_queue(root, review_status="active")
            validation_rows = read_review_queue(root, review_status="active", review_category="validation_issue")["rows"]
            scope_rows = read_review_queue(root, review_status="active", review_category="scope_warning")["rows"]
            warning_rows = read_review_queue(root, review_status="active", review_category="warning_candidate")["rows"]

            self.assertEqual(
                all_rows["review_category_counts"],
                {"validation_issue": 1, "scope_warning": 1, "warning_candidate": 1, "missing": 1},
            )
            self.assertEqual(validation_rows[0]["field_id"], "orders")
            self.assertEqual(scope_rows[0]["field_id"], "scope")
            self.assertEqual(warning_rows[0]["field_id"], "confidence")
            self.assertEqual(all_rows["review_category_labels"]["validation_issue"], "検算要確認")
            self.assertEqual(all_rows["review_category_labels"]["scope_warning"], "スコープ警告")

    def test_mark_company_field_not_applicable_saves_all_matching_company_years_and_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "INFR", "operating_company_name": "インフロニア"}])
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {"company_year_id": "INFR_2023", "fiscal_year": "2023", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "INFR_2024", "fiscal_year": "2024", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "MAEDA_2024", "fiscal_year": "2024", "field_id": "cost_labor", "field_name_ja": "労務費"},
                ],
            )

            result = mark_company_field_not_applicable(root, "INFR", "cost_labor", "HDなので対象外")

            self.assertEqual(result["marked"], 2)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual({row["company_year_id"] for row in resolved}, {"INFR_2023", "INFR_2024"})
            self.assertTrue(all(row["review_decision"] == "not_applicable" for row in resolved))
            exclusions = read_table(root / "config" / "company_field_exclusions.csv")
            self.assertEqual(exclusions[0]["company_id"], "INFR")
            self.assertEqual(exclusions[0]["field_id"], "cost_labor")

            active_rows = read_review_queue(root, review_status="active")["rows"]
            self.assertEqual([row["company_year_id"] for row in active_rows], ["MAEDA_2024"])

    def test_mark_company_field_not_applicable_can_be_limited_by_fiscal_year_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {"company_year_id": "MAEDA_2020", "fiscal_year": "2020", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "MAEDA_2021", "fiscal_year": "2021", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "MAEDA_2022", "fiscal_year": "2022", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "MAEDA_2023", "fiscal_year": "2023", "field_id": "cost_labor", "field_name_ja": "労務費"},
                ],
            )

            result = mark_company_field_not_applicable(root, "MAEDA", "cost_labor", "HD化後は対象外", start_year=2022)

            self.assertEqual(result["marked"], 2)
            self.assertEqual(result["start_year"], 2022)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual({row["company_year_id"] for row in resolved}, {"MAEDA_2022", "MAEDA_2023"})
            exclusions = read_table(root / "config" / "company_field_exclusions.csv")
            self.assertEqual(exclusions[0]["company_id"], "MAEDA")
            self.assertEqual(exclusions[0]["field_id"], "cost_labor")
            self.assertEqual(exclusions[0]["start_year"], "2022")
            self.assertEqual(exclusions[0]["end_year"], "")

    def test_mark_company_field_not_applicable_can_exclude_between_years_without_boundary_years(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {"company_year_id": "MAEDA_2021", "fiscal_year": "2021", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "MAEDA_2022", "fiscal_year": "2022", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "MAEDA_2023", "fiscal_year": "2023", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "MAEDA_2024", "fiscal_year": "2024", "field_id": "cost_labor", "field_name_ja": "労務費"},
                ],
            )

            result = mark_company_field_not_applicable(root, "MAEDA", "cost_labor", "境目年度は除外しない", start_year=2022, end_year=2023)

            self.assertEqual(result["marked"], 2)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual({row["company_year_id"] for row in resolved}, {"MAEDA_2022", "MAEDA_2023"})
            exclusions = read_table(root / "config" / "company_field_exclusions.csv")
            self.assertEqual(exclusions[0]["start_year"], "2022")
            self.assertEqual(exclusions[0]["end_year"], "2023")

    def test_mark_company_field_not_applicable_replaces_old_all_year_exclusion_and_stale_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "company_field_exclusions.csv",
                [{"company_id": "MAEDA", "field_id": "cost_labor", "reason": "old all years"}],
            )
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {"company_year_id": "MAEDA_2021", "fiscal_year": "2021", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "MAEDA_2022", "fiscal_year": "2022", "field_id": "cost_labor", "field_name_ja": "労務費"},
                    {"company_year_id": "MAEDA_2023", "fiscal_year": "2023", "field_id": "cost_labor", "field_name_ja": "労務費"},
                ],
            )
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {"company_year_id": "MAEDA_2021", "fiscal_year": "2021", "field_id": "cost_labor", "review_decision": "not_applicable"},
                    {"company_year_id": "MAEDA_2022", "fiscal_year": "2022", "field_id": "cost_labor", "review_decision": "not_applicable"},
                    {"company_year_id": "MAEDA_2023", "fiscal_year": "2023", "field_id": "cost_labor", "review_decision": "not_applicable"},
                ],
            )

            result = mark_company_field_not_applicable(root, "MAEDA", "cost_labor", "2023以降のみ", start_year=2023)

            self.assertEqual(result["marked"], 1)
            self.assertEqual(result["replaced_exclusions"], 1)
            self.assertEqual(result["stale_not_applicable_deleted"], 2)
            exclusions = read_table(root / "config" / "company_field_exclusions.csv")
            self.assertEqual(len(exclusions), 1)
            self.assertEqual(exclusions[0]["start_year"], "2023")
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual([row["company_year_id"] for row in resolved], ["MAEDA_2023"])

    def test_mark_resolved_reviews_applied_records_final_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {"company_year_id": "A_2024", "field_id": "roe", "review_decision": "correct", "corrected_value": "0.14"},
                    {"company_year_id": "A_2024", "field_id": "average_age", "review_decision": "reject"},
                    {"company_year_id": "B_2024", "field_id": "roe", "review_decision": "correct", "corrected_value": "0.12"},
                ],
            )
            write_table(
                root / "data" / "intermediate" / "normalized_validated_long.csv",
                [
                    {"company_year_id": "A_2024", "field_id": "roe"},
                    {"company_year_id": "A_2024", "field_id": "average_age"},
                ],
            )
            write_table(
                root / "data" / "final" / "final_master_long.csv",
                [{"company_year_id": "A_2024", "field_id": "roe", "value": "0.14", "review_status": "corrected"}],
            )

            result = mark_resolved_reviews_applied(root)

            self.assertEqual(result["total"], 3)
            rows = {
                (row["company_year_id"], row["field_id"]): row
                for row in read_table(root / "data" / "review" / "review_resolved.csv")
            }
            self.assertEqual(rows[("A_2024", "roe")]["applied_status"], "applied")
            self.assertEqual(rows[("A_2024", "roe")]["applied_value"], "0.14")
            self.assertTrue(rows[("A_2024", "roe")]["applied_at"])
            self.assertEqual(rows[("A_2024", "average_age")]["applied_status"], "rejected")
            self.assertEqual(rows[("B_2024", "roe")]["applied_status"], "not_found")

    def test_mark_resolved_reviews_applied_records_not_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [{"company_year_id": "INFR_2024", "field_id": "cost_labor", "review_decision": "not_applicable"}],
            )

            result = mark_resolved_reviews_applied(root)

            self.assertEqual(result["total"], 1)
            row = read_table(root / "data" / "review" / "review_resolved.csv")[0]
            self.assertEqual(row["applied_status"], "not_applicable")
            self.assertEqual(row["applied_value"], "")
            self.assertTrue(row["applied_at"])

    def test_review_upsert_rejects_key_not_in_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "data" / "review" / "review_queue.csv", [{"company_year_id": "A_2024", "field_id": "roe"}])

            with self.assertRaises(ValueError):
                upsert_resolved_reviews(
                    root,
                    [{"company_year_id": "B_2024", "field_id": "roe", "review_decision": "accept"}],
                )

    def test_ai_prompt_contains_domain_warnings_and_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in [
                "data/ai_bundle/AI_README.md",
                "data/ai_bundle/final_master_wide.csv",
                "data/ai_bundle/source_audit.csv",
            ]:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            result = build_prompt(
                root,
                {
                    "theme": "ROEと受注高の関係を見たい",
                    "companies": ["A", "B"],
                    "fiscal_years": ["2023", "2024"],
                    "fields": ["roe", "building_orders_total"],
                },
            )

            prompt = result["prompt"]
            self.assertIn("空欄は0として扱わない", prompt)
            self.assertIn("standalone、consolidated、segment", prompt)
            self.assertIn("source_audit.csv", prompt)
            self.assertIn("ROEと受注高の関係", prompt)
            self.assertIn("data/ai_bundle/final_master_wide.csv", result["references"])

    def test_cell_detail_marks_blank_with_review_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE", "target_unit": "%"}])
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [{"company_year_id": "A_2024", "fiscal_year": "2024", "operating_company_id": "A", "roe": ""}],
            )
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "fiscal_year": "2024",
                        "field_id": "roe",
                        "field_name_ja": "ROE",
                        "extracted_value": "8.2",
                        "review_reason": "confidence_below_threshold",
                    }
                ],
            )

            detail = read_cell_detail(root, "A_2024", "roe")

            self.assertEqual(detail["status"], "blank_with_review_candidate")
            self.assertTrue(detail["has_review_candidate"])
            self.assertEqual(detail["field_name_ja"], "ROE")
            self.assertIn("セル作業", detail["next_action"])

    def test_cell_detail_uses_same_corroboration_status_as_wide_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE", "target_unit": "%"}])
            write_table(root / "config" / "company_master.csv", [{"operating_company_id": "A", "operating_company_name": "A社"}])
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [{"company_year_id": "A_2024", "fiscal_year": "2024", "operating_company_id": "A", "roe": "0.082"}],
            )
            write_table(
                root / "data" / "final" / "source_audit.csv",
                [{"company_year_id": "A_2024", "field_id": "roe", "value": "0.082", "validation_status": "ok"}],
            )
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_cell_resolutions(
                    conn,
                    [
                        {
                            "company_year_id": "A_2024",
                            "concept_id": "roe",
                            "value": 0.082,
                            "resolution": "needs_reconciliation",
                            "corroboration_count": 1,
                            "conflict_count": 0,
                        }
                    ],
                    run_id="run1",
                )
            finally:
                conn.close()

            detail = read_cell_detail(root, "A_2024", "roe")
            wide = read_wide(root, fields=["roe"])
            wide_status = wide["cell_statuses"]["A_2024"]["roe"]

            self.assertEqual(detail["status"], "corroboration_needs_reconciliation")
            self.assertEqual(detail["status"], wide_status["status"])
            self.assertEqual(detail["status_label"], wide_status["status_label"])

    def test_cell_detail_marks_document_failure_before_extraction_advice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "field_definition.csv", [{"field_id": "average_age", "field_name_ja": "平均年齢"}])
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [{"company_year_id": "TAKENAKA_2015", "fiscal_year": "2015", "operating_company_id": "TAKENAKA", "average_age": ""}],
            )
            report_path = root / "data" / "final" / "run_report.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                "\n".join(
                    [
                        "## Failed Documents",
                        "",
                        "| company_year_id | failure_reason |",
                        "| --- | --- |",
                        "| TAKENAKA_2015 | target_document_not_found |",
                    ]
                ),
                encoding="utf-8",
            )

            detail = read_cell_detail(root, "TAKENAKA_2015", "average_age")

            self.assertEqual(detail["status"], "document_failed")
            self.assertEqual(detail["failure_reason"], "target_document_not_found")
            self.assertIn("docID", detail["next_action"])

    def test_cell_detail_includes_semantics_source_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE", "target_unit": "%"}])
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024", "fiscal_year": "2024", "roe": "8.2"}])
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_cell_resolutions(
                    conn,
                    [
                        {
                            "company_year_id": "A_2024",
                            "concept_id": "roe",
                            "value": 8.2,
                            "resolution": "auto_confirmed",
                            "buckets": ["xbrl"],
                            "sources": ["xbrl_vs_local:test"],
                        }
                    ],
                    run_id="run1",
                )
                semantics_store.replace_corroborations(
                    conn,
                    [
                        {
                            "company_year_id": "A_2024",
                            "field_id": "roe",
                            "check_kind": "xbrl_vs_local",
                            "check_ref": "test",
                            "matched": True,
                            "primary_value": 8.2,
                            "other_value": 8.2,
                            "difference": 0.0,
                            "restatement_suspected": False,
                            "detail": {"unit_normalized": "%"},
                        }
                    ],
                    run_id="run1",
                )
                semantics_store.replace_observed_items(
                    conn,
                    [
                        {
                            "observed_item_id": "xm_roe",
                            "item_kind": "xbrl",
                            "element_id": "jppfs_cor:ROE",
                            "label_ja": "自己資本利益率",
                            "unit": "%",
                            "source": "metric_catalog",
                        }
                    ],
                )
                semantics_store.replace_concept_mappings(
                    conn,
                    [
                        {
                            "mapping_id": "cmap_roe",
                            "observed_item_id": "xm_roe",
                            "concept_id": "roe",
                            "action": "map",
                            "status": "confirmed",
                            "decided_by": "human:test",
                        }
                    ],
                )
            finally:
                conn.close()

            detail = read_cell_detail(root, "A_2024", "roe")

            chain = detail["source_chain"]
            self.assertEqual(chain["status"], "ready")
            self.assertEqual(chain["fact_resolution"]["resolution"], "auto_confirmed")
            self.assertEqual(chain["fact_resolution"]["sources"], ["xbrl_vs_local:test"])
            self.assertEqual(chain["corroborations"][0]["detail"]["unit_normalized"], "%")
            self.assertEqual(chain["mappings"][0]["mapping_id"], "cmap_roe")
            self.assertEqual(chain["observed_items"][0]["observed_item_id"], "xm_roe")

    def test_review_queue_active_filter_hides_applied_saved_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {"company_year_id": "A_2024", "field_id": "roe", "extracted_value": "0.12"},
                    {"company_year_id": "B_2024", "field_id": "roe", "extracted_value": "0.11"},
                    {"company_year_id": "C_2024", "field_id": "roe", "extracted_value": "0.10"},
                ],
            )
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {"company_year_id": "A_2024", "field_id": "roe", "review_decision": "accept", "applied_status": "applied"},
                    {"company_year_id": "B_2024", "field_id": "roe", "review_decision": "accept", "applied_status": "not_exported"},
                ],
            )

            active_rows = read_review_queue(root, review_status="active")["rows"]
            all_rows = read_review_queue(root, review_status="")["rows"]

            self.assertEqual([row["company_year_id"] for row in active_rows], ["B_2024", "C_2024"])
            self.assertEqual(len(all_rows), 3)

    def test_reextract_with_review_runs_reextract_then_saved_review_apply(self):
        calls = []
        logs = []
        original_call = pipeline._call
        original_run_all = pipeline.run_all
        original_apply_review = pipeline.apply_review

        def fake_call(name, func, root, args, log):
            calls.append(("_call", name))
            if log:
                log(f"{name} called")
            return 0

        def fake_run_all(root, log=None, fiscal_years=None):
            calls.append(("run_all", root, fiscal_years))
            if log:
                log("run-all called")
            return 0

        def fake_apply_review(root, log=None, reviewed="data/review/review_resolved.csv"):
            calls.append(("apply_review", root, reviewed))
            if log:
                log("apply-review called")
            return 0

        try:
            pipeline._call = fake_call
            pipeline.run_all = fake_run_all
            pipeline.apply_review = fake_apply_review
            root = Path("/tmp/yuho-test-root")

            code = pipeline.reextract_with_review(root, log=logs.append)
        finally:
            pipeline._call = original_call
            pipeline.run_all = original_run_all
            pipeline.apply_review = original_apply_review

        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [
                ("_call", "locate-sections"),
                ("run_all", root, None),
                ("apply_review", root, "data/review/review_resolved.csv"),
            ],
        )
        self.assertIn("[saved-review] apply review_resolved.csv after re-extraction", logs)

    def test_job_start_returns_without_self_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = JobManager(Path(tmp))

            started = manager.start("quick", lambda root, log: 0)

            self.assertEqual(started["name"], "quick")
            self.assertIn(started["status"], {"running", "succeeded"})
            for _ in range(20):
                current = manager.current()
                if current["status"] == "succeeded":
                    break
                time.sleep(0.01)
            self.assertEqual(manager.current()["status"], "succeeded")

    def test_write_parquet_handles_mixed_bool_and_string_object_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.parquet"

            written = write_table(
                path,
                [
                    {"company_year_id": "A_2024", "review_required": False},
                    {"company_year_id": "B_2024", "review_required": "True"},
                ],
            )

            self.assertEqual(written, path)
            rows = read_table(path)
            self.assertEqual([str(row["review_required"]) for row in rows], ["False", "True"])

    def test_write_parquet_handles_numeric_column_with_blank_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "counts.parquet"

            written = write_table(
                path,
                [
                    {"company_year_id": "A_2024", "corroboration_count": 2},
                    {"company_year_id": "B_2024", "corroboration_count": ""},
                ],
            )

            self.assertEqual(written, path)
            rows = read_table(path)
            self.assertEqual(rows[0]["corroboration_count"], 2)
            self.assertTrue(is_blankish(rows[1]["corroboration_count"]))

    def test_review_queue_writes_nan_extracted_value_as_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "review_queue.csv"

            rows = build_review_queue(
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "average_age",
                        "value": float("nan"),
                        "review_required": True,
                        "review_reason": "xbrl_tag_not_found",
                    }
                ],
                [{"field_id": "average_age", "field_name_ja": "平均年齢"}],
                [{"company_year_id": "A_2024", "fiscal_year": "2024", "operating_company_id": "A"}],
            )

            write_table(path, rows)

            written = read_table(path)
            self.assertEqual(written[0]["extracted_value"], "")

    def test_review_queue_suppresses_blank_candidate_when_value_candidate_exists(self):
        rows = build_review_queue(
            [
                {
                    "company_year_id": "A_2024",
                    "field_id": "average_age",
                    "value": "",
                    "review_required": True,
                    "review_reason": "xbrl_tag_not_found",
                    "confidence": 0.0,
                },
                {
                    "company_year_id": "A_2024",
                    "field_id": "average_age",
                    "value": 43.7,
                    "review_required": True,
                    "review_reason": "confidence_below_threshold",
                    "confidence": 0.88,
                },
            ],
            [{"field_id": "average_age", "field_name_ja": "平均年齢"}],
            [{"company_year_id": "A_2024", "fiscal_year": "2024", "operating_company_id": "A"}],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["extracted_value"], 43.7)

    def test_review_queue_suppresses_blank_candidate_when_value_is_auto_accepted(self):
        rows = build_review_queue(
            [
                {
                    "company_year_id": "A_2024",
                    "field_id": "average_age",
                    "value": "",
                    "review_required": True,
                    "review_reason": "xbrl_tag_not_found",
                    "confidence": 0.0,
                },
                {
                    "company_year_id": "A_2024",
                    "field_id": "average_age",
                    "value": 43.7,
                    "review_required": False,
                    "review_reason": "",
                    "confidence": 0.92,
                    "unit_normalized": "歳",
                    "data_scope": "standalone",
                },
            ],
            [
                {
                    "field_id": "average_age",
                    "field_name_ja": "平均年齢",
                    "target_unit": "歳",
                    "data_scope_required": "standalone",
                    "review_threshold": "0.90",
                }
            ],
            [{"company_year_id": "A_2024", "fiscal_year": "2024", "operating_company_id": "A"}],
        )

        self.assertEqual(rows, [])

    def test_review_queue_skips_company_field_exclusions(self):
        rows = build_review_queue(
            [
                {"company_year_id": "INFR_2024", "field_id": "cost_labor", "value": "", "review_required": True, "review_reason": "blank"},
                {"company_year_id": "MAEDA_2024", "field_id": "cost_labor", "value": "", "review_required": True, "review_reason": "blank"},
            ],
            [{"field_id": "cost_labor", "field_name_ja": "労務費"}],
            [
                {"company_year_id": "INFR_2024", "fiscal_year": "2024", "operating_company_id": "INFR"},
                {"company_year_id": "MAEDA_2024", "fiscal_year": "2024", "operating_company_id": "MAEDA"},
            ],
            company_field_exclusions=[{"company_id": "INFR", "field_id": "cost_labor"}],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company_year_id"], "MAEDA_2024")

    def test_review_queue_applies_company_field_exclusions_only_inside_year_range(self):
        rows = build_review_queue(
            [
                {"company_year_id": "MAEDA_2020", "field_id": "cost_labor", "value": "", "review_required": True, "review_reason": "blank"},
                {"company_year_id": "MAEDA_2022", "field_id": "cost_labor", "value": "", "review_required": True, "review_reason": "blank"},
                {"company_year_id": "MAEDA_2024", "field_id": "cost_labor", "value": "", "review_required": True, "review_reason": "blank"},
            ],
            [{"field_id": "cost_labor", "field_name_ja": "労務費"}],
            [
                {"company_year_id": "MAEDA_2020", "fiscal_year": "2020", "operating_company_id": "MAEDA"},
                {"company_year_id": "MAEDA_2022", "fiscal_year": "2022", "operating_company_id": "MAEDA"},
                {"company_year_id": "MAEDA_2024", "fiscal_year": "2024", "operating_company_id": "MAEDA"},
            ],
            company_field_exclusions=[{"company_id": "MAEDA", "field_id": "cost_labor", "start_year": "2022"}],
        )

        self.assertEqual([row["company_year_id"] for row in rows], ["MAEDA_2020"])


if __name__ == "__main__":
    unittest.main()
