import csv
import unittest
from pathlib import Path

import yaml

from yuho_auto_extract.analysis_builder import build_analysis_dataset
from yuho_auto_extract.exporter import build_source_audit, filter_exportable_rows


PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_FIELDS = {
    "orders_purpose_office",
    "orders_purpose_hotel",
    "orders_purpose_store",
    "orders_purpose_factory",
    "orders_purpose_warehouse",
    "orders_purpose_housing",
    "orders_purpose_education",
    "orders_purpose_medical",
    "orders_purpose_entertainment",
    "orders_purpose_other",
    "orders_special_contract",
    "orders_design_build",
    "orders_renewal",
}


class ScopeExclusionTests(unittest.TestCase):
    def test_excluded_fields_are_not_active_definitions(self):
        with (PROJECT_ROOT / "config" / "field_definition.csv").open(encoding="utf-8-sig", newline="") as handle:
            fields = {row["field_id"] for row in csv.DictReader(handle)}
        # P4b（field_definition増分エンリッチ）で新概念27件が追加され65→92行になった。
        # 除外フィールドが含まれないことが本テストの主旨であり、行数は増分エンリッチ分を許容する。
        self.assertEqual(len(fields), 92)
        self.assertFalse(EXCLUDED_FIELDS & fields)
        self.assertIn("building_orders_special_contract_ratio", fields)
        self.assertIn("building_orders_competitive_ratio", fields)

    def test_excluded_fields_are_not_targeted_by_sections_or_validation(self):
        sections = yaml.safe_load((PROJECT_ROOT / "config" / "extraction_sections.yml").read_text(encoding="utf-8"))
        self.assertNotIn("purpose_orders", sections)
        section_targets = {
            field_id
            for section in sections.values()
            for field_id in section.get("target_fields", [])
        }
        self.assertFalse(EXCLUDED_FIELDS & section_targets)
        self.assertIn("building_orders_special_contract_ratio", section_targets)

        rules = yaml.safe_load((PROJECT_ROOT / "config" / "validation_rules.yml").read_text(encoding="utf-8"))
        self.assertNotIn("purpose_orders_sum", rules["rules"])
        referenced_fields = set()
        for rule in rules["rules"].values():
            referenced_fields.update(rule.get("right_sum", []))
            referenced_fields.update(rule.get("fields", []))
            referenced_fields.update(rule.get("fields_apply_to", []))
            if rule.get("left"):
                referenced_fields.add(rule["left"])
            if rule.get("upper_bound"):
                referenced_fields.add(rule["upper_bound"])
        self.assertFalse(EXCLUDED_FIELDS & referenced_fields)

    def test_analysis_dataset_does_not_emit_purpose_metrics(self):
        rows = build_analysis_dataset(
            [
                {
                    "company_year_id": "A_2024",
                    "operating_company_id": "A",
                    "fiscal_year": 2024,
                    "building_orders_total": 100,
                    "building_orders_government": 25,
                }
            ]
        )
        self.assertNotIn("purpose_count", rows[0])
        self.assertNotIn("purpose_top1_share", rows[0])
        self.assertNotIn("purpose_top3_share", rows[0])
        self.assertNotIn("purpose_hhi", rows[0])

    def test_exportable_rows_and_source_audit_include_segment_metadata_columns(self):
        rows = filter_exportable_rows(
            [
                {
                    "company_year_id": "A_2024",
                    "field_id": "segment_profit_A_custom",
                    "value": 100,
                    "validation_status": "pass",
                    "review_required": False,
                }
            ]
        )
        self.assertIn("source_segment_label", rows[0])
        self.assertIn("normalized_segment_key", rows[0])
        audit = build_source_audit(
            rows,
            [{"field_id": "segment_profit_A_custom", "field_name_ja": "A社カスタムセグメント利益"}],
        )
        self.assertEqual(audit[0]["field_name_ja"], "A社カスタムセグメント利益")
        self.assertIn("source_segment_label", audit[0])
        self.assertIn("segment_taxonomy_status", audit[0])


if __name__ == "__main__":
    unittest.main()
