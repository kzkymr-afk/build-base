import sqlite3
import unittest

from yuho_auto_extract.edinet_db import _effective_fact_candidate_count, _extract_segment_record_from_db, _query_fact_candidates


class EdinetDbSegmentTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            create table xbrl_facts (
                company_year_id text,
                operating_company_id text,
                fiscal_year integer,
                source_doc_id text,
                csv_file text,
                element_id text,
                item_name text,
                context_id text,
                relative_year text,
                consolidation_scope text,
                period_or_instant text,
                unit_id text,
                unit text,
                value text
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_sums_building_and_civil_construction_segment_sales(self):
        self._insert("jpcrp_cor:RevenuesFromExternalCustomers", "外部顧客への売上高", "CurrentYearDuration_XBuildingConstructionReportableSegmentsMember", "100000000")
        self._insert("jpcrp_cor:RevenuesFromExternalCustomers", "外部顧客への売上高", "CurrentYearDuration_XCivilEngineeringReportableSegmentsMember", "50000000")
        self._insert("jpcrp_cor:RevenuesFromExternalCustomers", "外部顧客への売上高", "CurrentYearDuration_XRealEstateReportableSegmentsMember", "99999999")
        record = _extract_segment_record_from_db(
            self.conn,
            {"field_id": "segment_sales_construction", "target_unit": "百万円", "data_scope_required": "segment"},
            self._target(),
            "run",
        )
        self.assertEqual(record["value_raw"], 150000000.0)
        self.assertEqual(record["data_scope"], "segment")
        self.assertFalse(record["review_required"])

    def test_prefers_broad_construction_segment_over_subsegments(self):
        self._insert("jpcrp_cor:RevenuesFromExternalCustomers", "外部顧客への売上高", "CurrentYearDuration_XConstructionReportableSegmentsMember", "160000000")
        self._insert("jpcrp_cor:RevenuesFromExternalCustomers", "外部顧客への売上高", "CurrentYearDuration_XBuildingConstructionReportableSegmentsMember", "100000000")
        self._insert("jpcrp_cor:RevenuesFromExternalCustomers", "外部顧客への売上高", "CurrentYearDuration_XCivilEngineeringReportableSegmentsMember", "50000000")
        record = _extract_segment_record_from_db(
            self.conn,
            {"field_id": "segment_sales_construction", "target_unit": "百万円", "data_scope_required": "segment"},
            self._target(),
            "run",
        )
        self.assertEqual(record["value_raw"], 160000000.0)
        self.assertEqual(record["candidate_count"], 1)

    def test_extracts_company_namespace_ifrs_business_profit(self):
        self._insert("jpcrp030000-asr_EX:BusinessProfitLossIFRS", "", "CurrentYearDuration_XConstructionReportableSegmentMember", "123000000")
        record = _extract_segment_record_from_db(
            self.conn,
            {"field_id": "segment_profit_construction", "target_unit": "百万円", "data_scope_required": "segment"},
            self._target(),
            "run",
        )
        self.assertEqual(record["value_raw"], 123000000.0)
        self.assertEqual(record["xbrl_element"], "jpcrp030000-asr_EX:BusinessProfitLossIFRS")

    def test_xbrl_candidates_prefer_first_matching_tag_over_later_fallbacks(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            create table xbrl_facts (
                id integer primary key,
                company_year_id text,
                operating_company_id text,
                fiscal_year integer,
                source_doc_id text,
                csv_file text,
                element_id text,
                item_name text,
                context_id text,
                relative_year text,
                consolidation_scope text,
                period_or_instant text,
                unit_id text,
                unit text,
                value text
            )
            """
        )
        facts = [
            ("jpcrp_cor:CashAndCashEquivalentsSummaryOfBusinessResults", "現金及び現金同等物", "CurrentYearInstant", "連結", "時点", "106935000000"),
            ("jppfs_cor:CashAndCashEquivalents", "現金及び現金同等物", "CurrentYearInstant", "連結", "時点", "106935000000"),
            ("jppfs_cor:CashAndDeposits", "現金及び預金", "CurrentYearInstant", "連結", "時点", "100617000000"),
            ("jppfs_cor:CashAndDeposits", "現金及び預金", "CurrentYearInstant_NonConsolidatedMember", "個別", "時点", "92363000000"),
        ]
        for item in facts:
            conn.execute(
                """
                insert into xbrl_facts(
                    company_year_id, operating_company_id, fiscal_year, source_doc_id, csv_file,
                    element_id, item_name, context_id, relative_year, consolidation_scope,
                    period_or_instant, unit_id, unit, value
                )
                values ('A_2024', 'A', 2024, 'doc', 'file.csv', ?, ?, ?, '当期', ?, ?, 'JPY', '円', ?)
                """,
                item,
            )

        try:
            candidates = _query_fact_candidates(
                conn,
                "A_2024",
                {
                    "xbrl_tag_candidates": "CashAndDeposits;CashAndCashEquivalents;現金及び預金;現金及び現金同等物",
                    "context_filters": "CurrentYearInstant;ConsolidatedMember",
                },
            )
        finally:
            conn.close()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["element_id"], "jppfs_cor:CashAndDeposits")
        self.assertEqual(candidates[0]["value"], "100617000000")
        self.assertEqual(_effective_fact_candidate_count(candidates), 1)

    def _insert(self, element_id, item_name, context_id, value):
        self.conn.execute(
            """
            insert into xbrl_facts values (
                'A_2024', 'A', 2024, 'doc', 'file.csv', ?, ?, ?, '当期', '連結', '期間', 'JPY', '円', ?
            )
            """,
            (element_id, item_name, context_id, value),
        )

    def _target(self):
        return {"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": 2024, "docID": "doc"}


if __name__ == "__main__":
    unittest.main()
