import json
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.ai_runner import AiCallResult, FakeAiRunner
from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.services import company_factbooks, factbook_ai, factbook_parsers


class CompanyFactbookTests(unittest.TestCase):
    def test_kajima_q_order_text_parser_extracts_annual_business_scope_orders(self):
        source = {
            "company_id": "KAJIMA",
            "company_name": "鹿島建設",
            "source_doc_type": "q_order",
            "source_dataset_id": "kajima_q_order",
            "fiscal_year": "2024",
            "period_label": "2025年3月期",
            "url": "https://example.com/quarterly-20244q-j.pdf",
        }
        texts = [
            "単体 四半期別受注額の推移（土木）\n24年度 累計 4,388 -2.1 2,512 -14.9 2,195 -18.9 317 29.2 1,479 -2.8 180 10.2 1,298 -4.4 397 -",
            "単体 四半期別受注額の推移（建築）\n24年度 累計 13,346 -1.8 136 -78.2 35 -78.6 101 -78.1 13,210 1.9 4,366 -13.1 8,843 11.4 0 -",
        ]

        rows = factbook_parsers._parse_kajima_q_order_texts(texts, source, Path("quarterly-20244q-j.pdf"), "now")

        by_category = {row["use_category_normalized"]: row for row in rows}
        self.assertEqual(len(rows), 6)
        self.assertEqual(by_category["building"]["amount_million_yen"], 1334600)
        self.assertEqual(by_category["domestic_building"]["order_amount"], 13346)
        self.assertEqual(by_category["civil"]["amount_million_yen"], 438800)
        self.assertEqual(by_category["domestic_civil"]["order_amount"], 3991)
        self.assertEqual(by_category["overseas_civil"]["order_amount"], 397)
        self.assertEqual(by_category["overseas_building"]["order_amount"], 0)
        self.assertEqual(by_category["overseas_building"]["amount_million_yen"], 0)
        self.assertEqual(by_category["building"]["source_metric_id"], "building_orders_by_business_scope")

    def test_obayashi_results_reference_text_parser_extracts_building_use_orders(self):
        source = {
            "company_id": "OBAYASHI",
            "company_name": "大林組",
            "source_doc_type": "results_reference",
            "source_dataset_id": "obayashi_results_reference",
            "fiscal_year": "2024",
            "period_label": "2025年3月期",
            "url": "https://example.com/20250513kessan3.pdf",
        }
        text = """
３ 建設事業の工事種類別の内訳（個別）
（１）受注高
建 築建 築 （単位：百万円）
事 務 所 ・ 庁 舎 461,573 39.6% 632,622 53.2% 347,296 31.2% 384,144 32.0% 515,724 34.0% 131,579 34.3%
宿 泊 施 設 38,115 3.3 16,486 1.4 32,858 3.0 22,696 1.9 78,826 5.2 56,130 247.3
店 舗 56,706 4.9 47,349 4.0 19,613 1.8 16,336 1.4 119,330 7.9 102,994 630.5
工 場 ・ 発 電 所 203,579 17.5 167,259 14.1 296,003 26.6 364,353 30.4 410,871 27.1 46,518 12.8
倉庫・流通施設 57,678 5.0 66,143 5.6 64,141 5.8 50,547 4.2 47,813 3.1 △ 2,733 △ 5.4
住 宅 61,398 5.3 16,633 1.4 94,609 8.5 96,502 8.1 39,626 2.6 △ 56,875 △ 58.9
教育研究文化施設 129,674 11.1 60,506 5.1 116,007 10.4 133,764 11.2 101,293 6.7 △ 32,470 △ 24.3
医療・福祉施設 47,929 4.1 32,893 2.8 62,447 5.6 31,765 2.6 18,717 1.2 △ 13,047 △ 41.1
娯 楽 施 設 20,010 1.7 23,176 1.9 9,796 0.9 25,542 2.1 7,285 0.5 △ 18,256 △ 71.5
そ の 他 87,420 7.5 124,808 10.5 69,236 6.2 72,920 6.1 176,795 11.7 103,874 142.4
土 木 （単位：百万円）
"""

        rows = factbook_parsers._parse_obayashi_results_reference_texts([text], source, Path("20250513kessan3.pdf"), "now")

        by_category = {row["use_category_normalized"]: row for row in rows}
        self.assertEqual(len(rows), 10)
        self.assertEqual(by_category["office"]["amount_million_yen"], 515724)
        self.assertEqual(by_category["factory"]["order_amount"], 410871)
        self.assertEqual(by_category["logistics"]["order_amount"], 47813)
        self.assertEqual(by_category["medical_welfare"]["source_metric_id"], "building_orders_by_use")

    def test_factbook_ai_runner_classifies_ambiguous_table_without_values(self):
        call_results = []
        runner = FakeAiRunner(
            {
                "ambiguous_table": lambda: AiCallResult(
                    call_id="call1",
                    purpose="factbook_table_classification",
                    model="claude-haiku-4-5-20251001",
                    tier="bulk",
                    input_ref="ambiguous_table",
                    prompt="",
                    raw_stdout="{}",
                    result_text='{"accept":"true","metric_id":"building_orders_by_business_scope","category_type":"business_scope","reason":"scope table"}',
                    parsed_result={
                        "accept": "true",
                        "metric_id": "building_orders_by_business_scope",
                        "category_type": "business_scope",
                        "reason": "scope table",
                    },
                )
            }
        )

        decision = factbook_ai.classify_table(
            "国内建築 国内土木 海外建築 海外土木",
            {"source_dataset_id": "test"},
            runner=runner,
            model="claude-haiku-4-5-20251001",
            input_ref="ambiguous_table",
            call_results=call_results,
        )

        self.assertEqual(decision["accept"], "true")
        self.assertEqual(decision["metric_id"], "building_orders_by_business_scope")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(len(call_results), 1)

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

    def test_refresh_replaces_existing_rows_for_target_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_config(root, parser="link_index_parse_documents")
            output = root / "data" / "marts" / "company_factbooks" / "building_orders_by_category.csv"
            output.parent.mkdir(parents=True, exist_ok=True)
            write_table(
                output,
                [
                    {
                        "company_id": "TEST",
                        "fiscal_year": "2024",
                        "period_type": "annual",
                        "source_dataset_id": "test_factbook_document",
                        "source_metric_id": "building_orders_by_use",
                        "category_type": "use",
                        "use_category_raw": "古い行",
                        "order_amount": "999",
                    }
                ],
            )

            result = company_factbooks.refresh_company_factbooks(
                root,
                fetcher=_fake_fetcher,
                source_ids=["test_factbook_document"],
            )

            self.assertEqual(result["status"], "succeeded")
            rows = read_table(output)
            self.assertEqual(len(rows), 2)
            self.assertNotIn("古い行", {row["use_category_raw"] for row in rows})

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

    def test_coverage_counts_business_scope_orders_as_building_order_coverage(self):
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
                        "source_metric_id": "building_orders_by_business_scope",
                        "category_type": "business_scope",
                        "use_category_raw": "国内建築",
                        "use_category_normalized": "domestic_building",
                        "order_amount": "1200",
                        "unit": "億円",
                        "amount_million_yen": "120000",
                        "extraction_status": "parsed",
                    }
                ],
            )

            coverage = company_factbooks.build_factbook_target_coverage(root)
            rows = read_table(root / "data" / "reports" / "company_factbook_target_coverage.csv")

            self.assertEqual(coverage["status_counts"]["parsed"], 1)
            target = next(row for row in rows if row["target_metric_id"] == "building_orders_by_use")
            self.assertEqual(target["coverage_status"], "parsed")


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
