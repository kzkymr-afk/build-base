import tempfile
import time
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, read_yaml, write_table, write_yaml
from yuho_auto_extract.review_queue import build_review_queue
from yuho_auto_extract.services.ai_prompt import build_prompt
from yuho_auto_extract.services.datasets import read_cell_detail, read_chart_data, read_options, read_review_queue, read_wide
from yuho_auto_extract.services import pipeline
from yuho_auto_extract.services.review_learning_impact import capture_field_coverage, write_review_learning_impact
from yuho_auto_extract.services.reviews import delete_resolved_reviews, mark_resolved_reviews_applied, upsert_resolved_reviews
from yuho_auto_extract.services.rule_candidates import (
    apply_rule_candidates,
    build_rule_candidates,
    generate_rule_candidates,
    parse_review_note,
    read_rule_candidates,
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
            self.assertEqual(len(saved_rows), 1)
            self.assertEqual(saved_rows[0]["field_id"], "roe")
            self.assertEqual(len(unsaved_rows), 1)
            self.assertEqual(unsaved_rows[0]["field_id"], "average_age")

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
            self.assertIn("accept", detail["next_action"])

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

    def test_parse_review_note_extracts_rule_hints(self):
        parsed = parse_review_note(
            "\n".join(
                [
                    "LOC: 第一部 企業情報 > 第1 企業の概況 > 従業員の状況",
                    "TABLE: 提出会社の状況",
                    "LABEL: 平均年間給与",
                    "SCOPE: standalone / 提出会社",
                    "UNIT: 円",
                    "QUOTE: 提出会社の状況｜平均年間給与｜10,050,302円",
                    "XBRL_TAG: AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees",
                    "RULE_HINT: section_keywords=従業員の状況; row_label=平均年間給与|平均給与; table=提出会社の状況",
                    "GENERALITY: 全社共通",
                ]
            )
        )

        self.assertIn("従業員の状況", parsed["section_keywords"])
        self.assertIn("提出会社の状況", parsed["tables"])
        self.assertIn("平均年間給与", parsed["row_labels"])
        self.assertIn("平均給与", parsed["row_labels"])
        self.assertIn("AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees", parsed["xbrl_tags"])
        self.assertIn("全社共通", parsed["generalities"])

    def test_parse_review_note_ignores_empty_xbrl_tag_and_embedded_rule_hint(self):
        parsed = parse_review_note(
            "XBRL_TAG: なしRULE_HINT: section_keywords=従業員の状況; row_label=平均年間給与|平均給与; table=提出会社の状況"
        )

        self.assertEqual(parsed.get("xbrl_tags"), None)
        self.assertIn("従業員の状況", parsed["section_keywords"])
        self.assertIn("平均給与", parsed["row_labels"])
        self.assertIn("提出会社の状況", parsed["tables"])

    def test_parse_review_note_accepts_japanese_keys(self):
        parsed = parse_review_note("場所：【完成工事原価報告書】Ⅳ 経費\n単位：百万円")

        self.assertIn("【完成工事原価報告書】Ⅳ 経費", parsed["section_keywords"])
        self.assertEqual(parsed["units"], ["百万円"])

    def test_build_rule_candidates_groups_resolved_review_notes_by_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "field_definition.csv", [{"field_id": "average_salary", "field_name_ja": "平均給与"}])
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "review_decision": "correct",
                        "corrected_value": "10050302",
                        "reviewer_note": "LOC: 従業員の状況\nLABEL: 平均年間給与\nXBRL_TAG: AverageAnnualSalary\nRULE_HINT: row_label=平均年間給与|平均給与; table=提出会社の状況\nGENERALITY: 全社共通",
                    },
                    {
                        "company_year_id": "B_2024",
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "review_decision": "correct",
                        "corrected_value": "9000000",
                        "reviewer_note": "LOC=従業員の状況; TABLE=提出会社の状況; LABEL=平均年間給与; SCOPE=standalone; UNIT=円",
                    },
                    {
                        "company_year_id": "C_2024",
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "review_decision": "reject",
                        "corrected_value": "",
                        "reviewer_note": "LABEL: should be ignored",
                    },
                ],
            )

            candidates = build_rule_candidates(root)

            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate["field_id"], "average_salary")
            self.assertEqual(candidate["evidence_count"], "2")
            self.assertIn("AverageAnnualSalary", candidate["proposed_xbrl_tags"])
            self.assertIn("従業員の状況", candidate["proposed_section_keywords"])
            self.assertIn("提出会社の状況", candidate["proposed_tables"])
            self.assertIn("平均給与", candidate["proposed_row_labels"])
            self.assertIn("10050302", candidate["reviewed_value_examples"])
            self.assertIn("field_definition.xbrl_tag_candidates", candidate["recommended_action"])

    def test_build_rule_candidates_infers_from_corrected_value_without_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "target_unit": "円",
                        "data_scope_required": "standalone",
                        "section_keywords": "従業員の状況",
                        "synonyms_ja": "平均年間給与;平均給与",
                    }
                ],
            )
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "review_decision": "correct",
                        "corrected_value": "8928794",
                        "reviewer_note": "",
                    }
                ],
            )
            write_table(
                root / "data" / "intermediate" / "candidate_blocks.jsonl",
                [
                    {
                        "company_year_id": "A_2024",
                        "section_name": "review_average_salary",
                        "heading_keywords": ["従業員の状況"],
                        "table_keywords": ["提出会社の状況", "平均年間給与"],
                        "target_fields": ["average_salary"],
                        "unit_hint": "円",
                        "scope_hint": "standalone",
                        "raw_text": "\n".join(
                            [
                                "提出会社の状況",
                                "従業員数(人)",
                                "平均年齢(歳)",
                                "平均勤続年数(年)",
                                "平均年間給与(円)",
                                "7,527〔1,746〕",
                                "43.7",
                                "18.3",
                                "8,928,794",
                            ]
                        ),
                    }
                ],
            )

            candidates = build_rule_candidates(root)

            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate["field_id"], "average_salary")
            self.assertIn("candidate_block", candidate["learning_source"])
            self.assertIn("従業員の状況", candidate["proposed_section_keywords"])
            self.assertIn("提出会社の状況", candidate["proposed_tables"])
            self.assertIn("平均年間給与", candidate["proposed_row_labels"])
            self.assertIn("8928794", candidate["reviewed_value_examples"])

    def test_build_rule_candidates_uses_company_candidate_blocks_for_sibling_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "average_tenure",
                        "field_name_ja": "平均勤続年数",
                        "target_unit": "年",
                        "data_scope_required": "standalone",
                        "section_keywords": "",
                        "synonyms_ja": "",
                    }
                ],
            )
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "average_tenure",
                        "field_name_ja": "平均勤続年数",
                        "review_decision": "correct",
                        "corrected_value": "21.0",
                        "reviewer_note": "",
                    }
                ],
            )
            write_table(
                root / "data" / "intermediate" / "candidate_blocks.jsonl",
                [
                    {
                        "company_year_id": "A_2024",
                        "section_name": "review_average_age",
                        "heading_keywords": ["従業員の状況"],
                        "table_keywords": ["提出会社の状況", "平均年齢"],
                        "target_fields": ["average_age"],
                        "unit_hint": "年",
                        "scope_hint": "standalone",
                        "raw_text": "５ 【従業員の状況】 提出会社の状況 従業員数(人) 平均年齢(歳) 平均勤続年数(年) 平均年間給与(円) 1,222 44.1 21.0 7,541,680",
                    },
                    {
                        "company_year_id": "A_2024",
                        "section_name": "orders_backlog",
                        "heading_keywords": ["受注実績"],
                        "table_keywords": ["建築"],
                        "target_fields": ["building_orders_total"],
                        "unit_hint": "百万円",
                        "scope_hint": "standalone",
                        "raw_text": "受注実績 建築 21.0",
                    }
                ],
            )

            candidates = build_rule_candidates(root)

            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate["field_id"], "average_tenure")
            self.assertIn("company_candidate_block", candidate["learning_source"])
            self.assertIn("従業員の状況", candidate["proposed_section_keywords"])
            self.assertNotIn("受注実績", candidate["proposed_section_keywords"])
            self.assertIn("提出会社の状況", candidate["proposed_tables"])
            self.assertIn("平均勤続年数", candidate["proposed_row_labels"])
            self.assertIn("21.0", candidate["reviewed_value_examples"])

    def test_build_rule_candidates_keeps_value_only_reviews_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "cost_labor",
                        "field_name_ja": "労務費",
                        "target_unit": "百万円",
                        "data_scope_required": "standalone",
                        "section_keywords": "",
                        "synonyms_ja": "",
                    }
                ],
            )
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "cost_labor",
                        "field_name_ja": "労務費",
                        "review_decision": "correct",
                        "corrected_value": "20531",
                        "reviewer_note": "",
                    }
                ],
            )

            candidates = build_rule_candidates(root)

            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate["field_id"], "cost_labor")
            self.assertEqual(candidate["confidence"], "low")
            self.assertEqual(candidate["needs_manual_check"], "yes")
            self.assertIn("労務費", candidate["proposed_row_labels"])
            self.assertIn("百万円", candidate["proposed_unit"])
            self.assertIn("review_value_only", candidate["learning_source"])

    def test_apply_rule_candidates_merges_field_definition_and_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "target_unit": "円",
                        "xbrl_tag_candidates": "AverageAnnualSalary",
                        "section_keywords": "",
                        "synonyms_ja": "",
                    }
                ],
            )
            write_yaml(
                root / "config" / "extraction_sections.yml",
                {
                    "orders_backlog": {
                        "description": "既存設定",
                        "heading_keywords": ["受注高"],
                        "table_keywords": ["建築"],
                        "target_fields": ["building_orders_total"],
                    }
                },
            )
            write_table(
                root / "data" / "review" / "rule_candidates.csv",
                [
                    {
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "evidence_count": "2",
                        "proposed_xbrl_tags": "なし;AverageAnnualSalary;AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees",
                        "proposed_section_keywords": "従業員の状況",
                        "proposed_tables": "提出会社の状況",
                        "proposed_row_labels": "平均年間給与;平均給与",
                    }
                ],
            )

            result = apply_rule_candidates(root, ["average_salary"])

            self.assertEqual(result["applied_candidates"], 1)
            self.assertEqual(result["updated_sections"], ["review_average_salary"])
            field = read_table(root / "config" / "field_definition.csv")[0]
            self.assertIn("AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees", field["xbrl_tag_candidates"])
            self.assertIn("従業員の状況", field["section_keywords"])
            self.assertIn("平均年間給与", field["synonyms_ja"])
            self.assertNotIn("なし", field["xbrl_tag_candidates"])
            sections = read_yaml(root / "config" / "extraction_sections.yml")
            self.assertEqual(sections["orders_backlog"]["target_fields"], ["building_orders_total"])
            self.assertIn("従業員の状況", sections["review_average_salary"]["heading_keywords"])
            self.assertIn("提出会社の状況", sections["review_average_salary"]["table_keywords"])
            self.assertIn("平均年間給与", sections["review_average_salary"]["table_keywords"])
            self.assertEqual(sections["review_average_salary"]["review_table_keywords"], ["提出会社の状況"])
            self.assertEqual(sections["review_average_salary"]["review_row_labels"], ["平均年間給与", "平均給与"])
            self.assertEqual(sections["review_average_salary"]["review_row_labels_by_field"], {"average_salary": ["平均年間給与", "平均給与"]})
            self.assertEqual(sections["review_average_salary"]["target_fields"], ["average_salary"])
            self.assertTrue(any("field_definition.csv.bak-" in path for path in result["backups"]))
            self.assertTrue(any("extraction_sections.yml.bak-" in path for path in result["backups"]))

    def test_apply_rule_candidates_marks_candidate_applied_and_hides_from_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "target_unit": "円",
                        "xbrl_tag_candidates": "",
                        "section_keywords": "",
                        "synonyms_ja": "",
                    }
                ],
            )
            write_yaml(root / "config" / "extraction_sections.yml", {})
            write_table(
                root / "data" / "review" / "rule_candidates.csv",
                [
                    {
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "evidence_count": "2",
                        "proposed_section_keywords": "従業員の状況",
                        "proposed_tables": "提出会社の状況",
                        "proposed_row_labels": "平均年間給与",
                        "proposed_scope": "standalone",
                        "proposed_unit": "円",
                    }
                ],
            )

            result = apply_rule_candidates(root, ["average_salary"])

            self.assertEqual(result["applied_candidates"], 1)
            self.assertEqual(read_rule_candidates(root, candidate_status="active"), [])
            applied = read_rule_candidates(root, candidate_status="applied")
            self.assertEqual(len(applied), 1)
            self.assertEqual(applied[0]["candidate_status"], "applied")
            self.assertTrue(applied[0]["candidate_applied_at"])
            decisions = read_table(root / "data" / "review" / "rule_candidate_decisions.csv")
            self.assertEqual(decisions[0]["candidate_status"], "applied")

    def test_generated_rule_candidate_stays_hidden_after_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "target_unit": "円",
                        "data_scope_required": "standalone",
                        "section_keywords": "",
                        "synonyms_ja": "平均年間給与",
                    }
                ],
            )
            write_yaml(root / "config" / "extraction_sections.yml", {})
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "review_decision": "correct",
                        "corrected_value": "8928794",
                        "reviewer_note": "",
                    }
                ],
            )
            write_table(
                root / "data" / "intermediate" / "candidate_blocks.jsonl",
                [
                    {
                        "company_year_id": "A_2024",
                        "section_name": "review_average_salary",
                        "heading_keywords": ["従業員の状況"],
                        "table_keywords": ["提出会社の状況", "平均年間給与"],
                        "target_fields": ["average_salary"],
                        "unit_hint": "円",
                        "scope_hint": "standalone",
                        "raw_text": "提出会社の状況 平均年間給与(円) 8,928,794",
                    }
                ],
            )

            generated = generate_rule_candidates(root)
            self.assertEqual(generated["total"], 1)
            self.assertEqual(generated["all_total"], 1)
            self.assertEqual(generated["applied_total"], 0)
            self.assertEqual(generated["status_counts"], {"active": 1, "applied": 0, "all": 1})
            apply_rule_candidates(root, ["average_salary"])
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "review_decision": "correct",
                        "corrected_value": "8928794",
                        "reviewer_note": "",
                    },
                    {
                        "company_year_id": "B_2024",
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "review_decision": "correct",
                        "corrected_value": "9000000",
                        "reviewer_note": "",
                    },
                ],
            )
            write_table(
                root / "data" / "intermediate" / "candidate_blocks.jsonl",
                [
                    {
                        "company_year_id": "A_2024",
                        "section_name": "review_average_salary",
                        "heading_keywords": ["従業員の状況"],
                        "table_keywords": ["提出会社の状況", "平均年間給与"],
                        "target_fields": ["average_salary"],
                        "unit_hint": "円",
                        "scope_hint": "standalone",
                        "raw_text": "提出会社の状況 平均年間給与(円) 8,928,794",
                    },
                    {
                        "company_year_id": "B_2024",
                        "section_name": "review_average_salary",
                        "heading_keywords": ["別セクション"],
                        "table_keywords": ["提出会社の状況", "平均年間給与"],
                        "target_fields": ["average_salary"],
                        "unit_hint": "円",
                        "scope_hint": "standalone",
                        "raw_text": "別セクション 提出会社の状況 平均年間給与(円) 9,000,000",
                    },
                ],
            )
            regenerated = generate_rule_candidates(root)

            self.assertEqual(regenerated["total"], 0)
            self.assertEqual(regenerated["all_total"], 1)
            self.assertEqual(regenerated["applied_total"], 1)
            self.assertEqual(regenerated["status_counts"], {"active": 0, "applied": 1, "all": 1})
            self.assertEqual(read_rule_candidates(root, candidate_status="active"), [])
            self.assertEqual(len(read_rule_candidates(root, candidate_status="applied")), 1)

    def test_review_learning_impact_reports_field_level_deltas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "rd_expense",
                        "field_name_ja": "研究開発費",
                    }
                ],
            )
            write_table(
                root / "data" / "final" / "field_coverage.csv",
                [
                    {
                        "field_id": "rd_expense",
                        "field_name_ja": "研究開発費",
                        "filled_company_years": "10",
                        "coverage_pct": "0.5000",
                    }
                ],
            )
            before = capture_field_coverage(root)
            write_table(
                root / "data" / "final" / "field_coverage.csv",
                [
                    {
                        "field_id": "rd_expense",
                        "field_name_ja": "研究開発費",
                        "filled_company_years": "12",
                        "coverage_pct": "0.6000",
                    }
                ],
            )
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [{"company_year_id": "A_2024", "field_id": "rd_expense", "review_decision": "correct"}],
            )
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [{"company_year_id": "B_2024", "field_id": "rd_expense"}],
            )
            write_table(
                root / "data" / "review" / "rule_candidates.csv",
                [
                    {
                        "field_id": "rd_expense",
                        "field_name_ja": "研究開発費",
                        "evidence_count": "2",
                        "confidence": "high",
                        "needs_manual_check": "no",
                        "recommended_action": "LOCAL_TABLE rule",
                    }
                ],
            )

            result = write_review_learning_impact(
                root,
                before,
                {
                    "auto_field_ids": ["rd_expense"],
                    "applied_result": {
                        "updated_fields": [{"field_id": "rd_expense", "columns": ["synonyms_ja"]}],
                        "updated_sections": ["review_rd_expense"],
                    },
                    "generated": {"status_counts": {"active": 1, "applied": 0, "all": 1}},
                },
            )

            self.assertEqual(result["summary"]["improved_fields"], 1)
            self.assertEqual(result["summary"]["total_filled_delta"], 2)
            rows = read_table(root / "data" / "review" / "review_learning_impact.csv")
            self.assertEqual(rows[0]["field_id"], "rd_expense")
            self.assertEqual(rows[0]["filled_delta"], "2")
            self.assertEqual(rows[0]["saved_review_count"], "1")
            self.assertEqual(rows[0]["review_queue_after"], "1")
            self.assertEqual(rows[0]["auto_applied"], "yes")
            self.assertEqual(rows[0]["applied_columns"], "synonyms_ja")
            self.assertEqual(rows[0]["applied_sections"], "review_rd_expense")
            markdown = (root / "data" / "review" / "review_learning_impact.md").read_text(encoding="utf-8")
            self.assertIn("Review Learning Impact", markdown)
            self.assertIn("total_filled_delta: 2", markdown)

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

    def test_reextract_with_review_auto_applies_high_confidence_review_learning(self):
        calls = []
        logs = []
        original_call = pipeline._call
        original_run_all = pipeline.run_all
        original_apply_review = pipeline.apply_review
        original_generate = pipeline.rule_candidates.generate_rule_candidates
        original_apply_candidates = pipeline.rule_candidates.apply_rule_candidates

        def fake_call(name, func, root, args, log):
            calls.append(("_call", name))
            return 0

        def fake_run_all(root, log=None, fiscal_years=None):
            calls.append(("run_all", fiscal_years))
            return 0

        def fake_apply_review(root, log=None, reviewed="data/review/review_resolved.csv"):
            calls.append(("apply_review", reviewed))
            return 0

        def fake_generate(root):
            calls.append(("generate_candidates", root))
            return {
                "status_counts": {"active": 2, "applied": 1, "all": 3},
                "rows": [
                    {"field_id": "average_salary", "confidence": "high", "needs_manual_check": "no"},
                    {"field_id": "cost_labor", "confidence": "medium", "needs_manual_check": "yes"},
                ],
            }

        def fake_apply_candidates(root, field_ids):
            calls.append(("apply_candidates", tuple(field_ids)))
            return {"updated_sections": ["review_average_salary"]}

        try:
            pipeline._call = fake_call
            pipeline.run_all = fake_run_all
            pipeline.apply_review = fake_apply_review
            pipeline.rule_candidates.generate_rule_candidates = fake_generate
            pipeline.rule_candidates.apply_rule_candidates = fake_apply_candidates

            code = pipeline.reextract_with_review(Path("/tmp/yuho-test-root"), log=logs.append)
        finally:
            pipeline._call = original_call
            pipeline.run_all = original_run_all
            pipeline.apply_review = original_apply_review
            pipeline.rule_candidates.generate_rule_candidates = original_generate
            pipeline.rule_candidates.apply_rule_candidates = original_apply_candidates

        self.assertEqual(code, 0)
        self.assertIn(("apply_candidates", ("average_salary",)), calls)
        self.assertIn("[review-learning] candidates active=2 applied=1 all=3", logs)
        self.assertTrue(any("auto-applied fields=average_salary" in item for item in logs))

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


if __name__ == "__main__":
    unittest.main()
