from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.services import golden, pipeline, semantics_store, source_inference as si


# 合成テーブル（tests/test_source_inference.py の SyntheticTableTests と同じ業界標準様式）。
# 前事業年度/当事業年度の2期分が並記され、建築/土木/計の3行×5列(前期繰越/当期受注/計/
# 当期完成/次期繰越)が恒等式を満たす。company_year_id を跨いで使い回すため、
# 当期(current)行の値だけを各年度のcandidate_blockに割り当てる想定で使う。
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

# 単年度のみのケース用（他社。複数年度成立しないため candidate_single_year になるはず）。
TABLE_TEXT_SINGLE_YEAR = (
    "(1）受注工事高、完成工事高及び次期繰越工事高\n"
    "期別\n区分\n前期繰越\n工事高\n(百万円)\n当期受注\n工事高\n(百万円)\n計\n(百万円)\n"
    "当期完成\n工事高\n(百万円)\n次期繰越\n工事高\n(百万円)\n"
    "当事業年度\n"
    "土木工事\n50,000\n30,000\n80,000\n20,000\n60,000\n"
    "建築工事\n70,000\n40,000\n110,000\n35,000\n75,000\n"
    "計\n120,000\n70,000\n190,000\n55,000\n135,000\n"
)


def _write_candidate_blocks(root: Path, blocks) -> None:
    """blocks: list of dict(candidate_block_id, company_year_id, section_name, raw_text)."""
    db_dir = root / "data" / "intermediate"
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_dir / "edinet.db"))
    conn.execute(
        "create table candidate_blocks (candidate_block_id text primary key, company_year_id text, "
        "source_doc_id text, section_name text, locator_score real, heading_text text, row_json text not null)"
    )
    for block in blocks:
        conn.execute(
            "insert into candidate_blocks (candidate_block_id, company_year_id, section_name, row_json) "
            "values (?, ?, ?, ?)",
            (
                block["candidate_block_id"],
                block["company_year_id"],
                block.get("section_name", "orders_backlog"),
                _row_json(block),
            ),
        )
    conn.commit()
    conn.close()


def _row_json(block) -> str:
    import json

    return json.dumps(
        {
            "candidate_block_id": block["candidate_block_id"],
            "company_year_id": block["company_year_id"],
            "section_name": block.get("section_name", "orders_backlog"),
            "raw_text": block["raw_text"],
        },
        ensure_ascii=False,
    )


def _write_final_master_long(root: Path, rows) -> None:
    """rows: list of dict(company_year_id, field_id, value_normalized, unit_normalized, validation_status)."""
    columns = ["company_year_id", "field_id", "value_normalized", "unit_normalized", "validation_status"]
    full_rows = [{col: row.get(col, "") for col in columns} for row in rows]
    write_table(root / "data" / "final" / "final_master_long.csv", full_rows)


def _write_review_queue(root: Path, keys) -> None:
    """keys: iterable of (company_year_id, field_id). review_queue.csvにダミー行を用意する
    （reviews.upsert_resolved_reviews がキュー内キーのみ書込みを許すため）。"""
    from yuho_auto_extract.review_queue import REVIEW_COLUMNS

    rows = []
    for company_year_id, field_id in keys:
        row = {column: "" for column in REVIEW_COLUMNS}
        row.update({"company_year_id": company_year_id, "field_id": field_id})
        rows.append(row)
    write_table(root / "data" / "review" / "review_queue.csv", rows)


class BuildPromotionPlanTests(unittest.TestCase):
    def test_multi_year_high_confidence_promotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_candidate_blocks(
                root,
                [
                    {
                        "candidate_block_id": "CO_2015_block1",
                        "company_year_id": "CO_2015",
                        "raw_text": TABLE_TEXT_2015,
                    },
                    {
                        "candidate_block_id": "CO_2016_block1",
                        "company_year_id": "CO_2016",
                        "raw_text": TABLE_TEXT_2016,
                    },
                ],
            )
            _write_final_master_long(root, [])  # 欠落セルのみ（既存値なし）

            plan = si.build_promotion_plan(root)

            promote_keys = {(e["company_year_id"], e["field_id"]) for e in plan["promote"]}
            self.assertIn(("CO_2015", "building_orders_total"), promote_keys)
            self.assertIn(("CO_2016", "building_orders_total"), promote_keys)
            self.assertIn(("CO_2015", "completed_building"), promote_keys)
            self.assertIn(("CO_2016", "backlog_building_next"), promote_keys)
            self.assertEqual(plan["candidate_single_year"], [])

            promote_by_key = {(e["company_year_id"], e["field_id"]): e for e in plan["promote"]}
            self.assertEqual(promote_by_key[("CO_2015", "building_orders_total")]["value"], 100000.0)
            self.assertEqual(promote_by_key[("CO_2016", "backlog_building_next")]["value"], 205000.0)

    def test_single_year_only_stays_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_candidate_blocks(
                root,
                [
                    {
                        "candidate_block_id": "SOLO_2020_block1",
                        "company_year_id": "SOLO_2020",
                        "raw_text": TABLE_TEXT_SINGLE_YEAR,
                    },
                ],
            )
            _write_final_master_long(root, [])

            plan = si.build_promotion_plan(root)

            promote_keys = {(e["company_year_id"], e["field_id"]) for e in plan["promote"]}
            candidate_keys = {(e["company_year_id"], e["field_id"]) for e in plan["candidate_single_year"]}
            self.assertEqual(promote_keys, set())
            self.assertIn(("SOLO_2020", "building_orders_total"), candidate_keys)

    def test_existing_value_cell_is_excluded_from_promote_and_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_candidate_blocks(
                root,
                [
                    {
                        "candidate_block_id": "CO_2015_block1",
                        "company_year_id": "CO_2015",
                        "raw_text": TABLE_TEXT_2015,
                    },
                    {
                        "candidate_block_id": "CO_2016_block1",
                        "company_year_id": "CO_2016",
                        "raw_text": TABLE_TEXT_2016,
                    },
                ],
            )
            # building_orders_total は既に値がある（=既存値。絶対制約: 一切書かない）。
            _write_final_master_long(
                root,
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

            plan = si.build_promotion_plan(root)

            promote_keys = {(e["company_year_id"], e["field_id"]) for e in plan["promote"]}
            candidate_keys = {(e["company_year_id"], e["field_id"]) for e in plan["candidate_single_year"]}
            self.assertNotIn(("CO_2015", "building_orders_total"), promote_keys)
            self.assertNotIn(("CO_2015", "building_orders_total"), candidate_keys)
            # 他のfieldは引き続きpromote対象になり得る。
            self.assertIn(("CO_2015", "completed_building"), promote_keys)

    def test_validation_fail_existing_value_with_deviation_becomes_suspect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_candidate_blocks(
                root,
                [
                    {
                        "candidate_block_id": "CO_2015_block1",
                        "company_year_id": "CO_2015",
                        "raw_text": TABLE_TEXT_2015,
                    },
                    {
                        "candidate_block_id": "CO_2016_block1",
                        "company_year_id": "CO_2016",
                        "raw_text": TABLE_TEXT_2016,
                    },
                ],
            )
            # 既存値(999999)はvalidation_status=failで、恒等式フィット値(100000)と大きく乖離。
            _write_final_master_long(
                root,
                [
                    {
                        "company_year_id": "CO_2015",
                        "field_id": "building_orders_total",
                        "value_normalized": "999999",
                        "unit_normalized": "百万円",
                        "validation_status": "fail",
                    }
                ],
            )

            plan = si.build_promotion_plan(root)

            suspects = {(e["company_year_id"], e["field_id"]): e for e in plan["suspect_existing_values"]}
            self.assertIn(("CO_2015", "building_orders_total"), suspects)
            entry = suspects[("CO_2015", "building_orders_total")]
            self.assertEqual(entry["existing_value"], 999999.0)
            self.assertEqual(entry["recovered_value"], 100000.0)
            self.assertEqual(entry["validation_status"], "fail")
            # 既存値は一切書き換えない: promote/candidateどちらにも出現しない。
            promote_keys = {(e["company_year_id"], e["field_id"]) for e in plan["promote"]}
            candidate_keys = {(e["company_year_id"], e["field_id"]) for e in plan["candidate_single_year"]}
            self.assertNotIn(("CO_2015", "building_orders_total"), promote_keys)
            self.assertNotIn(("CO_2015", "building_orders_total"), candidate_keys)


class _StubbedApplyReview:
    """apply_promotion_plan が最後に呼ぶ pipeline.apply_review (=export-final以降の
    フルパイプライン再実行) をスタブに差し替える。export-finalはconfig/company_master.xlsx
    等フルセットのconfigを要求するため、tests/test_web_services.py の
    test_reextract_with_review_runs_reextract_then_saved_review_apply と同じ
    モジュールレベル монkeypatchパターンを使う（apply_promotion_planはこの呼び出し結果を
    result["apply_review"]で返すのみで、実際にexport-finalが動くかどうかの検証は
    test_web_services.py 側の責務。ここではapply_promotion_plan自身の書込み経路
    （reviews.upsert_resolved_reviews呼び出し）だけを検証する）。
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


class ApplyPromotionPlanTests(unittest.TestCase):
    def _seed_multi_year(self, root: Path) -> None:
        _write_candidate_blocks(
            root,
            [
                {
                    "candidate_block_id": "CO_2015_block1",
                    "company_year_id": "CO_2015",
                    "raw_text": TABLE_TEXT_2015,
                },
                {
                    "candidate_block_id": "CO_2016_block1",
                    "company_year_id": "CO_2016",
                    "raw_text": TABLE_TEXT_2016,
                },
            ],
        )
        _write_final_master_long(root, [])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_multi_year(root)
            plan = si.build_promotion_plan(root)
            self.assertTrue(plan["promote"])

            result = si.apply_promotion_plan(root, plan, dry_run=True)

            self.assertTrue(result["dry_run"])
            self.assertEqual(result["applied"], 0)
            self.assertEqual(result["planned"], len(plan["promote"]))
            self.assertFalse((root / "data" / "review" / "review_resolved.csv").exists())
            db_path = root / "data" / "marts" / "semantics" / "semantics.db"
            self.assertFalse(db_path.exists())

    def test_apply_writes_reviewer_source_inference_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_multi_year(root)
            plan = si.build_promotion_plan(root)
            promote_keys = [(e["company_year_id"], e["field_id"]) for e in plan["promote"]]
            self.assertTrue(promote_keys)
            _write_review_queue(root, promote_keys)

            with _StubbedApplyReview() as stub:
                result = si.apply_promotion_plan(root, plan, dry_run=False)
            self.assertEqual(len(stub.calls), 1)

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["applied"], len(promote_keys))
            self.assertEqual(result["skipped_existing_review"], 0)

            resolved_rows = read_table(root / "data" / "review" / "review_resolved.csv")
            resolved_by_key = {
                (str(r.get("company_year_id")), str(r.get("field_id"))): r for r in resolved_rows
            }
            for key in promote_keys:
                row = resolved_by_key[key]
                self.assertEqual(row["reviewer"], "source_inference")
                self.assertEqual(row["review_decision"], "correct")
                self.assertTrue(str(row["corrected_value"]))

            # learned_label_patterns にも 'promoted' として記録される。
            conn = semantics_store.connect(root)
            try:
                patterns = semantics_store.fetch_learned_label_patterns(conn)
            finally:
                conn.close()
            statuses = {p["status"] for p in patterns.values()}
            self.assertIn("promoted", statuses)

    def test_apply_skips_cells_with_existing_review_resolved_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_multi_year(root)
            plan = si.build_promotion_plan(root)
            promote_keys = [(e["company_year_id"], e["field_id"]) for e in plan["promote"]]
            self.assertTrue(promote_keys)
            _write_review_queue(root, promote_keys)

            # 1件だけ、先に人手判断が確定済みという状況を作る（上書き禁止の対象）。
            pre_key = promote_keys[0]
            from yuho_auto_extract.services import reviews as reviews_service

            reviews_service.upsert_resolved_reviews(
                root,
                [
                    {
                        "company_year_id": pre_key[0],
                        "field_id": pre_key[1],
                        "review_decision": "correct",
                        "corrected_value": "1",
                        "reviewer": "human_reviewer",
                    }
                ],
            )

            with _StubbedApplyReview():
                result = si.apply_promotion_plan(root, plan, dry_run=False)

            self.assertEqual(result["skipped_existing_review"], 1)
            self.assertEqual(result["applied"], len(promote_keys) - 1)

            resolved_rows = read_table(root / "data" / "review" / "review_resolved.csv")
            resolved_by_key = {
                (str(r.get("company_year_id")), str(r.get("field_id"))): r for r in resolved_rows
            }
            # 既存レビュー行(human_reviewer)は上書きされていない。
            self.assertEqual(resolved_by_key[pre_key]["reviewer"], "human_reviewer")
            self.assertEqual(resolved_by_key[pre_key]["corrected_value"], "1")


class GoldenOriginSourceInferenceTests(unittest.TestCase):
    """freeze_golden が reviewer='source_inference' の行を専用origin(gated)に分類すること。"""

    def test_reviewer_source_inference_becomes_gated_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_table(
                root / "data" / "review" / "review_resolved.csv",
                [
                    {
                        "company_year_id": "CO_2015",
                        "field_id": "building_orders_total",
                        "review_decision": "correct",
                        "corrected_value": "100000.0",
                        "applied_status": "applied",
                        "applied_value": "100000.0",
                        "reviewer": "source_inference",
                    }
                ],
            )

            result = golden.freeze_golden(root)

            self.assertEqual(result["golden_cell_count"], 1)
            self.assertEqual(result["by_origin"].get("source_inference"), 1)

            conn = semantics_store.connect(root)
            try:
                fetched = semantics_store.fetch_golden_values(conn)
            finally:
                conn.close()
            row = fetched[("CO_2015", "building_orders_total")]
            self.assertEqual(row["value"], 100000.0)
            self.assertEqual(row["origin"], "source_inference")
            # 機械由来なのでロックはしない(人間ロックのhuman_correct/acceptとは区別)。
            self.assertEqual(row["locked"], 0)


if __name__ == "__main__":
    unittest.main()
