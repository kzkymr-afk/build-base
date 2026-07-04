from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract import corroboration_policy
from yuho_auto_extract.corroboration import summarize_cells
from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.review_queue import build_review_queue
from yuho_auto_extract.exporter import build_source_audit
from yuho_auto_extract.services import semantics_store


def _entry(records):
    """corroboration_policy.resolve_cell が期待する by_cell entry 形式を組み立てる。"""
    corroboration_count = sum(1 for r in records if r.get("matched"))
    conflict_count = sum(1 for r in records if not r.get("matched") and not r.get("restatement_suspected"))
    return {
        "corroboration_count": corroboration_count,
        "conflict_count": conflict_count,
        "restatement_suspected_count": sum(1 for r in records if r.get("restatement_suspected")),
        "corroborations": records,
    }


def _xbrl_vs_local(matched, method_a="XBRL_CSV", method_b="LOCAL_RULE_TABLE"):
    return {
        "check_kind": "xbrl_vs_local",
        "check_ref": "cell_pair",
        "matched": matched,
        "restatement_suspected": False,
        "detail": {"extraction_method_a": method_a, "extraction_method_b": method_b},
    }


def _next_year_prior(matched, restatement_suspected=False, primary_value=None):
    # primary_value は xbrl_facts 由来で「円」単位。セル値(百万円)と対応させるには
    # cell_value*1e6 を渡す（例: cell_value=100百万円 -> primary_value=100_000_000）。
    return {
        "check_kind": "next_year_prior",
        "check_ref": "jppfs_cor:Tag",
        "matched": matched,
        "restatement_suspected": restatement_suspected,
        "primary_value": primary_value,
        "detail": {},
    }


def _identity_rule(matched, rule_id="sum_building_orders"):
    return {
        "check_kind": "identity_rule",
        "check_ref": rule_id,
        "matched": matched,
        "restatement_suspected": False,
        "detail": {"status": "pass" if matched else "fail"},
    }


def _factbook(matched):
    return {
        "check_kind": "factbook",
        "check_ref": "shimizu_segment_orders",
        "matched": matched,
        "restatement_suspected": False,
        "detail": {},
    }


class IndependentSourceBucketsTests(unittest.TestCase):
    def test_two_xbrl_methods_matching_is_a_single_bucket(self):
        records = [_xbrl_vs_local(True, method_a="XBRL_CSV", method_b="XBRL_SEGMENT_CONTEXT")]
        buckets = corroboration_policy.independent_source_buckets(records)
        self.assertEqual(buckets, {"xbrl"})

    def test_xbrl_and_local_table_matching_is_two_buckets(self):
        records = [_xbrl_vs_local(True, method_a="XBRL_CSV", method_b="LOCAL_RULE_TABLE")]
        buckets = corroboration_policy.independent_source_buckets(records)
        self.assertEqual(buckets, {"xbrl", "local_table"})

    def test_unmatched_records_do_not_contribute_buckets(self):
        records = [_xbrl_vs_local(False)]
        buckets = corroboration_policy.independent_source_buckets(records)
        self.assertEqual(buckets, set())

    def test_next_year_prior_matched_supporting_cell_value_is_cross_year_bucket(self):
        # 証拠値(円)がセル値(百万円)に対応する場合のみ cross_year を数える。
        records = [_next_year_prior(True, primary_value=100_000_000)]  # 100百万円相当
        buckets = corroboration_policy.independent_source_buckets(records, cell_value=100.0)
        self.assertEqual(buckets, {"cross_year"})

    def test_next_year_prior_evidence_not_matching_cell_value_is_not_counted(self):
        # 監査知見5対策: 別element由来で matched=True でも、証拠値がセル値と
        # 一致しなければ cross_year 独立源に数えない。
        records = [_next_year_prior(True, primary_value=18_192_000_000)]  # 18,192百万円
        buckets = corroboration_policy.independent_source_buckets(records, cell_value=19_277.0)
        self.assertEqual(buckets, set())

    def test_cross_year_without_cell_value_is_not_counted(self):
        # cell_value 不明時は安全側で cross_year を数えない。
        records = [_next_year_prior(True, primary_value=100_000_000)]
        buckets = corroboration_policy.independent_source_buckets(records)
        self.assertEqual(buckets, set())

    def test_identity_rule_matched_is_identity_bucket(self):
        records = [_identity_rule(True)]
        buckets = corroboration_policy.independent_source_buckets(records)
        self.assertEqual(buckets, {"identity"})

    def test_factbook_matched_is_external_bucket(self):
        records = [_factbook(True)]
        buckets = corroboration_policy.independent_source_buckets(records)
        self.assertEqual(buckets, {"external"})


class ResolveCellTests(unittest.TestCase):
    def test_two_xbrl_methods_only_does_not_auto_confirm(self):
        records = [_xbrl_vs_local(True, method_a="XBRL_CSV", method_b="XBRL_SEGMENT_CONTEXT")]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="operating_income_consolidated",
            extraction_method="XBRL_CSV",
            validation_status="not_applicable",
            has_value=True,
        )
        self.assertNotEqual(decision["resolution"], corroboration_policy.RESOLUTION_AUTO_CONFIRMED)
        self.assertEqual(decision["independent_bucket_count"], 1)

    def test_xbrl_plus_local_table_matching_auto_confirms(self):
        records = [_xbrl_vs_local(True, method_a="XBRL_CSV", method_b="LOCAL_RULE_TABLE")]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="operating_income_consolidated",
            extraction_method="XBRL_CSV",
            validation_status="not_applicable",
            has_value=True,
        )
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_AUTO_CONFIRMED)
        self.assertEqual(decision["independent_bucket_count"], 2)

    def test_identity_fail_only_is_needs_reconciliation_not_conflicted(self):
        records = [_identity_rule(False, rule_id="sum_building_orders")]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="building_orders_total",
            extraction_method="XBRL_CSV",
            validation_status="fail",
            has_value=True,
        )
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_NEEDS_RECONCILIATION)
        self.assertIn("identity_group_mismatch", decision["review_reason"])
        self.assertNotEqual(decision["resolution"], corroboration_policy.RESOLUTION_CONFLICTED)

    def test_next_year_mismatch_alone_is_needs_review_not_conflicted(self):
        records = [_next_year_prior(False)]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="operating_income_consolidated",
            extraction_method="XBRL_CSV",
            validation_status="not_applicable",
            has_value=True,
        )
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_NEEDS_REVIEW)
        self.assertNotEqual(decision["resolution"], corroboration_policy.RESOLUTION_CONFLICTED)

    def test_value_level_conflict_is_always_conflicted(self):
        records = [_xbrl_vs_local(False)]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="operating_income_consolidated",
            extraction_method="XBRL_CSV",
            validation_status="not_applicable",
            has_value=True,
        )
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_CONFLICTED)

    def test_validation_fail_never_auto_confirms_even_with_two_buckets(self):
        records = [_xbrl_vs_local(True, method_a="XBRL_CSV", method_b="LOCAL_RULE_TABLE")]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="operating_income_consolidated",
            extraction_method="XBRL_CSV",
            validation_status="fail",
            has_value=True,
        )
        self.assertNotEqual(decision["resolution"], corroboration_policy.RESOLUTION_AUTO_CONFIRMED)
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_NEEDS_REVIEW)

    def test_manual_obsidian_single_source_auto_confirms(self):
        # MANUAL_OBSIDIANはsingle_source_ok_methodsの既定値に含まれるため、
        # 独立バケット1でもauto_confirmedになる。
        records = [_xbrl_vs_local(True, method_a="MANUAL_OBSIDIAN", method_b="MANUAL_OBSIDIAN")]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="architecture_engineers_1st_class",
            extraction_method="MANUAL_OBSIDIAN",
            validation_status="not_applicable",
            has_value=True,
        )
        self.assertEqual(decision["independent_bucket_count"], 1)
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_AUTO_CONFIRMED)

    def test_cross_year_on_wrong_element_does_not_auto_confirm(self):
        # 実データのsga_expense回帰: cross_year(matched・但し別element値18192百万) +
        # identity(pass) の2レコードだが、cross_year証拠がセル値19277と一致しないため
        # 独立バケットはidentityのみ(1) -> auto_confirmedにならず single_source。
        records = [
            _next_year_prior(True, primary_value=18_192_000_000),
            _identity_rule(True, rule_id="expense_less_than_sga"),
        ]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="sga_expense",
            extraction_method="XBRL_CSV",
            validation_status="pass",
            has_value=True,
            cell_value=19_277.0,
        )
        self.assertNotEqual(decision["resolution"], corroboration_policy.RESOLUTION_AUTO_CONFIRMED)
        self.assertEqual(decision["independent_bucket_count"], 1)
        self.assertEqual(decision["buckets"], ["identity"])

    def test_cross_year_on_correct_element_still_auto_confirms(self):
        # 逆に証拠がセル値と一致すれば cross_year を数え、identityと合わせ2バケットで確定。
        records = [
            _next_year_prior(True, primary_value=19_277_000_000),
            _identity_rule(True, rule_id="expense_less_than_sga"),
        ]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="sga_expense",
            extraction_method="XBRL_CSV",
            validation_status="pass",
            has_value=True,
            cell_value=19_277.0,
        )
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_AUTO_CONFIRMED)
        self.assertEqual(sorted(decision["buckets"]), ["cross_year", "identity"])

    def test_no_value_short_circuits(self):
        entry = _entry([])
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="operating_income_consolidated",
            extraction_method="",
            validation_status=None,
            has_value=False,
        )
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_NO_VALUE)

    def test_zero_corroboration_zero_conflict_is_needs_review(self):
        entry = _entry([])
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="operating_income_consolidated",
            extraction_method="XBRL_CSV",
            validation_status="not_applicable",
            has_value=True,
        )
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_NEEDS_REVIEW)

    def test_cross_year_mismatch_with_two_same_year_buckets_falls_through_to_auto_confirm(self):
        # xbrl+local一致(2バケット) + next_year_prior不一致 -> 遡及修正疑いとして
        # conflictedにせず、通常判定(auto_confirmed)にフォールスルーする。
        records = [
            _xbrl_vs_local(True, method_a="XBRL_CSV", method_b="LOCAL_RULE_TABLE"),
            _next_year_prior(False),
        ]
        entry = _entry(records)
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="operating_income_consolidated",
            extraction_method="XBRL_CSV",
            validation_status="not_applicable",
            has_value=True,
        )
        self.assertEqual(decision["resolution"], corroboration_policy.RESOLUTION_AUTO_CONFIRMED)
        self.assertEqual(decision["review_reason"], "cross_year_divergence_likely_restatement")

    def test_critical_field_requires_two_even_if_policy_relaxed(self):
        records = [_xbrl_vs_local(True, method_a="XBRL_CSV", method_b="XBRL_SEGMENT_CONTEXT")]
        entry = _entry(records)
        policy = {"auto_confirm_min_independent": 1, "critical_fields_require_two": ["building_orders_total"]}
        decision = corroboration_policy.resolve_cell(
            entry=entry,
            field_id="building_orders_total",
            extraction_method="XBRL_CSV",
            validation_status="not_applicable",
            has_value=True,
            policy=policy,
        )
        self.assertNotEqual(decision["resolution"], corroboration_policy.RESOLUTION_AUTO_CONFIRMED)


class ReviewQueueDowngradeTests(unittest.TestCase):
    def _base_args(self):
        field_definitions = [
            {"field_id": "f1", "field_name_ja": "フィールド1", "review_threshold": 0.9},
        ]
        company_year_master = [{"company_year_id": "A_2024", "fiscal_year": 2024, "operating_company_id": "A"}]
        return field_definitions, company_year_master

    def test_auto_confirmed_resolution_demotes_confidence_below_threshold(self):
        field_definitions, company_year_master = self._base_args()
        extracted_rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "f1",
                "value_normalized": 100.0,
                "confidence": 0.5,
                "validation_status": "not_applicable",
            }
        ]
        cell_resolutions = {("A_2024", "f1"): {"resolution": "auto_confirmed"}}
        queue_without = build_review_queue(extracted_rows, field_definitions, company_year_master)
        queue_with = build_review_queue(
            extracted_rows, field_definitions, company_year_master, cell_resolutions=cell_resolutions
        )
        self.assertEqual(len(queue_without), 1)
        self.assertEqual(len(queue_with), 0)

    def test_conflicted_resolution_is_never_removed_from_queue(self):
        field_definitions, company_year_master = self._base_args()
        extracted_rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "f1",
                "value_normalized": 100.0,
                "confidence": 0.5,
                "validation_status": "not_applicable",
            }
        ]
        cell_resolutions = {("A_2024", "f1"): {"resolution": "conflicted"}}
        queue = build_review_queue(
            extracted_rows, field_definitions, company_year_master, cell_resolutions=cell_resolutions
        )
        self.assertEqual(len(queue), 1)
        self.assertIn("corroboration_conflict", queue[0]["review_reason"])

    def test_validation_fail_row_is_never_removed_even_if_auto_confirmed(self):
        field_definitions, company_year_master = self._base_args()
        extracted_rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "f1",
                "value_normalized": 100.0,
                "confidence": 1.0,
                "validation_status": "fail",
            }
        ]
        cell_resolutions = {("A_2024", "f1"): {"resolution": "auto_confirmed"}}
        queue = build_review_queue(
            extracted_rows, field_definitions, company_year_master, cell_resolutions=cell_resolutions
        )
        self.assertEqual(len(queue), 1)
        self.assertIn("validation_fail", queue[0]["review_reason"])

    def test_no_cell_resolutions_preserves_existing_behavior(self):
        field_definitions, company_year_master = self._base_args()
        extracted_rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "f1",
                "value_normalized": 100.0,
                "confidence": 0.5,
                "validation_status": "not_applicable",
            }
        ]
        queue = build_review_queue(extracted_rows, field_definitions, company_year_master)
        self.assertEqual(len(queue), 1)


class ExporterCorroborationColumnsTests(unittest.TestCase):
    def test_build_source_audit_includes_corroboration_columns(self):
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "f1",
                "value": 100.0,
                "corroboration_count": 2,
                "conflict_count": 0,
                "resolution": "auto_confirmed",
            }
        ]
        audit = build_source_audit(rows, [])
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["corroboration_count"], 2)
        self.assertEqual(audit[0]["conflict_count"], 0)
        self.assertEqual(audit[0]["resolution"], "auto_confirmed")

    def test_build_source_audit_defaults_missing_corroboration_columns_to_empty(self):
        rows = [{"company_year_id": "A_2024", "field_id": "f1", "value": 100.0}]
        audit = build_source_audit(rows, [])
        self.assertEqual(audit[0]["corroboration_count"], "")
        self.assertEqual(audit[0]["resolution"], "")


class SemanticsCorroborateE2ETests(unittest.TestCase):
    def test_run_corroboration_writes_semantics_db_not_edinet_db(self):
        from yuho_auto_extract.services import semantics_corroborate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "intermediate" / "normalized_validated_long.csv",
                [
                    {
                        "company_year_id": "A_2023",
                        "operating_company_id": "A",
                        "fiscal_year": "2023",
                        "field_id": "operating_income_consolidated",
                        "extraction_method": "XBRL_CSV",
                        "value_normalized": 1000.0,
                        "unit_normalized": "百万円",
                        "review_required": False,
                        "validation_status": "not_applicable",
                    },
                    {
                        "company_year_id": "A_2023",
                        "operating_company_id": "A",
                        "fiscal_year": "2023",
                        "field_id": "operating_income_consolidated",
                        "extraction_method": "LOCAL_RULE_TABLE",
                        "value_normalized": 1000.0,
                        "unit_normalized": "百万円",
                        "review_required": False,
                        "validation_status": "not_applicable",
                    },
                ],
            )
            write_table(root / "data" / "intermediate" / "validation_results.csv", [])
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "operating_income_consolidated",
                        "field_name_ja": "営業利益_連結",
                        "target_unit": "百万円",
                        "xbrl_tag_candidates": "OperatingIncome",
                        "category": "pl",
                        "period_type": "duration",
                        "preferred_method": "XBRL_CSV",
                        "data_scope_required": "連結",
                    }
                ],
            )
            write_table(
                root / "config" / "company_year_master.csv",
                [
                    {
                        "company_year_id": "A_2023",
                        "fiscal_year": 2023,
                        "operating_company_id": "A",
                        "transition_year_flag": 0,
                        "fiscal_year_end": "2023-03-31",
                        "reporting_entity_id": "A",
                        "parent_group_id_at_year_end": "A",
                        "current_parent_group_id": "A",
                        "data_scope_allowed": "連結",
                        "analysis_treatment": "normal",
                    }
                ],
            )
            write_table(
                root / "config" / "company_master.csv",
                [
                    {
                        "operating_company_id": "A",
                        "operating_company_name": "A社",
                        "edinet_code": "E00000",
                        "fiscal_year_end_month": 3,
                        "default_data_scope": "連結",
                    }
                ],
            )
            import yaml

            (root / "config").mkdir(parents=True, exist_ok=True)
            (root / "config" / "document_filter.yml").write_text(yaml.safe_dump({}), encoding="utf-8")
            (root / "config" / "extraction_sections.yml").write_text(yaml.safe_dump({}), encoding="utf-8")
            (root / "config" / "validation_rules.yml").write_text(
                yaml.safe_dump({"rules": {}, "corroboration": {"auto_confirm_min_independent": 2}}),
                encoding="utf-8",
            )
            (root / "config" / "model_config.yml").write_text(yaml.safe_dump({}), encoding="utf-8")

            result = semantics_corroborate.run_corroboration(root)

            semantics_db_path = root / "data" / "marts" / "semantics" / "semantics.db"
            edinet_db_path = root / "data" / "intermediate" / "edinet.db"
            self.assertTrue(semantics_db_path.exists())
            self.assertFalse(edinet_db_path.exists())

            conn = sqlite3.connect(str(semantics_db_path))
            try:
                row = conn.execute(
                    "select resolution from cell_resolutions where company_year_id=? and concept_id=?",
                    ("A_2023", "operating_income_consolidated"),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "auto_confirmed")
            finally:
                conn.close()

            # normalized_validated_long に列が付与されていること
            written_rows = read_table(root / "data" / "intermediate" / "normalized_validated_long.csv")
            statuses = {row["corroboration_status"] for row in written_rows}
            self.assertIn("auto_confirmed", statuses)


if __name__ == "__main__":
    unittest.main()
