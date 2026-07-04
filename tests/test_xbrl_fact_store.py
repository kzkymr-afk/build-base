import tempfile
import unittest
import zipfile
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table, write_yaml
from yuho_auto_extract.xbrl_fact_store import build_xbrl_fact_store, compare_xbrl_fact_store, extract_from_xbrl_fact_store


class XbrlFactStoreTests(unittest.TestCase):
    def test_build_xbrl_fact_store_normalizes_facts_contexts_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_minimal_config(root)
            _write_target_documents(root)
            _write_csv_zip(root)

            result = build_xbrl_fact_store(root)
            facts = read_table(root / "data" / "marts" / "xbrl_fact_store" / "facts.csv")
            facts_json = read_table(root / "data" / "marts" / "xbrl_fact_store" / "facts.json")
            contexts = read_table(root / "data" / "marts" / "xbrl_fact_store" / "context_index.csv")
            contexts_json = read_table(root / "data" / "marts" / "xbrl_fact_store" / "context_index.json")
            digest = (root / "data" / "marts" / "xbrl_fact_store" / "document_digest.md").read_text(encoding="utf-8")

            self.assertEqual(result["facts"], 4)
            self.assertEqual(result["text_blocks"], 1)
            self.assertEqual(len(facts_json), 4)
            self.assertEqual(len(contexts_json), len(contexts))
            self.assertEqual(result["facts_json"], "data/marts/xbrl_fact_store/facts.json")
            self.assertEqual(result["context_json"], "data/marts/xbrl_fact_store/context_index.json")
            self.assertTrue((root / "data" / "marts" / "xbrl_fact_store" / "manifest.json").exists())
            net_sales = next(row for row in facts if row["element_local_name"] == "NetSales")
            self.assertEqual(net_sales["normalized_scope"], "consolidated")
            self.assertEqual(float(net_sales["value_numeric"]), 1234.0)
            text_block = next(row for row in facts if row["element_local_name"] == "NetSalesTextBlock")
            self.assertEqual(text_block["is_text_block"], "yes")
            self.assertEqual(text_block["value_type"], "text")
            standalone_context = next(row for row in contexts if row["context_id"] == "CurrentYearDuration_NonConsolidatedMember")
            self.assertEqual(standalone_context["normalized_scope"], "standalone")
            self.assertIn("A_2024", digest)
            self.assertIn("Numeric Facts Sample", digest)

    def test_extract_from_xbrl_fact_store_uses_existing_field_definition_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_minimal_config(root)
            _write_target_documents(root)
            _write_csv_zip(root)
            build_xbrl_fact_store(root)

            result = extract_from_xbrl_fact_store(root, root / "data" / "intermediate" / "xbrl_fact_store_extracted_long.csv")
            rows = read_table(root / "data" / "intermediate" / "xbrl_fact_store_extracted_long.csv")
            by_field = {row["field_id"]: row for row in rows}

            self.assertEqual(result["rows"], 2)
            self.assertEqual(by_field["net_sales_consolidated"]["value_raw"], "1,234")
            self.assertEqual(by_field["net_sales_consolidated"]["data_scope"], "consolidated")
            self.assertEqual(by_field["average_salary"]["context_ref"], "CurrentYearDuration_NonConsolidatedMember")
            self.assertEqual(by_field["average_salary"]["extraction_method"], "XBRL_CSV")
            self.assertNotIn("NetSalesTextBlock", by_field["net_sales_consolidated"]["xbrl_element"])

    def test_extract_from_xbrl_fact_store_maps_semiannual_contexts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_semiannual_config(root)
            _write_semiannual_target_documents(root)
            _write_semiannual_csv_zip(root)
            build_xbrl_fact_store(root)

            result = extract_from_xbrl_fact_store(
                root,
                root / "data" / "intermediate" / "semiannual_h1_extracted_long.csv",
                company_year_id="A_2024H1",
            )
            rows = read_table(root / "data" / "intermediate" / "semiannual_h1_extracted_long.csv")
            by_field = {row["field_id"]: row for row in rows}

            self.assertEqual(result["rows"], 2)
            self.assertEqual(by_field["net_sales_consolidated"]["value_raw"], "200")
            self.assertEqual(by_field["net_sales_consolidated"]["context_ref"], "CurrentYTDDuration")
            self.assertEqual(by_field["total_assets_consolidated"]["value_raw"], "900")
            self.assertEqual(by_field["total_assets_consolidated"]["context_ref"], "CurrentQuarterInstant")

    def test_build_xbrl_fact_store_merge_replaces_selected_company_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_minimal_config(root)
            _write_target_documents(root)
            _write_csv_zip(root)
            build_xbrl_fact_store(root)
            write_table(
                root / "data" / "intermediate" / "target_documents.csv",
                [
                    {
                        "company_year_id": "B_2024",
                        "operating_company_id": "B",
                        "fiscal_year": "2024",
                        "docID": "DOC002",
                        "resolution_status": "resolved",
                    }
                ],
            )
            _write_csv_zip(root, doc_id="DOC002", net_sales="2,468")

            result = build_xbrl_fact_store(root, company_year_id="B_2024", merge_existing=True)
            facts = read_table(root / "data" / "marts" / "xbrl_fact_store" / "facts.csv")
            company_years = {row["company_year_id"] for row in facts}

            self.assertEqual(result["facts_updated"], 4)
            self.assertEqual(company_years, {"A_2024", "B_2024"})

    def test_compare_xbrl_fact_store_outputs_match_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "intermediate" / "xbrl_extracted_long.csv",
                [
                    {"company_year_id": "A_2024", "field_id": "sales", "value": "100", "extraction_method": "old", "source_quote": "old sales"},
                    {"company_year_id": "A_2024", "field_id": "profit", "value": "50", "extraction_method": "old"},
                    {"company_year_id": "A_2024", "field_id": "assets", "value": "900", "extraction_method": "old"},
                ],
            )
            write_table(
                root / "data" / "intermediate" / "xbrl_fact_store_extracted_long.csv",
                [
                    {"company_year_id": "A_2024", "field_id": "sales", "value": "100.0", "extraction_method": "new", "source_quote": "new sales"},
                    {"company_year_id": "A_2024", "field_id": "profit", "value": "55", "extraction_method": "new"},
                    {"company_year_id": "A_2024", "field_id": "cash", "value": "10", "extraction_method": "new"},
                ],
            )

            result = compare_xbrl_fact_store(
                root,
                root / "data" / "intermediate" / "xbrl_extracted_long.csv",
                root / "data" / "intermediate" / "xbrl_fact_store_extracted_long.csv",
                root / "data" / "reports" / "xbrl_fact_store_compare.csv",
            )
            rows = read_table(root / "data" / "reports" / "xbrl_fact_store_compare.csv")
            statuses = {(row["field_id"], row["match_status"]) for row in rows}

            self.assertEqual(result["compared"], 4)
            self.assertIn(("sales", "match"), statuses)
            self.assertIn(("profit", "mismatch"), statuses)
            self.assertIn(("assets", "old_only"), statuses)
            self.assertIn(("cash", "new_only"), statuses)


def _write_minimal_config(root: Path) -> None:
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
                "field_id": "average_salary",
                "field_name_ja": "平均給与",
                "category": "human_capital",
                "target_unit": "千円",
                "data_scope_required": "standalone",
                "period_type": "duration",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "AverageAnnualSalary",
                "context_filters": "CurrentYearDuration;NonConsolidatedMember",
            },
        ],
    )
    write_yaml(root / "config" / "document_filter.yml", {})
    write_yaml(root / "config" / "extraction_sections.yml", {})
    write_yaml(root / "config" / "validation_rules.yml", {"rules": []})
    write_yaml(root / "config" / "model_config.yml", {})


def _write_target_documents(root: Path) -> None:
    write_table(
        root / "data" / "intermediate" / "target_documents.csv",
        [
            {
                "company_year_id": "A_2024",
                "operating_company_id": "A",
                "fiscal_year": "2024",
                "docID": "DOC001",
                "resolution_status": "resolved",
            }
        ],
    )


def _write_csv_zip(root: Path, doc_id: str = "DOC001", net_sales: str = "1,234") -> None:
    rows = [
        ["jpcrp_cor:NetSales", "売上高", "CurrentYearDuration", "当期", "連結", "期間", "JPY", "百万円", net_sales],
        [
            "jpcrp_cor:NetSalesTextBlock",
            "売上高 [テキストブロック]",
            "CurrentYearDuration",
            "当期",
            "連結",
            "期間",
            "－",
            "－",
            "売上高は1,234百万円です。",
        ],
        [
            "jpcrp_cor:AverageAnnualSalary",
            "平均年間給与",
            "CurrentYearDuration_NonConsolidatedMember",
            "当期",
            "個別",
            "期間",
            "JPY",
            "千円",
            "8,885",
        ],
        ["jpcrp_cor:PriorYearNetSales", "前期売上高", "PriorYearDuration", "前期", "連結", "期間", "JPY", "百万円", "900"],
    ]
    headers = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別", "期間・時点", "ユニットID", "単位", "値"]
    text = "\n".join(["\t".join(headers)] + ["\t".join(row) for row in rows])
    zip_path = root / "data" / "raw" / "documents" / doc_id / "csv.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("XBRL_TO_CSV/sample.csv", text.encode("utf-16"))


def _write_semiannual_config(root: Path) -> None:
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
                "company_year_id": "A_2024H1",
                "fiscal_year": "2024",
                "fiscal_year_end": "2025-03-31",
                "operating_company_id": "A",
                "reporting_entity_id": "A",
                "parent_group_id_at_year_end": "A",
                "current_parent_group_id": "A",
                "data_scope_allowed": "consolidated;standalone",
                "transition_year_flag": "false",
                "analysis_treatment": "semiannual_h1_pilot",
                "period_type": "semiannual_h1",
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
                "period_type": "current_year",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "NetSales",
                "context_filters": "CurrentYearDuration;ConsolidatedMember",
            },
            {
                "field_id": "total_assets_consolidated",
                "field_name_ja": "総資産_連結",
                "category": "financial_position",
                "target_unit": "百万円",
                "data_scope_required": "consolidated",
                "period_type": "period_end",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "TotalAssets",
                "context_filters": "CurrentYearInstant;ConsolidatedMember",
            },
        ],
    )
    write_yaml(root / "config" / "document_filter.yml", {})
    write_yaml(root / "config" / "extraction_sections.yml", {})
    write_yaml(root / "config" / "validation_rules.yml", {"rules": []})
    write_yaml(root / "config" / "model_config.yml", {})


def _write_semiannual_target_documents(root: Path) -> None:
    write_table(
        root / "data" / "intermediate" / "target_documents.csv",
        [
            {
                "company_year_id": "A_2024H1",
                "operating_company_id": "A",
                "fiscal_year": "2024",
                "docID": "DOC-H1",
                "resolution_status": "resolved",
                "period_type": "semiannual_h1",
            }
        ],
    )


def _write_semiannual_csv_zip(root: Path) -> None:
    rows = [
        ["jpcrp_cor:NetSales", "売上高", "Prior1YTDDuration", "前年度同四半期累計期間", "連結", "期間", "JPY", "百万円", "100"],
        ["jpcrp_cor:NetSales", "売上高", "CurrentYTDDuration", "当四半期累計期間", "連結", "期間", "JPY", "百万円", "200"],
        ["jpcrp_cor:TotalAssets", "総資産", "Prior1YearInstant", "前期末", "連結", "時点", "JPY", "百万円", "800"],
        ["jpcrp_cor:TotalAssets", "総資産", "CurrentQuarterInstant", "当四半期会計期間末", "連結", "時点", "JPY", "百万円", "900"],
    ]
    headers = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別", "期間・時点", "ユニットID", "単位", "値"]
    text = "\n".join(["\t".join(headers)] + ["\t".join(row) for row in rows])
    zip_path = root / "data" / "raw" / "documents" / "DOC-H1" / "csv.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("XBRL_TO_CSV/sample.csv", text.encode("utf-16"))


if __name__ == "__main__":
    unittest.main()
