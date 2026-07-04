from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table, write_yaml
from yuho_auto_extract.services import golden, semantics_store


# ---------------------------------------------------------------------------
# 純関数（_diff / _values_match）のテスト — DB/ファイルI/Oなし
# ---------------------------------------------------------------------------

class GoldenDiffPureFunctionTests(unittest.TestCase):
    def test_diff_reports_zero_mismatch_when_actual_matches_golden(self):
        golden_map = {("A_2024", "advertising_expense"): {"value": 158.0, "origin": "human_correct"}}
        actual = {("A_2024", "advertising_expense"): 158.0}

        diff_rows, summary = golden._diff(golden_map, set(), actual)

        self.assertEqual(diff_rows, [])
        self.assertEqual(summary["mismatch_count"], 0)
        self.assertTrue(summary["pass"])

    def test_diff_detects_value_mismatch(self):
        # 機械起源(corroborated_2plus)はゲート対象。値不一致でpass=False。
        golden_map = {("A_2024", "advertising_expense"): {"value": 158.0, "origin": "corroborated_2plus"}}
        actual = {("A_2024", "advertising_expense"): 200.0}

        diff_rows, summary = golden._diff(golden_map, set(), actual)

        self.assertEqual(summary["value_mismatch_count"], 1)
        self.assertEqual(summary["mismatch_count"], 1)
        self.assertFalse(summary["pass"])
        self.assertEqual(diff_rows[0]["kind"], "value_mismatch")

    def test_diff_detects_missing_in_actual(self):
        golden_map = {("A_2024", "advertising_expense"): {"value": 158.0, "origin": "corroborated_2plus"}}

        diff_rows, summary = golden._diff(golden_map, set(), {})

        self.assertEqual(summary["missing_in_actual_count"], 1)
        self.assertFalse(summary["pass"])
        self.assertEqual(diff_rows[0]["kind"], "missing_in_actual")

    def test_diff_human_origin_mismatch_is_informational_not_gating(self):
        # human_correct/human_accept はレビュー非適用のシャドウ再導出で再現不能。
        # 非再現はinformational扱いでゲート(pass)に影響しない。
        golden_map = {
            ("A_2024", "rd_expense"): {"value": 66943.0, "origin": "human_correct"},
            ("B_2024", "advertising_expense"): {"value": 158.0, "origin": "human_accept"},
        }
        actual = {("A_2024", "rd_expense"): 66.0}  # 是正前の生値 / Bは欠損

        diff_rows, summary = golden._diff(golden_map, set(), actual)

        self.assertEqual(summary["human_locked_unreproduced_count"], 2)
        self.assertEqual(summary["mismatch_count"], 0)
        self.assertTrue(summary["pass"])
        self.assertTrue(all(r["kind"] == "human_locked_unreproduced" for r in diff_rows))

    def test_diff_detects_negative_golden_violation(self):
        negative = {("A_2024", "rd_expense")}
        actual = {("A_2024", "rd_expense"): 999.0}

        diff_rows, summary = golden._diff({}, negative, actual)

        self.assertEqual(summary["negative_golden_violations"], 1)
        self.assertFalse(summary["pass"])
        self.assertEqual(diff_rows[0]["kind"], "negative_golden_violation")

    def test_diff_negative_golden_with_no_value_is_not_a_violation(self):
        negative = {("A_2024", "rd_expense")}

        diff_rows, summary = golden._diff({}, negative, {})

        self.assertEqual(summary["negative_golden_violations"], 0)
        self.assertTrue(summary["pass"])
        self.assertEqual(diff_rows, [])

    def test_values_match_allows_small_tolerance(self):
        self.assertTrue(golden._values_match(1000.0, 1000.4))
        self.assertFalse(golden._values_match(1000.0, 1005.0))

    def test_values_match_allows_relative_tolerance_for_large_values(self):
        # 0.1%許容: 1,000,000 の場合は ±1,000 まで一致とみなす
        self.assertTrue(golden._values_match(1_000_000.0, 1_000_500.0))
        self.assertFalse(golden._values_match(1_000_000.0, 1_010_000.0))


# ---------------------------------------------------------------------------
# freeze_golden: 3源からgolden集合を凍結する
# ---------------------------------------------------------------------------

class FreezeGoldenTests(unittest.TestCase):
    def test_freeze_golden_collects_from_review_resolved_human_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "rd_expense",
                        "review_decision": "correct",
                        "corrected_value": "2177.0",
                        "applied_status": "applied",
                        "applied_value": "2177.0",
                    }
                ],
            )

            result = golden.freeze_golden(root)

            self.assertEqual(result["golden_cell_count"], 1)
            self.assertEqual(result["by_origin"].get("human_correct"), 1)

            conn = semantics_store.connect(root)
            try:
                fetched = semantics_store.fetch_golden_values(conn)
            finally:
                conn.close()
            row = fetched[("A_2024", "rd_expense")]
            self.assertEqual(row["value"], 2177.0)
            self.assertEqual(row["origin"], "human_correct")
            self.assertEqual(row["locked"], 1)

    def test_freeze_golden_accept_uses_applied_value_not_extracted_value(self):
        """P1で発覚したrd_expense億円/百万円誤読バグの是正跡を再現。
        review_decision=accept でも corrected_value(=applied_value) を信じ、
        extracted_valueを信じてはならない（仕様書1.1節）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "ANDO_HAZAMA_2015",
                        "field_id": "rd_expense",
                        "extracted_value": "6.0",
                        "review_decision": "accept",
                        "corrected_value": "2177.0",
                        "applied_status": "applied",
                        "applied_value": "2177.0",
                    }
                ],
            )

            golden.freeze_golden(root)

            conn = semantics_store.connect(root)
            try:
                fetched = semantics_store.fetch_golden_values(conn)
            finally:
                conn.close()
            row = fetched[("ANDO_HAZAMA_2015", "rd_expense")]
            self.assertEqual(row["value"], 2177.0)
            self.assertNotEqual(row["value"], 6.0)
            self.assertEqual(row["origin"], "human_accept")

    def test_freeze_golden_not_applicable_becomes_negative_golden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "segment_overseas_sales",
                        "review_decision": "not_applicable",
                        "corrected_value": "",
                        "applied_status": "not_applicable",
                        "applied_value": "",
                    }
                ],
            )

            result = golden.freeze_golden(root)

            self.assertEqual(result["golden_cell_count"], 0)
            self.assertEqual(result["negative_golden_count"], 1)

            conn = semantics_store.connect(root)
            try:
                fetched = semantics_store.fetch_golden_negative(conn)
            finally:
                conn.close()
            self.assertIn(("A_2024", "segment_overseas_sales"), fetched)

    def test_freeze_golden_collects_auto_confirmed_from_cell_resolutions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_cell_resolutions(
                    conn,
                    [
                        {
                            "company_year_id": "B_2023",
                            "concept_id": "operating_income_consolidated",
                            "value": 5000.0,
                            "resolution": "auto_confirmed",
                        },
                        {
                            "company_year_id": "B_2023",
                            "concept_id": "net_sales",
                            "value": 90000.0,
                            "resolution": "conflicted",
                        },
                    ],
                    run_id="run1",
                )
            finally:
                conn.close()

            result = golden.freeze_golden(root)

            self.assertEqual(result["golden_cell_count"], 1)
            self.assertEqual(result["by_origin"].get("corroborated_2plus"), 1)

    def test_freeze_golden_collects_manual_obsidian_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "intermediate" / "normalized_validated_long.parquet",
                [
                    {
                        "company_year_id": "C_2022",
                        "field_id": "employee_count",
                        "value_normalized": 1200.0,
                        "extraction_method": "MANUAL_OBSIDIAN",
                    },
                    {
                        "company_year_id": "C_2022",
                        "field_id": "net_sales",
                        "value_normalized": 50000.0,
                        "extraction_method": "XBRL_CSV",
                    },
                ],
            )

            result = golden.freeze_golden(root)

            self.assertEqual(result["golden_cell_count"], 1)
            self.assertEqual(result["by_origin"].get("manual_master"), 1)

    def test_freeze_golden_human_takes_priority_over_auto_confirmed(self):
        """同一セルが auto_confirmed かつ human review 済みの場合、human優先(仕様書1.5節)。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_cell_resolutions(
                    conn,
                    [
                        {
                            "company_year_id": "A_2024",
                            "concept_id": "rd_expense",
                            "value": 999.0,
                            "resolution": "auto_confirmed",
                        }
                    ],
                    run_id="run1",
                )
            finally:
                conn.close()
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "rd_expense",
                        "review_decision": "correct",
                        "corrected_value": "2177.0",
                        "applied_status": "applied",
                        "applied_value": "2177.0",
                    }
                ],
            )

            golden.freeze_golden(root)

            conn = semantics_store.connect(root)
            try:
                fetched = semantics_store.fetch_golden_values(conn)
            finally:
                conn.close()
            row = fetched[("A_2024", "rd_expense")]
            self.assertEqual(row["value"], 2177.0)
            self.assertEqual(row["origin"], "human_correct")

    def test_freeze_golden_writes_csv_mirrors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "rd_expense",
                        "review_decision": "correct",
                        "corrected_value": "2177.0",
                        "applied_status": "applied",
                        "applied_value": "2177.0",
                    }
                ],
            )

            golden.freeze_golden(root)

            self.assertTrue((root / "data" / "marts" / "semantics" / "golden_values.csv").exists())


# ---------------------------------------------------------------------------
# run_regression: 実パイプラインをshadow_root上で再実行してdiffする
# ---------------------------------------------------------------------------

def _seed_minimal_project(root: Path) -> None:
    write_table(
        root / "config" / "company_master.csv",
        [
            {
                "operating_company_id": "A",
                "operating_company_name": "A社",
                "edinet_code": "E00001",
                "fiscal_year_end_month": "3",
                "default_data_scope": "standalone",
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
                "reporting_entity_id": "A",
                "parent_group_id_at_year_end": "A",
                "current_parent_group_id": "A",
                "data_scope_allowed": "standalone",
                "transition_year_flag": "0",
                "analysis_treatment": "normal",
            }
        ],
    )
    write_table(
        root / "config" / "field_definition.csv",
        [
            {
                "field_id": "advertising_expense",
                "field_name_ja": "広告宣伝費",
                "category": "cost",
                "target_unit": "百万円",
                "data_scope_required": "standalone",
                "period_type": "duration",
                "preferred_method": "XBRL_CSV",
            }
        ],
    )
    write_yaml(root / "config" / "document_filter.yml", {})
    write_yaml(root / "config" / "extraction_sections.yml", {})
    write_yaml(root / "config" / "validation_rules.yml", {"rules": {}})
    write_yaml(root / "config" / "model_config.yml", {})

    write_table(
        root / "data" / "intermediate" / "xbrl_extracted_long.csv",
        [
            {
                "run_id": "test",
                "company_year_id": "A_2024",
                "operating_company_id": "A",
                "fiscal_year": "2024",
                "source_doc_id": "S1",
                "source_file": "edinet.db:xbrl_facts",
                "source_heading": "jppfs_cor:AdvertisingExpensesSGA",
                "source_quote": "広告宣伝費: 158000000",
                "field_id": "advertising_expense",
                "value_raw": "158000000",
                "unit_raw": "円",
                "context_ref": "CurrentYearDuration",
                "xbrl_element": "jppfs_cor:AdvertisingExpensesSGA",
                "data_scope": "standalone",
                "extraction_method": "XBRL_CSV",
                "confidence": "0.95",
                "review_required": "False",
                "review_reason": "",
                "candidate_count": "1",
            }
        ],
    )

    write_table(
        root / "data" / "review" / "review_resolved.csv",
        [
            {
                "company_year_id": "A_2024",
                "field_id": "advertising_expense",
                "review_decision": "correct",
                "corrected_value": "158.0",
                "applied_status": "applied",
                "applied_value": "158.0",
            }
        ],
    )


def _remove_field_definition_row(root: Path, field_id: str) -> None:
    """synonym破壊の代替: field_definition.csv から対象field_idの行を削除する。
    normalizeの field_map が field_id を解決できなくなり、
    review_required=True(field_definition_missing) が付与されて
    filter_exportable_rows で弾かれる（missing_in_actualとして検出される）。
    field_definition.csvを完全に空にすると load_pipeline_config自体が
    例外を投げるため、ダミーの別fieldを1行残す。
    """
    write_table(
        root / "config" / "field_definition.csv",
        [
            {
                "field_id": "dummy_other_field",
                "field_name_ja": "ダミー",
                "category": "cost",
                "target_unit": "百万円",
                "data_scope_required": "standalone",
                "period_type": "duration",
                "preferred_method": "XBRL_CSV",
            }
        ],
    )


class RunRegressionTests(unittest.TestCase):
    def test_run_regression_passes_when_nothing_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_minimal_project(root)

            golden.freeze_golden(root)
            summary = golden.run_regression(root, mode="light")

            self.assertEqual(summary["mismatch_count"], 0)
            self.assertTrue(summary["pass"])
            self.assertEqual(summary["golden_cell_count"], 1)

    def test_run_regression_does_not_touch_real_final_or_intermediate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_minimal_project(root)
            golden.freeze_golden(root)

            # 本物のdata/final・data/intermediateにマーカーファイルを置いておき、
            # regression実行後も変化していないことを確認する。
            marker_final = root / "data" / "final" / "final_master_long.csv"
            marker_final.parent.mkdir(parents=True, exist_ok=True)
            marker_final.write_text("SENTINEL_DO_NOT_TOUCH", encoding="utf-8")
            marker_intermediate = root / "data" / "intermediate" / "normalized_long.parquet"
            marker_intermediate.write_text("SENTINEL_DO_NOT_TOUCH", encoding="utf-8")

            golden.run_regression(root, mode="light")

            self.assertEqual(marker_final.read_text(encoding="utf-8"), "SENTINEL_DO_NOT_TOUCH")
            self.assertEqual(marker_intermediate.read_text(encoding="utf-8"), "SENTINEL_DO_NOT_TOUCH")

    def test_run_regression_writes_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_minimal_project(root)
            golden.freeze_golden(root)

            golden.run_regression(root, mode="light")

            self.assertTrue((root / "data" / "reports" / "regression_summary.json").exists())
            self.assertTrue((root / "data" / "reports" / "regression_diff.csv").exists())

    def test_regression_detects_broken_field_definition(self):
        """意図的にfield_definitionから対象fieldを取り除くと、
        regressionがmissing_in_actualとして検出することを保証する
        （synonym破壊検知テストの骨子・仕様書4.3節）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_minimal_project(root)
            # advertising_expense を機械起源(corroborated_2plus)goldenにする。
            # human_correct はレビュー非適用のシャドウ再導出では再現不能=非ゲートのため、
            # 設定破壊の検出は「機械的に再導出されるべきgolden」に対して検証する必要がある。
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "advertising_expense",
                        "review_decision": "correct",
                        "corrected_value": "158.0",
                        "applied_status": "pending",  # 未適用→human goldenにしない
                        "applied_value": "",
                    }
                ],
            )
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_cell_resolutions(
                    conn,
                    [
                        {
                            "company_year_id": "A_2024",
                            "concept_id": "advertising_expense",
                            "value": 158.0,
                            "resolution": "auto_confirmed",
                        }
                    ],
                    run_id="seed",
                )
            finally:
                conn.close()

            golden.freeze_golden(root)
            baseline = golden.run_regression(root, mode="light")
            self.assertEqual(baseline["mismatch_count"], 0)

            # 本物のconfig/field_definition.csvを壊す
            _remove_field_definition_row(root, field_id="advertising_expense")

            regressed = golden.run_regression(root, mode="light")

            self.assertGreater(regressed["mismatch_count"], 0)
            self.assertGreater(regressed["missing_in_actual_count"], 0)
            self.assertFalse(regressed["pass"])

    def test_run_regression_detects_negative_golden_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_minimal_project(root)
            # advertising_expense を not_applicable として凍結（ネガティブゴールデン）
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "advertising_expense",
                        "review_decision": "not_applicable",
                        "corrected_value": "",
                        "applied_status": "not_applicable",
                        "applied_value": "",
                    }
                ],
            )
            golden.freeze_golden(root)

            summary = golden.run_regression(root, mode="light")

            # 抽出結果は値を出す(158.0)ので、ネガティブゴールデン違反として検出される
            self.assertEqual(summary["negative_golden_violations"], 1)
            self.assertFalse(summary["pass"])


if __name__ == "__main__":
    unittest.main()
