from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import write_table
from yuho_auto_extract.services import state_consistency


class StateConsistencyAuditTests(unittest.TestCase):
    def test_matching_applied_review_passes_core_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_core_tables(root, applied_value="100", final_value="100")

            result = state_consistency.run_state_consistency_audit(
                root,
                include_preview=False,
                include_static=False,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["p0_p1_count"], 0)
            self.assertEqual(result["scoreboard"]["review_resolved_rows"], 1)

    def test_applied_review_value_mismatch_is_p1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_core_tables(root, applied_value="99", final_value="100")

            result = state_consistency.run_state_consistency_audit(
                root,
                include_preview=False,
                include_static=False,
            )

            ids = {finding["finding_id"] for finding in result["findings"]}
            self.assertIn("applied_review_value_mismatch", ids)
            self.assertEqual(result["status"], "needs_attention")
            self.assertEqual(result["p0_p1_count"], 1)

    def test_not_applicable_review_with_final_value_is_p1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_core_tables(root, decision="not_applicable", applied_status="not_applicable", applied_value="", final_value="100")

            result = state_consistency.run_state_consistency_audit(
                root,
                include_preview=False,
                include_static=False,
            )

            ids = {finding["finding_id"] for finding in result["findings"]}
            self.assertIn("not_applicable_review_present_in_final", ids)


def _write_core_tables(
    root: Path,
    *,
    decision: str = "correct",
    applied_status: str = "applied",
    applied_value: str = "100",
    final_value: str = "100",
) -> None:
    write_table(root / "config" / "field_definition.csv", [{"field_id": "sales", "field_name_ja": "売上高"}])
    write_table(
        root / "data" / "review" / "review_resolved.csv",
        [
            {
                "company_year_id": "A_2024",
                "company_name": "A",
                "fiscal_year": "2024",
                "field_id": "sales",
                "field_name_ja": "売上高",
                "extracted_value": "90",
                "corrected_value": "100" if decision == "correct" else "",
                "review_decision": decision,
                "reviewed_at": "2026-07-08T00:00:00Z",
                "applied_status": applied_status,
                "applied_value": applied_value,
                "applied_at": "2026-07-08T00:01:00Z",
            }
        ],
    )
    write_table(root / "data" / "review" / "review_queue.csv", [])
    write_table(
        root / "data" / "final" / "final_master_long.csv",
        [
            {
                "company_year_id": "A_2024",
                "operating_company_id": "A",
                "fiscal_year": "2024",
                "field_id": "sales",
                "value": final_value,
                "value_normalized": final_value,
                "unit_normalized": "百万円",
                "review_status": "corrected",
            }
        ],
    )
    write_table(
        root / "data" / "final" / "final_master_wide.csv",
        [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "period_type": "annual", "sales": final_value}],
    )
    write_table(
        root / "data" / "final" / "source_audit.csv",
        [{"company_year_id": "A_2024", "field_id": "sales", "field_name_ja": "売上高", "value": final_value}],
    )


if __name__ == "__main__":
    unittest.main()
