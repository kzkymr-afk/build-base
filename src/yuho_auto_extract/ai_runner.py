"""BuildBase P5: AIランナー抽象化層。

`claude -p` をヘッドレスサブプロセスとして呼び出す本番実装（ClaudeCliRunner）と、
テスト専用の決定論的スタブ（FakeAiRunner）を提供する。

絶対制約:
    - 本番コード（services/ai_mapping.py 等）は `runner: AiRunner` を必須引数として
      受け取り、内部で ClaudeCliRunner() をデフォルト生成してはならない。
      呼び出し元（CLIエントリポイント）が明示的に構築して渡すことで、
      「テストや誤操作で実claudeが呼ばれてしまう」経路を型レベルで断つ。
    - テストは常に FakeAiRunner を注入する。ClaudeCliRunner の実subprocess起動は
      テストで検証しない（unittest.mock.patchでsubprocess.run自体をモックする
      形状テストのみ許容）。

P5小規模テストで判明した必須事項:
    - claude -p のharnessオーバーヘッドはcwdに依存する。プロジェクトdir実行では
      $0.0355、CLAUDE.mdの無い最小tempdir実行では$0.0175と約半減する。
      そのため ClaudeCliRunner は必ず tempfile 標準の一時ディレクトリを cwd にする。
    - haikuは「コードフェンスを付けないでください」という指示に反し、
      ```json ... ``` フェンス付きでJSON配列を返すことがある。
      パース側（_extract_json_from_result）でフェンス除去を必須実装する。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AiCallResult:
    """1回のAI呼び出し結果。ai_calls への1行分に対応する形。"""

    call_id: str
    purpose: str
    model: str
    tier: str
    input_ref: str
    prompt: str
    raw_stdout: str  # subprocessの生stdout（envelope JSON文字列 or フェンス付き等）
    result_text: str  # envelope["result"]（パース後）。パース失敗時は raw_stdout をそのまま保持
    parsed_result: Optional[Any]  # result_text からフェンス除去・JSON抽出した実データ（list/dict）。失敗時 None
    usage: Dict[str, Any] = field(default_factory=dict)  # envelope["usage"] 全体
    total_cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    exit_code: int = 0
    status: str = "ok"  # "ok" | "parse_error" | "timeout" | "process_error" | "budget_exceeded"
    error: str = ""
    created_at_utc: str = field(default_factory=_now_utc_iso)


class AiRunner(Protocol):
    """「1呼び出し=1カードチャンク or 1難例」を表す最小契約。

    バッチ内の複数observed_itemsを1プロンプトに同梱するかはカード生成側
    （services/ai_mapping.py）の責務であり、AiRunner自体は「文字列プロンプトを
    渡して文字列結果を受け取る」だけの薄いI/O層にする（テスト容易性のため）。
    """

    def call(
        self,
        *,
        prompt: str,
        model: str,
        purpose: str,
        tier: str,
        input_ref: str,
        timeout_seconds: Optional[int] = None,
    ) -> AiCallResult:
        ...


class BudgetExceeded(Exception):
    """今回のrunでのai_calls件数がbudget.max_calls_per_runを超えた場合に送出する。"""


# ---------------------------------------------------------------------------
# JSON抽出（フェンス除去・頑健パース）
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _candidates(text: str) -> List[str]:
    out = [text]
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        out.append(fence_match.group(1).strip())
    # 最初の [ か { から対応する終端までの緩い切り出し
    for open_ch, close_ch in [("[", "]"), ("{", "}")]:
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            out.append(text[start : end + 1])
    return out


def _extract_json_from_result(result_text: str) -> Optional[Any]:
    """envelope['result'] からJSON配列/オブジェクトを頑健に抽出する。

    1. そのままjson.loadsを試す（フェンス無しの正常ケース）。
    2. ```json ... ``` / ``` ... ``` フェンスを正規表現で除去して再試行。
    3. 文字列中の最初の '[' または '{' から最後の ']' or '}' までを切り出して再試行
       （前後に説明文が混じるケースへのフォールバック）。
    4. 全部失敗したら None（呼び出し側はstatus='parse_error'として扱う）。
    """
    text = (result_text or "").strip()
    if not text:
        return None
    for candidate in _candidates(text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# ClaudeCliRunner: 本番実装（claude -p サブプロセス呼び出し）
# ---------------------------------------------------------------------------


class ClaudeCliRunner:
    """claude -p をヘッドレスサブプロセスとして呼び出す本番実装。

    P5小規模テストの実測に基づく必須事項:
    - 最小の一時ディレクトリ（CLAUDE.mdが無い空dir）をcwdにする
      （プロジェクトdir実行=$0.0355 → 最小dir実行=$0.0175と約半減）。
    - --max-turns 1 でエージェント動作を抑止（単発応答のみ）。
    - --output-format json でenvelope取得。
    - stdin でプロンプトを渡す（コマンドライン引数の長さ制限・エスケープ問題を回避）。

    このクラスはテストでは呼び出されない（実subprocessは絶対に起動しない）。
    subprocess.run の呼び出し形状のみ unittest.mock.patch でモック検証する。
    """

    def __init__(self, claude_bin: str = "claude", default_timeout_seconds: int = 120) -> None:
        self.claude_bin = claude_bin
        self.default_timeout_seconds = default_timeout_seconds

    def call(
        self,
        *,
        prompt: str,
        model: str,
        purpose: str,
        tier: str,
        input_ref: str,
        timeout_seconds: Optional[int] = None,
    ) -> AiCallResult:
        call_id = uuid.uuid4().hex
        started = time.monotonic()

        cmd = [
            self.claude_bin,
            "-p",
            "--output-format",
            "json",
            "--model",
            model,
            "--max-turns",
            "1",
        ]
        effective_timeout = timeout_seconds or self.default_timeout_seconds

        # 最小cwd: tempfile標準の一時dir配下。CLAUDE.md・.claude/等プロジェクト設定が
        # 存在しない空ディレクトリであることを呼び出しごとに保証する。
        with tempfile.TemporaryDirectory(prefix="yuho_ai_") as tmp_cwd:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=tmp_cwd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                return AiCallResult(
                    call_id=call_id,
                    purpose=purpose,
                    model=model,
                    tier=tier,
                    input_ref=input_ref,
                    prompt=prompt,
                    raw_stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                    result_text="",
                    parsed_result=None,
                    usage={},
                    total_cost_usd=None,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    exit_code=-1,
                    status="timeout",
                    error=str(exc),
                )

        duration_ms = int((time.monotonic() - started) * 1000)

        if proc.returncode != 0:
            return AiCallResult(
                call_id=call_id,
                purpose=purpose,
                model=model,
                tier=tier,
                input_ref=input_ref,
                prompt=prompt,
                raw_stdout=proc.stdout or "",
                result_text="",
                parsed_result=None,
                usage={},
                total_cost_usd=None,
                duration_ms=duration_ms,
                exit_code=proc.returncode,
                status="process_error",
                error=(proc.stderr or "")[:2000],
            )

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return AiCallResult(
                call_id=call_id,
                purpose=purpose,
                model=model,
                tier=tier,
                input_ref=input_ref,
                prompt=prompt,
                raw_stdout=proc.stdout or "",
                result_text="",
                parsed_result=None,
                usage={},
                total_cost_usd=None,
                duration_ms=duration_ms,
                exit_code=proc.returncode,
                status="parse_error",
                error=f"envelope not JSON: {exc}",
            )

        result_text = str(envelope.get("result") or "")
        parsed = _extract_json_from_result(result_text)

        return AiCallResult(
            call_id=call_id,
            purpose=purpose,
            model=model,
            tier=tier,
            input_ref=input_ref,
            prompt=prompt,
            raw_stdout=proc.stdout or "",
            result_text=result_text,
            parsed_result=parsed,
            usage=envelope.get("usage") or {},
            total_cost_usd=envelope.get("total_cost_usd"),
            duration_ms=envelope.get("duration_ms") or duration_ms,
            exit_code=proc.returncode,
            status="ok" if parsed is not None else "parse_error",
            error="" if parsed is not None else "result did not contain parseable JSON",
        )


# ---------------------------------------------------------------------------
# AnthropicApiRunner: 将来のAPI直接呼び出し用スタブ
# ---------------------------------------------------------------------------


class AnthropicApiRunner:
    """Anthropic API を直接叩く将来実装用のスタブ。現時点では未実装。

    ClaudeCliRunner と同一の AiRunner インターフェースを満たすことのみ保証し、
    実際のAPI呼び出しは後日実装する（P5のスコープ外）。
    """

    def call(
        self,
        *,
        prompt: str,
        model: str,
        purpose: str,
        tier: str,
        input_ref: str,
        timeout_seconds: Optional[int] = None,
    ) -> AiCallResult:
        raise NotImplementedError("AnthropicApiRunner is a placeholder for future API-based execution")


# ---------------------------------------------------------------------------
# FakeAiRunner: テスト専用スタブ（実subprocessを一切起動しない）
# ---------------------------------------------------------------------------


class FakeAiRunner:
    """AiRunnerプロトコルの決定論的スタブ。実subprocessを一切起動しない。

    responses: input_ref をキーに、返すべき AiCallResult を生成する callable を
    事前登録する。未登録の input_ref が呼ばれたら AssertionError
    （テストの想定漏れを即検出するため黙ってNoneを返さない）。
    """

    def __init__(self, responses: Dict[str, Callable[[], AiCallResult]]) -> None:
        self._responses = responses
        self.calls: List[Dict[str, Any]] = []  # 呼び出し記録（何回呼ばれたか検証用）

    def call(
        self,
        *,
        prompt: str,
        model: str,
        purpose: str,
        tier: str,
        input_ref: str,
        timeout_seconds: Optional[int] = None,
    ) -> AiCallResult:
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "purpose": purpose,
                "tier": tier,
                "input_ref": input_ref,
            }
        )
        if input_ref not in self._responses:
            raise AssertionError(f"FakeAiRunner: unexpected input_ref={input_ref!r}")
        return self._responses[input_ref]()


# ---------------------------------------------------------------------------
# ai_calls 記録用ヘルパ（semantics_store 経由）
# ---------------------------------------------------------------------------


def ai_call_result_to_ai_calls_record(result: AiCallResult) -> Dict[str, Any]:
    """AiCallResult を semantics_store.insert_ai_call() が期待する辞書形式に変換する。

    output_json 相当（record["output"]）には result(生文字列含む)/usage/
    total_cost_usd/is_error等 envelope全体を監査可能な形で保存する。
    """
    return {
        "call_id": result.call_id,
        "created_at_utc": result.created_at_utc,
        "purpose": result.purpose,
        "model": result.model,
        "tier": result.tier,
        "input_ref": result.input_ref,
        "input_tokens": (result.usage or {}).get("input_tokens"),
        "output_tokens": (result.usage or {}).get("output_tokens"),
        "duration_ms": result.duration_ms,
        "exit_code": result.exit_code,
        "status": result.status,
        "output": {
            "result_text": result.result_text,
            "usage": result.usage,
            "total_cost_usd": result.total_cost_usd,
            "error": result.error,
        },
    }
