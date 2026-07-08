from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.review_queue import REVIEW_COLUMNS
from yuho_auto_extract.services import cells, pipeline


# 合成テーブル（tests/test_source_inference_apply.py と同じ業界標準様式・値）。
# 前期繰越/当期受注/計/当期完成/次期繰越が恒等式を満たす。
TABLE_TEXT_2015 = (
    "(1）受注工事高、完成工事高及び次期繰越工事高\n"
    "期別\n区分\n前期繰越\n工事高\n(百万円)\n当期受注\n工事高\n(百万円)\n計\n(百万円)\n"
    "当期完成\n工事高\n(百万円)\n次期繰越\n工事高\n(百万円)\n"
    "当事業年度\n"
    "土木工事\n110,000\n60,000\n170,000\n45,000\n125,000\n"
    "建築工事\n190,000\n100,000\n290,000\n95,000\n195,000\n"
    "計\n300,000\n160,000\n460,000\n140,000\n320,000\n"
)

# 2016年度: 前期繰越が2015年度の次期繰越(195,000)に一致するよう連動させた別年度分。
TABLE_TEXT_2016 = (
    "(1）受注工事高、完成工事高及び次期繰越工事高\n"
    "期別\n区分\n前期繰越\n工事高\n(百万円)\n当期受注\n工事高\n(百万円)\n計\n(百万円)\n"
    "当期完成\n工事高\n(百万円)\n次期繰越\n工事高\n(百万円)\n"
    "当事業年度\n"
    "土木工事\n125,000\n65,000\n190,000\n50,000\n140,000\n"
    "建築工事\n195,000\n110,000\n305,000\n100,000\n205,000\n"
    "計\n320,000\n175,000\n495,000\n150,000\n345,000\n"
)


def _write_candidate_blocks(root: Path, blocks) -> None:
    """blocks: list of dict(candidate_block_id, company_year_id, section_name, raw_text)."""
    import json

    db_dir = root / "data" / "intermediate"
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_dir / "edinet.db"))
    conn.execute(
        "create table candidate_blocks (candidate_block_id text primary key, company_year_id text, "
        "source_doc_id text, section_name text, locator_score real, heading_text text, row_json text not null)"
    )
    for block in blocks:
        row_json = json.dumps(
            {
                "candidate_block_id": block["candidate_block_id"],
                "company_year_id": block["company_year_id"],
                "section_name": block.get("section_name", "orders_backlog"),
                "raw_text": block["raw_text"],
            },
            ensure_ascii=False,
        )
        conn.execute(
            "insert into candidate_blocks (candidate_block_id, company_year_id, section_name, row_json) "
            "values (?, ?, ?, ?)",
            (block["candidate_block_id"], block["company_year_id"], block.get("section_name", "orders_backlog"), row_json),
        )
    conn.commit()
    conn.close()


def _write_review_queue_row(root: Path, company_year_id: str, field_id: str, extracted_value: str, unit: str = "百万円") -> None:
    row = {column: "" for column in REVIEW_COLUMNS}
    row.update(
        {
            "company_year_id": company_year_id,
            "field_id": field_id,
            "extracted_value": extracted_value,
            "unit_normalized": unit,
        }
    )
    write_table(root / "data" / "review" / "review_queue.csv", [row])


def _write_final_master_wide(root: Path, rows) -> None:
    """rows: list of dict(company_year_id, operating_company_id, fiscal_year, <field cols...>)."""
    write_table(root / "data" / "final" / "final_master_wide.csv", rows)


class _StubbedApplyReview:
    """apply_promotion_plan が最後に呼ぶ pipeline.apply_review（export-final以降の
    フルパイプライン再実行）をスタブに差し替える。tests/test_source_inference_apply.py
    と同じパターン（apply_promotion_plan自身の書込み経路のみを検証対象にする）。
    """

    def __enter__(self):
        self._original = pipeline.apply_review
        self.calls = []

        def fake_apply_review(root, log=None, reviewed="data/review/review_resolved.csv"):
            self.calls.append((root, reviewed))
            return 0

        pipeline.apply_review = fake_apply_review
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pipeline.apply_review = self._original
        return False


class SaveCellReviewInferredSourceSuggestionTests(unittest.TestCase):
    def test_order_field_correct_returns_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_candidate_blocks(
                root,
                [{"candidate_block_id": "CO_2015_block1", "company_year_id": "CO_2015", "raw_text": TABLE_TEXT_2015}],
            )
            write_table(root / "config" / "field_definition.csv", [{"field_id": "building_orders_total", "field_name_ja": "建築受注高", "target_unit": "百万円"}])
            _write_final_master_wide(
                root,
                [{"company_year_id": "CO_2015", "operating_company_id": "CO", "fiscal_year": "2015", "building_orders_total": ""}],
            )
            write_table(root / "data" / "review" / "review_queue.csv", [])

            result = cells.save_cell_review(
                root,
                "CO_2015",
                "building_orders_total",
                review_decision="correct",
                corrected_value="100000",
            )

            suggestion = result.get("inferred_source_suggestion")
            self.assertIsNotNone(suggestion)
            self.assertEqual(suggestion["company_year_id"], "CO_2015")
            self.assertEqual(suggestion["field_id"], "building_orders_total")
            self.assertEqual(suggestion["role"], "当期受注")
            self.assertGreaterEqual(suggestion["confidence"], 0.9)
            self.assertIn("section_name", suggestion)

    def test_non_order_field_returns_null_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(root / "config" / "field_definition.csv", [{"field_id": "roe", "field_name_ja": "ROE", "target_unit": "%"}])
            _write_final_master_wide(
                root,
                [{"company_year_id": "A_2024", "operating_company_id": "A", "fiscal_year": "2024", "roe": ""}],
            )
            write_table(root / "data" / "review" / "review_queue.csv", [])

            result = cells.save_cell_review(
                root,
                "A_2024",
                "roe",
                review_decision="correct",
                corrected_value="8.2",
            )

            self.assertIsNone(result.get("inferred_source_suggestion"))

    def test_order_field_without_matching_source_returns_null_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # candidate_blocksテーブルは存在するが行が0件 → 出典が見つからない。
            _write_candidate_blocks(root, [])
            write_table(root / "config" / "field_definition.csv", [{"field_id": "building_orders_total", "field_name_ja": "建築受注高", "target_unit": "百万円"}])
            _write_final_master_wide(
                root,
                [{"company_year_id": "NX_2020", "operating_company_id": "NX", "fiscal_year": "2020", "building_orders_total": ""}],
            )
            write_table(root / "data" / "review" / "review_queue.csv", [])

            result = cells.save_cell_review(
                root,
                "NX_2020",
                "building_orders_total",
                review_decision="correct",
                corrected_value="999999",
            )

            self.assertIsNone(result.get("inferred_source_suggestion"))

    def test_reject_decision_returns_null_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_candidate_blocks(
                root,
                [{"candidate_block_id": "CO_2015_block1", "company_year_id": "CO_2015", "raw_text": TABLE_TEXT_2015}],
            )
            write_table(root / "config" / "field_definition.csv", [{"field_id": "building_orders_total", "field_name_ja": "建築受注高", "target_unit": "百万円"}])
            _write_final_master_wide(
                root,
                [{"company_year_id": "CO_2015", "operating_company_id": "CO", "fiscal_year": "2015", "building_orders_total": ""}],
            )
            _write_review_queue_row(root, "CO_2015", "building_orders_total", "100000")

            result = cells.save_cell_review(
                root,
                "CO_2015",
                "building_orders_total",
                review_decision="reject",
            )

            self.assertIsNone(result.get("inferred_source_suggestion"))

    def test_accept_decision_from_queue_row_returns_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_candidate_blocks(
                root,
                [{"candidate_block_id": "CO_2015_block1", "company_year_id": "CO_2015", "raw_text": TABLE_TEXT_2015}],
            )
            write_table(root / "config" / "field_definition.csv", [{"field_id": "building_orders_total", "field_name_ja": "建築受注高", "target_unit": "百万円"}])
            _write_final_master_wide(
                root,
                [{"company_year_id": "CO_2015", "operating_company_id": "CO", "fiscal_year": "2015", "building_orders_total": ""}],
            )
            _write_review_queue_row(root, "CO_2015", "building_orders_total", "100000")

            result = cells.save_cell_review(
                root,
                "CO_2015",
                "building_orders_total",
                review_decision="accept",
            )

            suggestion = result.get("inferred_source_suggestion")
            self.assertIsNotNone(suggestion)
            self.assertEqual(suggestion["role"], "当期受注")


class ExpandToOtherYearsTests(unittest.TestCase):
    def test_preview_true_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_candidate_blocks(
                root,
                [
                    {"candidate_block_id": "CO_2015_block1", "company_year_id": "CO_2015", "raw_text": TABLE_TEXT_2015},
                    {"candidate_block_id": "CO_2016_block1", "company_year_id": "CO_2016", "raw_text": TABLE_TEXT_2016},
                ],
            )
            write_table(root / "data" / "final" / "final_master_long.csv", [])

            result = cells.expand_to_other_years(root, "CO_2015", "building_orders_total", preview=True)

            self.assertTrue(result["preview"])
            self.assertFalse((root / "data" / "review" / "review_resolved.csv").exists())
            self.assertGreaterEqual(result["target_count"], 1)
            keys = {(t["company_year_id"], t["field_id"]) for t in result["targets"]}
            self.assertIn(("CO_2016", "building_orders_total"), keys)

    def test_preview_false_fills_blank_years_only_and_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_candidate_blocks(
                root,
                [
                    {"candidate_block_id": "CO_2015_block1", "company_year_id": "CO_2015", "raw_text": TABLE_TEXT_2015},
                    {"candidate_block_id": "CO_2016_block1", "company_year_id": "CO_2016", "raw_text": TABLE_TEXT_2016},
                ],
            )
            # CO_2015 は既存値あり（=書込み禁止対象）。CO_2016は空白（=展開対象）。
            write_table(
                root / "data" / "final" / "final_master_long.csv",
                [
                    {
                        "company_year_id": "CO_2015",
                        "field_id": "building_orders_total",
                        "value_normalized": "100000",
                        "unit_normalized": "百万円",
                        "validation_status": "pass",
                    }
                ],
            )
            # review_queue.csv に CO_2016 のキーを用意（upsert_resolved_reviewsが許すキー）。
            row = {column: "" for column in REVIEW_COLUMNS}
            row.update({"company_year_id": "CO_2016", "field_id": "building_orders_total"})
            write_table(root / "data" / "review" / "review_queue.csv", [row])

            with _StubbedApplyReview():
                result = cells.expand_to_other_years(root, "CO_2015", "building_orders_total", preview=False)

            self.assertFalse(result["preview"])
            resolved_rows = read_table(root / "data" / "review" / "review_resolved.csv")
            resolved_keys = {(r["company_year_id"], r["field_id"]) for r in resolved_rows}
            # 既存値のあるCO_2015には一切書き込まれない。
            self.assertNotIn(("CO_2015", "building_orders_total"), resolved_keys)
            self.assertIn(("CO_2016", "building_orders_total"), resolved_keys)
            for r in resolved_rows:
                if (r["company_year_id"], r["field_id"]) == ("CO_2016", "building_orders_total"):
                    self.assertEqual(r["reviewer"], "source_inference")
                    self.assertEqual(r["review_decision"], "correct")


if __name__ == "__main__":
    unittest.main()
