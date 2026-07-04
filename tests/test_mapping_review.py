from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.services import mapping_review, semantics_store


def _seed_observed_item(
    conn,
    observed_item_id: str,
    element_id: str,
    *,
    label_ja: str = "",
    normalized_scope: str = "consolidated",
    unit: str = "円",
) -> None:
    semantics_store.replace_observed_items(
        conn,
        [
            {
                "observed_item_id": observed_item_id,
                "item_kind": "xbrl",
                "element_id": element_id,
                "element_local_name": element_id,
                "label_ja": label_ja,
                "normalized_scope": normalized_scope,
                "taxonomy_kind": "jppfs",
                "unit": unit,
                "source": "metric_catalog",
                "sample_values": {"sample_value_display": "1,000"},
            }
        ],
        delete_first=False,
    )


def _seed_concept(conn, concept_id: str, concept_name_ja: str) -> None:
    semantics_store.upsert_canonical_concepts(
        conn,
        [
            {
                "concept_id": concept_id,
                "concept_name_ja": concept_name_ja,
                "category": "financial",
                "data_scope": "consolidated",
                "target_unit": "円",
                "status": "active",
            }
        ],
    )


def _seed_mapping(
    conn,
    mapping_id: str,
    *,
    observed_item_id: str,
    concept_id: str,
    action: str = "map",
    status: str = "proposed",
    decided_by: str = "ai:claude-haiku-4-5-20251001",
    confidence=0.9,
    evidence: dict | None = None,
) -> None:
    semantics_store.replace_concept_mappings(
        conn,
        [
            {
                "mapping_id": mapping_id,
                "observed_item_id": observed_item_id,
                "concept_id": concept_id,
                "action": action,
                "status": status,
                "decided_by": decided_by,
                "confidence": confidence,
                "evidence": evidence or {"rationale": "test rationale"},
            }
        ],
        delete_first=False,
    )


def _write_edinet_facts(root: Path, rows) -> None:
    db_dir = root / "data" / "intermediate"
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_dir / "edinet.db"))
    conn.execute(
        "create table xbrl_facts (company_year_id text, element_id text, consolidation_scope text, relative_year text, value text)"
    )
    for row in rows:
        conn.execute(
            "insert into xbrl_facts values (?, ?, ?, ?, ?)",
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
    out = root / "data" / "final" / "final_master_long.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["company_year_id", "field_id", "value_normalized", "unit_normalized"])
        writer.writeheader()
        writer.writerows(rows)


class ReadMappingProposalsTests(unittest.TestCase):
    def test_returns_only_proposed_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:NetSales", label_ja="売上高")
            _seed_observed_item(conn, "xm_2", "jppfs_cor:Roe")
            _seed_concept(conn, "net_sales_standalone", "売上高")
            _seed_mapping(conn, "cmap_proposed", observed_item_id="xm_1", concept_id="net_sales_standalone", status="proposed")
            _seed_mapping(
                conn,
                "cmap_confirmed",
                observed_item_id="xm_2",
                concept_id="net_sales_standalone",
                status="confirmed",
                decided_by="human:reviewer_a",
            )
            conn.close()

            result = mapping_review.read_mapping_proposals(root)
            self.assertEqual(result["total"], 1)
            ids = [p["mapping_id"] for p in result["proposals"]]
            self.assertEqual(ids, ["cmap_proposed"])
            self.assertEqual(result["proposals"][0]["observed_item"]["label_ja"], "売上高")
            self.assertEqual(result["proposals"][0]["concept"]["concept_name_ja"], "売上高")
            self.assertEqual(result["proposals"][0]["rationale"], "test rationale")

    def test_filters_by_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:NetSales")
            _seed_observed_item(conn, "xm_2", "jppfs_cor:Roe")
            _seed_mapping(conn, "cmap_map", observed_item_id="xm_1", concept_id="", action="map")
            _seed_mapping(conn, "cmap_ignore", observed_item_id="xm_2", concept_id="", action="ignore")
            conn.close()

            result = mapping_review.read_mapping_proposals(root, action="ignore")
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["proposals"][0]["mapping_id"], "cmap_ignore")
            # action_counts はフィルタ後の集合に対して計算される
            self.assertEqual(result["action_counts"], {"ignore": 1})

    def test_filters_by_decided_by_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:NetSales")
            _seed_observed_item(conn, "xm_2", "jppfs_cor:Roe")
            _seed_mapping(conn, "cmap_ai", observed_item_id="xm_1", concept_id="", decided_by="ai:model-x")
            _seed_mapping(
                conn,
                "cmap_det",
                observed_item_id="xm_2",
                concept_id="",
                decided_by="deterministic:xbrl_tag_candidates_match",
                confidence=None,
                evidence={"matched_via": "matched_field_ids"},
            )
            conn.close()

            result = mapping_review.read_mapping_proposals(root, decided_by_kind="deterministic")
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["proposals"][0]["mapping_id"], "cmap_det")
            self.assertEqual(result["proposals"][0]["rationale"], "matched_field_ids")

    def test_percentage_corroboration_uses_point_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_roe", "jppfs_cor:ReturnOnEquity", unit="%")
            _seed_concept(conn, "roe", "ROE")
            _seed_mapping(conn, "cmap_roe", observed_item_id="xm_roe", concept_id="roe")
            conn.close()
            _write_edinet_facts(
                root,
                [{"company_year_id": f"CO_{i}", "element_id": "jppfs_cor:ReturnOnEquity", "value": "8.2"} for i in range(4)],
            )
            _write_final_master_long(
                root,
                [{"company_year_id": f"CO_{i}", "field_id": "roe", "value_normalized": "8.6", "unit_normalized": "%"} for i in range(4)],
            )

            result = mapping_review.read_mapping_proposals(root)

            corr = result["proposals"][0]["corroboration"]
            self.assertEqual(corr["verdict"], "corroborated")
            self.assertEqual(corr["match_count"], 4)
            self.assertEqual(corr["examples"][0]["unit"], "%")

    def test_min_confidence_filters_ai_but_keeps_null_confidence_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:NetSales")
            _seed_observed_item(conn, "xm_2", "jppfs_cor:Roe")
            _seed_mapping(conn, "cmap_low", observed_item_id="xm_1", concept_id="", confidence=0.3)
            _seed_mapping(
                conn,
                "cmap_null_conf",
                observed_item_id="xm_2",
                concept_id="",
                decided_by="deterministic:xbrl_tag_candidates_match",
                confidence=None,
            )
            conn.close()

            result = mapping_review.read_mapping_proposals(root, min_confidence=0.8)
            ids = sorted(p["mapping_id"] for p in result["proposals"])
            self.assertEqual(ids, ["cmap_null_conf"])


class ConfirmRejectMappingProposalTests(unittest.TestCase):
    def test_confirm_updates_status_and_appends_decided_by_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:NetSales")
            _seed_mapping(conn, "cmap_1", observed_item_id="xm_1", concept_id="", decided_by="ai:model-x")
            conn.close()

            result = mapping_review.confirm_mapping_proposal(root, "cmap_1", reviewer="tester")
            self.assertTrue(result["updated"])
            self.assertEqual(result["new_status"], "confirmed")

            conn = semantics_store.connect(root)
            row = conn.execute(
                "select status, decided_by, evidence_json from concept_mappings where mapping_id='cmap_1'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "confirmed")
            self.assertEqual(row["decided_by"], "ai:model-x+human_review")
            evidence = json.loads(row["evidence_json"])
            self.assertEqual(evidence["human_review"]["decision"], "confirm")
            self.assertEqual(evidence["human_review"]["reviewer"], "tester")

    def test_reject_updates_status_and_appends_decided_by_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:Roe")
            _seed_mapping(
                conn,
                "cmap_2",
                observed_item_id="xm_1",
                concept_id="",
                action="ignore",
                decided_by="deterministic:xbrl_tag_candidates_match",
                confidence=None,
            )
            conn.close()

            result = mapping_review.reject_mapping_proposal(root, "cmap_2", reviewer="tester", note="不要")
            self.assertTrue(result["updated"])
            self.assertEqual(result["new_status"], "rejected")

            conn = semantics_store.connect(root)
            row = conn.execute(
                "select status, decided_by, evidence_json from concept_mappings where mapping_id='cmap_2'"
            ).fetchone()
            conn.close()
            self.assertEqual(row["status"], "rejected")
            self.assertEqual(row["decided_by"], "deterministic:xbrl_tag_candidates_match+human_review")
            evidence = json.loads(row["evidence_json"])
            self.assertEqual(evidence["human_review"]["decision"], "reject")
            self.assertEqual(evidence["human_review"]["note"], "不要")

    def test_confirm_on_already_confirmed_human_row_is_noop(self):
        """既存confirmed（human判断）行はconfirm/reject呼び出しに対して一切変化しない。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:NetSales")
            _seed_mapping(
                conn,
                "cmap_human1",
                observed_item_id="xm_1",
                concept_id="net_sales_standalone",
                status="confirmed",
                decided_by="human:reviewer_a",
                confidence=None,
                evidence={},
            )
            conn.close()

            before_conn = semantics_store.connect(root)
            before_row = dict(
                before_conn.execute(
                    "select * from concept_mappings where mapping_id='cmap_human1'"
                ).fetchone()
            )
            before_conn.close()

            confirm_result = mapping_review.confirm_mapping_proposal(root, "cmap_human1", reviewer="tester")
            self.assertFalse(confirm_result["updated"])

            reject_result = mapping_review.reject_mapping_proposal(root, "cmap_human1", reviewer="tester")
            self.assertFalse(reject_result["updated"])

            after_conn = semantics_store.connect(root)
            after_row = dict(
                after_conn.execute(
                    "select * from concept_mappings where mapping_id='cmap_human1'"
                ).fetchone()
            )
            after_conn.close()
            self.assertEqual(before_row, after_row)

    def test_confirm_on_missing_mapping_id_returns_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            conn.close()

            result = mapping_review.confirm_mapping_proposal(root, "does_not_exist")
            self.assertFalse(result["updated"])
            self.assertEqual(result["reason"], "not_found")


if __name__ == "__main__":
    unittest.main()
