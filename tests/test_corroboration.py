from __future__ import annotations

import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.corroboration import (
    corroborate_factbook,
    corroborate_next_year_prior,
    corroborate_same_cell,
    corroborate_validation_rules,
    summarize_cells,
)
from yuho_auto_extract.io_utils import write_table
from yuho_auto_extract.services.corroboration_report import build_corroboration_report


class CorroborateSameCellTests(unittest.TestCase):
    def test_matching_values_across_extraction_methods_corroborate(self):
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "operating_income_consolidated",
                "extraction_method": "XBRL_CSV",
                "value_normalized": 1000.0,
                "unit_normalized": "百万円",
            },
            {
                "company_year_id": "A_2024",
                "field_id": "operating_income_consolidated",
                "extraction_method": "LOCAL_RULE_TABLE",
                "value_normalized": 1000.4,
                "unit_normalized": "百万円",
            },
        ]
        records = corroborate_same_cell(rows)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["matched"])
        self.assertEqual(records[0]["check_kind"], "xbrl_vs_local")

    def test_conflicting_values_are_not_matched(self):
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "operating_income_consolidated",
                "extraction_method": "XBRL_CSV",
                "value_normalized": 1000.0,
                "unit_normalized": "百万円",
            },
            {
                "company_year_id": "A_2024",
                "field_id": "operating_income_consolidated",
                "extraction_method": "LOCAL_RULE_TABLE",
                "value_normalized": 2000.0,
                "unit_normalized": "百万円",
            },
        ]
        records = corroborate_same_cell(rows)
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["matched"])

    def test_same_extraction_method_pair_is_skipped(self):
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "operating_income_consolidated",
                "extraction_method": "XBRL_CSV",
                "value_normalized": 1000.0,
                "unit_normalized": "百万円",
            },
            {
                "company_year_id": "A_2024",
                "field_id": "operating_income_consolidated",
                "extraction_method": "XBRL_CSV",
                "value_normalized": 1000.0,
                "unit_normalized": "百万円",
            },
        ]
        records = corroborate_same_cell(rows)
        self.assertEqual(len(records), 0)

    def test_mismatched_units_are_skipped_not_conflicted(self):
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "average_salary",
                "extraction_method": "XBRL_CSV",
                "value_normalized": 5_000_000.0,
                "unit_normalized": "円",
            },
            {
                "company_year_id": "A_2024",
                "field_id": "average_salary",
                "extraction_method": "LOCAL_RULE_TABLE",
                "value_normalized": 5.0,
                "unit_normalized": "百万円",
            },
        ]
        records = corroborate_same_cell(rows)
        self.assertEqual(len(records), 0)

    def test_null_values_are_ignored(self):
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "operating_income_consolidated",
                "extraction_method": "XBRL_CSV",
                "value_normalized": None,
                "unit_normalized": "百万円",
            },
            {
                "company_year_id": "A_2024",
                "field_id": "operating_income_consolidated",
                "extraction_method": "LOCAL_RULE_TABLE",
                "value_normalized": 1000.0,
                "unit_normalized": "百万円",
            },
        ]
        records = corroborate_same_cell(rows)
        self.assertEqual(len(records), 0)

    def test_nan_values_are_ignored_like_null(self):
        # pandas経由(parquet読み込み)では欠損値がfloat('nan')になる。
        # `value in (None, "")` の等価判定だけでは漏れる回帰テスト。
        rows = [
            {
                "company_year_id": "A_2024",
                "field_id": "average_age",
                "extraction_method": "XBRL_CSV",
                "value_normalized": math.nan,
                "unit_normalized": "歳",
            },
            {
                "company_year_id": "A_2024",
                "field_id": "average_age",
                "extraction_method": "LOCAL_RULE_TABLE",
                "value_normalized": 45.5,
                "unit_normalized": "歳",
            },
        ]
        records = corroborate_same_cell(rows)
        self.assertEqual(len(records), 0)


class CorroborateNextYearPriorTests(unittest.TestCase):
    def test_matching_current_and_prior_values_corroborate(self):
        current_facts = [
            {
                "company_year_id": "A_2023",
                "operating_company_id": "A",
                "fiscal_year": 2023,
                "element_id": "jppfs_cor:OperatingIncome",
                "context_id": "CurrentYearDuration",
                "consolidation_scope": "連結",
                "period_or_instant": "期間",
                "value": "1000000000",
            }
        ]
        prior_facts_by_key = {
            ("A_2024", "jppfs_cor:OperatingIncome"): [
                {
                    "context_id": "Prior1YearDuration",
                    "consolidation_scope": "連結",
                    "period_or_instant": "期間",
                    "value": "1000000000",
                }
            ]
        }
        next_lookup = {("A", 2024): "A_2024"}
        records = corroborate_next_year_prior(
            current_facts=current_facts,
            prior_facts_by_key=prior_facts_by_key,
            next_company_year_lookup=next_lookup,
            transition_flags={},
            valid_company_year_ids={"A_2023", "A_2024"},
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["matched"])
        self.assertEqual(records[0]["check_kind"], "next_year_prior")
        self.assertFalse(records[0]["restatement_suspected"])

    def test_mismatched_values_without_transition_flag_are_conflicts(self):
        current_facts = [
            {
                "company_year_id": "A_2023",
                "operating_company_id": "A",
                "fiscal_year": 2023,
                "element_id": "jppfs_cor:OperatingIncome",
                "context_id": "CurrentYearDuration",
                "consolidation_scope": "連結",
                "period_or_instant": "期間",
                "value": "1000000000",
            }
        ]
        prior_facts_by_key = {
            ("A_2024", "jppfs_cor:OperatingIncome"): [
                {
                    "context_id": "Prior1YearDuration",
                    "consolidation_scope": "連結",
                    "period_or_instant": "期間",
                    "value": "5000000000",
                }
            ]
        }
        next_lookup = {("A", 2024): "A_2024"}
        records = corroborate_next_year_prior(
            current_facts=current_facts,
            prior_facts_by_key=prior_facts_by_key,
            next_company_year_lookup=next_lookup,
            transition_flags={"A_2023": 0, "A_2024": 0},
            valid_company_year_ids={"A_2023", "A_2024"},
        )
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["matched"])
        self.assertFalse(records[0]["restatement_suspected"])

    def test_mismatched_values_with_transition_flag_are_flagged_not_conflicted(self):
        current_facts = [
            {
                "company_year_id": "A_2020",
                "operating_company_id": "A",
                "fiscal_year": 2020,
                "element_id": "jppfs_cor:OperatingIncome",
                "context_id": "CurrentYearDuration",
                "consolidation_scope": "連結",
                "period_or_instant": "期間",
                "value": "1000000000",
            }
        ]
        prior_facts_by_key = {
            ("A_2021", "jppfs_cor:OperatingIncome"): [
                {
                    "context_id": "Prior1YearDuration",
                    "consolidation_scope": "連結",
                    "period_or_instant": "期間",
                    "value": "5000000000",
                }
            ]
        }
        next_lookup = {("A", 2021): "A_2021"}
        records = corroborate_next_year_prior(
            current_facts=current_facts,
            prior_facts_by_key=prior_facts_by_key,
            next_company_year_lookup=next_lookup,
            transition_flags={"A_2020": 0, "A_2021": 1},
            valid_company_year_ids={"A_2020", "A_2021"},
        )
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["matched"])
        self.assertTrue(records[0]["restatement_suspected"])

    def test_missing_next_year_data_yields_no_record(self):
        current_facts = [
            {
                "company_year_id": "A_2024",
                "operating_company_id": "A",
                "fiscal_year": 2024,
                "element_id": "jppfs_cor:OperatingIncome",
                "context_id": "CurrentYearDuration",
                "consolidation_scope": "連結",
                "period_or_instant": "期間",
                "value": "1000000000",
            }
        ]
        records = corroborate_next_year_prior(
            current_facts=current_facts,
            prior_facts_by_key={},
            next_company_year_lookup={},
            transition_flags={},
            valid_company_year_ids={"A_2024"},
        )
        self.assertEqual(len(records), 0)

    def test_context_member_suffix_mismatch_is_not_paired(self):
        current_facts = [
            {
                "company_year_id": "A_2023",
                "operating_company_id": "A",
                "fiscal_year": 2023,
                "element_id": "jppfs_cor:OperatingIncome",
                "context_id": "CurrentYearDuration_NonConsolidatedMember",
                "consolidation_scope": "個別",
                "period_or_instant": "期間",
                "value": "1000000000",
            }
        ]
        prior_facts_by_key = {
            ("A_2024", "jppfs_cor:OperatingIncome"): [
                {
                    # 連結側（サフィックス無し）なので個別側とはペアにならない
                    "context_id": "Prior1YearDuration",
                    "consolidation_scope": "個別",
                    "period_or_instant": "期間",
                    "value": "1000000000",
                }
            ]
        }
        next_lookup = {("A", 2024): "A_2024"}
        records = corroborate_next_year_prior(
            current_facts=current_facts,
            prior_facts_by_key=prior_facts_by_key,
            next_company_year_lookup=next_lookup,
            transition_flags={},
            valid_company_year_ids={"A_2023", "A_2024"},
        )
        self.assertEqual(len(records), 0)


class CorroborateValidationRulesTests(unittest.TestCase):
    def test_pass_status_corroborates_each_field(self):
        validation_results = [
            {
                "company_year_id": "A_2024",
                "rule_id": "sum_building_orders",
                "status": "pass",
                "field_ids": ["building_orders_total", "building_orders_private"],
            }
        ]
        records = corroborate_validation_rules(validation_results)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r["matched"] for r in records))
        self.assertEqual(records[0]["check_kind"], "identity_rule")

    def test_fail_status_is_conflict_for_each_field(self):
        validation_results = [
            {
                "company_year_id": "A_2024",
                "rule_id": "sum_building_orders",
                "status": "fail",
                "field_ids": ["building_orders_total", "building_orders_private"],
            }
        ]
        records = corroborate_validation_rules(validation_results)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(not r["matched"] for r in records))

    def test_warn_and_not_applicable_are_excluded(self):
        validation_results = [
            {"company_year_id": "A_2024", "rule_id": "yoy_anomaly", "status": "warn", "field_ids": ["x"]},
            {"company_year_id": "A_2024", "rule_id": "yoy_anomaly", "status": "not_applicable", "field_ids": ["y"]},
        ]
        records = corroborate_validation_rules(validation_results)
        self.assertEqual(len(records), 0)


class CorroborateFactbookTests(unittest.TestCase):
    def test_matching_factbook_value_corroborates(self):
        cell_lookup = {("SHIMIZU_2020", "domestic_building_orders_total"): 500.0}
        factbook_rows = [
            {
                "company_id": "SHIMIZU",
                "fiscal_year": 2020,
                "period_type": "annual",
                "use_category_normalized": "domestic_building",
                "amount_million_yen": 500.0,
            }
        ]
        records = corroborate_factbook(
            cell_lookup=cell_lookup,
            factbook_rows=factbook_rows,
            field_map={"domestic_building": "domestic_building_orders_total"},
            check_ref="shimizu_segment_orders",
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["matched"])
        self.assertEqual(records[0]["check_kind"], "factbook")

    def test_forecast_period_type_is_excluded(self):
        cell_lookup = {("SHIMIZU_2020", "domestic_building_orders_total"): 500.0}
        factbook_rows = [
            {
                "company_id": "SHIMIZU",
                "fiscal_year": 2020,
                "period_type": "forecast_annual",
                "use_category_normalized": "domestic_building",
                "amount_million_yen": 999.0,
            }
        ]
        records = corroborate_factbook(
            cell_lookup=cell_lookup,
            factbook_rows=factbook_rows,
            field_map={"domestic_building": "domestic_building_orders_total"},
            check_ref="shimizu_segment_orders",
        )
        self.assertEqual(len(records), 0)

    def test_unmapped_category_is_skipped(self):
        cell_lookup = {("SHIMIZU_2020", "domestic_building_orders_total"): 500.0}
        factbook_rows = [
            {
                "company_id": "SHIMIZU",
                "fiscal_year": 2020,
                "period_type": "annual",
                "use_category_normalized": "development_other",
                "amount_million_yen": 100.0,
            }
        ]
        records = corroborate_factbook(
            cell_lookup=cell_lookup,
            factbook_rows=factbook_rows,
            field_map={"domestic_building": "domestic_building_orders_total"},
            check_ref="shimizu_segment_orders",
        )
        self.assertEqual(len(records), 0)


class SummarizeCellsTests(unittest.TestCase):
    def test_counts_matched_and_conflicted(self):
        records = [
            {"company_year_id": "A_2024", "field_id": "f1", "matched": True, "restatement_suspected": False},
            {"company_year_id": "A_2024", "field_id": "f1", "matched": True, "restatement_suspected": False},
            {"company_year_id": "A_2024", "field_id": "f1", "matched": False, "restatement_suspected": False},
        ]
        by_cell = summarize_cells(records)
        entry = by_cell[("A_2024", "f1")]
        self.assertEqual(entry["corroboration_count"], 2)
        self.assertEqual(entry["conflict_count"], 1)

    def test_restatement_suspected_is_neither_matched_nor_conflict(self):
        records = [
            {"company_year_id": "A_2021", "field_id": "f1", "matched": False, "restatement_suspected": True},
        ]
        by_cell = summarize_cells(records)
        entry = by_cell[("A_2021", "f1")]
        self.assertEqual(entry["corroboration_count"], 0)
        self.assertEqual(entry["conflict_count"], 0)
        self.assertEqual(entry["restatement_suspected_count"], 1)

    def test_all_cells_without_records_are_zero_filled(self):
        by_cell = summarize_cells([], all_cells=[("A_2024", "f1")])
        entry = by_cell[("A_2024", "f1")]
        self.assertEqual(entry["corroboration_count"], 0)
        self.assertEqual(entry["conflict_count"], 0)


class BuildCorroborationReportIntegrationTests(unittest.TestCase):
    """corroboration_report.build_corroboration_report のエンドツーエンド（合成データ・tempdir）。"""

    def test_end_to_end_with_synthetic_data(self):
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
                    {
                        "company_year_id": "A_2024",
                        "operating_company_id": "A",
                        "fiscal_year": "2024",
                        "field_id": "operating_income_consolidated",
                        "extraction_method": "XBRL_CSV",
                        "value_normalized": 2000.0,
                        "unit_normalized": "百万円",
                        "review_required": False,
                        "validation_status": "not_applicable",
                    },
                ],
            )
            write_table(
                root / "data" / "intermediate" / "validation_results.csv",
                [],
            )
            write_table(
                root / "config" / "field_definition.csv",
                [
                    {
                        "field_id": "operating_income_consolidated",
                        "field_name_ja": "営業利益_連結",
                        "target_unit": "百万円",
                        "xbrl_tag_candidates": "OperatingIncome",
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
                    },
                    {
                        "company_year_id": "A_2024",
                        "fiscal_year": 2024,
                        "operating_company_id": "A",
                        "transition_year_flag": 0,
                    },
                ],
            )

            db_path = root / "data" / "intermediate" / "edinet.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                create table xbrl_facts (
                    id integer primary key autoincrement,
                    company_year_id text,
                    operating_company_id text,
                    fiscal_year integer,
                    element_id text,
                    context_id text,
                    relative_year text,
                    consolidation_scope text,
                    period_or_instant text,
                    unit text,
                    value text
                )
                """
            )
            conn.execute(
                "insert into xbrl_facts (company_year_id, operating_company_id, fiscal_year, element_id, "
                "context_id, relative_year, consolidation_scope, period_or_instant, unit, value) values "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "A_2023",
                    "A",
                    2023,
                    "jppfs_cor:OperatingIncome",
                    "CurrentYearDuration",
                    "当期",
                    "連結",
                    "期間",
                    "円",
                    "1000000000",
                ),
            )
            conn.execute(
                "insert into xbrl_facts (company_year_id, operating_company_id, fiscal_year, element_id, "
                "context_id, relative_year, consolidation_scope, period_or_instant, unit, value) values "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "A_2024",
                    "A",
                    2024,
                    "jppfs_cor:OperatingIncome",
                    "Prior1YearDuration",
                    "前期",
                    "連結",
                    "期間",
                    "円",
                    "1000000000",
                ),
            )
            conn.commit()
            conn.close()

            result = build_corroboration_report(root)
            summary = result["summary"]
            self.assertEqual(summary["cells_total"], 2)
            # A_2023: same-cell match(+1) + next_year_prior match(+1) = 2 -> corroborated_2plus
            # A_2024: no corroboration -> corroborated_0
            self.assertEqual(summary["corroborated_2plus"], 1)
            self.assertEqual(summary["corroborated_0"], 1)
            self.assertEqual(summary["conflicts"], 0)

            cells_path = Path(result["cells_path"])
            self.assertTrue(cells_path.exists())
            summary_json_path = Path(result["summary_json_path"])
            self.assertTrue(summary_json_path.exists())
            summary_md_path = Path(result["summary_md_path"])
            self.assertTrue(summary_md_path.exists())

    def test_nan_value_normalized_from_parquet_is_excluded_from_cells(self):
        # 実データで踏んだ回帰: pandas経由(.parquet)で読むと欠損値がfloat('nan')に
        # なり、`value in (None, "")` の等価判定だけでは除外されず
        # 誤ってconflict扱いされていた。.parquet拡張子で書いてparquet経由の
        # 欠損値表現を再現する。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "intermediate" / "normalized_validated_long.parquet",
                [
                    {
                        "company_year_id": "A_2023",
                        "operating_company_id": "A",
                        "fiscal_year": "2023",
                        "field_id": "average_age",
                        "extraction_method": "XBRL_CSV",
                        "value_normalized": None,  # pandas経由でNaNになる
                        "unit_normalized": "歳",
                        "review_required": False,
                        "validation_status": "not_applicable",
                    },
                    {
                        "company_year_id": "A_2023",
                        "operating_company_id": "A",
                        "fiscal_year": "2023",
                        "field_id": "average_age",
                        "extraction_method": "LOCAL_RULE_TABLE",
                        "value_normalized": 45.5,
                        "unit_normalized": "歳",
                        "review_required": False,
                        "validation_status": "not_applicable",
                    },
                ],
            )
            write_table(root / "data" / "intermediate" / "validation_results.csv", [])
            write_table(
                root / "config" / "field_definition.csv",
                [{"field_id": "average_age", "field_name_ja": "平均年齢", "target_unit": "歳", "xbrl_tag_candidates": ""}],
            )
            write_table(root / "config" / "company_year_master.csv", [])

            result = build_corroboration_report(root)
            summary = result["summary"]
            # NaN側の行はセルとして数えられない（grouped対象外）ため、
            # 残る有効値行1件のみが1セルとして扱われ、照合0件（他extraction_methodが無い）。
            self.assertEqual(summary["cells_total"], 1)
            self.assertEqual(summary["conflicts"], 0)
            self.assertEqual(summary["corroborated_0"], 1)


if __name__ == "__main__":
    unittest.main()
