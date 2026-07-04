import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table, write_yaml
from yuho_auto_extract.services import datasets
from yuho_auto_extract.services.xbrl_discovered_metrics import (
    bulk_upsert_xbrl_metric_mappings,
    build_xbrl_discovered_metrics,
    read_xbrl_discovered_metrics,
    upsert_xbrl_metric_mapping,
)


class XbrlDiscoveredMetricsTests(unittest.TestCase):
    def test_build_xbrl_discovered_metrics_keeps_similar_items_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            _write_fact_store(root)

            result = build_xbrl_discovered_metrics(root)
            values = read_table(root / "data" / "marts" / "xbrl_discovered_metrics" / "value_long.csv")
            catalog = read_table(root / "data" / "marts" / "xbrl_discovered_metrics" / "metric_catalog.csv")
            suggestions = read_table(root / "data" / "marts" / "xbrl_discovered_metrics" / "similarity_suggestions.csv")

            self.assertEqual(result["numeric_current_facts"], 3)
            self.assertEqual(result["discovered_metrics"], 3)
            self.assertEqual(result["excluded_counts"]["non_current_year"], 1)
            self.assertEqual(result["excluded_counts"]["text_block"], 1)
            self.assertEqual({row["discovered_metric_label"] for row in values}, {"売上高", "売上総利益", "完成工事総利益"})

            gross_profit = next(row for row in catalog if row["discovered_metric_label"] == "売上総利益")
            construction_gross_profit = next(row for row in catalog if row["discovered_metric_label"] == "完成工事総利益")
            self.assertNotEqual(gross_profit["discovered_metric_id"], construction_gross_profit["discovered_metric_id"])
            self.assertIn("gross_profit_consolidated", gross_profit["matched_field_ids"])
            self.assertEqual(next(row for row in values if row["discovered_metric_label"] == "売上高")["value_display"], "1,234,567")

            suggested_pairs = {
                frozenset([row["left_metric_label"], row["right_metric_label"]])
                for row in suggestions
            }
            self.assertIn(frozenset(["売上総利益", "完成工事総利益"]), suggested_pairs)

    def test_project_status_exposes_xbrl_discovered_metric_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            _write_fact_store(root)
            build_xbrl_discovered_metrics(root)

            status = datasets.project_status(root)

            self.assertTrue(status["files"]["xbrl_discovered_metrics_manifest"])
            self.assertTrue(status["files"]["xbrl_discovered_metric_catalog"])
            self.assertTrue(status["files"]["xbrl_discovered_value_long"])
            self.assertTrue(status["files"]["xbrl_discovered_similarity_suggestions"])

    def test_discovered_metric_mapping_can_be_saved_filtered_and_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            _write_fact_store(root)
            build_xbrl_discovered_metrics(root)

            initial = read_xbrl_discovered_metrics(root, search="完成工事")
            metric_id = initial["rows"][0]["discovered_metric_id"]

            result = upsert_xbrl_metric_mapping(
                root,
                metric_id,
                target_field_id="gross_profit_consolidated",
                mapping_status="candidate",
                note="表記ゆれ候補",
            )
            mapped = read_xbrl_discovered_metrics(root, mapping_status="candidate")
            rows = read_table(root / "data" / "marts" / "xbrl_discovered_metrics" / "field_mappings.csv")

            self.assertEqual(result["target_field_name_ja"], "売上総利益_連結")
            self.assertEqual(mapped["total"], 1)
            self.assertEqual(mapped["rows"][0]["mapping_status"], "candidate")
            self.assertEqual(mapped["rows"][0]["mapping_note"], "表記ゆれ候補")
            self.assertEqual(rows[0]["target_field_id"], "gross_profit_consolidated")

            with self.assertRaises(ValueError):
                upsert_xbrl_metric_mapping(root, metric_id, mapping_status="accepted")

            upsert_xbrl_metric_mapping(root, metric_id, mapping_status="unmapped")
            cleared = read_xbrl_discovered_metrics(root, mapping_status="candidate")

            self.assertEqual(cleared["total"], 0)

    def test_bulk_mapping_marks_selected_metrics_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root)
            _write_fact_store(root)
            build_xbrl_discovered_metrics(root)
            initial = read_xbrl_discovered_metrics(root)
            metric_ids = [row["discovered_metric_id"] for row in initial["rows"][:2]]

            result = bulk_upsert_xbrl_metric_mappings(
                root,
                metric_ids,
                mapping_status="rejected",
                note="まとめて使わない",
            )
            rejected = read_xbrl_discovered_metrics(root, mapping_status="rejected")
            rows = read_table(root / "data" / "marts" / "xbrl_discovered_metrics" / "field_mappings.csv")

            self.assertEqual(result["changed"], 2)
            self.assertEqual(rejected["total"], 2)
            self.assertEqual({row["mapping_status"] for row in rows}, {"rejected"})
            self.assertEqual({row["mapping_note"] for row in rows}, {"まとめて使わない"})


def _write_config(root: Path) -> None:
    write_table(
        root / "config" / "company_master.csv",
        [
            {
                "operating_company_id": "A",
                "operating_company_name": "A社",
                "edinet_code": "E00001",
                "fiscal_year_end_month": "3",
                "default_data_scope": "consolidated",
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
                "data_scope_allowed": "consolidated;standalone",
                "transition_year_flag": "false",
                "analysis_treatment": "normal",
            }
        ],
    )
    write_table(
        root / "config" / "field_definition.csv",
        [
            {
                "field_id": "net_sales_consolidated",
                "field_name_ja": "売上高_連結",
                "category": "performance",
                "target_unit": "百万円",
                "data_scope_required": "consolidated",
                "period_type": "duration",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "NetSales;売上高",
                "context_filters": "CurrentYearDuration;ConsolidatedMember",
            },
            {
                "field_id": "gross_profit_consolidated",
                "field_name_ja": "売上総利益_連結",
                "category": "performance",
                "target_unit": "百万円",
                "data_scope_required": "consolidated",
                "period_type": "duration",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "GrossProfit;売上総利益",
                "context_filters": "CurrentYearDuration;ConsolidatedMember",
            },
        ],
    )
    write_yaml(root / "config" / "document_filter.yml", {})
    write_yaml(root / "config" / "extraction_sections.yml", {})
    write_yaml(root / "config" / "validation_rules.yml", {"rules": []})
    write_yaml(root / "config" / "model_config.yml", {})


def _write_fact_store(root: Path) -> None:
    base = {
        "company_year_id": "A_2024",
        "operating_company_id": "A",
        "fiscal_year": "2024",
        "source_doc_id": "S100TEST",
        "source_file": "data/raw/documents/S100TEST/csv.zip",
        "csv_file": "PublicDoc/test.csv",
        "relative_year": "当期",
        "consolidation_scope": "連結",
        "normalized_scope": "consolidated",
        "period_or_instant": "期間",
        "unit_id": "JPY",
        "unit": "百万円",
        "value_type": "numeric",
        "is_text_block": "no",
    }
    write_table(
        root / "data" / "marts" / "xbrl_fact_store" / "facts.csv",
        [
            {
                **base,
                "csv_row": "1",
                "element_id": "jpcrp_cor:NetSales",
                "element_local_name": "NetSales",
                "item_name": "売上高",
                "context_id": "CurrentYearDuration",
                "value": "1,234,567",
                "value_numeric": 1234567,
                "source_quote": "売上高: 1,234,567",
            },
            {
                **base,
                "csv_row": "2",
                "element_id": "jpcrp_cor:GrossProfit",
                "element_local_name": "GrossProfit",
                "item_name": "売上総利益",
                "context_id": "CurrentYearDuration",
                "value": "456,789",
                "value_numeric": 456789,
                "source_quote": "売上総利益: 456,789",
            },
            {
                **base,
                "csv_row": "3",
                "element_id": "jpcrp_cor:GrossProfitOnCompletedConstructionContracts",
                "element_local_name": "GrossProfitOnCompletedConstructionContracts",
                "item_name": "完成工事総利益",
                "context_id": "CurrentYearDuration",
                "value": "345,678",
                "value_numeric": 345678,
                "source_quote": "完成工事総利益: 345,678",
            },
            {
                **base,
                "csv_row": "4",
                "element_id": "jpcrp_cor:PriorYearNetSales",
                "element_local_name": "PriorYearNetSales",
                "item_name": "前期売上高",
                "context_id": "PriorYearDuration",
                "relative_year": "前期",
                "value": "1,111",
                "value_numeric": 1111,
                "source_quote": "前期売上高: 1,111",
            },
            {
                **base,
                "csv_row": "5",
                "element_id": "jpcrp_cor:BusinessOverviewTextBlock",
                "element_local_name": "BusinessOverviewTextBlock",
                "item_name": "事業の状況 [テキストブロック]",
                "context_id": "CurrentYearDuration",
                "value": "文章です。",
                "value_numeric": "",
                "value_type": "text",
                "is_text_block": "yes",
                "source_quote": "文章です。",
            },
        ],
    )


if __name__ == "__main__":
    unittest.main()
