from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import write_table
from yuho_auto_extract.services import semantics_backfill, semantics_coverage, semantics_store


# ---------------------------------------------------------------------------
# 純関数テスト（DB/ファイルI/Oなし）
# ---------------------------------------------------------------------------

class ClassifyTaxonomyKindTests(unittest.TestCase):
    def test_jppfs_prefix(self):
        self.assertEqual(
            semantics_backfill.classify_taxonomy_kind("jppfs_cor:OtherNetPPE"), "jppfs"
        )

    def test_jpcrp_prefix(self):
        self.assertEqual(
            semantics_backfill.classify_taxonomy_kind(
                "jpcrp_cor:OtherRemunerationEtcByCategoryOfDirectorsAndOtherOfficers"
            ),
            "jpcrp",
        )

    def test_ifrs_prefix(self):
        self.assertEqual(
            semantics_backfill.classify_taxonomy_kind("jpigp_cor:OtherOtherIncomeIFRS"), "ifrs"
        )

    def test_extension_prefix_company_specific_asr_form(self):
        self.assertEqual(
            semantics_backfill.classify_taxonomy_kind("jpcrp030000-asr_E00053-000:Aaaa"),
            "extension",
        )

    def test_empty_element_id_is_local(self):
        self.assertEqual(semantics_backfill.classify_taxonomy_kind(""), "local")

    def test_unknown_prefix_falls_back_to_extension(self):
        self.assertEqual(semantics_backfill.classify_taxonomy_kind("unknown_ns:Foo"), "extension")


class IdGenerationDeterminismTests(unittest.TestCase):
    def test_xbrl_observed_item_id_passes_through_discovered_metric_id(self):
        self.assertEqual(
            semantics_backfill.xbrl_observed_item_id("xm_21c9a78d1cd983f1"), "xm_21c9a78d1cd983f1"
        )

    def test_local_observed_item_id_is_deterministic(self):
        id1 = semantics_backfill.local_observed_item_id("見出しA", "average_age", "ANDO_HAZAMA")
        id2 = semantics_backfill.local_observed_item_id("見出しA", "average_age", "ANDO_HAZAMA")
        self.assertEqual(id1, id2)
        self.assertTrue(id1.startswith("lt_"))

    def test_local_observed_item_id_differs_by_company(self):
        id1 = semantics_backfill.local_observed_item_id("見出しA", "average_age", "ANDO_HAZAMA")
        id2 = semantics_backfill.local_observed_item_id("見出しA", "average_age", "KAJIMA")
        self.assertNotEqual(id1, id2)

    def test_review_resolved_observed_item_id_is_deterministic(self):
        id1 = semantics_backfill.review_resolved_observed_item_id(
            "ANDO_HAZAMA_2015", "average_age", "S10081C6"
        )
        id2 = semantics_backfill.review_resolved_observed_item_id(
            "ANDO_HAZAMA_2015", "average_age", "S10081C6"
        )
        self.assertEqual(id1, id2)
        self.assertTrue(id1.startswith("rr_"))

    def test_mapping_id_is_deterministic_and_order_invariant_across_calls(self):
        id1 = semantics_backfill.mapping_id("xm_abc", "rd_expense", "map", "human:x")
        id2 = semantics_backfill.mapping_id("xm_abc", "rd_expense", "map", "human:x")
        self.assertEqual(id1, id2)
        self.assertTrue(id1.startswith("cmap_"))

    def test_mapping_id_differs_by_decided_by(self):
        id1 = semantics_backfill.mapping_id("xm_abc", "rd_expense", "map", "human:x")
        id2 = semantics_backfill.mapping_id("xm_abc", "rd_expense", "map", "deterministic:y")
        self.assertNotEqual(id1, id2)

    def test_exclusion_mapping_id_is_deterministic(self):
        id1 = semantics_backfill.exclusion_mapping_id("INFR", "advertising_expense", "", "")
        id2 = semantics_backfill.exclusion_mapping_id("INFR", "advertising_expense", "", "")
        self.assertEqual(id1, id2)

    def test_company_from_company_year_strips_4digit_year_suffix(self):
        self.assertEqual(
            semantics_backfill._company_from_company_year("ANDO_HAZAMA_2015"), "ANDO_HAZAMA"
        )

    def test_company_from_company_year_handles_no_year_suffix(self):
        self.assertEqual(semantics_backfill._company_from_company_year("KAJIMA"), "KAJIMA")


class SplitMatchedFieldIdsTests(unittest.TestCase):
    def test_splits_semicolon_separated(self):
        self.assertEqual(
            semantics_backfill._split_matched_field_ids("a;b;c"), ["a", "b", "c"]
        )

    def test_empty_value_returns_empty_list(self):
        self.assertEqual(semantics_backfill._split_matched_field_ids(""), [])
        self.assertEqual(semantics_backfill._split_matched_field_ids(None), [])


# ---------------------------------------------------------------------------
# field_mappings.csv 変換規則のテーブル駆動テスト
# ---------------------------------------------------------------------------

class FieldMappingsRowConversionTests(unittest.TestCase):
    def test_accepted_with_target_becomes_confirmed_map(self):
        row = {
            "discovered_metric_id": "xm_e96495cd85ed04f7",
            "target_field_id": "rd_expense",
            "mapping_status": "accepted",
            "mapping_note": "",
        }
        result = semantics_backfill._mapping_from_field_mappings_row(row)
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "map")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["concept_id"], "rd_expense")
        self.assertEqual(result["decided_by"], semantics_backfill.DECIDED_BY_FIELD_MAPPINGS)

    def test_rejected_with_target_becomes_ignore_with_rejected_against_evidence(self):
        row = {
            "discovered_metric_id": "xm_30777320581de38d",
            "target_field_id": "gross_profit_consolidated",
            "mapping_status": "rejected",
            "mapping_note": "",
        }
        result = semantics_backfill._mapping_from_field_mappings_row(row)
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "ignore")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["concept_id"], "")
        self.assertEqual(result["evidence"]["rejected_against_field_id"], "gross_profit_consolidated")

    def test_rejected_without_target_becomes_ignore_no_candidate(self):
        row = {
            "discovered_metric_id": "xm_zzz",
            "target_field_id": "",
            "mapping_status": "rejected",
            "mapping_note": "まとめて使わない",
        }
        result = semantics_backfill._mapping_from_field_mappings_row(row)
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "ignore")
        self.assertEqual(result["status"], "confirmed")
        self.assertNotIn("rejected_against_field_id", result["evidence"])

    def test_missing_discovered_metric_id_returns_none(self):
        row = {"discovered_metric_id": "", "target_field_id": "rd_expense", "mapping_status": "accepted"}
        self.assertIsNone(semantics_backfill._mapping_from_field_mappings_row(row))

    def test_unknown_status_returns_none(self):
        row = {"discovered_metric_id": "xm_zzz", "target_field_id": "", "mapping_status": "unmapped"}
        self.assertIsNone(semantics_backfill._mapping_from_field_mappings_row(row))


# ---------------------------------------------------------------------------
# review_resolved.csv 変換規則のテーブル駆動テスト
# ---------------------------------------------------------------------------

class ReviewResolvedRowConversionTests(unittest.TestCase):
    def test_correct_applied_becomes_confirmed_map(self):
        row = {
            "company_year_id": "ANDO_HAZAMA_2015",
            "field_id": "average_age",
            "source_doc_id": "S10081C6",
            "review_decision": "correct",
            "applied_status": "applied",
            "corrected_value": "45.5",
            "source_heading": "５【従業員の状況】",
            "source_quote": "quote",
            "reviewer_note": "note",
            "reviewed_at": "2026-01-01",
        }
        item_row, map_row = semantics_backfill._mappings_from_review_resolved_row(row)
        self.assertIsNotNone(item_row)
        self.assertIsNotNone(map_row)
        self.assertEqual(map_row["action"], "map")
        self.assertEqual(map_row["status"], "confirmed")
        self.assertEqual(map_row["concept_id"], "average_age")
        self.assertEqual(map_row["decided_by"], semantics_backfill.DECIDED_BY_REVIEW_RESOLVED)
        self.assertTrue(map_row["evidence"]["has_citation"])

    def test_accept_applied_becomes_confirmed_map(self):
        row = {
            "company_year_id": "ANDO_HAZAMA_2015",
            "field_id": "rd_expense",
            "source_doc_id": "S10081C6",
            "review_decision": "accept",
            "applied_status": "applied",
            "corrected_value": "2177.0",
        }
        item_row, map_row = semantics_backfill._mappings_from_review_resolved_row(row)
        self.assertIsNotNone(map_row)
        self.assertEqual(map_row["action"], "map")

    def test_not_applicable_becomes_confirmed_ignore_with_company_scope(self):
        row = {
            "company_year_id": "INFR_2021",
            "field_id": "advertising_expense",
            "source_doc_id": "S1",
            "review_decision": "not_applicable",
            "applied_status": "not_applicable",
            "reviewer_note": "対象外",
        }
        item_row, map_row = semantics_backfill._mappings_from_review_resolved_row(row)
        self.assertIsNotNone(item_row)
        self.assertIsNotNone(map_row)
        self.assertEqual(map_row["action"], "ignore")
        self.assertEqual(map_row["status"], "confirmed")
        self.assertEqual(map_row["concept_id"], "")
        self.assertEqual(map_row["company_scope"], "INFR")

    def test_no_citation_marks_has_citation_false(self):
        row = {
            "company_year_id": "A_2020",
            "field_id": "f1",
            "source_doc_id": "S1",
            "review_decision": "accept",
            "applied_status": "applied",
            "corrected_value": "1.0",
            "source_heading": "",
        }
        item_row, _ = semantics_backfill._mappings_from_review_resolved_row(row)
        self.assertFalse(item_row["sample_values"]["has_citation"])

    def test_unmatched_combination_skips_both(self):
        row = {
            "company_year_id": "A_2020",
            "field_id": "f1",
            "review_decision": "correct",
            "applied_status": "pending",
        }
        item_row, map_row = semantics_backfill._mappings_from_review_resolved_row(row)
        self.assertIsNone(item_row)
        self.assertIsNone(map_row)


# ---------------------------------------------------------------------------
# company_field_exclusions.csv 変換規則
# ---------------------------------------------------------------------------

class ExclusionRowConversionTests(unittest.TestCase):
    def test_exclusion_row_allows_empty_observed_item_id(self):
        row = {
            "company_id": "INFR",
            "field_id": "advertising_expense",
            "start_year": "",
            "end_year": "",
            "reason": "対象外",
        }
        result = semantics_backfill._mapping_from_exclusion_row(row)
        self.assertIsNotNone(result)
        self.assertEqual(result["observed_item_id"], "")
        self.assertEqual(result["action"], "ignore")
        self.assertEqual(result["company_scope"], "INFR")

    def test_exclusion_row_with_year_range(self):
        row = {
            "company_id": "MAEDA",
            "field_id": "cost_expense",
            "start_year": "2021",
            "end_year": "",
            "reason": "reason",
        }
        result = semantics_backfill._mapping_from_exclusion_row(row)
        self.assertEqual(result["valid_from_year"], "2021")
        self.assertIsNone(result["valid_to_year"])

    def test_missing_company_or_field_returns_none(self):
        self.assertIsNone(
            semantics_backfill._mapping_from_exclusion_row({"company_id": "", "field_id": "x"})
        )


# ---------------------------------------------------------------------------
# 重複解決の純関数テスト
# ---------------------------------------------------------------------------

class DedupeTests(unittest.TestCase):
    def test_dedupe_by_id_keeps_last_occurrence(self):
        items = [
            {"observed_item_id": "a", "label_ja": "first"},
            {"observed_item_id": "a", "label_ja": "second"},
        ]
        result = semantics_backfill._dedupe_by_id(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label_ja"], "second")

    def test_dedupe_by_id_skips_empty_id(self):
        items = [{"observed_item_id": "", "label_ja": "x"}]
        result = semantics_backfill._dedupe_by_id(items)
        self.assertEqual(result, [])

    def test_dedupe_mappings_distinct_decided_by_coexist(self):
        m1 = semantics_backfill._mapping_row(
            observed_item_id="xm_1", concept_id="rd_expense", action="map",
            status="proposed", decided_by="deterministic:x",
        )
        m2 = semantics_backfill._mapping_row(
            observed_item_id="xm_1", concept_id="rd_expense", action="map",
            status="confirmed", decided_by="human:y",
        )
        result = semantics_backfill._dedupe_mappings([m1, m2])
        self.assertEqual(len(result), 2)

    def test_dedupe_mappings_same_id_keeps_last(self):
        m1 = semantics_backfill._mapping_row(
            observed_item_id="xm_1", concept_id="rd_expense", action="map",
            status="proposed", decided_by="human:y", evidence={"v": 1},
        )
        m2 = semantics_backfill._mapping_row(
            observed_item_id="xm_1", concept_id="rd_expense", action="map",
            status="confirmed", decided_by="human:y", evidence={"v": 2},
        )
        result = semantics_backfill._dedupe_mappings([m1, m2])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["evidence"]["v"], 2)


# ---------------------------------------------------------------------------
# semantics_store.py の対応層3テーブル DDL / upsert テスト
# ---------------------------------------------------------------------------

class SemanticsStoreP4aDDLTests(unittest.TestCase):
    def test_connect_creates_p4a_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                tables = {
                    row[0] for row in conn.execute("select name from sqlite_master where type='table'")
                }
                self.assertIn("observed_items", tables)
                self.assertIn("canonical_concepts", tables)
                self.assertIn("concept_mappings", tables)
            finally:
                conn.close()

    def test_replace_observed_items_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                items = [
                    {
                        "observed_item_id": "xm_1",
                        "item_kind": "xbrl",
                        "element_id": "jppfs_cor:Foo",
                        "taxonomy_kind": "jppfs",
                        "source": "metric_catalog",
                    }
                ]
                n1 = semantics_store.replace_observed_items(conn, items)
                n2 = semantics_store.replace_observed_items(conn, items)
                self.assertEqual(n1, 1)
                self.assertEqual(n2, 1)
                rows = semantics_store.fetch_observed_items(conn)
                self.assertEqual(len(rows), 1)
                self.assertIn("xm_1", rows)
            finally:
                conn.close()

    def test_replace_concept_mappings_allows_empty_observed_item_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                mappings = [
                    {
                        "mapping_id": "cmap_x",
                        "observed_item_id": "",
                        "concept_id": "advertising_expense",
                        "action": "ignore",
                        "status": "confirmed",
                        "decided_by": "human:company_field_exclusions_backfill",
                        "company_scope": "INFR",
                    }
                ]
                n = semantics_store.replace_concept_mappings(conn, mappings)
                self.assertEqual(n, 1)
                rows = semantics_store.fetch_concept_mappings(conn)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["observed_item_id"], "")
            finally:
                conn.close()

    def test_upsert_canonical_concepts_updates_existing_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.upsert_canonical_concepts(
                    conn, [{"concept_id": "rd_expense", "concept_name_ja": "研究開発費"}]
                )
                semantics_store.upsert_canonical_concepts(
                    conn, [{"concept_id": "rd_expense", "concept_name_ja": "研究開発費_更新"}]
                )
                rows = semantics_store.fetch_canonical_concepts(conn)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows["rd_expense"]["concept_name_ja"], "研究開発費_更新")
            finally:
                conn.close()

    def test_write_csv_mirrors_includes_p4a_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.upsert_canonical_concepts(conn, [{"concept_id": "rd_expense"}])
                paths = semantics_store.write_csv_mirrors(root, conn)
                self.assertIn("observed_items_csv", paths)
                self.assertIn("canonical_concepts_csv", paths)
                self.assertIn("concept_mappings_csv", paths)
                self.assertTrue(paths["canonical_concepts_csv"].exists())
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# backfill_semantics() 統合テスト — 実プロジェクトの config/data を読む
# ---------------------------------------------------------------------------

def _real_project_root() -> Path:
    # tests/ の1つ上がプロジェクトルート
    return Path(__file__).resolve().parent.parent


@unittest.skipUnless(
    (_real_project_root() / "config" / "field_definition.csv").exists(),
    "実プロジェクトのconfig/dataが無い環境ではスキップ",
)
class BackfillSemanticsIntegrationTests(unittest.TestCase):
    """実データ（config/field_definition.csv, data/marts/xbrl_discovered_metrics/*,
    data/final/source_audit.csv, data/review/review_resolved.csv,
    config/company_field_exclusions.csv）を読み、一時rootのsemantics.dbへ書く。

    実データを直接汚さないよう、data/marts/semantics/semantics.db だけは
    一時ディレクトリに向ける（他のsrc設定・dataファイルは実rootを直接参照する
    read-onlyアクセスのため安全）。ただし backfill_semantics() は root配下の
    data/marts/semantics/semantics.db に書き込む実装のため、実rootを直接
    渡すとGitで追跡中の実データ semantics.db を書き換えてしまう。
    そのため一時ディレクトリに実dataをシンボリックリンクし、
    semantics.db だけが一時ディレクトリ固有になるようにする。
    """

    def _build_shadow_root(self, tmp: Path) -> Path:
        real_root = _real_project_root()
        shadow_root = tmp / "shadow"
        shadow_root.mkdir()
        for name in ("config", "data"):
            src = real_root / name
            dst = shadow_root / name
            dst.symlink_to(src, target_is_directory=True)
        # data/marts/semantics だけ書き込み可能な実ディレクトリに差し替える
        # (シンボリックリンク越しの生DB書き込みで実データを汚さないため)
        semantics_dir = shadow_root / "data" / "marts" / "semantics"
        real_data_dir = shadow_root / "data"
        real_data_dir.unlink()
        # data 全体を実体コピーせず、marts/semantics のみ差し替えるため
        # data 直下を実ディレクトリとして再構築し、他サブディレクトリを
        # シンボリックリンクする。
        real_data_dir.mkdir()
        for child in (real_root / "data").iterdir():
            if child.name == "marts":
                marts_dst = real_data_dir / "marts"
                marts_dst.mkdir()
                for marts_child in child.iterdir():
                    if marts_child.name == "semantics":
                        continue
                    (marts_dst / marts_child.name).symlink_to(marts_child, target_is_directory=marts_child.is_dir())
                # semantics ディレクトリのみ実体（空、backfillが書く）
                (marts_dst / "semantics").mkdir()
            else:
                (real_data_dir / child.name).symlink_to(child, target_is_directory=child.is_dir())
        return shadow_root

    def test_backfill_is_idempotent_and_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow_root = self._build_shadow_root(Path(tmp))

            result1 = semantics_backfill.backfill_semantics(shadow_root)
            result2 = semantics_backfill.backfill_semantics(shadow_root)

            self.assertEqual(result1["observed_items_total"], result2["observed_items_total"])
            self.assertEqual(result1["concept_mappings_total"], result2["concept_mappings_total"])
            # P4b（field_definition増分エンリッチ）で新概念27件が追加され65→92行になった。
            # backfill_semantics は field_definition.csv の全行数分をupsertするため件数もそれに追随する。
            self.assertEqual(result1["concepts_upserted"], 92)
            self.assertGreater(result1["observed_items_total"], 0)
            self.assertGreater(result1["concept_mappings_total"], 0)

            conn = semantics_store.connect(shadow_root)
            try:
                observed_count = conn.execute("select count(*) from observed_items").fetchone()[0]
                mapping_count = conn.execute("select count(*) from concept_mappings").fetchone()[0]
                concept_count = conn.execute("select count(*) from canonical_concepts").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(observed_count, result1["observed_items_total"])
            self.assertEqual(mapping_count, result1["concept_mappings_total"])
            self.assertEqual(concept_count, 92)

    def test_exclusion_mappings_have_empty_observed_item_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow_root = self._build_shadow_root(Path(tmp))
            semantics_backfill.backfill_semantics(shadow_root)
            conn = semantics_store.connect(shadow_root)
            try:
                rows = conn.execute(
                    "select * from concept_mappings where decided_by = ?",
                    (semantics_backfill.DECIDED_BY_EXCLUSIONS,),
                ).fetchall()
            finally:
                conn.close()
            self.assertGreater(len(rows), 0)
            for row in rows:
                self.assertEqual(row["observed_item_id"], "")

    def test_coverage_report_builds_from_backfilled_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow_root = self._build_shadow_root(Path(tmp))
            semantics_backfill.backfill_semantics(shadow_root)
            result = semantics_coverage.build_and_write_coverage_report(shadow_root)
            summary = result["summary"]
            self.assertGreater(summary["observed_total"], 0)
            self.assertGreater(summary["mappings_total"], 0)
            self.assertTrue(result["report_json_path"].exists())
            self.assertTrue(result["report_md_path"].exists())
            self.assertTrue(result["report_csv_path"].exists())


if __name__ == "__main__":
    unittest.main()
