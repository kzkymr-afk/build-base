from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


FIXED_WARNINGS = [
    "空欄は0として扱わないでください。未取得、非開示、または有報から安全に取れなかった値です。",
    "standalone、consolidated、segment の data_scope を混同しないでください。",
    "重要な値や違和感のある値は source_audit.csv の source_quote で根拠確認してください。",
    "値を推定補完しないでください。海外列がない会社で海外=0とみなすことも禁止です。",
]


REFERENCE_FILES = [
    "data/ai_bundle/AI_README.md",
    "data/ai_bundle/final_master_wide.csv",
    "data/ai_bundle/analysis_dataset.csv",
    "data/ai_bundle/source_audit.csv",
    "data/ai_bundle/field_definition.csv",
    "data/ai_bundle/company_year_master.csv",
]


def build_prompt(root: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    theme = str(payload.get("theme") or "建設会社の有価証券報告書データを分析する").strip()
    companies = _list(payload.get("companies"))
    fiscal_years = _list(payload.get("fiscal_years"))
    fields = _list(payload.get("fields"))
    extra_instruction = str(payload.get("extra_instruction") or "").strip()
    references = [path for path in REFERENCE_FILES if (root / path).exists()]

    lines: List[str] = [
        "# AI分析依頼",
        "",
        "## 目的",
        theme,
        "",
        "## 対象範囲",
        f"- 会社: {_display_list(companies)}",
        f"- 年度: {_display_list(fiscal_years)}",
        f"- 指標: {_display_list(fields)}",
        "",
        "## 参照ファイル",
    ]
    lines.extend(f"- `{path}`" for path in references)
    lines.extend(
        [
            "",
            "## 必ず守る解釈ルール",
            *[f"- {warning}" for warning in FIXED_WARNINGS],
        ]
    )
    if extra_instruction:
        lines.extend(["", "## 追加指示", extra_instruction])
    lines.extend(
        [
            "",
            "## 進め方",
            "1. まず `AI_README.md` と `field_definition.csv` を読んで、項目定義と単位を確認してください。",
            "2. 主表は `final_master_wide.csv` とし、必要に応じて `analysis_dataset.csv` の派生指標を使ってください。",
            "3. 出典確認が必要な値は `source_audit.csv` の `source_quote` を確認してください。",
            "4. 分析結果では、使った指標、対象年度、除外した欠損の扱いを明記してください。",
        ]
    )
    prompt = "\n".join(lines) + "\n"
    return {"prompt": prompt, "references": references}


def _list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _display_list(values: List[str]) -> str:
    return "指定なし（全体）" if not values else ", ".join(values)

