import unittest

from yuho_auto_extract.xbrl_csv_parser import _effective_candidate_count, _match_field_candidates


class XbrlCsvParserTests(unittest.TestCase):
    def test_candidate_order_prefers_primary_tag_over_later_fallback_values(self):
        rows = [
            {
                "要素ID": "jpcrp_cor:CashAndCashEquivalentsSummaryOfBusinessResults",
                "項目名": "現金及び現金同等物",
                "コンテキストID": "CurrentYearInstant",
                "連結・個別": "連結",
                "値": "106935000000",
            },
            {
                "要素ID": "jppfs_cor:CashAndCashEquivalents",
                "項目名": "現金及び現金同等物",
                "コンテキストID": "CurrentYearInstant",
                "連結・個別": "連結",
                "値": "106935000000",
            },
            {
                "要素ID": "jppfs_cor:CashAndDeposits",
                "項目名": "現金及び預金",
                "コンテキストID": "CurrentYearInstant",
                "連結・個別": "連結",
                "値": "100617000000",
            },
            {
                "要素ID": "jppfs_cor:CashAndDeposits",
                "項目名": "現金及び預金",
                "コンテキストID": "CurrentYearInstant_NonConsolidatedMember",
                "連結・個別": "個別",
                "値": "92363000000",
            },
        ]
        field = {
            "xbrl_tag_candidates": "CashAndDeposits;CashAndCashEquivalents;現金及び預金;現金及び現金同等物",
            "context_filters": "CurrentYearInstant;ConsolidatedMember",
        }

        candidates = _match_field_candidates(rows, field)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["要素ID"], "jppfs_cor:CashAndDeposits")
        self.assertEqual(candidates[0]["値"], "100617000000")
        self.assertEqual(_effective_candidate_count(candidates), 1)


if __name__ == "__main__":
    unittest.main()
