from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable

from .io_utils import ensure_parent


def build_run_report(
    run_id: str,
    target_documents: Iterable[Dict[str, Any]],
    normalized_rows: Iterable[Dict[str, Any]],
    validation_results: Iterable[Dict[str, Any]],
    review_queue: Iterable[Dict[str, Any]],
) -> str:
    targets = list(target_documents)
    rows = list(normalized_rows)
    validations = list(validation_results)
    queue = list(review_queue)
    resolved = [row for row in targets if row.get("resolution_status") == "resolved"]
    missing = [row for row in rows if row.get("value") in (None, "")]
    validation_fail = [row for row in validations if row.get("status") == "fail"]
    reorg = [row for row in targets if str(row.get("transition_year_flag", "0")) in {"1", "true", "True"}]
    return "\n".join(
        [
            f"# Run Report: {run_id}",
            "",
            "## Summary",
            "",
            f"- 対象会社×年度数: {len(targets)}",
            f"- 解決済み文書数: {len(resolved)}",
            f"- 自動抽出セル数: {len(rows)}",
            f"- レビュー必要セル数: {len(queue)}",
            f"- 欠損セル数: {len(missing)}",
            f"- 検算失敗件数: {len(validation_fail)}",
            f"- 再編年度の処理件数: {len(reorg)}",
            "",
            "## Failed Documents",
            "",
            _markdown_table([row for row in targets if row.get("resolution_status") != "resolved"], ["company_year_id", "failure_reason"]),
            "",
            "## Validation Failures",
            "",
            _markdown_table(validation_fail, ["company_year_id", "rule_id", "difference", "description"]),
            "",
            "## Next Improvements",
            "",
            "- 再編年度の候補セクションが事業会社別に切り出せているか確認する。",
            "- 10年分の手確認済み golden_values.csv を追加し、抽出候補の自動採用率を測る。",
            "- XBRLタグ候補を実書類のtaxonomyに合わせて拡張する。",
        ]
    )


def write_run_report(path: Path, content: str) -> Path:
    ensure_parent(path)
    path.write_text(content, encoding="utf-8")
    return path


def _markdown_table(rows: Iterable[Dict[str, Any]], columns: list) -> str:
    rows_list = list(rows)
    if not rows_list:
        return "_なし_"
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows_list:
        body.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join([header, separator] + body)
