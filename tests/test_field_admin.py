import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.services import field_admin


class FieldAdminTests(unittest.TestCase):
    def test_append_field_terms_merges_without_duplicates_and_writes_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_field_definition(root)

            result = field_admin.append_field_terms(
                root,
                "construction_gross_profit_consolidated",
                synonyms=["売上総利益", "完成工事総利益"],
                xbrl_tags=["GrossProfit", "GrossProfitOnCompletedConstructionContracts"],
                section_keywords=["損益計算書", "完成工事"],
                note="会社別表記ゆれとして追加",
            )
            rows = read_table(root / "config" / "field_definition.csv")
            row = rows[0]

            self.assertEqual(result["changed_columns"], ["synonyms_ja", "xbrl_tag_candidates", "section_keywords", "notes"])
            self.assertTrue(Path(result["backup_path"]).exists())
            self.assertEqual(row["synonyms_ja"], "完成工事総利益;売上総利益")
            self.assertEqual(row["xbrl_tag_candidates"], "GrossProfitOnCompletedConstructionContracts;GrossProfit")
            self.assertEqual(row["section_keywords"], "完成工事;損益計算書")
            self.assertIn("会社別表記ゆれ", row["notes"])
            self.assertTrue((root / "config" / "field_definition.xlsx").exists())

    def test_update_field_definition_rejects_non_editable_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_field_definition(root)

            with self.assertRaises(ValueError):
                field_admin.update_field_definition(root, "construction_gross_profit_consolidated", {"field_id": "other"})

    def test_read_field_definitions_filters_by_search_and_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_field_definition(root)
            write_table(
                root / "config" / "field_definition.csv",
                read_table(root / "config" / "field_definition.csv")
                + [
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
                        "section_keywords": "従業員の状況",
                        "synonyms_ja": "平均年間給与",
                        "calculation_formula": "",
                        "validation_rule_ids": "",
                        "review_threshold": "0.90",
                        "notes": "",
                    }
                ],
            )

            result = field_admin.read_field_definitions(root, category="construction", search="完成工事総利益")

            self.assertEqual(result["total"], 1)
            self.assertEqual(result["rows"][0]["field_id"], "construction_gross_profit_consolidated")
            self.assertEqual(result["rows"][0]["category_label"], "完成工事")


def _write_field_definition(root: Path) -> None:
    write_table(
        root / "config" / "field_definition.csv",
        [
            {
                "field_id": "construction_gross_profit_consolidated",
                "field_name_ja": "完成工事総利益_連結",
                "category": "construction",
                "target_unit": "百万円",
                "data_scope_required": "consolidated",
                "period_type": "current_year",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "GrossProfitOnCompletedConstructionContracts",
                "context_filters": "CurrentYearDuration;ConsolidatedMember",
                "section_keywords": "完成工事",
                "synonyms_ja": "完成工事総利益",
                "calculation_formula": "",
                "validation_rule_ids": "",
                "review_threshold": "0.85",
                "notes": "",
            }
        ],
    )


if __name__ == "__main__":
    unittest.main()
