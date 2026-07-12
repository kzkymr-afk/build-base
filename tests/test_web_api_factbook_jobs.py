import unittest
from pathlib import Path
from unittest.mock import patch

from yuho_auto_extract.web_api import app as web_app


class FactbookJobApiTests(unittest.TestCase):
    def test_factbook_refresh_passes_company_filter_to_pipeline(self):
        request = web_app.FactbookRefreshRequest(force=True, companies=["ANDO_HAZAMA"])
        with patch.object(web_app, "_start_job", side_effect=lambda _name, worker: worker(Path("/tmp/project"), None)):
            with patch.object(web_app.pipeline, "refresh_company_factbooks", return_value=0) as refresh:
                result = web_app.start_factbook_refresh(request)

        self.assertEqual(result, 0)
        refresh.assert_called_once_with(
            Path("/tmp/project"),
            log=None,
            force=True,
            dry_run=False,
            company_ids=["ANDO_HAZAMA"],
        )

    def test_annual_refresh_does_not_require_factbook_company_filter(self):
        request = web_app.AnnualRefreshRequest(fiscal_year=2024, force=True)
        with patch.object(web_app, "_start_job", side_effect=lambda _name, worker: worker(Path("/tmp/project"), None)):
            with patch.object(web_app.pipeline, "annual_refresh", return_value=0) as refresh:
                result = web_app.start_annual_refresh(request)

        self.assertEqual(result, 0)
        refresh.assert_called_once_with(
            Path("/tmp/project"),
            log=None,
            fiscal_year=2024,
            force=True,
            dry_run=False,
        )


if __name__ == "__main__":
    unittest.main()
