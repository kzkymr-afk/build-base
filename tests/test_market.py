import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.services import market


class MarketTests(unittest.TestCase):
    def test_refresh_stock_prices_writes_monthly_prices_with_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "company_master.csv",
                [
                    {
                        "operating_company_id": "A",
                        "operating_company_name": "A建設",
                        "securities_code": "1801",
                        "fiscal_year_end_month": "3",
                        "valid_from_year": "2015",
                    }
                ],
            )

            result = market.refresh_stock_prices(
                root,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 2, 29),
                force=True,
                fetcher=_fake_fetcher,
            )

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["new_rows"], 2)
            rows = read_table(root / "data" / "marts" / "market" / "stock_price_monthly.csv")
            self.assertEqual([row["date"] for row in rows], ["2024-01-31", "2024-02-29"])
            self.assertEqual(rows[0]["ticker"], "1801.T")
            self.assertEqual(rows[0]["adjusted_close"], "100.0")
            self.assertEqual(rows[1]["monthly_return"], "0.1")
            self.assertEqual(rows[1]["monthly_return_pct"], "10.0")
            self.assertEqual(rows[1]["dividend"], "5.0")
            self.assertTrue((root / "data" / "marts" / "market" / "security_master.csv").exists())
            self.assertTrue((root / "data" / "automation" / "stock_monthly_last.json").exists())

    def test_stock_monthly_status_reports_due_when_no_latest_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "company_master.csv",
                [
                    {
                        "operating_company_id": "A",
                        "operating_company_name": "A建設",
                        "securities_code": "1801",
                        "fiscal_year_end_month": "3",
                    }
                ],
            )

            status = market.stock_monthly_status(root, today=date(2026, 6, 22))

            self.assertTrue(status["due"])
            self.assertEqual(status["target_month"], "2026-05")
            self.assertEqual(status["enabled_securities"], 1)

    def test_stock_monthly_status_ignores_previous_errors_after_listing_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "security_master.csv",
                [
                    {
                        "listed_company_id": "OLD",
                        "operating_company_id": "OLD",
                        "operating_company_name": "旧上場建設",
                        "stock_code": "9999",
                        "ticker": "9999.T",
                        "exchange": "JPX",
                        "currency": "JPY",
                        "fiscal_year_end_month": "3",
                        "listed_from": "2015-01-01",
                        "listed_to": "2021-09-29",
                        "successor_listed_company_id": "NEW",
                        "enabled": "true",
                    }
                ],
            )
            write_table(
                root / "data" / "marts" / "market" / "stock_price_monthly.csv",
                [
                    {
                        "listed_company_id": "CURRENT",
                        "operating_company_id": "CURRENT",
                        "operating_company_name": "現上場建設",
                        "date": "2026-05-31",
                        "month": "2026-05",
                    }
                ],
            )
            automation_dir = root / "data" / "automation"
            automation_dir.mkdir(parents=True, exist_ok=True)
            (automation_dir / "stock_monthly_last.json").write_text(
                json.dumps(
                    {
                        "status": "partial_success",
                        "last_successful_month": "2026-05",
                        "errors": [{"listed_company_id": "OLD", "ticker": "9999.T", "error": "not found"}],
                    }
                ),
                encoding="utf-8",
            )

            status = market.stock_monthly_status(root, today=date(2026, 6, 22))

            self.assertFalse(status["due"])
            self.assertEqual(status["last_error_count"], 0)
            self.assertEqual(status["last_ignored_listing_error_count"], 1)
            self.assertIn("自動更新対象外", status["message"])

    def test_refresh_stock_prices_skips_security_after_listing_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "config" / "security_master.csv",
                [
                    {
                        "listed_company_id": "OLD",
                        "operating_company_id": "OLD",
                        "operating_company_name": "旧上場建設",
                        "stock_code": "9999",
                        "ticker": "9999.T",
                        "exchange": "JPX",
                        "currency": "JPY",
                        "fiscal_year_end_month": "3",
                        "listed_from": "2015-01-01",
                        "listed_to": "2021-09-29",
                        "successor_listed_company_id": "NEW",
                        "enabled": "true",
                    }
                ],
            )

            def fail_fetcher(ticker: str, start: date, end: date, cfg: dict) -> dict:
                raise AssertionError("fetcher should not be called outside listing period")

            result = market.refresh_stock_prices(
                root,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 31),
                force=True,
                fetcher=fail_fetcher,
            )

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["fetched_securities"], 0)
            self.assertEqual(result["skipped_securities"], 1)
            self.assertEqual(result["skipped_listing_period"][0]["ticker"], "9999.T")

    def test_refresh_stock_prices_clips_security_to_delisting_month_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captured = {}
            write_table(
                root / "config" / "security_master.csv",
                [
                    {
                        "listed_company_id": "OLD",
                        "operating_company_id": "OLD",
                        "operating_company_name": "旧上場建設",
                        "stock_code": "9999",
                        "ticker": "9999.T",
                        "exchange": "JPX",
                        "currency": "JPY",
                        "fiscal_year_end_month": "3",
                        "listed_from": "2024-01-01",
                        "listed_to": "2024-02-15",
                        "successor_listed_company_id": "NEW",
                        "enabled": "true",
                    }
                ],
            )

            def capture_fetcher(ticker: str, start: date, end: date, cfg: dict) -> dict:
                captured["ticker"] = ticker
                captured["start"] = start
                captured["end"] = end
                return _fake_fetcher(ticker, start, end, cfg)

            result = market.refresh_stock_prices(
                root,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
                force=True,
                fetcher=capture_fetcher,
            )

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(captured["start"], date(2024, 1, 1))
            self.assertEqual(captured["end"], date(2024, 2, 29))
            self.assertEqual(result["new_rows"], 2)

    def test_stock_options_and_chart_data_use_monthly_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "marts" / "market" / "stock_price_monthly.csv",
                [
                    {
                        "listed_company_id": "A",
                        "operating_company_id": "A",
                        "operating_company_name": "A建設",
                        "stock_code": "1801",
                        "ticker": "1801.T",
                        "date": "2024-01-31",
                        "month": "2024-01",
                        "adjusted_close": "100",
                        "monthly_return_pct": "",
                        "volume": "1000",
                    },
                    {
                        "listed_company_id": "A",
                        "operating_company_id": "A",
                        "operating_company_name": "A建設",
                        "stock_code": "1801",
                        "ticker": "1801.T",
                        "date": "2024-02-29",
                        "month": "2024-02",
                        "adjusted_close": "110",
                        "monthly_return_pct": "10",
                        "volume": "2000",
                    },
                ],
            )

            options = market.stock_monthly_options(root)
            chart = market.read_stock_chart_data(root, companies=["A"], months=["2024-02"], fields=["adjusted_close", "monthly_return_pct"])

            self.assertEqual(options["months"], ["2024-01", "2024-02"])
            self.assertIn("adjusted_close", [field["id"] for field in options["fields"]])
            self.assertEqual(chart["total"], 1)
            self.assertEqual(chart["rows"][0]["fiscal_year"], "2024-02")
            self.assertEqual(chart["rows"][0]["adjusted_close"], 110.0)
            self.assertEqual(chart["rows"][0]["monthly_return_pct"], 10.0)
            self.assertEqual(chart["fields"][0]["name"], "調整後終値")


def _fake_fetcher(ticker: str, start: date, end: date, cfg: dict) -> dict:
    jan = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    feb = int(datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp())
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "JPY", "symbol": ticker, "exchangeName": "JPX"},
                    "timestamp": [jan, feb],
                    "events": {
                        "dividends": {
                            str(feb): {"amount": 5.0, "date": feb},
                        }
                    },
                    "indicators": {
                        "quote": [
                            {
                                "open": [90.0, 100.0],
                                "high": [105.0, 115.0],
                                "low": [80.0, 95.0],
                                "close": [101.0, 111.0],
                                "volume": [1000, 2000],
                            }
                        ],
                        "adjclose": [{"adjclose": [100.0, 110.0]}],
                    },
                }
            ],
            "error": None,
        }
    }


if __name__ == "__main__":
    unittest.main()
