import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.ai_bundle import build_ai_bundle
from yuho_auto_extract.io_utils import read_table, write_table, write_yaml
from yuho_auto_extract.services.algorithm_audit import build_algorithm_audit_bundle


class AIBundleTests(unittest.TestCase):
    def test_build_ai_bundle_copies_only_ai_friendly_materials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root / "data" / "final"
            final.mkdir(parents=True)
            fixtures = {
                "final_master_wide.csv": "company_year_id,value\nA_2024,1\n",
                "analysis_dataset.csv": "company_year_id,value\nA_2024,1\n",
                "final_master_long.csv": "company_year_id,field_id,value\nA_2024,x,1\n",
                "source_audit.csv": "company_year_id,field_id,field_name_ja,value\nA_2024,x,項目,1\n",
                "final_master_field_definition.csv": "field_id,field_name_ja\nx,項目\n",
                "final_master_company_year_master.csv": "company_year_id\nA_2024\n",
                "field_coverage.csv": "field_id,filled_company_years\nx,1\n",
                "field_coverage.md": "# Field Coverage\n",
                "run_report.md": "# Run Report\n",
                "unsupported_fields_plan.md": "# Scope Exclusions\n",
                "table_pattern_backlog.md": "# Table Pattern Backlog\n",
            }
            for name, content in fixtures.items():
                (final / name).write_text(content, encoding="utf-8")
            (final / "final_master_wide.xlsx").write_text("not copied", encoding="utf-8")

            copied = build_ai_bundle(root)
            bundle = root / "data" / "ai_bundle"
            names = {path.name for path in bundle.iterdir() if path.is_file()}

            self.assertEqual(len(copied), len(fixtures))
            self.assertIn("AI_README.md", names)
            self.assertIn("manifest.json", names)
            self.assertIn("field_definition.csv", names)
            self.assertNotIn("final_master_wide.xlsx", names)
            self.assertIn("空欄は0ではありません", (bundle / "AI_README.md").read_text(encoding="utf-8"))

    def test_build_algorithm_audit_bundle_flags_low_evidence_global_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "category": "human_capital",
                        "preferred_method": "XBRL_CSV",
                        "target_unit": "円",
                        "data_scope_required": "standalone",
                        "xbrl_tag_candidates": "",
                        "synonyms_ja": "平均年間給与;平均給与",
                        "section_keywords": "従業員の状況",
                    }
                ],
            )
            write_yaml(
                root / "config" / "extraction_sections.yml",
                {
                    "review_average_salary": {
                        "description": "レビュー由来ヒント: 平均給与",
                        "heading_keywords": ["従業員の状況"],
                        "table_keywords": ["提出会社の状況", "平均年間給与"],
                        "target_fields": ["average_salary"],
                    }
                },
            )
            write_yaml(root / "config" / "validation_rules.yml", {"rules": []})
            write_table(
                root / "data" / "final" / "field_coverage.csv",
                [
                    {
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "filled_company_years": "1",
                        "total_company_years": "4",
                        "coverage_pct": "0.25",
                    }
                ],
            )
            (root / "data" / "final" / "run_report.md").parent.mkdir(parents=True, exist_ok=True)
            (root / "data" / "final" / "run_report.md").write_text("# Run Report\n", encoding="utf-8")
            write_table(
                root / "data" / "final" / "final_master_long.csv",
                [{"company_year_id": "A_2024", "field_id": "average_salary", "extraction_method": "LOCAL_RULE_TABLE"}],
            )
            write_table(
                root / "data" / "final" / "source_audit.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "value": "8885000",
                        "extraction_method": "LOCAL_RULE_TABLE",
                        "source_quote": "平均年間給与 8,885千円",
                    }
                ],
            )
            write_table(
                root / "data" / "review" / "rule_candidates.csv",
                [
                    {
                        "field_id": "average_salary",
                        "field_name_ja": "平均給与",
                        "evidence_count": "1",
                        "company_year_ids": "A_2024",
                        "generality": "全社共通",
                        "needs_manual_check": "yes",
                        "recommended_action": "LOCAL_TABLE rule",
                    }
                ],
            )
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "average_salary",
                        "review_decision": "correct",
                        "corrected_value": "8885000",
                        "applied_status": "",
                    }
                ],
            )

            result = build_algorithm_audit_bundle(root)
            audit_dir = root / "data" / "algorithm_audit"
            risk_flags = read_table(audit_dir / "risk_flags.csv")

            self.assertTrue((audit_dir / "ALGORITHM_AUDIT_PROMPT.md").exists())
            self.assertIn("レビュー由来のルール追加", result["prompt"])
            self.assertGreaterEqual(result["summary"]["risk_flags"], 3)
            self.assertTrue(any(row["issue"] == "single_evidence_global_rule" for row in risk_flags))
            self.assertTrue(any(row["issue"] == "saved_review_not_applied" for row in risk_flags))
            self.assertTrue((audit_dir / "config" / "field_definition.csv").exists())


if __name__ == "__main__":
    unittest.main()
