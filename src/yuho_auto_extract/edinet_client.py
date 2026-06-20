from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from .io_utils import ensure_parent


LOGGER = logging.getLogger(__name__)


class EdinetClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.edinet-fsa.go.jp/api/v2",
        timeout: int = 30,
        retry_count: int = 3,
        retry_backoff_seconds: int = 2,
    ) -> None:
        self.api_key = api_key or os.getenv("EDINET_API_KEY")
        if not self.api_key:
            raise RuntimeError("EDINET_API_KEY is required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_backoff_seconds = retry_backoff_seconds

    def list_documents(self, file_date: date, doc_type: int = 2) -> Dict[str, Any]:
        url = f"{self.base_url}/documents.json"
        params = {
            "date": file_date.isoformat(),
            "type": str(doc_type),
            "Subscription-Key": self.api_key,
        }
        return self._get_json(url, params)

    def get_document_bytes(self, doc_id: str, document_type: int) -> bytes:
        url = f"{self.base_url}/documents/{doc_id}"
        params = {"type": str(document_type), "Subscription-Key": self.api_key}
        response = self._request("GET", url, params=params)
        content_type = response.headers.get("Content-Type", "")
        if content_type.startswith("application/json"):
            raise RuntimeError(f"EDINET document API returned JSON error for {doc_id}: {response.text[:500]}")
        return response.content

    def save_document(self, doc_id: str, document_type: int, path: Path) -> Dict[str, Any]:
        content = self.get_document_bytes(doc_id, document_type)
        ensure_parent(path)
        path.write_bytes(content)
        return {
            "docID": doc_id,
            "type": document_type,
            "path": str(path),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }

    def _get_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        response = self._request("GET", url, params=params)
        return response.json()

    def _request(self, method: str, url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        scrubbed = dict(params or {})
        if "Subscription-Key" in scrubbed:
            scrubbed["Subscription-Key"] = "***"
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retry_count + 1):
            try:
                response = requests.request(method, url, params=params, timeout=self.timeout)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.retry_count:
                    time.sleep(self.retry_backoff_seconds * attempt)
                    continue
                response.raise_for_status()
                return response
            except Exception as exc:  # pragma: no cover - network path
                last_error = exc
                LOGGER.warning("EDINET request failed attempt=%s url=%s params=%s error=%s", attempt, url, scrubbed, exc)
                if attempt < self.retry_count:
                    time.sleep(self.retry_backoff_seconds * attempt)
        raise RuntimeError(f"EDINET request failed after retries: {url}") from last_error


def save_index_response(path: Path, response: Dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
