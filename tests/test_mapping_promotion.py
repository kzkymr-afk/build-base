from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.services import mapping_promotion, semantics_store


def _write_edinet_facts(root: Path, rows) -> None:
    """rows: list of dict(company_year_id, element_id, consolidation_scope, relative_year, value)"""
    db_dir = root / "data" / "intermediate"
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_dir / "edinet.db"))
    conn.execute(
        "create table xbrl_facts (company_year_id text, element_id text, "
        "consolidation_scope text, relative_year text, value text)"
    )
    for row in rows:
        conn.execute(
            "insert into xbrl_facts (company_year_id, element_id, consolidation_scope, relative_year, value) "
            "values (?, ?, ?, ?, ?)",
            (
                row["company_year_id"],
                row["element_id"],
                row.get("consolidation_scope", "連結"),
                row.get("relative_year", "当期"),
                str(row["value"]),
            ),
        )
    conn.commit()
    conn.close()


def _write_final_master_long(root: Path, rows) -> None:
    """rows: list of dict(company_year_id, field_id, value_normalized, unit_normalized)"""
    final_dir = root / "data" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    with open(final_dir / "final_master_long.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["company_year_id", "field_id", "value_normalized", "unit_normalized"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _seed_observed_item(conn, observed_item_id: str, element_id: str, normalized_scope: str = "consolidated", unit: str = "円") -> None:
    semantics_store.replace_observed_items(
        conn,
        [
            {
                "observed_item_id": observed_item_id,
                "item_kind": "xbrl",
                "element_id": element_id,
                "normalized_scope": normalized_scope,
                "taxonomy_kind": "jppfs",
                "unit": unit,
                "source": "metric_catalog",
            }
        ],
        delete_first=False,
    )


def _seed_ai_map_proposal(conn, mapping_id: str, observed_item_id: str, concept_id: str, model: str = "claude-haiku-4-5-20251001") -> None:
    semantics_store.replace_concept_mappings(
        conn,
        [
            {
                "mapping_id": mapping_id,
                "observed_item_id": observed_item_id,
                "concept_id": concept_id,
                "action": "map",
                "status": "proposed",
                "decided_by": f"ai:{model}",
                "confidence": 0.9,
                "evidence": {"rationale": "test"},
            }
        ],
        delete_first=False,
    )


class PromoteVerifiedMapProposalsTests(unittest.TestCase):
    def test_corroborated_map_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:ProfitLoss")
            _seed_ai_map_proposal(conn, "cmap_test1", "xm_1", "profit_consolidated")
            conn.close()

            _write_edinet_facts(
                root,
                [
                    {"company_year_id": f"CO_{i}", "element_id": "jppfs_cor:ProfitLoss", "value": 14_993_000_000}
                    for i in range(4)
                ],
            )
            _write_final_master_long(
                root,
                [
                    {
                        "company_year_id": f"CO_{i}",
                        "field_id": "profit_consolidated",
                        "value_normalized": "14993",
                        "unit_normalized": "百万円",
                    }
                    for i in range(4)
                ],
            )

            result = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)
            self.assertEqual(result["corroborated"], 1)
            self.assertEqual(result["not_corroborated"], 0)

            conn = semantics_store.connect(root)
            row = conn.execute(
                "select status, decided_by from concept_mappings where mapping_id='cmap_test1'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "confirmed")
            self.assertTrue(row["decided_by"].startswith("ai:"))
            self.assertTrue(row["decided_by"].endswith("+corroboration"))

    def test_percentage_map_uses_point_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_roe", "jppfs_cor:ReturnOnEquity", unit="%")
            _seed_ai_map_proposal(conn, "cmap_roe", "xm_roe", "roe")
            conn.close()

            _write_edinet_facts(
                root,
                [
                    {"company_year_id": f"CO_{i}", "element_id": "jppfs_cor:ReturnOnEquity", "value": "8.2"}
                    for i in range(4)
                ],
            )
            _write_final_master_long(
                root,
                [
                    {"company_year_id": f"CO_{i}", "field_id": "roe", "value_normalized": "8.6", "unit_normalized": "%"}
                    for i in range(4)
                ],
            )

            result = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)

            self.assertEqual(result["corroborated"], 1)
            conn = semantics_store.connect(root)
            row = conn.execute("select status from concept_mappings where mapping_id='cmap_roe'").fetchone()
            conn.close()
            self.assertEqual(row["status"], "confirmed")

    def test_mismatched_values_not_confirmed(self):
        """ケース1相当: 概念の既存値が比率(0.353)、観測要素は金額(億単位)で一致しない。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:ShareholdersEquity")
            _seed_ai_map_proposal(conn, "cmap_test1", "xm_1", "equity_consolidated")
            conn.close()

            _write_edinet_facts(
                root,
                [
                    {"company_year_id": f"CO_{i}", "element_id": "jppfs_cor:ShareholdersEquity", "value": 141_987_000_000}
                    for i in range(4)
                ],
            )
            _write_final_master_long(
                root,
                [
                    {
                        "company_year_id": f"CO_{i}",
                        "field_id": "equity_consolidated",
                        "value_normalized": "0.353",
                        "unit_normalized": "百万円",
                    }
                    for i in range(4)
                ],
            )

            result = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)
            self.assertEqual(result["corroborated"], 0)
            self.assertEqual(result["not_corroborated"], 1)

            conn = semantics_store.connect(root)
            row = conn.execute(
                "select status, decided_by from concept_mappings where mapping_id='cmap_test1'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "proposed")
            self.assertEqual(row["decided_by"], "ai:claude-haiku-4-5-20251001")

    def test_zero_overlap_not_confirmed(self):
        """要素がxbrl_factsに存在しない(重複0件)場合はproposedのまま。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:NoSuchElement")
            _seed_ai_map_proposal(conn, "cmap_test1", "xm_1", "some_concept")
            conn.close()

            _write_edinet_facts(root, [])
            _write_final_master_long(root, [])

            result = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)
            self.assertEqual(result["corroborated"], 0)

            conn = semantics_store.connect(root)
            row = conn.execute(
                "select status from concept_mappings where mapping_id='cmap_test1'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "proposed")

    def test_low_match_rate_not_confirmed(self):
        """match_rate < 0.8 の場合はproposedのまま（rd_expense相当ケース）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:ResearchAndDevelopmentExpenses")
            _seed_ai_map_proposal(conn, "cmap_test1", "xm_1", "rd_expense")
            conn.close()

            # 10社年中、一致は7件（match_rate=0.7 < 0.8）
            edinet_rows = []
            final_rows = []
            for i in range(10):
                edinet_rows.append(
                    {"company_year_id": f"CO_{i}", "element_id": "jppfs_cor:ResearchAndDevelopmentExpenses", "value": 1_000_000_000}
                )
                if i < 7:
                    final_rows.append(
                        {"company_year_id": f"CO_{i}", "field_id": "rd_expense", "value_normalized": "1000", "unit_normalized": "百万円"}
                    )
                else:
                    final_rows.append(
                        {"company_year_id": f"CO_{i}", "field_id": "rd_expense", "value_normalized": "999999", "unit_normalized": "百万円"}
                    )
            _write_edinet_facts(root, edinet_rows)
            _write_final_master_long(root, final_rows)

            result = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)
            self.assertEqual(result["corroborated"], 0)
            self.assertEqual(result["results"][0]["overlap_count"], 10)
            self.assertEqual(result["results"][0]["match_count"], 7)

    def test_scope_empty_string_searches_without_scope_filter(self):
        """normalized_scope='' の場合はscope制約なしで検索する。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults", normalized_scope="")
            _seed_ai_map_proposal(conn, "cmap_test1", "xm_1", "total_assets_consolidated")
            conn.close()

            _write_edinet_facts(
                root,
                [
                    {
                        "company_year_id": f"CO_{i}",
                        "element_id": "jpcrp_cor:TotalAssetsIFRSSummaryOfBusinessResults",
                        "consolidation_scope": "",
                        "value": 5_000_000_000,
                    }
                    for i in range(4)
                ],
            )
            _write_final_master_long(
                root,
                [
                    {
                        "company_year_id": f"CO_{i}",
                        "field_id": "total_assets_consolidated",
                        "value_normalized": "5000",
                        "unit_normalized": "百万円",
                    }
                    for i in range(4)
                ],
            )

            result = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)
            self.assertEqual(result["corroborated"], 1)

    def test_human_confirmed_row_untouched_by_update_function(self):
        """update_concept_mapping_status を human:confirmed行に呼んでもNoop。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            semantics_store.replace_concept_mappings(
                conn,
                [
                    {
                        "mapping_id": "cmap_human1",
                        "observed_item_id": "xm_h1",
                        "concept_id": "net_sales_standalone",
                        "action": "map",
                        "status": "confirmed",
                        "decided_by": "human:reviewer_a",
                    }
                ],
                delete_first=False,
            )
            updated = semantics_store.update_concept_mapping_status(
                conn,
                "cmap_human1",
                new_status="rejected",
                new_decided_by="ai:test+corroboration",
            )
            self.assertFalse(updated)
            row = conn.execute(
                "select status, decided_by from concept_mappings where mapping_id='cmap_human1'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "confirmed")
            self.assertEqual(row["decided_by"], "human:reviewer_a")

    def test_human_confirmed_row_untouched_by_full_promote_run(self):
        """promote_verified_map_proposals実行そのものが human:confirmed 行を対象にしないこと。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_h1", "jppfs_cor:NetSales")
            semantics_store.replace_concept_mappings(
                conn,
                [
                    {
                        "mapping_id": "cmap_human1",
                        "observed_item_id": "xm_h1",
                        "concept_id": "net_sales_standalone",
                        "action": "map",
                        "status": "confirmed",
                        "decided_by": "human:reviewer_a",
                    }
                ],
                delete_first=False,
            )
            conn.close()
            _write_edinet_facts(root, [])
            _write_final_master_long(root, [])

            result = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)
            self.assertEqual(result["map_proposals_checked"], 0)

            conn = semantics_store.connect(root)
            row = conn.execute(
                "select status, decided_by from concept_mappings where mapping_id='cmap_human1'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "confirmed")
            self.assertEqual(row["decided_by"], "human:reviewer_a")

    def test_idempotent_second_run_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:ProfitLoss")
            _seed_ai_map_proposal(conn, "cmap_test1", "xm_1", "profit_consolidated")
            conn.close()

            _write_edinet_facts(
                root,
                [
                    {"company_year_id": f"CO_{i}", "element_id": "jppfs_cor:ProfitLoss", "value": 14_993_000_000}
                    for i in range(4)
                ],
            )
            _write_final_master_long(
                root,
                [
                    {
                        "company_year_id": f"CO_{i}",
                        "field_id": "profit_consolidated",
                        "value_normalized": "14993",
                        "unit_normalized": "百万円",
                    }
                    for i in range(4)
                ],
            )

            first = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)
            self.assertEqual(first["corroborated"], 1)
            second = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)
            self.assertEqual(second["map_proposals_checked"], 0)
            self.assertEqual(second["corroborated"], 0)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:ProfitLoss")
            _seed_ai_map_proposal(conn, "cmap_test1", "xm_1", "profit_consolidated")
            conn.close()

            _write_edinet_facts(
                root,
                [
                    {"company_year_id": f"CO_{i}", "element_id": "jppfs_cor:ProfitLoss", "value": 14_993_000_000}
                    for i in range(4)
                ],
            )
            _write_final_master_long(
                root,
                [
                    {
                        "company_year_id": f"CO_{i}",
                        "field_id": "profit_consolidated",
                        "value_normalized": "14993",
                        "unit_normalized": "百万円",
                    }
                    for i in range(4)
                ],
            )

            result = mapping_promotion.promote_verified_map_proposals(root, dry_run=True)
            self.assertEqual(result["corroborated"], 1)

            conn = semantics_store.connect(root)
            row = conn.execute(
                "select status from concept_mappings where mapping_id='cmap_test1'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "proposed")  # dry-runなので変化なし

    def test_independent_map_proposals_judged_separately(self):
        """同一conceptへの複数map提案は独立に判定する（ケース3相当）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_bad", "jppfs_cor:RevenueFromContractsWithCustomers")
            _seed_observed_item(conn, "xm_good", "jppfs_cor:NetSales")
            _seed_ai_map_proposal(conn, "cmap_bad", "xm_bad", "net_sales_consolidated")
            _seed_ai_map_proposal(conn, "cmap_good", "xm_good", "net_sales_consolidated")
            conn.close()

            edinet_rows = []
            final_rows = []
            for i in range(4):
                edinet_rows.append(
                    {"company_year_id": f"BADCO_{i}", "element_id": "jppfs_cor:RevenueFromContractsWithCustomers", "value": 999_000_000}
                )
            for i in range(5):
                edinet_rows.append(
                    {"company_year_id": f"GOODCO_{i}", "element_id": "jppfs_cor:NetSales", "value": 10_000_000_000}
                )
                final_rows.append(
                    {"company_year_id": f"GOODCO_{i}", "field_id": "net_sales_consolidated", "value_normalized": "10000", "unit_normalized": "百万円"}
                )
            # BADCOには対応final値なし(概念に既存値が無い) -> overlap 0
            _write_edinet_facts(root, edinet_rows)
            _write_final_master_long(root, final_rows)

            result = mapping_promotion.promote_verified_map_proposals(root, dry_run=False)
            self.assertEqual(result["not_corroborated"], 1)

            conn = semantics_store.connect(root)
            bad_row = conn.execute("select status from concept_mappings where mapping_id='cmap_bad'").fetchone()
            good_row = conn.execute("select status from concept_mappings where mapping_id='cmap_good'").fetchone()
            conn.close()
            self.assertEqual(bad_row["status"], "proposed")
            self.assertEqual(good_row["status"], "confirmed")


class AdoptNewConceptsDuplicateResolutionTests(unittest.TestCase):
    def _seed_new_concept_proposal(self, conn, mapping_id, observed_item_id, concept_name_ja, category="construction", model="claude-haiku-4-5-20251001", definition_ja="test def"):
        semantics_store.replace_concept_mappings(
            conn,
            [
                {
                    "mapping_id": mapping_id,
                    "observed_item_id": observed_item_id,
                    "concept_id": None,
                    "action": "new_concept",
                    "status": "proposed",
                    "decided_by": f"ai:{model}",
                    "confidence": 0.8,
                    "evidence": {
                        "rationale": "test",
                        "new_concept": {
                            "category": category,
                            "concept_name_ja": concept_name_ja,
                            "definition_ja": definition_ja,
                        },
                    },
                }
            ],
            delete_first=False,
        )

    def test_duplicate_concept_name_ja_merged_into_one_concept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_a", "jpcrp_cor:PriceEarningsRatio", unit="倍")
            _seed_observed_item(conn, "xm_b", "jpcrp_cor:PriceEarningsRatioOther", unit="倍")
            self._seed_new_concept_proposal(conn, "cmap_a", "xm_a", "株価収益率")
            self._seed_new_concept_proposal(conn, "cmap_b", "xm_b", "株価収益率")
            conn.close()

            result = mapping_promotion.adopt_new_concepts(root, dry_run=False)
            self.assertEqual(result["concepts_created"], 1)
            self.assertEqual(result["mappings_confirmed"], 2)

            conn = semantics_store.connect(root)
            concepts = semantics_store.fetch_canonical_concepts(conn)
            per_names = [c["concept_name_ja"] for c in concepts.values()]
            self.assertEqual(per_names.count("株価収益率"), 1)

            mappings = semantics_store.fetch_concept_mappings(conn)
            confirmed = [m for m in mappings if m["mapping_id"] in ("cmap_a", "cmap_b")]
            conn.close()
            self.assertEqual(len(confirmed), 2)
            concept_ids = {m["concept_id"] for m in confirmed}
            self.assertEqual(len(concept_ids), 1)
            for m in confirmed:
                self.assertEqual(m["status"], "confirmed")
                self.assertEqual(m["action"], "map")
                self.assertTrue(m["decided_by"].endswith("+human_adopt"))

    def test_scope_suffixed_names_not_merged(self):
        """「完成工事原価_単独」「完成工事原価_連結」は別概念として統合しない。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_a", "jppfs_cor:CostOfConstruction")
            _seed_observed_item(conn, "xm_b", "jppfs_cor:CostOfConstructionConsolidated")
            self._seed_new_concept_proposal(conn, "cmap_a", "xm_a", "完成工事原価_単独")
            self._seed_new_concept_proposal(conn, "cmap_b", "xm_b", "完成工事原価_連結")
            conn.close()

            result = mapping_promotion.adopt_new_concepts(root, dry_run=False)
            self.assertEqual(result["concepts_created"], 2)
            self.assertEqual(result["mappings_confirmed"], 2)

    def test_new_concept_id_generation_is_deterministic(self):
        cid1 = mapping_promotion.new_concept_id("株価収益率")
        cid2 = mapping_promotion.new_concept_id("株価収益率")
        self.assertEqual(cid1, cid2)
        self.assertTrue(cid1.startswith("nc_"))

    def test_data_scope_inference(self):
        self.assertEqual(mapping_promotion._infer_data_scope("完成工事原価_単独"), "standalone")
        self.assertEqual(mapping_promotion._infer_data_scope("完成工事原価_連結"), "consolidated")
        self.assertEqual(mapping_promotion._infer_data_scope("セグメント売上高_不動産"), "segment")
        self.assertEqual(mapping_promotion._infer_data_scope("株価収益率"), "")

    def test_human_confirmed_new_concept_like_row_not_touched(self):
        """action='new_concept'でも decided_by='human:...' status='confirmed' は対象外。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            semantics_store.replace_concept_mappings(
                conn,
                [
                    {
                        "mapping_id": "cmap_human_nc",
                        "observed_item_id": "xm_h",
                        "concept_id": None,
                        "action": "new_concept",
                        "status": "confirmed",
                        "decided_by": "human:reviewer_b",
                        "evidence": {"new_concept": {"concept_name_ja": "何か", "category": "x", "definition_ja": "y"}},
                    }
                ],
                delete_first=False,
            )
            conn.close()

            result = mapping_promotion.adopt_new_concepts(root, dry_run=False)
            self.assertEqual(result["new_concept_proposals_checked"], 0)
            self.assertEqual(result["concepts_created"], 0)

            conn = semantics_store.connect(root)
            row = conn.execute(
                "select status, decided_by from concept_mappings where mapping_id='cmap_human_nc'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "confirmed")
            self.assertEqual(row["decided_by"], "human:reviewer_b")

    def test_idempotent_second_run_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_a", "jpcrp_cor:PriceEarningsRatio", unit="倍")
            self._seed_new_concept_proposal(conn, "cmap_a", "xm_a", "株価収益率")
            conn.close()

            first = mapping_promotion.adopt_new_concepts(root, dry_run=False)
            self.assertEqual(first["concepts_created"], 1)
            self.assertEqual(first["mappings_confirmed"], 1)

            second = mapping_promotion.adopt_new_concepts(root, dry_run=False)
            self.assertEqual(second["new_concept_proposals_checked"], 0)
            self.assertEqual(second["concepts_created"], 0)
            self.assertEqual(second["mappings_confirmed"], 0)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_a", "jpcrp_cor:PriceEarningsRatio", unit="倍")
            self._seed_new_concept_proposal(conn, "cmap_a", "xm_a", "株価収益率")
            conn.close()

            result = mapping_promotion.adopt_new_concepts(root, dry_run=True)
            self.assertEqual(result["concepts_created"], 1)
            self.assertEqual(result["mappings_confirmed"], 1)

            conn = semantics_store.connect(root)
            concepts = semantics_store.fetch_canonical_concepts(conn)
            row = conn.execute("select status from concept_mappings where mapping_id='cmap_a'").fetchone()
            conn.close()
            self.assertEqual(len(concepts), 0)  # dry-runなので未作成
            self.assertEqual(row["status"], "proposed")


class LoadFinalMasterLongIndexTests(unittest.TestCase):
    def test_keeps_units_in_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_final_master_long(
                root,
                [
                    {"company_year_id": "CO_1", "field_id": "roe", "value_normalized": "8.5", "unit_normalized": "%"},
                    {"company_year_id": "CO_1", "field_id": "net_sales_consolidated", "value_normalized": "1000", "unit_normalized": "百万円"},
                ],
            )
            index = mapping_promotion.load_final_master_long_index(root)
            self.assertEqual(index[("CO_1", "roe")], [(8.5, "%")])
            self.assertEqual(index[("CO_1", "net_sales_consolidated")], [(1000.0, "百万円")])


if __name__ == "__main__":
    unittest.main()
