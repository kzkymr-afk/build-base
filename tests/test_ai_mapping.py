from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.ai_runner import AiCallResult, FakeAiRunner
from yuho_auto_extract.services import ai_mapping, semantics_store


def _write_ai_config(root: Path) -> None:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "ai.yml").write_text(
        """
tiers:
  bulk:
    model: claude-haiku-4-5-20251001
    chunk_size: 2
  hard:
    model: claude-sonnet-5
    chunk_size: 1
timeout_seconds: 30
budget:
  max_calls_per_run: 3
batch_size: 2
""",
        encoding="utf-8",
    )


def _seed_observed_item(conn, observed_item_id: str, element_id: str = "jppfs_cor:Sga") -> None:
    semantics_store.replace_observed_items(
        conn,
        [
            {
                "observed_item_id": observed_item_id,
                "item_kind": "xbrl",
                "element_id": element_id,
                "element_local_name": "GeneralAndAdministrativeExpensesSGA",
                "taxonomy_kind": "jppfs",
                "label_ja": "販売費及び一般管理費",
                "unit": "円",
                "sample_values": {"sample_value_display": "12,345,678", "sample_source_quote": "販管費 12,345,678千円"},
                "source": "metric_catalog",
            }
        ],
        delete_first=False,
    )


def _seed_concept(conn) -> None:
    semantics_store.upsert_canonical_concepts(
        conn,
        [
            {
                "concept_id": "sga_expense_consolidated",
                "concept_name_ja": "販管費_連結",
                "category": "performance",
                "data_scope": "consolidated",
                "target_unit": "百万円",
            }
        ],
    )


class SelectUnmappedXbrlObservedItemsTests(unittest.TestCase):
    def test_excludes_items_with_confirmed_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_observed_items(
                    conn,
                    [
                        {"observed_item_id": "xm_1", "item_kind": "xbrl", "taxonomy_kind": "jppfs", "source": "metric_catalog"},
                        {"observed_item_id": "xm_2", "item_kind": "xbrl", "taxonomy_kind": "jppfs", "source": "metric_catalog"},
                    ],
                )
                semantics_store.replace_concept_mappings(
                    conn,
                    [
                        {
                            "mapping_id": "m1",
                            "observed_item_id": "xm_1",
                            "concept_id": "net_sales_consolidated",
                            "action": "map",
                            "status": "confirmed",
                            "decided_by": "human:x",
                        }
                    ],
                )
                unmapped = ai_mapping.select_unmapped_xbrl_observed_items(conn)
                ids = {row["observed_item_id"] for row in unmapped}
                self.assertEqual(ids, {"xm_2"})
            finally:
                conn.close()

    def test_proposed_mapping_does_not_exclude_item(self):
        """statusがproposedのマッピングしか無い場合はまだ「未マップ」扱いにする。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_observed_items(
                    conn,
                    [{"observed_item_id": "xm_1", "item_kind": "xbrl", "taxonomy_kind": "jppfs", "source": "metric_catalog"}],
                )
                semantics_store.replace_concept_mappings(
                    conn,
                    [
                        {
                            "mapping_id": "m1",
                            "observed_item_id": "xm_1",
                            "concept_id": "net_sales_consolidated",
                            "action": "map",
                            "status": "proposed",
                            "decided_by": "deterministic:xbrl_tag_candidates_match",
                        }
                    ],
                )
                unmapped = ai_mapping.select_unmapped_xbrl_observed_items(conn)
                ids = {row["observed_item_id"] for row in unmapped}
                self.assertEqual(ids, {"xm_1"})
            finally:
                conn.close()


class ParseAiDecisionsValidationTests(unittest.TestCase):
    def test_different_scope_requires_concept_id(self):
        with self.assertRaises(ValueError):
            ai_mapping.validate_ai_decision(
                {"observed_item_id": "xm_1", "action": "different_scope", "concept_id": None, "rationale": "r"}
            )

    def test_map_requires_concept_id(self):
        with self.assertRaises(ValueError):
            ai_mapping.validate_ai_decision(
                {"observed_item_id": "xm_1", "action": "map", "concept_id": "", "rationale": "r"}
            )

    def test_ignore_allows_null_concept_id(self):
        ai_mapping.validate_ai_decision(
            {"observed_item_id": "xm_1", "action": "ignore", "concept_id": None, "rationale": "r"}
        )  # no raise

    def test_new_concept_requires_new_concept_dict(self):
        with self.assertRaises(ValueError):
            ai_mapping.validate_ai_decision(
                {"observed_item_id": "xm_1", "action": "new_concept", "concept_id": None, "rationale": "r"}
            )

    def test_missing_rationale_raises(self):
        with self.assertRaises(ValueError):
            ai_mapping.validate_ai_decision(
                {"observed_item_id": "xm_1", "action": "ignore", "concept_id": None, "rationale": ""}
            )

    def test_invalid_action_raises(self):
        with self.assertRaises(ValueError):
            ai_mapping.validate_ai_decision(
                {"observed_item_id": "xm_1", "action": "delete", "concept_id": None, "rationale": "r"}
            )

    def test_parse_ai_decisions_requires_list(self):
        with self.assertRaises(ValueError):
            ai_mapping.parse_ai_decisions({"not": "a list"})


class AiProposalWriteDoesNotOverwriteHumanMappingTests(unittest.TestCase):
    def test_delete_first_false_preserves_existing_human_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            try:
                semantics_store.replace_concept_mappings(
                    conn,
                    [
                        {
                            "mapping_id": "human1",
                            "observed_item_id": "xm_1",
                            "concept_id": "net_sales_consolidated",
                            "action": "map",
                            "status": "confirmed",
                            "decided_by": "human:x",
                        }
                    ],
                )
                ai_row = {
                    "mapping_id": "ai1",
                    "observed_item_id": "xm_1",
                    "concept_id": "net_sales_consolidated",
                    "action": "map",
                    "status": "proposed",
                    "decided_by": "ai:claude-haiku-4-5-20251001",
                }
                semantics_store.replace_concept_mappings(conn, [ai_row], delete_first=False)
                all_mappings = semantics_store.fetch_concept_mappings(conn)
                self.assertEqual(len(all_mappings), 2)
                human = next(m for m in all_mappings if m["mapping_id"] == "human1")
                self.assertEqual(human["status"], "confirmed")
            finally:
                conn.close()

    def test_mapping_row_from_ai_decision_is_always_proposed(self):
        decision = {
            "observed_item_id": "xm_1",
            "action": "map",
            "concept_id": "sga_expense_consolidated",
            "rationale": "一致",
            "confidence": 0.9,
        }
        row = ai_mapping.mapping_row_from_ai_decision(decision, "claude-haiku-4-5-20251001", "call123")
        self.assertEqual(row["status"], "proposed")
        self.assertEqual(row["decided_by"], "ai:claude-haiku-4-5-20251001")
        self.assertNotEqual(row["status"], "confirmed")


class RunAiMappingBatchDryRunTests(unittest.TestCase):
    def test_dry_run_writes_cards_without_calling_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_ai_config(root)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1")
            _seed_concept(conn)
            conn.close()

            runner = FakeAiRunner({})  # 何も登録しない。呼ばれたら即AssertionError
            result = ai_mapping.run_ai_mapping_batch(root, runner=runner, tier="bulk", limit=10, dry_run=True)

            self.assertEqual(result["dry_run"], True)
            self.assertEqual(result["ai_calls_made"], 0)
            self.assertEqual(len(runner.calls), 0)
            chunk_path = root / "data" / "ai_evidence" / "mapping_cards" / "prompt_chunks" / "chunk_001.md"
            self.assertTrue(chunk_path.exists())
            manifest_path = root / "data" / "ai_evidence" / "mapping_cards" / "manifest.json"
            self.assertTrue(manifest_path.exists())

    def test_dry_run_with_none_runner_does_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_ai_config(root)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1")
            _seed_concept(conn)
            conn.close()

            result = ai_mapping.run_ai_mapping_batch(root, runner=None, tier="bulk", limit=10, dry_run=True)
            self.assertEqual(result["ai_calls_made"], 0)

    def test_non_dry_run_requires_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_ai_config(root)
            conn = semantics_store.connect(root)
            conn.close()
            with self.assertRaises(ValueError):
                ai_mapping.run_ai_mapping_batch(root, runner=None, tier="bulk", limit=10, dry_run=False)


class RunAiMappingBatchExecuteTests(unittest.TestCase):
    def _make_ok_result(self, input_ref: str, decisions) -> AiCallResult:
        return AiCallResult(
            call_id=f"call_{input_ref}",
            purpose="ai_mapping_bulk",
            model="claude-haiku-4-5-20251001",
            tier="bulk",
            input_ref=input_ref,
            prompt="prompt",
            raw_stdout="{}",
            result_text=json.dumps(decisions, ensure_ascii=False),
            parsed_result=decisions,
            usage={"input_tokens": 100, "output_tokens": 40},
            total_cost_usd=0.005,
            duration_ms=500,
            exit_code=0,
            status="ok",
        )

    def test_writes_proposals_and_ai_calls_for_valid_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_ai_config(root)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1")
            _seed_concept(conn)
            conn.close()

            decisions = [
                {
                    "observed_item_id": "xm_1",
                    "action": "map",
                    "concept_id": "sga_expense_consolidated",
                    "new_concept": None,
                    "rationale": "販管費の標準タグと一致",
                    "confidence": 0.92,
                }
            ]
            runner = FakeAiRunner({"chunk_001": lambda: self._make_ok_result("chunk_001", decisions)})

            result = ai_mapping.run_ai_mapping_batch(root, runner=runner, tier="bulk", limit=10, dry_run=False)

            self.assertEqual(result["ai_calls_made"], 1)
            self.assertEqual(result["proposals_written"], 1)
            self.assertEqual(result["parse_errors"], 0)
            self.assertEqual(len(runner.calls), 1)

            conn = semantics_store.connect(root)
            try:
                mappings = semantics_store.fetch_concept_mappings(conn)
                ai_mappings = [m for m in mappings if str(m["decided_by"]).startswith("ai:")]
                self.assertEqual(len(ai_mappings), 1)
                self.assertEqual(ai_mappings[0]["status"], "proposed")

                ai_calls = semantics_store.fetch_ai_calls(conn)
                self.assertEqual(len(ai_calls), 1)
                self.assertEqual(ai_calls[0]["status"], "ok")
                self.assertEqual(ai_calls[0]["input_tokens"], 100)
            finally:
                conn.close()

    def test_does_not_overwrite_existing_human_confirmed_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_ai_config(root)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1")
            _seed_concept(conn)
            semantics_store.replace_concept_mappings(
                conn,
                [
                    {
                        "mapping_id": "human1",
                        "observed_item_id": "xm_1",
                        "concept_id": "sga_expense_consolidated",
                        "action": "map",
                        "status": "confirmed",
                        "decided_by": "human:reviewer",
                    }
                ],
                delete_first=False,
            )
            conn.close()

            # xm_1はすでにconfirmedなので select_unmapped では対象外になるはず。
            decisions = [
                {
                    "observed_item_id": "xm_1",
                    "action": "map",
                    "concept_id": "sga_expense_consolidated",
                    "rationale": "販管費",
                    "confidence": 0.9,
                }
            ]
            runner = FakeAiRunner({"chunk_001": lambda: self._make_ok_result("chunk_001", decisions)})
            result = ai_mapping.run_ai_mapping_batch(root, runner=runner, tier="bulk", limit=10, dry_run=False)

            # confirmed済みのため対象0件、AI呼び出しも発生しない
            self.assertEqual(result["observed_items_targeted"], 0)
            self.assertEqual(result["ai_calls_made"], 0)

            conn = semantics_store.connect(root)
            try:
                mappings = semantics_store.fetch_concept_mappings(conn)
                human = next(m for m in mappings if m["mapping_id"] == "human1")
                self.assertEqual(human["status"], "confirmed")
                self.assertEqual(human["decided_by"], "human:reviewer")
            finally:
                conn.close()

    def test_parse_error_response_is_counted_and_no_mapping_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_ai_config(root)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1")
            _seed_concept(conn)
            conn.close()

            bad_result = AiCallResult(
                call_id="call_bad",
                purpose="ai_mapping_bulk",
                model="claude-haiku-4-5-20251001",
                tier="bulk",
                input_ref="chunk_001",
                prompt="prompt",
                raw_stdout="{}",
                result_text="not json at all",
                parsed_result=None,
                usage={"input_tokens": 10, "output_tokens": 5},
                total_cost_usd=0.001,
                duration_ms=100,
                exit_code=0,
                status="parse_error",
                error="result did not contain parseable JSON",
            )
            runner = FakeAiRunner({"chunk_001": lambda: bad_result})
            result = ai_mapping.run_ai_mapping_batch(root, runner=runner, tier="bulk", limit=10, dry_run=False)

            self.assertEqual(result["ai_calls_made"], 1)
            self.assertEqual(result["parse_errors"], 1)
            self.assertEqual(result["proposals_written"], 0)

    def test_budget_exceeded_stops_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "ai.yml").write_text(
                """
tiers:
  bulk:
    model: claude-haiku-4-5-20251001
    chunk_size: 1
timeout_seconds: 30
budget:
  max_calls_per_run: 1
batch_size: 1
""",
                encoding="utf-8",
            )
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1")
            _seed_observed_item(conn, "xm_2", element_id="jppfs_cor:Other")
            _seed_concept(conn)
            conn.close()

            decisions_1 = [
                {"observed_item_id": "xm_1", "action": "ignore", "concept_id": None, "rationale": "no match"}
            ]
            decisions_2 = [
                {"observed_item_id": "xm_2", "action": "ignore", "concept_id": None, "rationale": "no match"}
            ]
            runner = FakeAiRunner(
                {
                    "chunk_001": lambda: self._make_ok_result("chunk_001", decisions_1),
                    "chunk_002": lambda: self._make_ok_result("chunk_002", decisions_2),
                }
            )
            with self.assertRaises(Exception):
                ai_mapping.run_ai_mapping_batch(root, runner=runner, tier="bulk", limit=10, dry_run=False)

            # 最初の1回だけ呼ばれ、2回目のバッチ前にbudgetチェックで止まっていること
            self.assertLessEqual(len(runner.calls), 1)


if __name__ == "__main__":
    unittest.main()
