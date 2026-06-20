import unittest
import sqlite3

from yuho_auto_extract.edinet_db import _parse_cost_detail_value, _query_cost_detail_text_block


class CostTextBlockTests(unittest.TestCase):
    def test_extracts_current_year_cost_values_from_compact_text(self):
        text = (
            "【完成工事原価明細書】 前事業年度 当事業年度 "
            "区分 金額（百万円）構成比（％）金額（百万円）構成比（％）"
            "材料費 47,29115.454,04915.8"
            "労務費（うち労務外注費） 5,532(5,120)1.8(1.7)5,389(5,030)1.6(1.5)"
            "外注費 203,07466.0227,58466.7"
            "経費（うち人件費） 51,626(15,366)16.8(5.0)54,356(16,593)15.9(4.9)"
            "計 307,525100.0341,378100.0"
        )
        self.assertEqual(_parse_cost_detail_value(text, "材料費")[0], 54049.0)
        self.assertEqual(_parse_cost_detail_value(text, "労務費")[0], 5389.0)
        self.assertEqual(_parse_cost_detail_value(text, "外注費")[0], 227584.0)
        self.assertEqual(_parse_cost_detail_value(text, "経費")[0], 54356.0)

    def test_stops_before_real_estate_cost_report(self):
        text = (
            "【完成工事原価報告書】 材料費 123,1918.6109,9477.6"
            "労務費 155,77510.9166,69111.4"
            "外注費 960,76767.2999,38368.6"
            "経費 189,20213.3180,35812.4"
            "計 1,428,9371001,456,380100"
            "【不動産事業等売上原価報告書】 経費 18,77188.719,03197.9"
        )
        self.assertEqual(_parse_cost_detail_value(text, "経費")[0], 180358.0)

    def test_extracts_small_uncommaed_amount_pairs(self):
        text = "【完成工事原価報告書】 労務費 1900.13800.1(うち労務外注費) (190)(0.1)(380)(0.1) 外注費 233,98074.3229,68773.1"
        self.assertEqual(_parse_cost_detail_value(text, "労務費")[0], 380.0)

    def test_extracts_stacked_label_cost_report(self):
        text = (
            "（完成工事原価報告書） 前事業年度 当事業年度 "
            "区分 注記番号 金額（百万円）構成比（％）金額（百万円）構成比（％）"
            "Ⅰ 材料費Ⅱ 労務費 （うち労務外注費）Ⅲ 外注費Ⅳ 経費 （うち人件費） "
            "11,28910,753（10,753）68,20814,174（6,225）"
            "10.810.3（10.3）65.313.6（6.0）"
            "13,8679,964（9,964）68,72211,633（5,096）"
            "13.39.5（9.5）66.011.2（4.9）"
            "計 104,427100104,187100"
        )

        self.assertEqual(_parse_cost_detail_value(text, "材料費")[0], 13867.0)
        self.assertEqual(_parse_cost_detail_value(text, "労務費")[0], 9964.0)
        self.assertEqual(_parse_cost_detail_value(text, "外注費")[0], 68722.0)
        self.assertEqual(_parse_cost_detail_value(text, "経費")[0], 11633.0)

    def test_query_accepts_company_specific_cost_report_text_block(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            create table xbrl_facts(
              id integer primary key,
              company_year_id text,
              context_id text,
              element_id text,
              value text
            )
            """
        )
        conn.execute(
            """
            insert into xbrl_facts(company_year_id, context_id, element_id, value)
            values (?, ?, ?, ?)
            """,
            (
                "A_2024",
                "CurrentYearDuration",
                "jpcrp030000-asr_EXXXX-000:CostReportOfCompletedConstructionContractsTextBlock",
                "【完成工事原価報告書】 材料費 1,00010.02,00020.0 労務費 3,00030.04,00040.0 外注費 5,00050.06,00060.0 経費 7,00070.08,00080.0",
            ),
        )
        try:
            row = _query_cost_detail_text_block(conn, "A_2024")
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertIn("CostReportOfCompletedConstructionContractsTextBlock", row["element_id"])


if __name__ == "__main__":
    unittest.main()
