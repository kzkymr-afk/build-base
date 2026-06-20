from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


JobTarget = Callable[[Path, Callable[[str], None]], int]


@dataclass
class JobState:
    id: str = ""
    name: str = ""
    status: str = "idle"
    started_at: str = ""
    finished_at: str = ""
    exit_code: Optional[int] = None
    error: str = ""
    logs: List[str] = field(default_factory=list)


class JobAlreadyRunning(RuntimeError):
    pass


class JobManager:
    def __init__(self, root: Path, max_logs: int = 1000) -> None:
        self.root = root
        self.max_logs = max_logs
        self._lock = threading.Lock()
        self._state = JobState()
        self._thread: Optional[threading.Thread] = None

    def start(self, name: str, target: JobTarget) -> Dict[str, Any]:
        with self._lock:
            if self._state.status == "running":
                raise JobAlreadyRunning("another job is already running")
            self._state = JobState(
                id=uuid.uuid4().hex,
                name=name,
                status="running",
                started_at=_now(),
                logs=[],
            )
            self._thread = threading.Thread(target=self._run, args=(target,), daemon=True)
            self._thread.start()
            return self._snapshot_unlocked()

    def current(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()

    def _run(self, target: JobTarget) -> None:
        try:
            code = target(self.root, self._append_log)
            with self._lock:
                self._state.exit_code = code
                self._state.status = "succeeded" if code == 0 else "failed"
                self._state.finished_at = _now()
        except Exception as exc:  # pragma: no cover - exercised through API failure paths
            with self._lock:
                self._state.exit_code = 1
                self._state.status = "failed"
                self._state.error = str(exc)
                self._state.finished_at = _now()
                self._append_log_unlocked(f"ERROR: {exc}")

    def _append_log(self, line: str) -> None:
        with self._lock:
            self._append_log_unlocked(line)

    def _append_log_unlocked(self, line: str) -> None:
        self._state.logs.append(line)
        if len(self._state.logs) > self.max_logs:
            self._state.logs = self._state.logs[-self.max_logs :]

    def _snapshot_unlocked(self) -> Dict[str, Any]:
        return {
            "id": self._state.id,
            "name": self._state.name,
            "status": self._state.status,
            "started_at": self._state.started_at,
            "finished_at": self._state.finished_at,
            "exit_code": self._state.exit_code,
            "error": self._state.error,
            "logs": list(self._state.logs),
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
