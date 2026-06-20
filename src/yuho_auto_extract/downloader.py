from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .edinet_client import EdinetClient
from .io_utils import ensure_parent


DOCUMENT_TYPES = {
    "xbrl": (1, "xbrl.zip"),
    "pdf": (2, "pdf.pdf"),
    "attachments": (3, "attachments.zip"),
    "english": (4, "english.zip"),
    "csv": (5, "csv.zip"),
}


def download_target_documents(client: EdinetClient, targets: Iterable[Dict[str, Any]], raw_documents_dir: Path) -> List[Dict[str, Any]]:
    manifest: List[Dict[str, Any]] = []
    for target in targets:
        if target.get("resolution_status") != "resolved":
            continue
        doc_id = str(target["docID"])
        doc_dir = raw_documents_dir / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        _write_metadata(doc_dir, target)
        if str(target.get("xbrlFlag", "0")) == "1":
            manifest.append(client.save_document(doc_id, DOCUMENT_TYPES["xbrl"][0], doc_dir / DOCUMENT_TYPES["xbrl"][1]))
        if str(target.get("csvFlag", "0")) == "1":
            manifest.append(client.save_document(doc_id, DOCUMENT_TYPES["csv"][0], doc_dir / DOCUMENT_TYPES["csv"][1]))
        if str(target.get("pdfFlag", "0")) == "1":
            manifest.append(client.save_document(doc_id, DOCUMENT_TYPES["pdf"][0], doc_dir / DOCUMENT_TYPES["pdf"][1]))
    return manifest


def _write_metadata(doc_dir: Path, target: Dict[str, Any]) -> None:
    path = doc_dir / "metadata.json"
    ensure_parent(path)
    path.write_text(json.dumps(target, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
