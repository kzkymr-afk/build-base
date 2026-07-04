from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.services import semantics_store


class SemanticsStorePathTests(unittest.TestCase):
    def test_semantics_db_path_is_under_data_marts_semantics(self):
        root = Path("/some/project")
        path = semantics_store.semantics_db_path(root)
        self.assertEqual(path, root / "data" / "marts" / "semantics" / "semantics.db")

    def test_semantics_db_path_never_equals_edinet_db_path(self):
        root = Path("/some/project")
        path = semantics_store.semantics_db_path(root)
        edinet_db_path = root / "data" / "intermediate" / "edinet.db"
        self.assertNotEqual(path, edinet_db_path)


class SemanticsStoreDDLTests(unittest.TestCase):
    def test_connect_creates_db_file_at_expected_path_and_all_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                db_path = semantics_store.semantics_db_path(root)
                self.assertTrue(db_path.exists())
                tables = {
                    row[0]
                    for row in conn.execute("select name from sqlite_master where type='table'")
                }
                self.assertIn("corroborations", tables)
                self.assertIn("cell_resolutions", tables)
                self.assertIn("ai_calls", tables)
                self.assertIn("golden_values", tables)
                self.assertIn("golden_negative", tables)
            finally:
                conn.close()

    def test_init_schema_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn1 = semantics_store.connect(root)
            conn1.close()
            # 2回目の接続でもエラーにならない（CREATE IF NOT EXISTS）
            conn2 = semantics_store.connect(root)
            conn2.close()
            self.assertTrue(semantics_store.semantics_db_path(root).exists())


class SemanticsStoreUpsertTests(unittest.TestCase):
    def test_replace_corroborations_is_idempotent_by_corroboration_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                records = [
                    {
                        "company_year_id": "A_2024",
                        "field_id": "operating_income_consolidated",
                        "check_kind": "xbrl_vs_local",
                        "check_ref": "cell_pair",
                        "matched": True,
                        "primary_value": 1000.0,
                        "other_value": 1000.0,
                        "difference": 0.0,
                        "restatement_suspected": False,
                        "detail": {"extraction_method_a": "XBRL_CSV", "extraction_method_b": "LOCAL_RULE_TABLE"},
                    }
                ]
                written1 = semantics_store.replace_corroborations(conn, records, run_id="run1")
                self.assertEqual(written1, 1)
                # 同じレコードをrun_id違いで再度書き込む -> 行数は増えず1のまま(upsert)
                written2 = semantics_store.replace_corroborations(conn, records, run_id="run2")
                self.assertEqual(written2, 1)
                count = conn.execute("select count(*) from corroborations").fetchone()[0]
                self.assertEqual(count, 1)
                row = conn.execute("select run_id from corroborations").fetchone()
                self.assertEqual(row["run_id"], "run2")
            finally:
                conn.close()

    def test_replace_cell_resolutions_is_idempotent_by_company_year_and_concept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                resolutions = [
                    {
                        "company_year_id": "A_2024",
                        "concept_id": "operating_income_consolidated",
                        "value": 1000.0,
                        "corroboration_count": 2,
                        "conflict_count": 0,
                        "independent_bucket_count": 2,
                        "buckets": ["xbrl", "local_table"],
                        "resolution": "auto_confirmed",
                        "review_reason": "",
                        "sources": ["xbrl_vs_local:cell_pair"],
                    }
                ]
                written1 = semantics_store.replace_cell_resolutions(conn, resolutions, run_id="run1")
                self.assertEqual(written1, 1)
                written2 = semantics_store.replace_cell_resolutions(conn, resolutions, run_id="run2")
                self.assertEqual(written2, 1)
                count = conn.execute("select count(*) from cell_resolutions").fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                conn.close()

    def test_fetch_cell_resolutions_returns_keyed_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                resolutions = [
                    {
                        "company_year_id": "A_2024",
                        "concept_id": "operating_income_consolidated",
                        "resolution": "auto_confirmed",
                    }
                ]
                semantics_store.replace_cell_resolutions(conn, resolutions, run_id="run1")
                fetched = semantics_store.fetch_cell_resolutions(conn)
                key = ("A_2024", "operating_income_consolidated")
                self.assertIn(key, fetched)
                self.assertEqual(fetched[key]["resolution"], "auto_confirmed")
            finally:
                conn.close()


class SemanticsStoreCsvMirrorTests(unittest.TestCase):
    def test_write_csv_mirrors_writes_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_cell_resolutions(
                    conn,
                    [{"company_year_id": "A_2024", "concept_id": "f1", "resolution": "auto_confirmed"}],
                    run_id="run1",
                )
                semantics_store.replace_corroborations(
                    conn,
                    [
                        {
                            "company_year_id": "A_2024",
                            "field_id": "f1",
                            "check_kind": "identity_rule",
                            "check_ref": "rule1",
                            "matched": True,
                            "primary_value": None,
                            "other_value": None,
                            "difference": None,
                            "restatement_suspected": False,
                            "detail": {},
                        }
                    ],
                    run_id="run1",
                )
                paths = semantics_store.write_csv_mirrors(root, conn)
                self.assertTrue(paths["cell_resolutions_csv"].exists())
                self.assertTrue(paths["corroborations_csv"].exists())
                self.assertTrue(paths["golden_values_csv"].exists())
                self.assertTrue(paths["golden_negative_csv"].exists())
            finally:
                conn.close()


class SemanticsStoreGoldenValuesTests(unittest.TestCase):
    def test_replace_golden_values_is_idempotent_by_company_year_and_concept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                entries = [
                    {
                        "company_year_id": "A_2024",
                        "concept_id": "rd_expense",
                        "value": 2177.0,
                        "origin": "human_correct",
                        "locked": True,
                    }
                ]
                written1 = semantics_store.replace_golden_values(conn, entries, run_id="run1")
                self.assertEqual(written1, 1)
                written2 = semantics_store.replace_golden_values(conn, entries, run_id="run2")
                self.assertEqual(written2, 1)
                count = conn.execute("select count(*) from golden_values").fetchone()[0]
                self.assertEqual(count, 1)
                row = conn.execute("select run_id, locked from golden_values").fetchone()
                self.assertEqual(row["run_id"], "run2")
                self.assertEqual(row["locked"], 1)
            finally:
                conn.close()

    def test_replace_golden_values_is_a_full_replace_not_an_accumulate(self):
        """freeze_goldenは毎回「現在の正しい状態」を表すため、古いセルが
        golden集合から消えた場合はDBからも消えなければならない
        （replace_corroborationsと同じ完全置換方式）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_golden_values(
                    conn,
                    [{"company_year_id": "A_2024", "concept_id": "old_field", "value": 1.0, "origin": "human_correct"}],
                    run_id="run1",
                )
                semantics_store.replace_golden_values(
                    conn,
                    [{"company_year_id": "A_2024", "concept_id": "new_field", "value": 2.0, "origin": "human_correct"}],
                    run_id="run2",
                )
                fetched = semantics_store.fetch_golden_values(conn)
                self.assertNotIn(("A_2024", "old_field"), fetched)
                self.assertIn(("A_2024", "new_field"), fetched)
            finally:
                conn.close()

    def test_replace_golden_negative_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_golden_negative(
                    conn,
                    [{"company_year_id": "A_2024", "concept_id": "segment_overseas_sales", "origin": "human_not_applicable"}],
                    run_id="run1",
                )
                fetched = semantics_store.fetch_golden_negative(conn)
                self.assertIn(("A_2024", "segment_overseas_sales"), fetched)
                self.assertEqual(fetched[("A_2024", "segment_overseas_sales")]["origin"], "human_not_applicable")
            finally:
                conn.close()


class SemanticsStoreBackupTests(unittest.TestCase):
    def test_backup_returns_none_when_no_existing_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = semantics_store.backup_semantics_db(root)
            self.assertIsNone(result)

    def test_backup_copies_existing_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            conn.close()
            backup_path = semantics_store.backup_semantics_db(root)
            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())
            self.assertIn(".bak", backup_path.name)

    @staticmethod
    def _make_backup(root: Path, timestamp: str) -> Path:
        """production と同じ命名規則でダミーのバックアップファイルを作る。"""
        db_path = semantics_store.semantics_db_path(root)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        backup = db_path.with_name(f"{db_path.stem}.{timestamp}.bak{db_path.suffix}")
        backup.write_bytes(b"x")
        return backup

    def test_prune_keeps_only_last_n(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 名前昇順=時刻昇順（ゼロ埋め固定幅タイムスタンプ）: 01が最古、08が最新
            stamps = [f"20260704T1837{n:02d}Z" for n in range(1, 9)]
            for stamp in stamps:
                self._make_backup(root, stamp)

            deleted = semantics_store._prune_semantics_backups(root, keep=3)

            self.assertEqual(len(deleted), 5)
            # 削除されたのは古い5世代（01..05）
            deleted_names = sorted(p.name for p in deleted)
            self.assertEqual(deleted_names, [f"semantics.{s}.bak.db" for s in stamps[:5]])
            # 残るのは最新3世代（06..08）
            db_path = semantics_store.semantics_db_path(root)
            remaining = sorted(p.name for p in db_path.parent.glob("semantics.*.bak.db"))
            self.assertEqual(remaining, [f"semantics.{s}.bak.db" for s in stamps[5:]])

    def test_prune_noop_when_within_keep(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for n in range(1, 4):
                self._make_backup(root, f"20260704T1837{n:02d}Z")

            deleted = semantics_store._prune_semantics_backups(root, keep=5)

            self.assertEqual(deleted, [])
            db_path = semantics_store.semantics_db_path(root)
            self.assertEqual(len(list(db_path.parent.glob("semantics.*.bak.db"))), 3)

    def test_prune_ignores_live_db_and_csv_mirrors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)  # ライブ semantics.db を作る
            conn.close()
            db_path = semantics_store.semantics_db_path(root)
            csv_mirror = db_path.with_name("corroborations.csv")
            csv_mirror.write_text("company_year_id\n", encoding="utf-8")
            for n in range(1, 5):
                self._make_backup(root, f"20260704T1837{n:02d}Z")

            semantics_store._prune_semantics_backups(root, keep=0)

            # keep=0 は全バックアップを削除するが、ライブDBとCSVミラーは温存する
            self.assertTrue(db_path.exists())
            self.assertTrue(csv_mirror.exists())
            self.assertEqual(list(db_path.parent.glob("semantics.*.bak.db")), [])

    def test_backup_semantics_db_prunes_old_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)  # ライブ semantics.db を作る
            conn.close()
            # 保持上限を超える古いバックアップを用意（最新の実バックアップ名より小さいstamp）
            for n in range(1, semantics_store.SEMANTICS_DB_BACKUP_KEEP + 4):
                self._make_backup(root, f"20200101T0000{n:02d}Z")

            backup_path = semantics_store.backup_semantics_db(root)

            # 返り値契約は不変: 今作ったバックアップのPathを返し、それは残る
            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())
            db_path = semantics_store.semantics_db_path(root)
            remaining = list(db_path.parent.glob("semantics.*.bak.db"))
            self.assertEqual(len(remaining), semantics_store.SEMANTICS_DB_BACKUP_KEEP)
            self.assertIn(backup_path, remaining)


if __name__ == "__main__":
    unittest.main()
