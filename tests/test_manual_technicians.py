from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.services.manual_technicians import import_manual_technicians, parse_markdown_table


class ManualTechniciansTest(unittest.TestCase):
    def test_parse_markdown_table_preserves_blank_cells(self) -> None:
        rows = parse_markdown_table(
            "\n".join(
                [
                    "| 年度 | 会社名 | 建築技術者数（一級） | 建築監理技術者数 |",
                    "| --- | --- | ---: | ---: |",
                    "| 2025 | 鹿島建設 | 2,769 | 1,465 |",
                    "| 2020 | ナカノフドー |  |  |",
                ]
            )
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["会社名"], "鹿島建設")
        self.assertEqual(rows[1]["建築技術者数（一級）"], "")

    def test_import_matches_note_year_to_fiscal_year_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True)
            note = root / "technicians.md"
            note.write_text(
                "\n".join(
                    [
                        "| 年度 | 会社名 | 建築技術者数（一級） | 建築監理技術者数 |",
                        "| --- | --- | ---: | ---: |",
                        "| 2025 | 鹿島建設 | 2,769 | 1,465 |",
                        "| 2025 | フジタ | 1,105 | 432 |",
                    ]
                ),
                encoding="utf-8",
            )
            write_table(
                root / "config" / "company_master.csv",
                [
                    {
                        "operating_company_id": "KAJIMA",
                        "operating_company_name": "鹿島建設",
                        "fiscal_year_end_month": "3",
                    }
                ],
            )
            write_table(
                root / "config" / "company_year_master.csv",
                [
                    {
                        "company_year_id": "KAJIMA_2024",
                        "operating_company_id": "KAJIMA",
                        "fiscal_year": "2024",
                        "fiscal_year_end": "2025-03-31",
                    }
                ],
            )

            summary = import_manual_technicians(root, note_path=note)
            rows = read_table(root / "data" / "intermediate" / "manual_technician_extracted_long.csv")
            unmatched = read_table(root / "data" / "marts" / "manual_technicians" / "unmatched_rows.csv")

            self.assertEqual(summary["imported_long_rows"], 2)
            self.assertEqual({row["field_id"] for row in rows}, {"architecture_engineers_1st_class", "architecture_engineers_1st_class_training"})
            self.assertEqual(rows[0]["company_year_id"], "KAJIMA_2024")
            self.assertEqual(rows[0]["fiscal_year"], "2024")
            self.assertEqual(unmatched[0]["reason"], "company_not_in_master")


if __name__ == "__main__":
    unittest.main()
