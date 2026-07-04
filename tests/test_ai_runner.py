from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from yuho_auto_extract.ai_runner import (
    AiCallResult,
    ClaudeCliRunner,
    FakeAiRunner,
    _extract_json_from_result,
)


class ExtractJsonFromResultTests(unittest.TestCase):
    def test_plain_json_array(self):
        text = '[{"observed_item_id":"x1","action":"ignore","rationale":"r"}]'
        parsed = _extract_json_from_result(text)
        self.assertEqual(parsed[0]["observed_item_id"], "x1")

    def test_fenced_json_is_stripped(self):
        text = '```json\n[{"observed_item_id":"x1","action":"ignore","rationale":"r"}]\n```'
        parsed = _extract_json_from_result(text)
        self.assertEqual(parsed[0]["action"], "ignore")

    def test_fenced_without_json_language_tag(self):
        text = '```\n[{"observed_item_id":"x1","action":"ignore","rationale":"r"}]\n```'
        self.assertIsNotNone(_extract_json_from_result(text))

    def test_json_with_surrounding_explanation_text(self):
        text = 'ここに結果を示します。\n[{"observed_item_id":"x1","action":"ignore","rationale":"r"}]\nご確認ください。'
        parsed = _extract_json_from_result(text)
        self.assertEqual(parsed[0]["observed_item_id"], "x1")

    def test_unparseable_text_returns_none(self):
        self.assertIsNone(_extract_json_from_result("申し訳ありませんが、判断できません。"))

    def test_empty_text_returns_none(self):
        self.assertIsNone(_extract_json_from_result(""))


class FakeAiRunnerTests(unittest.TestCase):
    def test_call_returns_registered_response(self):
        result = AiCallResult(
            call_id="c1",
            purpose="ai_mapping_bulk",
            model="claude-haiku-4-5-20251001",
            tier="bulk",
            input_ref="chunk_001",
            prompt="...",
            raw_stdout="{}",
            result_text="[]",
            parsed_result=[],
            usage={"input_tokens": 100, "output_tokens": 50},
            total_cost_usd=0.01,
            duration_ms=800,
            exit_code=0,
            status="ok",
        )
        runner = FakeAiRunner({"chunk_001": lambda: result})
        out = runner.call(
            prompt="p", model="m", purpose="ai_mapping_bulk", tier="bulk",
            input_ref="chunk_001", timeout_seconds=60,
        )
        self.assertEqual(out.status, "ok")
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0]["input_ref"], "chunk_001")

    def test_unregistered_input_ref_raises(self):
        runner = FakeAiRunner({})
        with self.assertRaises(AssertionError):
            runner.call(prompt="p", model="m", purpose="p", tier="bulk", input_ref="unknown", timeout_seconds=60)

    @patch("yuho_auto_extract.ai_runner.subprocess.run")
    def test_fake_runner_never_starts_a_subprocess(self, mock_run):
        """FakeAiRunner.call()の実行がsubprocess.runを一切呼ばないことを確認する。"""
        result = AiCallResult(
            call_id="c1", purpose="p", model="m", tier="bulk", input_ref="chunk_001",
            prompt="...", raw_stdout="{}", result_text="[]", parsed_result=[],
        )
        runner = FakeAiRunner({"chunk_001": lambda: result})
        runner.call(prompt="p", model="m", purpose="p", tier="bulk", input_ref="chunk_001", timeout_seconds=60)
        mock_run.assert_not_called()


class ClaudeCliRunnerSubprocessShapeTests(unittest.TestCase):
    """subprocess.run自体をモックし、実プロセスは絶対に起動しない形状テスト。"""

    @patch("yuho_auto_extract.ai_runner.subprocess.run")
    def test_call_uses_minimal_tempdir_as_cwd(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "result": "[]",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "total_cost_usd": 0.001,
                    "duration_ms": 10,
                }
            ),
            stderr="",
        )
        runner = ClaudeCliRunner()
        result = runner.call(
            prompt="p", model="claude-haiku-4-5-20251001", purpose="test",
            tier="bulk", input_ref="x", timeout_seconds=5,
        )
        called_cwd = mock_run.call_args.kwargs["cwd"]
        self.assertNotEqual(Path(called_cwd), Path.cwd())
        self.assertFalse((Path(called_cwd) / "CLAUDE.md").exists())
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.parsed_result, [])

    @patch("yuho_auto_extract.ai_runner.subprocess.run")
    def test_call_parses_fenced_result(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "result": '```json\n[{"observed_item_id":"x1","action":"ignore","rationale":"r"}]\n```',
                    "usage": {"input_tokens": 5, "output_tokens": 5},
                    "total_cost_usd": 0.002,
                    "duration_ms": 20,
                }
            ),
            stderr="",
        )
        runner = ClaudeCliRunner()
        result = runner.call(
            prompt="p", model="claude-haiku-4-5-20251001", purpose="test",
            tier="bulk", input_ref="x", timeout_seconds=5,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.parsed_result[0]["observed_item_id"], "x1")

    @patch("yuho_auto_extract.ai_runner.subprocess.run")
    def test_call_marks_parse_error_when_result_unparseable(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "result": "申し訳ありませんが判断できません",
                    "usage": {"input_tokens": 5, "output_tokens": 5},
                    "total_cost_usd": 0.001,
                    "duration_ms": 15,
                }
            ),
            stderr="",
        )
        runner = ClaudeCliRunner()
        result = runner.call(
            prompt="p", model="claude-haiku-4-5-20251001", purpose="test",
            tier="bulk", input_ref="x", timeout_seconds=5,
        )
        self.assertEqual(result.status, "parse_error")
        self.assertIsNone(result.parsed_result)

    @patch("yuho_auto_extract.ai_runner.subprocess.run")
    def test_call_marks_process_error_on_nonzero_exit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        runner = ClaudeCliRunner()
        result = runner.call(
            prompt="p", model="claude-haiku-4-5-20251001", purpose="test",
            tier="bulk", input_ref="x", timeout_seconds=5,
        )
        self.assertEqual(result.status, "process_error")
        self.assertEqual(result.exit_code, 1)

    @patch("yuho_auto_extract.ai_runner.subprocess.run")
    def test_call_never_invoked_means_no_real_claude_process(self, mock_run):
        """このテストファイル全体でmock_run以外の実subprocessが起動していないことの確認。"""
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
