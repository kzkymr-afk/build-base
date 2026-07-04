import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_yaml
from yuho_auto_extract.validator import attach_validation_status, validate_records


RULES = {
    "rules": {
        "sum_building_orders": {
            "description": "建築受注高の内訳合計",
            "left": "building_orders_total",
            "right_sum": ["building_orders_private", "building_orders_government", "building_orders_overseas"],
            "tolerance_pct": 0.01,
            "tolerance_abs": 10,
        }
    }
}


class ValidatorTests(unittest.TestCase):
    def test_sum_rule_passes_inside_tolerance(self):
        rows = [
            {"company_year_id": "A_2024", "field_id": "building_orders_total", "value": 1000},
            {"company_year_id": "A_2024", "field_id": "building_orders_private", "value": 700},
            {"company_year_id": "A_2024", "field_id": "building_orders_government", "value": 200},
            {"company_year_id": "A_2024", "field_id": "building_orders_overseas", "value": 100},
        ]
        results = validate_records(rows, RULES)
        self.assertEqual(results[0]["status"], "pass")

    def test_sum_rule_failure_marks_cells_for_review(self):
        rows = [
            {"company_year_id": "A_2024", "field_id": "building_orders_total", "value": 1000},
            {"company_year_id": "A_2024", "field_id": "building_orders_private", "value": 600},
            {"company_year_id": "A_2024", "field_id": "building_orders_government", "value": 200},
            {"company_year_id": "A_2024", "field_id": "building_orders_overseas", "value": 100},
        ]
        results = validate_records(rows, RULES)
        self.assertEqual(results[0]["status"], "fail")
        attached = attach_validation_status(rows, results)
        self.assertTrue(all(row["review_required"] for row in attached))
        self.assertTrue(all(row["validation_status"] == "fail" for row in attached))

    def test_rd_expense_is_not_flagged_by_yoy_anomaly_config(self):
        root = Path(__file__).resolve().parents[1]
        rules = read_yaml(root / "config" / "validation_rules.yml")
        yoy_fields = rules["rules"]["yoy_anomaly"]["fields_apply_to"]
        self.assertNotIn("rd_expense", yoy_fields)


if __name__ == "__main__":
    unittest.main()
