import unittest

from yuho_auto_extract.local_table_extractor import extract_local_table_rows, _table_amount_unit


class LocalTableExtractorTests(unittest.TestCase):
    def test_review_derived_zero_value_marker_extracts_zero(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "MATSUI_2024",
            "operating_company_id": "MATSUI",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_rd_expense",
            "unit_hint": "百万円",
            "scope_hint": "consolidated",
            "locator_score": 10,
            "heading_text": "研究開発活動",
            "target_fields": ["rd_expense"],
            "review_row_labels": ["特記事項なし"],
            "review_row_labels_by_field": {"rd_expense": ["特記事項なし"]},
            "review_units_by_field": {"rd_expense": "百万円"},
            "raw_text": "\n".join(
                [
                    "6 【研究開発活動】",
                    "特記事項なし",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field_id"], "rd_expense")
        self.assertEqual(rows[0]["value"], 0.0)
        self.assertEqual(rows[0]["data_scope"], "consolidated")

    def test_extracts_current_building_orders_backlog_row(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注高、売上高、繰越高",
            "raw_text": "\n".join(
                [
                    "受注高、売上高、繰越高及び施工高",
                    "前期",
                    "繰越高",
                    "当期",
                    "受注高",
                    "当期",
                    "売上高",
                    "次期繰越高",
                    "当事業年度",
                    "建築事業",
                    "236,637",
                    "235,190",
                    "471,827",
                    "206,886",
                    "264,941",
                    "2.6",
                    "6,763",
                    "208,243",
                    "土木事業",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["building_orders_total"], 235190.0)
        self.assertEqual(by_field["completed_building"], 206886.0)
        self.assertEqual(by_field["backlog_building_next"], 264941.0)
        self.assertFalse(rows[0]["review_required"])

    def test_extracts_latest_period_without_current_year_label(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注高、売上高、繰越高",
            "raw_text": "\n".join(
                [
                    "受注高、売上高及び繰越高",
                    "期別",
                    "種類別",
                    "前期繰越高",
                    "当期受注高",
                    "当期売上高",
                    "次期繰越高",
                    "第120期",
                    "建設事業",
                    "建築",
                    "1,706,732",
                    "1,516,284",
                    "3,223,016",
                    "1,297,716",
                    "1,925,300",
                    "② 受注工事高",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["building_orders_total"], 1516284.0)
        self.assertEqual(by_field["completed_building"], 1297716.0)
        self.assertEqual(by_field["backlog_building_next"], 1925300.0)

    def test_segment_order_rows_keep_source_segment_label(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注実績",
            "raw_text": "\n".join(
                [
                    "受注実績",
                    "セグメントの名称",
                    "前連結会計年度",
                    "当連結会計年度",
                    "建築事業 1,000 1,200",
                    "土木事業 500 600",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["segment_orders_building"]["source_segment_label"], "建築事業")
        self.assertEqual(by_field["segment_orders_building"]["normalized_segment_key"], "building")
        self.assertEqual(by_field["segment_orders_building"]["segment_taxonomy_status"], "common")

    def test_review_hint_section_extracts_employee_average_age_and_salary(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_average_age",
            "unit_hint": "円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "従業員の状況",
            "target_fields": ["average_age", "average_salary"],
            "table_keywords": ["提出会社の状況", "平均年齢", "平均年間給与"],
            "raw_text": "\n".join(
                [
                    "従業員の状況",
                    "提出会社の状況",
                    "従業員数（人）",
                    "平均年齢（歳）",
                    "平均勤続年数（年）",
                    "平均年間給与（円）",
                    "2,857",
                    "［397］",
                    "43.9",
                    "18.6",
                    "8,088,242",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["average_age"]["value"], 43.9)
        self.assertEqual(by_field["average_age"]["unit_raw"], "歳")
        self.assertEqual(by_field["average_age"]["data_scope"], "standalone")
        self.assertGreaterEqual(by_field["average_age"]["confidence"], 0.9)
        self.assertEqual(by_field["average_salary"]["value"], 8088242.0)
        self.assertEqual(by_field["average_salary"]["unit_raw"], "円")
        self.assertFalse(by_field["average_salary"]["review_required"])

    def test_review_hint_section_maps_single_target_salary_by_full_header_order(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_average_salary",
            "unit_hint": "千円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "従業員の状況",
            "target_fields": ["average_salary"],
            "table_keywords": ["提出会社の状況", "平均年間給与"],
            "raw_text": "\n".join(
                [
                    "提出会社の状況",
                    "従業員数（人） 平均年齢（歳） 平均勤続年数（年） 平均年間給与（千円）",
                    "2,857 ［397］ 43.9 18.6 8,885",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field_id"], "average_salary")
        self.assertEqual(rows[0]["value"], 8885.0)
        self.assertEqual(rows[0]["unit_raw"], "千円")

    def test_review_hint_section_extracts_generic_review_learned_row_label(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_rd_expense",
            "unit_hint": "百万円",
            "scope_hint": "consolidated",
            "locator_score": 10,
            "heading_text": "研究開発活動",
            "heading_keywords": ["研究開発活動"],
            "table_keywords": ["研究開発費"],
            "target_fields": ["rd_expense"],
            "review_row_labels_by_field": {"rd_expense": ["研究開発費"]},
            "review_units_by_field": {"rd_expense": "百万円"},
            "raw_text": "\n".join(
                [
                    "研究開発活動",
                    "前連結会計年度 当連結会計年度",
                    "研究開発費 1,234 1,567",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field_id"], "rd_expense")
        self.assertEqual(rows[0]["value"], 1567.0)
        self.assertEqual(rows[0]["unit_raw"], "百万円")
        self.assertEqual(rows[0]["data_scope"], "consolidated")
        self.assertFalse(rows[0]["review_required"])

    def test_review_hint_rd_expense_scope_uses_value_context_before_block_hint(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_rd_expense",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "研究開発活動",
            "heading_keywords": ["研究開発活動"],
            "table_keywords": ["研究開発費"],
            "target_fields": ["rd_expense"],
            "review_row_labels_by_field": {"rd_expense": ["研究開発費"]},
            "review_units_by_field": {"rd_expense": "百万円"},
            "raw_text": "\n".join(
                [
                    "研究開発活動】 当社グループは技術開発を推進しております。",
                    "当連結会計年度 研究開発費 383 百万円",
                    "なお、提出会社においても研究開発活動を行っております。",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 383.0)
        self.assertEqual(rows[0]["data_scope"], "consolidated")

    def test_review_hint_rd_expense_unit_uses_value_context_before_hint(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "KAJIMA_2024",
            "operating_company_id": "KAJIMA",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_rd_expense",
            "unit_hint": "億円",
            "scope_hint": "consolidated",
            "locator_score": 10,
            "heading_text": "研究開発活動",
            "heading_keywords": ["研究開発活動"],
            "table_keywords": ["研究開発費"],
            "target_fields": ["rd_expense"],
            "review_row_labels_by_field": {"rd_expense": ["研究開発費"]},
            "review_units_by_field": {"rd_expense": "億円"},
            "raw_text": "\n".join(
                [
                    "研究開発活動",
                    "投資額は総額2,500億円である。",
                    "当連結会計年度における研究開発費は 22,207 百万円である。",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company_year_id"], "KAJIMA_2024")
        self.assertEqual(rows[0]["value"], 22207.0)
        self.assertEqual(rows[0]["unit_raw"], "百万円")

    def test_table_amount_unit_prefers_local_value_context(self):
        segment = "受注実績 単位:億円 建築事業 1,000 2,000 百万円"
        self.assertEqual(_table_amount_unit(segment, {"unit_hint": "億円"}, "建築事業 1,000 2,000 百万円"), "百万円")

    def test_review_hint_employee_scope_prefers_submitting_company_context(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_average_age",
            "unit_hint": "人",
            "scope_hint": "consolidated",
            "locator_score": 10,
            "heading_text": "従業員の状況",
            "heading_keywords": ["従業員の状況"],
            "table_keywords": ["提出会社の状況", "平均年齢"],
            "target_fields": ["average_age"],
            "review_row_labels_by_field": {"average_age": ["平均年齢"]},
            "review_units_by_field": {"average_age": "歳"},
            "raw_text": "\n".join(
                [
                    "提出会社の状況 2025年3月31日 現在",
                    "従業員数(人) 平均年齢(歳) 平均勤続年数(年)",
                    "102 [ 1 ] 42.8 14.2",
                    "(参考)主要な連結子会社の状況",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 42.8)
        self.assertEqual(rows[0]["data_scope"], "standalone")

    def test_review_hint_section_extracts_generic_amount_from_amount_ratio_pairs(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_advertising_expense",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "販売費及び一般管理費",
            "target_fields": ["advertising_expense"],
            "review_row_labels": ["広告宣伝費"],
            "review_units_by_field": {"advertising_expense": "百万円"},
            "raw_text": "販売費及び一般管理費 前事業年度 金額 構成比 当事業年度 金額 構成比 広告宣伝費 4,000 2.5 5,100 3.0",
        }

        rows = extract_local_table_rows([block])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field_id"], "advertising_expense")
        self.assertEqual(rows[0]["value"], 5100.0)

    def test_review_hint_section_ignores_inline_temporary_employee_count(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_average_salary",
            "unit_hint": "円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "従業員の状況",
            "target_fields": ["average_age", "average_salary"],
            "table_keywords": ["提出会社の状況", "平均年齢", "平均年間給与"],
            "raw_text": "\n".join(
                [
                    "提出会社の状況",
                    "従業員数（人）",
                    "平均年齢（歳）",
                    "平均勤続年数（年）",
                    "平均年間給与（円）",
                    "7,527〔1,746〕",
                    "43.7",
                    "18.3",
                    "8,928,794",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["average_age"]["value"], 43.7)
        self.assertEqual(by_field["average_salary"]["value"], 8928794.0)

    def test_review_hint_section_ignores_multiline_temporary_employee_count(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_average_salary",
            "unit_hint": "円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "従業員の状況",
            "target_fields": ["average_age", "average_salary"],
            "table_keywords": ["提出会社の状況", "平均年齢", "平均年間給与"],
            "raw_text": "\n".join(
                [
                    "提出会社の状況",
                    "従業員数（人）",
                    "平均年齢（歳）",
                    "平均勤続年数（年）",
                    "平均年間給与（円）",
                    "8,501",
                    "〔",
                    "1,118",
                    "〕",
                    "43.0",
                    "18.3",
                    "9,872,883",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["average_age"]["value"], 43.0)
        self.assertEqual(by_field["average_salary"]["value"], 9872883.0)

    def test_review_hint_section_accepts_old_average_age_label(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "review_average_age",
            "unit_hint": "円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "従業員の状況",
            "target_fields": ["average_age"],
            "table_keywords": ["提出会社の状況", "平均年令"],
            "raw_text": "\n".join(
                [
                    "提出会社の状況",
                    "従業員数（人）",
                    "平均年令（才）",
                    "平均勤続年数（年）",
                    "平均年間給与（円）",
                    "8,501〔1,118〕",
                    "43.0",
                    "18.3",
                    "9,872,883",
                ]
            ),
        }

        rows = extract_local_table_rows([block])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field_id"], "average_age")
        self.assertEqual(rows[0]["value"], 43.0)

    def test_extracts_orders_table_labeled_completed_work(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "TOKYU_2015",
            "operating_company_id": "TOKYU",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高、完成工事高及び次期繰越工事高",
            "raw_text": "\n".join(
                [
                    "① 受注工事高、完成工事高及び次期繰越工事高",
                    "期別",
                    "区分",
                    "前期繰越工事高",
                    "当期受注工事高",
                    "計",
                    "当期完成工事高",
                    "次期繰越工事高",
                    "前事業年度",
                    "建築工事",
                    "173,034",
                    "239,925",
                    "412,959",
                    "190,082",
                    "222,877",
                    "当事業年度",
                    "建築工事",
                    "222,877",
                    "227,259",
                    "450,136",
                    "221,870",
                    "228,266",
                    "土木工事",
                    "② 受注工事高の受注方法別比率",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["building_orders_total"], 227259.0)
        self.assertEqual(by_field["completed_building"], 221870.0)
        self.assertEqual(by_field["backlog_building_next"], 228266.0)

    def test_uses_positive_corrected_backlog_after_parenthesized_reference_value(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "KUMAGAI_2015",
            "operating_company_id": "KUMAGAI",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高、完成工事高及び次期繰越工事高",
            "raw_text": "\n".join(
                [
                    "(1) 受注工事高、完成工事高及び次期繰越工事高",
                    "期別",
                    "区分",
                    "前期繰越工事高",
                    "当期受注工事高",
                    "計",
                    "当期完成工事高",
                    "次期繰越工事高",
                    "当事業年度",
                    "建築工事",
                    "179,592",
                    "184,094",
                    "363,687",
                    "177,391",
                    "(186,295)",
                    "186,304",
                    "土木工事",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["building_orders_total"], 184094.0)
        self.assertEqual(by_field["completed_building"], 177391.0)
        self.assertEqual(by_field["backlog_building_next"], 186304.0)

    def test_parses_amount_with_space_after_comma(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "KUMAGAI_2018",
            "operating_company_id": "KUMAGAI",
            "fiscal_year": 2018,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高、完成工事高及び次期繰越工事高",
            "raw_text": "\n".join(
                [
                    "受注工事高、完成工事高及び次期繰越工事高",
                    "当事業年度",
                    "前期繰越工事高",
                    "（千円）",
                    "当期受注工事高",
                    "（千円）",
                    "当期完成工事高",
                    "（千円）",
                    "次期繰越工事高",
                    "（千円）",
                    "建築工事",
                    "249, 211",
                    "298,255",
                    "547,467",
                    "195,432",
                    "(352,034)",
                    "352,041",
                    "計",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["building_orders_total"]["value"], 298255.0)
        self.assertEqual(by_field["completed_building"]["value"], 195432.0)
        self.assertEqual(by_field["backlog_building_next"]["value"], 352041.0)
        self.assertEqual(by_field["building_orders_total"]["unit_normalized"], "千円")

    def test_orders_backlog_stops_before_numbered_order_breakdown_with_extra_spaces(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "SMCC_2015",
            "operating_company_id": "SMCC",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高、完成工事高及び次期繰越工事高",
            "raw_text": "\n".join(
                [
                    "① 受注工事高、完成工事高及び次期繰越工事高",
                    "期別",
                    "区分",
                    "前期繰越工事高",
                    "当期受注工事高",
                    "計",
                    "当期完成工事高",
                    "次期繰越工事高",
                    "当事業年度",
                    "建築工事",
                    "201,078",
                    "229,418",
                    "430,497",
                    "197,651",
                    "232,845",
                    "計",
                    "2  受注工事高",
                    "国内",
                    "海外",
                    "計",
                    "官公庁",
                    "民間",
                    "当事業年度",
                    "建築工事",
                    "12,113",
                    "208,187",
                    "9,117",
                    "4.0",
                    "229,418",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["building_orders_total"], 229418.0)
        self.assertEqual(by_field["completed_building"], 197651.0)
        self.assertEqual(by_field["backlog_building_next"], 232845.0)

    def test_extracts_building_order_customer_breakdown(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "KAJIMA_2015",
            "operating_company_id": "KAJIMA",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高",
            "raw_text": "\n".join(
                [
                    "② 受注工事高",
                    "期別",
                    "区分",
                    "国内",
                    "海外",
                    "計",
                    "官公庁 (百万円)",
                    "民間 (百万円)",
                    "(百万円)",
                    "(百万円)",
                    "前事業年度",
                    "建築工事",
                    "70,000",
                    "800,000",
                    "10",
                    "870,010",
                    "土木工事",
                    "当事業年度",
                    "建築工事",
                    "89,967",
                    "812,120",
                    "4",
                    "902,092",
                    "土木工事",
                    "③ 受注工事高の受注方法別比率",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["building_orders_government"]["value"], 89967.0)
        self.assertEqual(by_field["building_orders_private"]["value"], 812120.0)
        self.assertEqual(by_field["building_orders_overseas"]["value"], 4.0)
        self.assertEqual(by_field["building_orders_total"]["value"], 902092.0)
        self.assertEqual(by_field["building_orders_private"]["review_reason"], "local_company_pattern_review_required")

    def test_extracts_negative_overseas_order_value(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高",
            "raw_text": "\n".join(
                [
                    "② 受注工事高",
                    "国内",
                    "海外",
                    "官公庁",
                    "民間",
                    "当事業年度",
                    "建築工事",
                    "10,000",
                    "5,000",
                    "△4,717",
                    "10,283",
                    "土木工事",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["building_orders_overseas"], -4717.0)
        self.assertEqual(by_field["building_orders_total"], 10283.0)

    def test_extracts_breakdown_with_overseas_ratio_column(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "OBAYASHI_2017",
            "operating_company_id": "OBAYASHI",
            "fiscal_year": 2017,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高",
            "raw_text": "\n".join(
                [
                    "2 受注工事高",
                    "官公庁",
                    "民間",
                    "海外",
                    "海外比率",
                    "計",
                    "当事業年度",
                    "建 築",
                    "52,877",
                    "929,497",
                    "13,051",
                    "1.3",
                    "995,425",
                    "土 木",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["building_orders_government"], 52877.0)
        self.assertEqual(by_field["building_orders_private"], 929497.0)
        self.assertEqual(by_field["building_orders_overseas"], 13051.0)
        self.assertEqual(by_field["building_orders_total"], 995425.0)

    def test_prioritizes_breakdown_total_over_later_sales_table(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "OBAYASHI_2017",
            "operating_company_id": "OBAYASHI",
            "fiscal_year": 2017,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注高（契約高）及び売上高の状況",
            "raw_text": "\n".join(
                [
                    "受注高（契約高）及び売上高の状況",
                    "前期繰越高",
                    "当期受注高",
                    "当期売上高",
                    "次期繰越高",
                    "2 受注工事高",
                    "官公庁",
                    "民間",
                    "海外",
                    "海外比率",
                    "計",
                    "第114期",
                    "建 築",
                    "52,877",
                    "929,497",
                    "13,051",
                    "1.3",
                    "995,425",
                    "土 木",
                    "3 売上高",
                    "建 築",
                    "100,721",
                    "1,100,592",
                    "14,522",
                    "1.2",
                    "1,215,835",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["building_orders_total"]["value"], 995425.0)
        self.assertEqual(by_field["building_orders_total"]["review_reason"], "local_company_pattern_review_required")

    def test_treats_dash_overseas_as_zero_when_total_matches(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "KAJIMA_2016",
            "operating_company_id": "KAJIMA",
            "fiscal_year": 2016,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高",
            "raw_text": "\n".join(
                [
                    "2 受注工事高",
                    "国内",
                    "海外",
                    "官公庁",
                    "民間",
                    "当事業年度",
                    "建築工事",
                    "101,054",
                    "839,219",
                    "―",
                    "940,273",
                    "土木工事",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["building_orders_overseas"], 0.0)
        self.assertEqual(by_field["building_orders_total"], 940273.0)

    def test_extracts_completed_building_customer_breakdown_without_overseas(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "MAEDA_2015",
            "operating_company_id": "MAEDA",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "完成工事高",
            "raw_text": "\n".join(
                [
                    "(3）完成工事高",
                    "期別",
                    "区分",
                    "官公庁（百万円）",
                    "民間（百万円）",
                    "計（百万円）",
                    "前事業年度",
                    "建築工事",
                    "33,575",
                    "172,670",
                    "206,246",
                    "土木工事",
                    "当事業年度",
                    "建築工事",
                    "29,075",
                    "177,811",
                    "206,886",
                    "土木工事",
                    "(4）手持工事高",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["completed_building_government"], 29075.0)
        self.assertEqual(by_field["completed_building_private"], 177811.0)
        self.assertNotIn("completed_building_overseas", by_field)

    def test_extracts_backlog_building_customer_breakdown_with_overseas_ratio(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "TAISEI_2015",
            "operating_company_id": "TAISEI",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "手持工事高",
            "raw_text": "\n".join(
                [
                    "(4) 手持工事高",
                    "区分",
                    "国内",
                    "海外",
                    "合計 (Ｂ) (百万円)",
                    "官公庁 (百万円)",
                    "民間 (百万円)",
                    "（Ａ） (百万円)",
                    "(Ａ)／(Ｂ) (％)",
                    "土木工事",
                    "308,042",
                    "216,874",
                    "85,085",
                    "13.9",
                    "610,002",
                    "建築工事",
                    "277,995",
                    "1,069,206",
                    "9,705",
                    "0.7",
                    "1,356,907",
                    "計",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["backlog_building_government"], 277995.0)
        self.assertEqual(by_field["backlog_building_private"], 1069206.0)
        self.assertEqual(by_field["backlog_building_overseas"], 9705.0)

    def test_skips_customer_breakdown_when_total_does_not_match(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "A_2024",
            "operating_company_id": "A",
            "fiscal_year": 2024,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "完成工事高",
            "raw_text": "\n".join(
                [
                    "完成工事高",
                    "官公庁",
                    "民間",
                    "計",
                    "当事業年度",
                    "建築工事",
                    "10,000",
                    "20,000",
                    "31,000",
                    "土木工事",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertNotIn("completed_building_government", by_field)

    def test_extracts_current_building_sales_style_ratio(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "MAEDA_2015",
            "operating_company_id": "MAEDA",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "sales_style_orders",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高の受注方法別比率",
            "raw_text": "\n".join(
                [
                    "受注工事高の受注方法別比率",
                    "工事の受注方法は、特命と競争に大別される。",
                    "期別",
                    "区分",
                    "特命（％）",
                    "競争（％）",
                    "計（％）",
                    "前事業年度",
                    "建築工事",
                    "46.4",
                    "53.6",
                    "100",
                    "土木工事",
                    "44.4",
                    "55.6",
                    "100",
                    "当事業年度",
                    "建築工事",
                    "61.0",
                    "39.0",
                    "100",
                    "土木工事",
                    "41.5",
                    "58.5",
                    "100",
                    "完成工事高",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["building_orders_special_contract_ratio"]["value"], 61.0)
        self.assertEqual(by_field["building_orders_competitive_ratio"]["value"], 39.0)
        self.assertEqual(by_field["building_orders_special_contract_ratio"]["unit_normalized"], "%")
        self.assertFalse(by_field["building_orders_special_contract_ratio"]["review_required"])

    def test_extracts_single_period_sales_style_ratio(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "MAEDA_2021",
            "operating_company_id": "MAEDA",
            "fiscal_year": 2021,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "sales_style_orders",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "事業会社別受注工事高の受注方法別比率",
            "raw_text": "\n".join(
                [
                    "事業会社別受注工事高の受注方法別比率",
                    "前田建設",
                    "区分",
                    "特命（％）",
                    "競争（％）",
                    "計（％）",
                    "建築工事",
                    "52.8",
                    "47.2",
                    "100.0",
                    "土木工事",
                    "62.4",
                    "37.6",
                    "100.0",
                    "前田道路",
                    "舗装工事他",
                    "8.3",
                    "91.7",
                    "100.0",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row["value"] for row in rows}
        self.assertEqual(by_field["building_orders_special_contract_ratio"], 52.8)
        self.assertEqual(by_field["building_orders_competitive_ratio"], 47.2)

    def test_extracts_domestic_building_row_without_polluting_total_building(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "PENTA_2015",
            "operating_company_id": "PENTA",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注高、売上高及び繰越高",
            "raw_text": "\n".join(
                [
                    "受注高、売上高及び繰越高",
                    "前期繰越高",
                    "当期受注高",
                    "計",
                    "当期売上高",
                    "次期繰越高",
                    "提出会社単独",
                    "国内建築事業",
                    "163,711",
                    "145,084",
                    "308,795",
                    "159,340",
                    "149,455",
                    "国内土木事業",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["domestic_building_orders_total"]["value"], 145084.0)
        self.assertEqual(by_field["domestic_completed_building"]["value"], 159340.0)
        self.assertEqual(by_field["domestic_backlog_building_next"]["value"], 149455.0)
        self.assertFalse(by_field["domestic_building_orders_total"]["review_required"])
        self.assertNotIn("building_orders_total", by_field)

    def test_uses_corrected_leading_backlog_for_orders_row(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "ANDO_HAZAMA_2015",
            "operating_company_id": "ANDO_HAZAMA",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "orders_backlog",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注工事高、完成工事高及び次期繰越工事高",
            "raw_text": "\n".join(
                [
                    "受注工事高、完成工事高及び次期繰越工事高",
                    "期別",
                    "区分",
                    "前期繰越工事高",
                    "当期受注工事高",
                    "計",
                    "当期完成工事高",
                    "次期繰越工事高",
                    "当事業年度",
                    "建築工事",
                    "(184,296)",
                    "184,321",
                    "238,921",
                    "423,242",
                    "233,462",
                    "189,780",
                    "土木工事",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["building_orders_total"]["value"], 238921.0)
        self.assertEqual(by_field["completed_building"]["value"], 233462.0)
        self.assertEqual(by_field["backlog_building_next"]["value"], 189780.0)
        self.assertFalse(by_field["building_orders_total"]["review_required"])

    def test_extracts_segment_order_table_rows(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "PENTA_2016",
            "operating_company_id": "PENTA",
            "fiscal_year": 2016,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "segment_orders",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "受注実績",
            "raw_text": "\n".join(
                [
                    "受注実績",
                    "当連結会計年度における受注実績をセグメントごとに示すと、次のとおりである。",
                    "セグメントの名称",
                    "前連結会計年度（百万円）",
                    "当連結会計年度（百万円）",
                    "国内土木事業 164,546 199,006（20.9％増）",
                    "国内建築事業 150,925 179,900（19.2％増）",
                    "海外建設事業 139,123 101,651（26.9％減）",
                    "合計 454,595 480,558（5.7％増）",
                    "売上実績",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["segment_orders_domestic_civil"]["value"], 199006.0)
        self.assertEqual(by_field["segment_orders_domestic_building"]["value"], 179900.0)
        self.assertEqual(by_field["segment_orders_overseas_construction"]["value"], 101651.0)
        self.assertEqual(by_field["segment_orders_domestic_building"]["unit_raw"], "百万円")
        self.assertEqual(by_field["segment_orders_domestic_building"]["data_scope"], "segment")
        self.assertFalse(by_field["segment_orders_domestic_building"]["review_required"])

    def test_extracts_segment_order_narrative_rows(self):
        block = {
            "run_id": "run",
            "candidate_block_id": "block",
            "company_year_id": "ANDO_HAZAMA_2015",
            "operating_company_id": "ANDO_HAZAMA",
            "fiscal_year": 2015,
            "source_doc_id": "doc",
            "source_file": "xbrl.zip",
            "section_name": "segment_orders",
            "unit_hint": "百万円",
            "scope_hint": "standalone",
            "locator_score": 10,
            "heading_text": "セグメントの業績",
            "raw_text": "\n".join(
                [
                    "セグメントの業績は、次のとおりである。",
                    "（土木事業） 受注高は 1,232 億円（前連結会計年度比 26.2％減少）、売上高は1,225億円となった。",
                    "（建築事業） 受注高は 2,389 億円（前連結会計年度比2.9％増加）、売上高は2,334億円となった。",
                ]
            ),
        }
        rows = extract_local_table_rows([block])
        by_field = {row["field_id"]: row for row in rows}
        self.assertEqual(by_field["segment_orders_civil"]["value"], 1232.0)
        self.assertEqual(by_field["segment_orders_civil"]["unit_normalized"], "億円")
        self.assertEqual(by_field["segment_orders_building"]["value"], 2389.0)
        self.assertEqual(by_field["segment_orders_building"]["data_scope"], "segment")


if __name__ == "__main__":
    unittest.main()
