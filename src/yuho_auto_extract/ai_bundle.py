from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .io_utils import ensure_parent


AI_BUNDLE_DIR = Path("data") / "ai_bundle"

AI_BUNDLE_FILES = [
    ("data/final/final_master_wide.csv", "final_master_wide.csv", "会社×年度の横持ち最終データ。通常の分析はここから始める。"),
    ("data/final/analysis_dataset.csv", "analysis_dataset.csv", "派生指標を追加した分析用データ。目的変数や説明変数の探索向け。"),
    ("data/final/final_master_long.csv", "final_master_long.csv", "1行1項目のロング形式。項目別の再集計や欠損確認向け。"),
    ("data/final/source_audit.csv", "source_audit.csv", "値ごとの根拠確認用。field_id、field_name_ja、source_quoteを含む。"),
    ("data/final/final_master_field_definition.csv", "field_definition.csv", "項目辞書。field_idの日本語名、単位、スコープ、抽出方法を定義。"),
    ("data/final/final_master_company_year_master.csv", "company_year_master.csv", "会社年度マスタ。再編年度、対象会社、分析上の扱いを確認する。"),
    ("data/final/field_coverage.csv", "field_coverage.csv", "項目別の取得件数とカバレッジ。"),
    ("data/final/field_coverage.md", "field_coverage.md", "項目別カバレッジのMarkdown版。"),
    ("data/final/run_report.md", "run_report.md", "実行サマリー、未解決文書、検算失敗の有無。"),
    ("data/final/unsupported_fields_plan.md", "unsupported_fields_plan.md", "今回対象外にした項目と理由。"),
    ("data/final/table_pattern_backlog.md", "table_pattern_backlog.md", "今後改善すべき表パターンの整理。"),
]


def build_ai_bundle(root: Path) -> List[Dict[str, Any]]:
    bundle_dir = root / AI_BUNDLE_DIR
    bundle_dir.mkdir(parents=True, exist_ok=True)
    _clear_generated_bundle_files(bundle_dir)

    copied: List[Dict[str, Any]] = []
    for source_rel, target_name, description in AI_BUNDLE_FILES:
        source = root / source_rel
        if not source.exists():
            continue
        target = bundle_dir / target_name
        shutil.copyfile(source, target)
        copied.append(
            {
                "file": target_name,
                "source": source_rel,
                "description": description,
                "bytes": target.stat().st_size,
            }
        )

    (bundle_dir / "AI_README.md").write_text(_ai_readme(copied), encoding="utf-8")

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bundle_dir": str(AI_BUNDLE_DIR),
        "files": copied,
    }
    ensure_parent(bundle_dir / "manifest.json")
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return copied


def _clear_generated_bundle_files(bundle_dir: Path) -> None:
    generated_names = {"AI_README.md", "manifest.json"} | {target for _source, target, _description in AI_BUNDLE_FILES}
    for path in bundle_dir.iterdir():
        if path.is_file() and path.name in generated_names:
            path.unlink()


def _ai_readme(files: List[Dict[str, Any]]) -> str:
    file_lines = "\n".join(f"- `{row['file']}`: {row['description']}" for row in files)
    return f"""# AI Analysis Bundle

このフォルダは、AIに丸ごと渡して分析させるための資料だけを集めたものです。`data/final/` は生成物全体の保管場所で、この `data/ai_bundle/` はAI投入用の軽量パッケージです。

## まず渡すファイル

基本は、このフォルダを丸ごと渡してください。分析の主表は `final_master_wide.csv` です。出典確認が必要な場合は `source_audit.csv` を参照します。

## 同梱ファイル

{file_lines}

## 重要な解釈ルール

- 空欄は0ではありません。未取得、非開示、または有報から安全に取れなかった値です。
- 金額項目の基本単位は `field_definition.csv` の `target_unit` を正とします。多くは百万円です。
- `data_scope` は重要です。`standalone`、`consolidated`、`segment` を混ぜて比較しないでください。
- `building_orders_special_contract_ratio` と `building_orders_competitive_ratio` は比率です。`orders_special_contract` の金額は今回対象外です。
- 用途別受注高、特命受注高、設計施工受注高、リニューアル受注高は、有価証券報告書だけでは安定して特定できないため今回対象外です。
- 値を推定補完しないでください。海外列がない会社で海外=0とみなすことも禁止です。
- `review_status=auto_accepted` は機械的な採用を意味します。重要な分析では `source_audit.csv` の `source_quote` を確認してください。

## 推奨するAIへの指示例

```text
このフォルダ内のAI_README.mdを最初に読み、field_definition.csvで項目定義を確認したうえで、final_master_wide.csvを主表として分析してください。
空欄は0として扱わず、必要に応じてsource_audit.csvで根拠を確認してください。
standalone/consolidated/segmentのスコープを混同しないでください。
```
"""
