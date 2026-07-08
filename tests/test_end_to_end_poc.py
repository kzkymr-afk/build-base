import unittest

from yuho_auto_extract.exporter import apply_review_decisions, build_wide_values, filter_exportable_rows
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

    def test_corrected_review_without_extracted_row_is_exported(self):
        reviewed = [
            {
                "company_year_id": "A_2024",
                "field_id": "building_orders_total",
                "field_name_ja": "建築受注高_合計",
                "review_decision": "correct",
                "corrected_value": "1000",
                "unit_normalized": "百万円",
                "reviewer": "web_cell_workbench",
            }
        ]

        final = apply_review_decisions([], reviewed)
        exportable = filter_exportable_rows(final)
        wide = build_wide_values(
            exportable,
            [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024"}],
            [{"field_id": "building_orders_total", "field_name_ja": "建築受注高_合計"}],
        )

        self.assertEqual(len(exportable), 1)
        self.assertEqual(exportable[0]["value"], "1000")
        self.assertEqual(exportable[0]["review_status"], "corrected")
        self.assertEqual(wide[0]["building_orders_total"], "1000")

    def test_accept_review_without_extracted_row_uses_review_value(self):
        reviewed = [
            {
                "company_year_id": "A_2024",
                "field_id": "roe",
                "review_decision": "accept",
                "extracted_value": "8.2",
                "unit_normalized": "%",
            }
        ]

        final = apply_review_decisions([], reviewed)
        exportable = filter_exportable_rows(final)

        self.assertEqual(len(exportable), 1)
        self.assertEqual(exportable[0]["value"], "8.2")
        self.assertEqual(exportable[0]["review_status"], "approved")

    def test_accept_review_with_existing_extracted_row_uses_saved_review_value(self):
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "rd_expense",
                "value": "8",
                "value_normalized": "8",
                "validation_status": "fail",
                "review_required": True,
            }
        ]
        reviewed = [
            {
                "company_year_id": "A_2024",
                "field_id": "rd_expense",
                "review_decision": "accept",
                "corrected_value": "168",
                "extracted_value": "8",
                "unit_normalized": "百万円",
            }
        ]

        final = apply_review_decisions(rows, reviewed)
        exportable = filter_exportable_rows(final)

        self.assertEqual(len(exportable), 1)
        self.assertEqual(exportable[0]["value"], "168")
        self.assertEqual(exportable[0]["value_normalized"], "168")
        self.assertEqual(exportable[0]["review_status"], "approved")
        self.assertFalse(exportable[0]["review_required"])

    def test_accept_review_with_blank_extracted_row_is_exported_from_saved_value(self):
        rows = [
            {
                "company_year_id": "KAJIMA_2015",
                "field_id": "building_orders_total",
                "value": "",
                "value_normalized": "",
                "validation_status": "fail",
                "review_required": True,
            }
        ]
        reviewed = [
            {
                "company_year_id": "KAJIMA_2015",
                "field_id": "building_orders_total",
                "review_decision": "accept",
                "extracted_value": "902092",
                "unit_normalized": "百万円",
            }
        ]

        final = apply_review_decisions(rows, reviewed)
        exportable = filter_exportable_rows(final)

        self.assertEqual(len(exportable), 1)
        self.assertEqual(exportable[0]["value"], "902092")
        self.assertEqual(exportable[0]["review_status"], "approved")

    def test_accept_review_sets_duplicate_extracted_rows_to_saved_value(self):
        rows = [
            {"company_year_id": "A_2024", "field_id": "rd_expense", "value": "168", "validation_status": "pass"},
            {"company_year_id": "A_2024", "field_id": "rd_expense", "value": "8", "validation_status": "fail", "review_required": True},
        ]
        reviewed = [
            {
                "company_year_id": "A_2024",
                "field_id": "rd_expense",
                "review_decision": "accept",
                "corrected_value": "168",
                "extracted_value": "8",
            }
        ]

        final = apply_review_decisions(rows, reviewed)

        self.assertEqual({row["value"] for row in final}, {"168"})
        self.assertTrue(all(row["review_status"] == "approved" for row in final))

    def test_wide_values_prefers_field_preferred_method_when_multiple_sources_agree(self):
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "rd_expense",
                "value": 22207,
                "extraction_method": "XBRL_CSV",
                "review_status": "auto_accepted",
            },
            {
                "company_year_id": "A_2024",
                "field_id": "rd_expense",
                "value": 22200,
                "extraction_method": "LOCAL_RULE_TABLE",
                "review_status": "auto_accepted",
            },
        ]
        wide = build_wide_values(
            rows,
            [{"company_year_id": "A_2024"}],
            [{"field_id": "rd_expense", "preferred_method": "XBRL_CSV"}],
        )
        self.assertEqual(wide[0]["rd_expense"], 22207)


if __name__ == "__main__":
    unittest.main()
