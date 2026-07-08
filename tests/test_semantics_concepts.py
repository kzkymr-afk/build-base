from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import write_table
from yuho_auto_extract.services import semantics_concepts, semantics_store


class SemanticsConceptsTests(unittest.TestCase):
    def test_merge_concepts_marks_source_and_retargets_mappings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.upsert_canonical_concepts(
                    conn,
                    [
                        {"concept_id": "source", "concept_name_ja": "旧概念", "category": "performance"},
                        {"concept_id": "target", "concept_name_ja": "新概念", "category": "performance"},
                    ],
                )
                semantics_store.replace_concept_mappings(
                    conn,
                    [
                        {
                            "mapping_id": "m1",
                            "observed_item_id": "oi1",
                            "concept_id": "source",
                            "action": "map",
                            "status": "confirmed",
                            "decided_by": "human:test",
                        }
                    ],
                    delete_first=False,
                )
            finally:
                conn.close()

            result = semantics_concepts.merge_concepts(root, "source", "target")

            self.assertTrue(result["merged"])
            self.assertEqual(result["mappings_retargeted"], 1)
            conn = semantics_store.connect(root)
            try:
                source = conn.execute("select status, merged_into_concept_id from canonical_concepts where concept_id='source'").fetchone()
                mapping = conn.execute("select concept_id, superseded_by from concept_mappings where mapping_id='m1'").fetchone()
                self.assertEqual(source["status"], "merged")
                self.assertEqual(source["merged_into_concept_id"], "target")
                self.assertEqual(mapping["concept_id"], "target")
                self.assertEqual(mapping["superseded_by"], "target")
            finally:
                conn.close()

    def test_split_concept_creates_active_children_without_changing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.upsert_canonical_concepts(
                    conn,
                    [{"concept_id": "orders", "concept_name_ja": "受注", "category": "orders", "target_unit": "百万円"}],
                )
            finally:
                conn.close()

            result = semantics_concepts.split_concept(
                root,
                "orders",
                [
                    {
                        "concept_id": "orders_building",
                        "concept_name_ja": "建築受注",
                        "category": "orders",
                        "target_unit": "百万円",
                    }
                ],
            )

            self.assertEqual(result["created_count"], 1)
            conn = semantics_store.connect(root)
            try:
                source = conn.execute("select status from canonical_concepts where concept_id='orders'").fetchone()
                child = conn.execute("select status, target_unit from canonical_concepts where concept_id='orders_building'").fetchone()
                self.assertEqual(source["status"], "active")
                self.assertEqual(child["status"], "active")
                self.assertEqual(child["target_unit"], "百万円")
            finally:
                conn.close()

    def test_list_concepts_filters_and_counts_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.upsert_canonical_concepts(
                    conn,
                    [
                        {"concept_id": "a", "concept_name_ja": "売上", "category": "performance", "status": "active"},
                        {"concept_id": "b", "concept_name_ja": "統合済", "category": "performance", "status": "merged"},
                    ],
                )
            finally:
                conn.close()

            result = semantics_concepts.list_concepts(root, status="active", search="売上")

            self.assertEqual(result["total"], 1)
            self.assertEqual(result["rows"][0]["concept_id"], "a")
            self.assertEqual(result["status_counts"], {"active": 1})

    def test_list_concepts_reports_final_value_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.upsert_canonical_concepts(
                    conn,
                    [
                        {"concept_id": "sales", "concept_name_ja": "売上高", "category": "performance", "status": "active"},
                        {"concept_id": "nc_new", "concept_name_ja": "新概念", "category": "orders", "status": "active"},
                    ],
                )
            finally:
                conn.close()
            write_table(
                root / "data" / "final" / "final_master_long.csv",
                [
                    {"company_year_id": "A_2024", "field_id": "sales", "value": "100"},
                    {"company_year_id": "B_2024", "field_id": "sales", "value": "0"},
                    {"company_year_id": "A_2024", "field_id": "nc_new", "value": ""},
                ],
            )

            result = semantics_concepts.list_concepts(root, status="active")
            rows = {row["concept_id"]: row for row in result["rows"]}

            self.assertEqual(rows["sales"]["final_value_count"], 2)
            self.assertEqual(rows["sales"]["coverage_hint"], "2件")
            self.assertEqual(rows["nc_new"]["final_value_count"], 0)
            self.assertEqual(rows["nc_new"]["coverage_hint"], "未実値化")


if __name__ == "__main__":
    unittest.main()
