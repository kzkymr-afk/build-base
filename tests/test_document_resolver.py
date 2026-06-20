import unittest

from yuho_auto_extract.document_resolver import fiscal_year_from_period_end, resolve_target_documents


class DocumentResolverTests(unittest.TestCase):
    def test_fiscal_year_for_march_end(self):
        self.assertEqual(fiscal_year_from_period_end("2025-03-31", 3), 2024)
        self.assertEqual(fiscal_year_from_period_end("2024-12-31", 12), 2024)

    def test_prefers_latest_correction(self):
        docs = [
            {
                "docID": "S1",
                "edinetCode": "E1",
                "ordinanceCode": "010",
                "formCode": "030000",
                "docDescription": "有価証券報告書",
                "periodEnd": "2024-03-31",
                "submitDateTime": "2024-06-01 10:00",
            },
            {
                "docID": "S2",
                "edinetCode": "E1",
                "ordinanceCode": "010",
                "formCode": "030001",
                "docDescription": "訂正有価証券報告書",
                "periodEnd": "2024-03-31",
                "submitDateTime": "2024-07-01 10:00",
            },
        ]
        companies = [{"operating_company_id": "A", "operating_company_name": "A社", "edinet_code": "E1", "fiscal_year_end_month": 3}]
        company_years = [{"company_year_id": "A_2023", "operating_company_id": "A", "fiscal_year": 2023, "fiscal_year_end": "2024-03-31"}]
        filter_cfg = {
            "securities_report": {
                "doc_description_include": ["有価証券報告書"],
                "doc_description_exclude": ["四半期", "半期"],
                "correction_doc_description_include": ["訂正有価証券報告書"],
                "ordinance_code_candidates": ["010"],
                "form_code_candidates": ["030000", "030001"],
                "prefer_latest_correction": True,
            }
        }
        targets = resolve_target_documents(docs, companies, company_years, filter_cfg)
        self.assertEqual(targets[0]["docID"], "S2")
        self.assertTrue(targets[0]["is_correction"])

    def test_uses_reporting_entity_code_for_reorganized_company_year(self):
        docs = [
            {
                "docID": "R1",
                "edinetCode": "EHD",
                "ordinanceCode": "010",
                "formCode": "030000",
                "docDescription": "有価証券報告書",
                "periodEnd": "2024-03-31",
                "submitDateTime": "2024-06-01 10:00",
            }
        ]
        companies = [
            {"operating_company_id": "OP", "operating_company_name": "事業会社", "edinet_code": "EOLD", "fiscal_year_end_month": 3},
            {"operating_company_id": "HD", "operating_company_name": "HD会社", "edinet_code": "EHD", "fiscal_year_end_month": 3},
        ]
        company_years = [
            {
                "company_year_id": "OP_2023",
                "operating_company_id": "OP",
                "reporting_entity_id": "HD",
                "fiscal_year": 2023,
                "fiscal_year_end": "2024-03-31",
            }
        ]
        filter_cfg = {
            "securities_report": {
                "doc_description_include": ["有価証券報告書"],
                "doc_description_exclude": ["四半期", "半期"],
                "ordinance_code_candidates": ["010"],
                "form_code_candidates": ["030000"],
            }
        }
        targets = resolve_target_documents(docs, companies, company_years, filter_cfg)
        self.assertEqual(targets[0]["docID"], "R1")
        self.assertEqual(targets[0]["reporting_entity_edinet_code"], "EHD")


if __name__ == "__main__":
    unittest.main()
