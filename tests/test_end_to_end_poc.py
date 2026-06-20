import unittest

from yuho_auto_extract.exporter import apply_review_decisions, filter_exportable_rows
from yuho_auto_extract.normalizer import normalize_extraction
from yuho_auto_extract.review_queue import build_review_queue
from yuho_auto_extract.validator import attach_validation_status, validate_records


class EndToEndPocContractTests(unittest.TestCase):
    def test_failed_validation_enters_review_queue(self):
        fields = [
            {"field_id": "building_orders_total", "field_name_ja": "建築受注高_合計", "target_unit": "百万円", "data_scope_required": "standalone", "review_threshold": 0.85},
            {"field_id": "building_orders_private", "field_name_ja": "建築受注高_民間", "target_unit": "百万円", "data_scope_required": "standalone", "review_threshold": 0.85},
            {"field_id": "building_orders_government", "field_name_ja": "建築受注高_官庁", "target_unit": "百万円", "data_scope_required": "standalone", "review_threshold": 0.85},
            {"field_id": "building_orders_overseas", "field_name_ja": "建築受注高_海外", "target_unit": "百万円", "data_scope_required": "standalone", "review_threshold": 0.85},
        ]
        raw = [
            {"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": 2024, "field_id": "building_orders_total", "value": "1000", "unit_raw": "百万円", "data_scope": "standalone", "confidence": 0.95},
            {"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": 2024, "field_id": "building_orders_private", "value": "600", "unit_raw": "百万円", "data_scope": "standalone", "confidence": 0.95},
            {"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": 2024, "field_id": "building_orders_government", "value": "200", "unit_raw": "百万円", "data_scope": "standalone", "confidence": 0.95},
            {"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": 2024, "field_id": "building_orders_overseas", "value": "100", "unit_raw": "百万円", "data_scope": "standalone", "confidence": 0.95},
        ]
        field_map = {row["field_id"]: row for row in fields}
        normalized = [normalize_extraction(row, field_map[row["field_id"]]) for row in raw]
        rules = {"rules": {"sum_building_orders": {"left": "building_orders_total", "right_sum": ["building_orders_private", "building_orders_government", "building_orders_overseas"], "tolerance_pct": 0.01, "tolerance_abs": 10}}}
        validation = validate_records(normalized, rules)
        attached = attach_validation_status(normalized, validation)
        queue = build_review_queue(attached, fields, [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": 2024}])
        self.assertEqual(len(queue), 4)
        self.assertTrue(all("validation_fail" in item["review_reason"] for item in queue))

    def test_string_review_required_is_not_exported_without_review(self):
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "building_orders_total",
                "value": 100,
                "review_required": "True",
                "validation_status": "pass",
            }
        ]
        final = apply_review_decisions(rows, [])
        self.assertEqual(final[0]["review_status"], "unreviewed")
        self.assertEqual(filter_exportable_rows(final), [])


if __name__ == "__main__":
    unittest.main()
