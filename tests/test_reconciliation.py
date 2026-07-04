from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.services import reconciliation


class ReconciliationTests(unittest.TestCase):
    def test_read_groups_collects_identity_group_mismatch_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "building_orders_total",
                        "field_name_ja": "建築受注",
                        "extracted_value": "100",
                        "review_reason": "identity_group_mismatch:sum_building_orders",
                    },
                    {
                        "company_year_id": "A_2024",
                        "field_id": "building_orders_private",
                        "field_name_ja": "民間",
                        "extracted_value": "60",
                        "review_reason": "identity_group_mismatch:sum_building_orders;validation_fail",
                    },
                    {
                        "company_year_id": "B_2024",
                        "field_id": "roe",
                        "review_reason": "confidence_below_threshold",
                    },
                ],
            )

            result = reconciliation.read_reconciliation_groups(root)

            self.assertEqual(result["total"], 1)
            group = result["groups"][0]
            self.assertEqual(group["group_id"], "identity_group_mismatch:sum_building_orders")
            self.assertEqual(group["item_count"], 2)
            self.assertEqual(group["company_year_count"], 1)
            self.assertEqual(group["field_count"], 2)

    def test_apply_group_uses_resolved_review_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_queue.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "building_orders_total",
                        "extracted_value": "100",
                        "review_reason": "identity_group_mismatch:sum_building_orders",
                    },
                    {
                        "company_year_id": "A_2024",
                        "field_id": "building_orders_private",
                        "extracted_value": "60",
                        "review_reason": "identity_group_mismatch:sum_building_orders",
                    },
                ],
            )

            result = reconciliation.apply_reconciliation_group(
                root,
                "identity_group_mismatch:sum_building_orders",
                decision="accept",
                reviewer_note="group checked",
                reviewer="tester",
            )

            self.assertEqual(result["applied_items"], 2)
            resolved = read_table(root / "data" / "review" / "review_resolved.csv")
            self.assertEqual(len(resolved), 2)
            self.assertTrue(all(row["review_decision"] == "accept" for row in resolved))
            self.assertTrue(all(row["reviewer_note"] == "group checked" for row in resolved))


if __name__ == "__main__":
    unittest.main()
