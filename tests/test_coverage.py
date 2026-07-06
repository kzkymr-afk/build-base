from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from yuho_auto_extract.io_utils import ensure_parent, write_table, write_yaml
from yuho_auto_extract.services import coverage


def _write_company_year_master(root: Path, rows) -> None:
    path = root / coverage.COMPANY_YEAR_MASTER_RELATIVE_PATH
    ensure_parent(path)
    write_table(path, rows)


def _write_final_master_long(root: Path, rows) -> None:
    path = root / coverage.FINAL_MASTER_LONG_RELATIVE_PATH
    ensure_parent(path)
    write_table(path, rows)


def _write_exclusions(root: Path, rows) -> None:
    path = root / coverage.COMPANY_FIELD_EXCLUSIONS_RELATIVE_PATH
    ensure_parent(path)
    write_table(path, rows)


def _write_field_definition(root: Path, rows) -> None:
    path = root / coverage.FIELD_DEFINITION_RELATIVE_PATH
    ensure_parent(path)
    write_table(path, rows)


def _write_core_fields(root: Path, field_ids) -> None:
    path = root / coverage.CORE_FIELDS_CONFIG_RELATIVE_PATH
    write_yaml(path, {"core_fields": field_ids})


def _base_company_year_rows():
    # 2社 x 2年度 = 4会社年度（annual）+ 1件の非annual（対象外になることを確認するため）
    return [
        {
            "company_year_id": "ACME_2020",
            "fiscal_year": "2020",
            "operating_company_id": "ACME",
            "period_type": "annual",
        },
        {
            "company_year_id": "ACME_2021",
            "fiscal_year": "2021",
            "operating_company_id": "ACME",
            "period_type": "annual",
        },
        {
            "company_year_id": "BETA_2020",
            "fiscal_year": "2020",
            "operating_company_id": "BETA",
            "period_type": "annual",
        },
        {
            "company_year_id": "BETA_2021",
            "fiscal_year": "2021",
            "operating_company_id": "BETA",
            "period_type": "annual",
        },
        {
            # non-annual: 母集団から除外されるべき
            "company_year_id": "ACME_2020Q2",
            "fiscal_year": "2020",
            "operating_company_id": "ACME",
            "period_type": "quarterly",
        },
    ]


class BuildCoreCoverageMatrixTests(unittest.TestCase):
    def test_matrix_and_summary_basic(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_company_year_master(root, _base_company_year_rows())
            _write_field_definition(
                root,
                [{"field_id": "net_sales_consolidated", "field_name_ja": "売上高_連結"}],
            )
            _write_final_master_long(
                root,
                [
                    {
                        "company_year_id": "ACME_2020",
                        "field_id": "net_sales_consolidated",
                        "value_normalized": "1000",
                        "value": "1000",
                    },
                    {
                        "company_year_id": "ACME_2021",
                        "field_id": "net_sales_consolidated",
                        "value_normalized": "",
                        "value": "",
                    },
                    {
                        "company_year_id": "BETA_2020",
                        "field_id": "net_sales_consolidated",
                        "value_normalized": "2000",
                        "value": "2000",
                    },
                    # BETA_2021 は行自体が存在しない（欠損として扱われるべき）
                ],
            )
            _write_exclusions(root, [])
            _write_core_fields(root, ["net_sales_consolidated"])

            result = coverage.build_core_coverage_matrix(root)

            self.assertEqual(result["companies"], ["ACME", "BETA"])
            self.assertEqual(
                [f["field_id"] for f in result["fields"]], ["net_sales_consolidated"]
            )

            field_matrix = result["matrix"]["net_sales_consolidated"]
            self.assertEqual(field_matrix["ACME"]["filled_years"], 1)
            self.assertEqual(field_matrix["ACME"]["total_years"], 2)
            self.assertEqual(field_matrix["ACME"]["blank_years"], [2021])
            self.assertEqual(field_matrix["ACME"]["excluded_years"], [])

            self.assertEqual(field_matrix["BETA"]["filled_years"], 1)
            self.assertEqual(field_matrix["BETA"]["total_years"], 2)
            self.assertEqual(field_matrix["BETA"]["blank_years"], [2021])

            summary = result["summary"]["net_sales_consolidated"]
            self.assertEqual(summary["filled"], 2)
            self.assertEqual(summary["total"], 4)
            self.assertAlmostEqual(summary["rate"], 0.5)

    def test_excluded_is_distinct_from_blank(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_company_year_master(root, _base_company_year_rows())
            _write_field_definition(
                root, [{"field_id": "rd_expense", "field_name_ja": "研究開発費"}]
            )
            _write_final_master_long(root, [])
            _write_exclusions(
                root,
                [
                    {
                        "company_id": "ACME",
                        "field_id": "rd_expense",
                        "start_year": "",
                        "end_year": "",
                        "reason": "対象外の会社区分",
                    }
                ],
            )
            _write_core_fields(root, ["rd_expense"])

            result = coverage.build_core_coverage_matrix(root)
            field_matrix = result["matrix"]["rd_expense"]

            # ACME は全年度除外 -> blank_years はゼロ、excluded_years に両年度
            self.assertEqual(field_matrix["ACME"]["excluded_years"], [2020, 2021])
            self.assertEqual(field_matrix["ACME"]["blank_years"], [])
            self.assertEqual(field_matrix["ACME"]["total_years"], 0)

            # BETA は除外登録がないので通常どおり blank として扱う
            self.assertEqual(field_matrix["BETA"]["blank_years"], [2020, 2021])
            self.assertEqual(field_matrix["BETA"]["excluded_years"], [])

            # summary の total は除外分を含まない
            summary = result["summary"]["rd_expense"]
            self.assertEqual(summary["total"], 2)  # BETAの2年度のみ
            self.assertEqual(summary["filled"], 0)

    def test_exclusion_year_range_is_respected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_company_year_master(root, _base_company_year_rows())
            _write_field_definition(
                root, [{"field_id": "rd_expense", "field_name_ja": "研究開発費"}]
            )
            _write_final_master_long(root, [])
            _write_exclusions(
                root,
                [
                    {
                        "company_id": "ACME",
                        "field_id": "rd_expense",
                        "start_year": "2021",
                        "end_year": "2021",
                        "reason": "2021年度のみ対象外",
                    }
                ],
            )
            _write_core_fields(root, ["rd_expense"])

            result = coverage.build_core_coverage_matrix(root)
            field_matrix = result["matrix"]["rd_expense"]
            self.assertEqual(field_matrix["ACME"]["excluded_years"], [2021])
            self.assertEqual(field_matrix["ACME"]["blank_years"], [2020])

    def test_recoverable_years_from_dry_run_report(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_company_year_master(root, _base_company_year_rows())
            _write_field_definition(
                root,
                [{"field_id": "building_orders_total", "field_name_ja": "建築受注高_合計"}],
            )
            _write_final_master_long(root, [])
            _write_exclusions(root, [])
            _write_core_fields(root, ["building_orders_total"])

            report_path = root / coverage.SOURCE_INFERENCE_DRY_RUN_RELATIVE_PATH
            ensure_parent(report_path)
            report_path.write_text(
                json.dumps({"field_ids": ["building_orders_total"]}), encoding="utf-8"
            )

            fake_classification = {
                "ACME_2020": {"building_orders_total": "high_confidence"},
                "ACME_2021": {"building_orders_total": "low_confidence"},
                "BETA_2020": {"building_orders_total": "high_confidence"},
            }

            from yuho_auto_extract.services import source_inference as si

            original = si.estimate_recovery
            si.estimate_recovery = lambda root, field_ids=None: {  # type: ignore
                "classification": fake_classification
            }
            try:
                result = coverage.build_core_coverage_matrix(root)
            finally:
                si.estimate_recovery = original

            field_matrix = result["matrix"]["building_orders_total"]
            self.assertEqual(field_matrix["ACME"]["recoverable_years"], [2020])
            self.assertEqual(field_matrix["ACME"]["blank_years"], [2020, 2021])
            self.assertEqual(field_matrix["BETA"]["recoverable_years"], [2020])
            self.assertEqual(result["summary"]["building_orders_total"]["recoverable"], 2)

    def test_no_dry_run_report_means_no_recoverable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_company_year_master(root, _base_company_year_rows())
            _write_field_definition(
                root,
                [{"field_id": "building_orders_total", "field_name_ja": "建築受注高_合計"}],
            )
            _write_final_master_long(root, [])
            _write_exclusions(root, [])
            _write_core_fields(root, ["building_orders_total"])
            # dry-run report を書かない

            result = coverage.build_core_coverage_matrix(root)
            field_matrix = result["matrix"]["building_orders_total"]
            self.assertEqual(field_matrix["ACME"]["recoverable_years"], [])
            self.assertEqual(result["summary"]["building_orders_total"]["recoverable"], 0)

    def test_field_not_covered_by_dry_run_is_skipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_company_year_master(root, _base_company_year_rows())
            _write_field_definition(
                root,
                [{"field_id": "net_sales_consolidated", "field_name_ja": "売上高_連結"}],
            )
            _write_final_master_long(root, [])
            _write_exclusions(root, [])
            _write_core_fields(root, ["net_sales_consolidated"])

            report_path = root / coverage.SOURCE_INFERENCE_DRY_RUN_RELATIVE_PATH
            ensure_parent(report_path)
            report_path.write_text(
                json.dumps({"field_ids": ["building_orders_total"]}), encoding="utf-8"
            )

            result = coverage.build_core_coverage_matrix(root)
            field_matrix = result["matrix"]["net_sales_consolidated"]
            self.assertEqual(field_matrix["ACME"]["recoverable_years"], [])

    def test_load_core_field_ids_reads_yaml(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_core_fields(root, ["a", "b", "c"])
            self.assertEqual(coverage.load_core_field_ids(root), ["a", "b", "c"])

    def test_load_core_field_ids_missing_file_returns_empty(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(coverage.load_core_field_ids(root), [])


if __name__ == "__main__":
    unittest.main()
