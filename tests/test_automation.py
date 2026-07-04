import tempfile
import unittest
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table, write_yaml
from yuho_auto_extract.services import automation, pipeline


class AutomationTests(unittest.TestCase):
    def test_annual_window_maps_june_2026_to_fiscal_2025(self):
        cfg = automation.load_automation_config(Path("."))

        status = automation.annual_window_status(cfg, date(2026, 6, 20))

        self.assertTrue(status["in_window"])
        self.assertEqual(status["target_fiscal_year"], 2025)
        self.assertEqual(status["window_start"], "2026-06-01")
        self.assertEqual(status["window_end"], "2026-08-15")

    def test_review_gate_blocks_active_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "data" / "review" / "review_queue.csv", [{"company_year_id": "A_2024", "field_id": "roe"}])
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024"}])
            write_table(root / "data" / "final" / "source_audit.csv", [{"company_year_id": "A_2024", "field_id": "roe"}])
            write_table(root / "data" / "final" / "field_coverage.csv", [{"field_id": "roe"}])

            gate = automation.review_gate_status(root)

            self.assertFalse(gate["ready"])
            self.assertEqual(gate["active_review_items"], 1)
            self.assertIn("active_review_items=1 exceeds 0", gate["blocking_reasons"])

    def test_review_gate_treats_not_applicable_as_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "data" / "review" / "review_queue.csv", [{"company_year_id": "A_2024", "field_id": "cost_labor"}])
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "cost_labor",
                        "review_decision": "not_applicable",
                        "applied_status": "not_applicable",
                    }
                ],
            )
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024"}])
            write_table(root / "data" / "final" / "source_audit.csv", [{"company_year_id": "A_2024", "field_id": "cost_labor"}])
            write_table(root / "data" / "final" / "field_coverage.csv", [{"field_id": "cost_labor"}])

            gate = automation.review_gate_status(root)

            self.assertEqual(gate["active_review_items"], 0)
            self.assertEqual(gate["saved_unapplied_reviews"], 0)

    def test_review_gate_requires_recent_algorithm_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "data" / "review" / "review_queue.csv", [])
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024"}])
            write_table(root / "data" / "final" / "source_audit.csv", [{"company_year_id": "A_2024", "field_id": "roe"}])
            write_table(root / "data" / "final" / "field_coverage.csv", [{"field_id": "roe"}])

            missing = automation.review_gate_status(root)

            self.assertFalse(missing["ready"])
            self.assertIn("algorithm_audit_missing", missing["blocking_reasons"])

            manifest_path = root / "data" / "algorithm_audit" / "manifest.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            stale_time = datetime.now(timezone.utc) - timedelta(days=30)
            manifest_path.write_text(json.dumps({"generated_at_utc": stale_time.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")
            stale = automation.review_gate_status(root)
            self.assertFalse(stale["ready"])
            self.assertTrue(any(reason.startswith("algorithm_audit_stale=") for reason in stale["blocking_reasons"]))

            fresh_time = datetime.now(timezone.utc)
            manifest_path.write_text(json.dumps({"generated_at_utc": fresh_time.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")
            fresh = automation.review_gate_status(root)
            self.assertTrue(fresh["ready"])
            self.assertTrue(fresh["algorithm_audit"]["exists"])
            self.assertFalse(fresh["algorithm_audit"]["stale"])

    def test_review_gate_requires_regression_pass_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_yaml(
                root / "config" / "automation.yml",
                {
                    "annual_refresh": {
                        "review_gate": {
                            "require_algorithm_audit": False,
                            "require_regression_pass": True,
                            "regression_max_age_days": 14,
                        }
                    }
                },
            )
            write_table(root / "data" / "review" / "review_queue.csv", [])
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024"}])
            write_table(root / "data" / "final" / "source_audit.csv", [{"company_year_id": "A_2024", "field_id": "roe"}])
            write_table(root / "data" / "final" / "field_coverage.csv", [{"field_id": "roe"}])

            missing = automation.review_gate_status(root)

            self.assertFalse(missing["ready"])
            self.assertIn("regression_missing", missing["blocking_reasons"])

            reports_dir = root / "data" / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            stale_time = datetime.now(timezone.utc) - timedelta(days=30)
            (reports_dir / "regression_summary.json").write_text(
                json.dumps({"generated_at_utc": stale_time.strftime("%Y-%m-%dT%H:%M:%SZ"), "mismatch_count": 0}),
                encoding="utf-8",
            )
            stale = automation.review_gate_status(root)
            self.assertFalse(stale["ready"])
            self.assertTrue(any(reason.startswith("regression_stale=") for reason in stale["blocking_reasons"]))

            fresh_time = datetime.now(timezone.utc)
            (reports_dir / "regression_summary.json").write_text(
                json.dumps({"generated_at_utc": fresh_time.strftime("%Y-%m-%dT%H:%M:%SZ"), "mismatch_count": 0}),
                encoding="utf-8",
            )
            fresh = automation.review_gate_status(root)
            self.assertTrue(fresh["ready"])
            self.assertTrue(fresh["regression"]["exists"])
            self.assertFalse(fresh["regression"]["stale"])

            (reports_dir / "regression_summary.json").write_text(
                json.dumps({"generated_at_utc": fresh_time.strftime("%Y-%m-%dT%H:%M:%SZ"), "mismatch_count": 3}),
                encoding="utf-8",
            )
            mismatched = automation.review_gate_status(root)
            self.assertFalse(mismatched["ready"])
            self.assertIn("regression_mismatch_count=3", mismatched["blocking_reasons"])

    def test_review_gate_ignores_regression_when_not_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "data" / "review" / "review_queue.csv", [])
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024"}])
            write_table(root / "data" / "final" / "source_audit.csv", [{"company_year_id": "A_2024", "field_id": "roe"}])
            write_table(root / "data" / "final" / "field_coverage.csv", [{"field_id": "roe"}])

            # DEFAULT_AUTOMATION_CONFIGはrequire_regression_pass=False。
            # regression_summary.jsonが存在しなくてもブロックされない。
            gate = automation.review_gate_status(root)

            self.assertNotIn("regression_missing", gate["blocking_reasons"])

    def test_roll_forward_company_years_copies_latest_company_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "company_master.csv",
                [
                    {
                        "operating_company_id": "A",
                        "operating_company_name": "A社",
                        "fiscal_year_end_month": "3",
                    }
                ],
            )
            write_table(
                root / "config" / "company_year_master.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "fiscal_year": "2024",
                        "fiscal_year_end": "2025-03-31",
                        "operating_company_id": "A",
                        "reporting_entity_id": "HOLDCO",
                        "parent_group_id_at_year_end": "HOLDCO",
                        "current_parent_group_id": "HOLDCO",
                        "ownership_status": "完全子会社",
                        "listing_status": "非上場",
                        "data_scope_allowed": "standalone,segment",
                        "transition_year_flag": "1",
                        "reorg_event_type": "HD化",
                        "event_date": "2024-10-01",
                        "analysis_treatment": "normal",
                        "notes": "latest rule",
                    }
                ],
            )

            result = automation.roll_forward_company_years(root, 2025)

            self.assertEqual(result["added_rows"], 1)
            rows = read_table(root / "config" / "company_year_master.csv")
            added = rows[-1]
            self.assertEqual(added["company_year_id"], "A_2025")
            self.assertEqual(added["fiscal_year_end"], "2026-03-31")
            self.assertEqual(added["reporting_entity_id"], "HOLDCO")
            self.assertEqual(added["transition_year_flag"], "0")
            self.assertEqual(added["reorg_event_type"], "")
            self.assertIn("auto_roll_forward from A_2024", added["notes"])

    def test_annual_refresh_dry_run_writes_plan_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_yaml(root / "config" / "automation.yml", automation.DEFAULT_AUTOMATION_CONFIG)
            write_table(
                root / "config" / "company_master.csv",
                [{"operating_company_id": "A", "operating_company_name": "A社", "fiscal_year_end_month": "3"}],
            )
            write_table(
                root / "config" / "company_year_master.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "fiscal_year": "2024",
                        "fiscal_year_end": "2025-03-31",
                        "operating_company_id": "A",
                        "reporting_entity_id": "A",
                        "parent_group_id_at_year_end": "A",
                        "current_parent_group_id": "A",
                        "data_scope_allowed": "standalone,consolidated,segment",
                        "transition_year_flag": "0",
                        "analysis_treatment": "normal",
                    }
                ],
            )
            write_table(root / "data" / "review" / "review_queue.csv", [])
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024"}])
            write_table(root / "data" / "final" / "source_audit.csv", [{"company_year_id": "A_2024"}])
            write_table(root / "data" / "final" / "field_coverage.csv", [{"field_id": "roe"}])

            code = pipeline.annual_refresh(root, fiscal_year=2025, dry_run=True)

            self.assertEqual(code, 0)
            self.assertTrue((root / "data" / "automation" / "annual_refresh_last.json").exists())
            rows = read_table(root / "config" / "company_year_master.csv")
            self.assertEqual(len(rows), 1)

    def test_annual_refresh_blocked_by_review_gate_does_not_roll_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_yaml(root / "config" / "automation.yml", automation.DEFAULT_AUTOMATION_CONFIG)
            write_table(
                root / "config" / "company_master.csv",
                [{"operating_company_id": "A", "operating_company_name": "A社", "fiscal_year_end_month": "3"}],
            )
            write_table(
                root / "config" / "company_year_master.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "fiscal_year": "2024",
                        "fiscal_year_end": "2025-03-31",
                        "operating_company_id": "A",
                        "reporting_entity_id": "A",
                        "parent_group_id_at_year_end": "A",
                        "current_parent_group_id": "A",
                        "data_scope_allowed": "standalone,consolidated,segment",
                        "transition_year_flag": "0",
                        "analysis_treatment": "normal",
                    }
                ],
            )
            write_table(root / "data" / "review" / "review_queue.csv", [{"company_year_id": "A_2024", "field_id": "roe"}])
            write_table(root / "data" / "final" / "final_master_wide.csv", [{"company_year_id": "A_2024"}])
            write_table(root / "data" / "final" / "source_audit.csv", [{"company_year_id": "A_2024"}])
            write_table(root / "data" / "final" / "field_coverage.csv", [{"field_id": "roe"}])

            code = pipeline.annual_refresh(root, fiscal_year=2025)

            self.assertEqual(code, 2)
            rows = read_table(root / "config" / "company_year_master.csv")
            self.assertEqual([row["company_year_id"] for row in rows], ["A_2024"])


if __name__ == "__main__":
    unittest.main()
