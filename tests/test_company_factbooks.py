import json
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.services import company_factbooks


class CompanyFactbookTests(unittest.TestCase):
    def test_refresh_parses_irpocket_json_chart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root, parser="irpocket_json_chart")

            result = company_factbooks.refresh_company_factbooks(root, fetcher=_fake_fetcher)

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["new_order_rows"], 4)
            rows = read_table(root / "data" / "marts" / "company_factbooks" / "building_orders_by_category.csv")
            self.assertEqual(len(rows), 4)
            rows_json = read_table(root / "data" / "marts" / "company_factbooks" / "building_orders_by_category.json")
            self.assertEqual(len(rows_json), 4)
            self.assertEqual(rows[0]["company_id"], "TEST")
            self.assertEqual(rows[0]["fiscal_year"], "2024")
            self.assertEqual(rows[0]["use_category_normalized"], "factory")
            self.assertEqual(rows[0]["amount_million_yen"], "12000")
            self.assertTrue((root / "data" / "automation" / "company_factbooks_last.json").exists())

            options = company_factbooks.factbook_options(root)
            field_ids = [field["id"] for field in options["fields"]]
            self.assertIn("order_amount__use__factory", field_ids)

            chart = company_factbooks.read_factbook_chart_data(root, companies=["TEST"], fields=["order_amount__use__factory"])
            self.assertEqual(chart["total"], 2)
            self.assertEqual(chart["rows"][0]["order_amount__use__factory"], 120.0)

    def test_refresh_discovers_source_documents_from_index_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root, parser="link_index")

            result = company_factbooks.refresh_company_factbooks(root, fetcher=_fake_fetcher)

            self.assertEqual(result["status"], "succeeded")
            documents = read_table(root / "data" / "marts" / "company_factbooks" / "source_documents.csv")
            documents_json = read_table(root / "data" / "marts" / "company_factbooks" / "source_documents.json")
            self.assertEqual(len(documents), 1)
            self.assertEqual(len(documents_json), 1)
            self.assertEqual(documents[0]["company_id"], "TEST")
            self.assertEqual(documents[0]["fiscal_year"], "2025")
            self.assertEqual(documents[0]["file_ext"], "pdf")
            self.assertEqual(documents[0]["parser_status"], "pending_parser")

    def test_refresh_parses_downloaded_factbook_document_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root, parser="link_index_parse_documents")

            result = company_factbooks.refresh_company_factbooks(root, fetcher=_fake_fetcher)

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["new_order_rows"], 2)
            rows = read_table(root / "data" / "marts" / "company_factbooks" / "building_orders_by_category.csv")
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["source_metric_id"], "building_orders_by_use")
            self.assertEqual(rows[0]["use_category_normalized"], "factory")
            self.assertEqual(rows[0]["amount_million_yen"], "12000")
            documents = read_table(root / "data" / "marts" / "company_factbooks" / "source_documents.csv")
            self.assertEqual(documents[0]["parser_status"], "parsed")

    def test_refresh_keeps_source_page_when_index_has_no_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root, parser="link_index_empty")

            result = company_factbooks.refresh_company_factbooks(root, fetcher=_fake_fetcher)

            self.assertEqual(result["status"], "succeeded")
            documents = read_table(root / "data" / "marts" / "company_factbooks" / "source_documents.csv")
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["company_id"], "TEST")
            self.assertEqual(documents[0]["url"], "https://example.com/empty.html")
            self.assertEqual(documents[0]["file_ext"], "html")
            self.assertEqual(documents[0]["parser_status"], "pending_parser")

    def test_refresh_expands_company_document_sources_and_follows_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root, parser="company_document_sources")

            result = company_factbooks.refresh_company_factbooks(root, fetcher=_fake_fetcher)

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["sources"], 1)
            status = company_factbooks.factbook_status(root)
            self.assertEqual(status["source_count"], 1)
            self.assertEqual(status["enabled_source_count"], 1)
            documents = read_table(root / "data" / "marts" / "company_factbooks" / "source_documents.csv")
            self.assertEqual(len(documents), 1)
            self.assertEqual(documents[0]["company_id"], "TEST")
            self.assertEqual(documents[0]["source_dataset_id"], "test_factbook")
            self.assertEqual(documents[0]["file_ext"], "pdf")
            self.assertEqual(documents[0]["fiscal_year"], "2024")
            self.assertEqual(documents[0]["target_metric_ids"], "building_orders_by_use;completed_building_by_use")

            coverage = company_factbooks.build_factbook_target_coverage(root)
            self.assertEqual(coverage["rows"], 2)
            self.assertTrue(Path(coverage["json_output_path"]).exists())
            self.assertEqual(coverage["status_counts"]["candidate_documents"], 2)
            coverage_rows = read_table(root / "data" / "reports" / "company_factbook_target_coverage.csv")
            coverage_json_rows = read_table(root / "data" / "reports" / "company_factbook_target_coverage.json")
            self.assertEqual({row["target_metric_id"] for row in coverage_rows}, {"building_orders_by_use", "completed_building_by_use"})
            self.assertEqual(len(coverage_json_rows), 2)

    def test_refresh_imports_manual_csv_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root, parser="none")
            write_table(
                root / "data" / "raw" / "company_factbooks" / "manual" / "orders.csv",
                [
                    {
                        "company_id": "TEST",
                        "company_name": "テスト建設",
                        "fiscal_year": "2024",
                        "category": "医療施設",
                        "order_amount": "50",
                        "unit": "億円",
                    }
                ],
            )

            result = company_factbooks.refresh_company_factbooks(root, fetcher=_fake_fetcher)

            self.assertEqual(result["status"], "succeeded")
            rows = read_table(root / "data" / "marts" / "company_factbooks" / "building_orders_by_category.csv")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["use_category_normalized"], "medical_welfare")
            self.assertEqual(rows[0]["amount_million_yen"], "5000")

    def test_validate_factbook_against_yuho_flags_pass_and_missing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root, parser="none")
            write_table(
                root / "data" / "marts" / "company_factbooks" / "building_orders_by_category.csv",
                [
                    {
                        "company_id": "TEST",
                        "company_name": "テスト建設",
                        "fiscal_year": "2024",
                        "period_type": "annual",
                        "period_label": "2025年3月期",
                        "source_dataset_id": "test_orders",
                        "category_type": "business_scope",
                        "use_category_raw": "国内建築",
                        "use_category_normalized": "domestic_building",
                        "use_category_label": "国内建築",
                        "order_amount": "1200",
                        "unit": "億円",
                        "amount_million_yen": "120000",
                        "source_url": "https://example.com/factbook.pdf",
                        "source_quote": "国内建築 1200億円",
                        "extraction_status": "parsed",
                    },
                    {
                        "company_id": "TEST",
                        "company_name": "テスト建設",
                        "fiscal_year": "2024",
                        "period_type": "annual",
                        "period_label": "2025年3月期",
                        "source_dataset_id": "test_orders",
                        "category_type": "business_scope",
                        "use_category_raw": "開発事業等",
                        "use_category_normalized": "development_other",
                        "use_category_label": "開発事業等",
                        "order_amount": "50",
                        "unit": "億円",
                        "amount_million_yen": "5000",
                        "extraction_status": "parsed",
                    },
                ],
            )
            write_table(
                root / "data" / "final" / "final_master_wide.csv",
                [
                    {
                        "company_year_id": "TEST_2024",
                        "operating_company_id": "TEST",
                        "fiscal_year": "2024",
                        "segment_orders_domestic_building": "120050",
                    }
                ],
            )
            write_table(
                root / "config" / "field_definition.csv",
                [{"field_id": "segment_orders_domestic_building", "field_name_ja": "セグメント受注高_国内建築"}],
            )

            result = company_factbooks.validate_factbook_against_yuho(root)

            self.assertEqual(result["status"], "incomplete")
            self.assertTrue(Path(result["json_output_path"]).exists())
            self.assertEqual(result["comparable_rows"], 1)
            self.assertEqual(result["incomplete_rows"], 1)
            self.assertEqual(result["status_counts"]["pass"], 1)
            self.assertEqual(result["status_counts"]["no_mapping"], 1)
            self.assertEqual(result["pending_rows"], 1)
            rows = read_table(root / "data" / "reports" / "company_factbook_yuho_validation.csv")
            json_rows = read_table(root / "data" / "reports" / "company_factbook_yuho_validation.json")
            pending_rows = read_table(root / "data" / "reports" / "company_factbook_pending_rows.csv")
            self.assertEqual(rows[0]["validation_status"], "pass")
            self.assertEqual(rows[1]["validation_status"], "no_mapping")
            self.assertEqual(len(json_rows), 2)
            self.assertEqual(len(pending_rows), 1)


def _write_config(root: Path, parser: str) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    write_table(
        root / "config" / "company_master.csv",
        [{"operating_company_id": "TEST", "operating_company_name": "テスト建設"}],
    )
    sources = []
    if parser == "irpocket_json_chart":
        sources.append(
            {
                "id": "test_irpocket",
                "company_id": "TEST",
                "company_name": "テスト建設",
                "enabled": True,
                "parser": "irpocket_json_chart",
                "source_doc_type": "data_book",
                "source_dataset_id": "test_orders",
                "source_metric_id": "building_orders_by_use",
                "category_type": "use",
                "source_table_title": "用途別受注",
                "data_url": "https://example.com/data.json",
                "style_url": "https://example.com/style.json",
                "scope": "standalone",
                "business_scope": "building_orders",
                "unit": "億円",
            }
        )
    elif parser == "link_index":
        sources.append(
            {
                "id": "test_index",
                "company_id": "TEST",
                "company_name": "テスト建設",
                "enabled": True,
                "parser": "link_index",
                "source_doc_type": "q_order",
                "source_dataset_id": "test_documents",
                "source_metric_id": "building_orders_by_use",
                "category_type": "use",
                "source_page_url": "https://example.com/index.html",
                "href_includes": [".pdf"],
            }
        )
    elif parser == "link_index_empty":
        sources.append(
            {
                "id": "test_empty_index",
                "company_id": "TEST",
                "company_name": "テスト建設",
                "enabled": True,
                "parser": "link_index",
                "source_doc_type": "results_reference",
                "source_dataset_id": "test_empty_documents",
                "source_metric_id": "major_indicators",
                "category_type": "major_metrics",
                "source_page_url": "https://example.com/empty.html",
                "link_text_includes": ["決算説明"],
                "href_includes": [".pdf"],
            }
        )
    elif parser == "link_index_parse_documents":
        sources.append(
            {
                "id": "test_index_parse",
                "company_id": "TEST",
                "company_name": "テスト建設",
                "enabled": True,
                "parser": "link_index",
                "parse_documents": True,
                "source_doc_type": "factbook",
                "source_dataset_id": "test_factbook_document",
                "source_metric_id": "building_use_metrics",
                "category_type": "use",
                "source_page_url": "https://example.com/factbook-index.html",
                "href_includes": [".csv"],
                "unit": "億円",
            }
        )
    company_document_sources = []
    document_source_defaults = {}
    if parser == "company_document_sources":
        document_source_defaults = {
            "parser": "link_index",
            "follow_links": True,
            "link_text_includes": ["FACT BOOK", "ファクト"],
            "href_includes": [".pdf"],
            "follow_link_text_includes": ["ファクトブック"],
            "follow_href_includes": ["factbook"],
        }
        company_document_sources.append(
            {
                "id": "test_company_factbook",
                "company_id": "TEST",
                "company_name": "テスト建設",
                "source_page_url": "https://example.com/ir.html",
                "source_dataset_id": "test_factbook",
                "source_doc_type": "factbook",
            }
        )
    config = {
        "enabled": True,
        "canonical_store": "data/marts/company_factbooks/building_orders_by_category.csv",
        "source_document_store": "data/marts/company_factbooks/source_documents.csv",
        "raw_store": "data/raw/company_factbooks",
        "manual_csv_globs": ["data/raw/company_factbooks/manual/*.csv"],
        "document_source_defaults": document_source_defaults,
        "company_document_sources": company_document_sources,
        "category_labels": {
            "factory": "工場・倉庫",
            "medical_welfare": "医療・福祉",
        },
        "category_keyword_map": {
            "factory": ["工場"],
            "medical_welfare": ["医療"],
        },
        "sources": sources,
    }
    (root / "config" / "company_factbook_sources.yml").write_text(
        json.dumps(config, ensure_ascii=False),
        encoding="utf-8",
    )


def _fake_fetcher(url: str, cfg: dict) -> str:
    if url.endswith("data.json"):
        return json.dumps(
            {
                "categories": ["20250304", {"categories": ["20260304"], "name": "latest"}],
                "series": [
                    {"name": "1Q", "data": [{"y": 120}, {"y": 130}]},
                    {"name": "2Q", "data": [{"y": 45}, {"y": 55}]},
                ],
            }
        )
    if url.endswith("style.json"):
        return json.dumps(
            {
                "series": [
                    {"name": "工場"},
                    {"name": "医療施設"},
                ]
            },
            ensure_ascii=False,
        )
    if url.endswith("factbook-index.html"):
        return '<h2>2025年3月期 ファクトブック</h2><a href="/factbook-orders-2025.csv">用途別受注高CSV</a>'
    if url.endswith("factbook-orders-2025.csv"):
        return "用途,2025年3月期\n工場,120\n医療施設,45\n"
    if url.endswith("index.html"):
        return '<h2>2026年3月期</h2><a href="/orders-2026.pdf">第4四半期</a>'
    if url.endswith("empty.html"):
        return '<h1>IR資料</h1><a href="/news/">ニュース</a>'
    if url.endswith("ir.html"):
        return '<h1>IR資料</h1><a href="/factbook.html">ファクトブック</a>'
    if url.endswith("factbook.html"):
        return '<h2>FACT BOOK 2025年3月期</h2><a href="/factbook-2025.pdf">PDF</a>'
    raise AssertionError(f"unexpected url {url}")
