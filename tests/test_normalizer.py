import unittest

from yuho_auto_extract.normalizer import convert_unit, normalize_extraction, normalize_numeric, normalize_unit


class NormalizerTests(unittest.TestCase):
    def test_normalize_numeric_handles_japanese_negative_marks(self):
        self.assertEqual(normalize_numeric("1,234"), 1234.0)
        self.assertEqual(normalize_numeric("△1,234"), -1234.0)
        self.assertEqual(normalize_numeric("▲1,234"), -1234.0)
        self.assertEqual(normalize_numeric("(1,234)"), -1234.0)
        self.assertIsNone(normalize_numeric("－"))

    def test_convert_amount_units_to_million_yen(self):
        self.assertEqual(convert_unit(1_000_000, "円", "百万円"), 1)
        self.assertEqual(convert_unit(1_000, "千円", "百万円"), 1)
        self.assertEqual(convert_unit(12, "億円", "百万円"), 1200)
        self.assertEqual(convert_unit(8_885, "千円", "円"), 8_885_000)
        self.assertEqual(normalize_unit("単位：百万円"), "百万円")

    def test_age_unit_keeps_age_distinct_from_tenure_years(self):
        self.assertEqual(normalize_unit("平均年齢（歳）"), "歳")
        self.assertEqual(normalize_unit("平均年齢（才）"), "歳")
        self.assertEqual(convert_unit(45.5, "歳", "歳"), 45.5)
        self.assertEqual(convert_unit(45.5, "年", "歳"), 45.5)

    def test_average_age_normalizes_without_unit_conversion_review(self):
        row = {"field_id": "average_age", "value": "45.5", "unit_raw": "歳", "data_scope": "standalone"}
        field = {"field_id": "average_age", "target_unit": "歳", "data_scope_required": "standalone"}
        normalized = normalize_extraction(row, field)
        self.assertEqual(normalized["value"], 45.5)
        self.assertEqual(normalized["unit_normalized"], "歳")
        self.assertFalse(normalized.get("review_required"))
        self.assertNotIn("unit_conversion_failed", normalized.get("review_reason", ""))

    def test_unit_unknown_forces_review(self):
        row = {"field_id": "building_orders_total", "value": "1,000", "unit_raw": "不明", "data_scope": "standalone"}
        field = {"field_id": "building_orders_total", "target_unit": "百万円", "data_scope_required": "standalone"}
        normalized = normalize_extraction(row, field)
        self.assertTrue(normalized["review_required"])
        self.assertIn("unit_conversion_failed", normalized["review_reason"])


if __name__ == "__main__":
    unittest.main()
