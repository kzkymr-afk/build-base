from __future__ import annotations

import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from yuho_auto_extract.io_utils import ensure_parent, read_table, read_yaml, write_table


AUDIT_DIR = Path("data") / "algorithm_audit"

COPY_FILES = [
    ("config/field_definition.csv", "config/field_definition.csv", "項目定義。抽出方法、単位、スコープ、候補タグを監査する。"),
    ("config/extraction_sections.yml", "config/extraction_sections.yml", "セクション探索設定。review_* の肥大化を監査する。"),
    ("config/validation_rules.yml", "config/validation_rules.yml", "検算・整合性ルール。抽出値の採否条件を監査する。"),
    ("data/final/run_report.md", "reports/run_report.md", "直近実行の全体サマリー。"),
    ("data/final/field_coverage.csv", "reports/field_coverage.csv", "項目別カバレッジ。低カバレッジ項目の監査に使う。"),
    ("data/review/review_resolved.csv", "review/review_resolved.csv", "保存済みレビュー判断。"),
    ("src/yuho_auto_extract/section_locator.py", "code/section_locator.py", "候補ブロック探索ロジック。"),
    ("src/yuho_auto_extract/local_table_extractor.py", "code/local_table_extractor.py", "ローカル表抽出ロジック。レビュー由来ルールの受け皿。"),
    ("src/yuho_auto_extract/edinet_db.py", "code/edinet_db.py", "XBRL DB抽出とローカル抽出の統合箇所。"),
    ("src/yuho_auto_extract/normalizer.py", "code/normalizer.py", "数値・単位・スコープ正規化。"),
]

GENERATED_FILES = [
    ("README.md", "監査パックの使い方。"),
    ("ALGORITHM_AUDIT_PROMPT.md", "AI/Codexに渡す固定監査プロンプト。"),
    ("CODEX_MAINTENANCE_PROMPT.md", "Codexに渡す監査・実装プロンプト。"),
    ("manifest.json", "生成日時と同梱ファイル一覧。"),
    ("field_algorithm_inventory.csv", "項目別の抽出方式・カバレッジ・レビュー由来状況。"),
    ("section_inventory.csv", "抽出セクション別のキーワードと対象項目。"),
    ("risk_flags.csv", "複雑化・過剰適合・未反映の自動検出フラグ。"),
    ("local_rule_samples.csv", "LOCAL_RULE_TABLE由来の根拠サンプル。"),
]


def build_algorithm_audit_bundle(root: Path) -> Dict[str, Any]:
    audit_dir = root / AUDIT_DIR
    audit_dir.mkdir(parents=True, exist_ok=True)
    _clear_generated_files(audit_dir)

    field_rows = _read_rows(root / "config" / "field_definition.csv")
    sections = read_yaml(root / "config" / "extraction_sections.yml") if (root / "config" / "extraction_sections.yml").exists() else {}
    final_long_rows = _read_rows(root / "data" / "final" / "final_master_long.csv")
    source_audit_rows = _read_rows(root / "data" / "final" / "source_audit.csv")
    coverage_rows = _read_rows(root / "data" / "final" / "field_coverage.csv")
    resolved_rows = _read_rows(root / "data" / "review" / "review_resolved.csv")

    field_inventory = _field_inventory(field_rows, final_long_rows, coverage_rows, resolved_rows)
    section_inventory = _section_inventory(sections)
    risk_flags = _risk_flags(field_inventory, section_inventory, resolved_rows)
    local_samples = _local_rule_samples(source_audit_rows)

    generated = [
        _write_table(audit_dir / "field_algorithm_inventory.csv", field_inventory, "項目別の抽出方式・カバレッジ・レビュー由来状況。"),
        _write_table(audit_dir / "section_inventory.csv", section_inventory, "抽出セクション別のキーワードと対象項目。"),
        _write_table(audit_dir / "risk_flags.csv", risk_flags, "複雑化・過剰適合・未反映の自動検出フラグ。"),
        _write_table(audit_dir / "local_rule_samples.csv", local_samples, "LOCAL_RULE_TABLE由来の根拠サンプル。"),
    ]

    copied = [_copy_file(root, audit_dir, source, target, description) for source, target, description in COPY_FILES]
    copied = [row for row in copied if row]

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prompt = _audit_prompt()
    readme = _readme(generated + copied)
    (audit_dir / "ALGORITHM_AUDIT_PROMPT.md").write_text(prompt, encoding="utf-8")
    (audit_dir / "CODEX_MAINTENANCE_PROMPT.md").write_text(prompt, encoding="utf-8")
    (audit_dir / "README.md").write_text(readme, encoding="utf-8")

    generated.extend(
        [
            _file_row(audit_dir, audit_dir / "README.md", "監査パックの使い方。"),
            _file_row(audit_dir, audit_dir / "ALGORITHM_AUDIT_PROMPT.md", "AI/Codexに渡す固定監査プロンプト。"),
            _file_row(audit_dir, audit_dir / "CODEX_MAINTENANCE_PROMPT.md", "Codexに渡す監査・実装プロンプト。"),
        ]
    )

    risk_counts = dict(Counter(str(row.get("severity", "")) for row in risk_flags))
    manifest = {
        "generated_at_utc": generated_at,
        "bundle_dir": str(AUDIT_DIR),
        "files": generated + copied,
        "summary": {
            "fields": len(field_inventory),
            "sections": len(section_inventory),
            "risk_flags": len(risk_flags),
            "risk_counts": risk_counts,
            "local_rule_samples": len(local_samples),
        },
    }
    (audit_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"].append(_file_row(audit_dir, audit_dir / "manifest.json", "生成日時と同梱ファイル一覧。"))
    (audit_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "generated_at_utc": generated_at,
        "bundle_dir": str(audit_dir),
        "summary": manifest["summary"],
        "files": manifest["files"],
        "prompt": prompt,
        "prompt_path": str(audit_dir / "ALGORITHM_AUDIT_PROMPT.md"),
        "maintenance_prompt_path": str(audit_dir / "CODEX_MAINTENANCE_PROMPT.md"),
        "readme_path": str(audit_dir / "README.md"),
    }


def read_algorithm_audit_manifest(root: Path) -> Dict[str, Any]:
    path = root / AUDIT_DIR / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _clear_generated_files(audit_dir: Path) -> None:
    targets = [target for _source, target, _description in COPY_FILES] + [name for name, _description in GENERATED_FILES]
    for rel in targets:
        path = audit_dir / rel
        if path.exists() and path.is_file():
            path.unlink()


def _field_inventory(
    field_rows: Sequence[Dict[str, Any]],
    final_long_rows: Sequence[Dict[str, Any]],
    coverage_rows: Sequence[Dict[str, Any]],
    resolved_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    extracted_by_field: Dict[str, Counter] = defaultdict(Counter)
    for row in final_long_rows:
        field_id = str(row.get("field_id", "")).strip()
        if not field_id:
            continue
        extracted_by_field[field_id][str(row.get("extraction_method", "") or "unknown")] += 1

    coverage_by_field = {str(row.get("field_id", "")): row for row in coverage_rows}
    review_counts: Dict[str, Counter] = defaultdict(Counter)
    for row in resolved_rows:
        field_id = str(row.get("field_id", "")).strip()
        if not field_id:
            continue
        review_counts[field_id]["saved"] += 1
        review_counts[field_id][f"applied_status:{str(row.get('applied_status', '') or 'blank')}"] += 1

    out: List[Dict[str, Any]] = []
    for field in field_rows:
        field_id = str(field.get("field_id", "")).strip()
        if not field_id:
            continue
        counts = extracted_by_field.get(field_id, Counter())
        coverage = coverage_by_field.get(field_id, {})
        out.append(
            {
                "field_id": field_id,
                "field_name_ja": field.get("field_name_ja", ""),
                "category": field.get("category", ""),
                "preferred_method": field.get("preferred_method", ""),
                "target_unit": field.get("target_unit", ""),
                "data_scope_required": field.get("data_scope_required", ""),
                "xbrl_tag_candidate_count": len(_split_values(field.get("xbrl_tag_candidates", ""))),
                "synonym_count": len(_split_values(field.get("synonyms_ja", ""))),
                "section_keyword_count": len(_split_values(field.get("section_keywords", ""))),
                "filled_company_years": coverage.get("filled_company_years", ""),
                "total_company_years": coverage.get("total_company_years", ""),
                "coverage_pct": coverage.get("coverage_pct", ""),
                "final_rows": sum(counts.values()),
                "xbrl_rows": counts.get("XBRL_CSV", 0),
                "local_rule_rows": counts.get("LOCAL_RULE_TABLE", 0),
                "manual_rows": counts.get("MANUAL", 0),
                "saved_review_count": review_counts[field_id].get("saved", 0),
                "applied_review_count": review_counts[field_id].get("applied_status:applied", 0),
            }
        )
    return out


def _section_inventory(sections: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for section_name, section in sorted(sections.items()):
        if not isinstance(section, dict):
            continue
        target_fields = [str(item) for item in section.get("target_fields", []) if str(item).strip()]
        heading_keywords = [str(item) for item in section.get("heading_keywords", []) if str(item).strip()]
        table_keywords = [str(item) for item in section.get("table_keywords", []) if str(item).strip()]
        out.append(
            {
                "section_name": section_name,
                "rule_type": "core",
                "description": section.get("description", ""),
                "target_fields": ";".join(target_fields),
                "target_field_count": len(target_fields),
                "heading_keywords": ";".join(heading_keywords),
                "heading_keyword_count": len(heading_keywords),
                "table_keywords": ";".join(table_keywords),
                "table_keyword_count": len(table_keywords),
            }
        )
    return out


def _risk_flags(
    field_inventory: Sequence[Dict[str, Any]],
    section_inventory: Sequence[Dict[str, Any]],
    resolved_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []

    for section in section_inventory:
        section_name = str(section.get("section_name", ""))
        if _as_int(section.get("target_field_count")) >= 8:
            flags.append(
                _flag(
                    "low",
                    "section_config",
                    section_name,
                    "large_section_target_surface",
                    f"対象項目が{section.get('target_field_count')}件あります。",
                    "複数の表パターンを1セクションに詰め込みすぎていないか確認してください。",
                )
            )

    for field in field_inventory:
        field_id = str(field.get("field_id", ""))
        coverage = _as_float(field.get("coverage_pct"))
        if coverage and coverage < 0.75:
            flags.append(
                _flag(
                    "medium",
                    "field_coverage",
                    field_id,
                    "low_coverage",
                    f"coverage_pct={coverage:.4f}",
                    "対象外にするか、XBRLタグ候補/表パーサ/レビュー学習のどれで改善するか切り分けてください。",
                )
            )
        if _as_int(field.get("synonym_count")) >= 8 or _as_int(field.get("xbrl_tag_candidate_count")) >= 8:
            flags.append(
                _flag(
                    "low",
                    "field_definition",
                    field_id,
                    "many_candidates",
                    f"synonyms={field.get('synonym_count')} xbrl_tags={field.get('xbrl_tag_candidate_count')}",
                    "候補が過剰に広くなって誤抽出を増やしていないか確認してください。",
                )
            )
        if _as_int(field.get("local_rule_rows")) > 0 and str(field.get("preferred_method", "")) == "XBRL_CSV":
            flags.append(
                _flag(
                    "low",
                    "field_method",
                    field_id,
                    "local_rule_supplements_xbrl_field",
                    f"LOCAL_RULE_TABLE rows={field.get('local_rule_rows')} / preferred_method=XBRL_CSV",
                    "XBRLが欠ける期間だけの補完なのか、preferred_methodを見直すべきか確認してください。",
                )
            )

    for row in resolved_rows:
        status = str(row.get("applied_status", "")).strip()
        decision = str(row.get("review_decision", "")).strip().lower()
        if decision in {"accept", "correct"} and status not in {"applied"}:
            flags.append(
                _flag(
                    "high",
                    "review_application",
                    f"{row.get('company_year_id')}:{row.get('field_id')}",
                    "saved_review_not_applied",
                    f"review_decision={decision} applied_status={status or 'blank'}",
                    "レビュー反映を実行するか、反映できない理由を確認してください。",
                )
            )

    return flags


def _local_rule_samples(source_audit_rows: Sequence[Dict[str, Any]], limit: int = 100) -> List[Dict[str, Any]]:
    columns = [
        "company_year_id",
        "field_id",
        "field_name_ja",
        "value",
        "unit_normalized",
        "data_scope",
        "source_heading",
        "source_quote",
        "confidence",
        "validation_status",
        "review_status",
    ]
    out: List[Dict[str, Any]] = []
    for row in source_audit_rows:
        if str(row.get("extraction_method", "")) != "LOCAL_RULE_TABLE":
            continue
        out.append({column: row.get(column, "") for column in columns})
        if len(out) >= limit:
            break
    return out


def _audit_prompt() -> str:
    return """# 有報抽出アルゴリズム定期メンテナンス

あなたはBuildBaseの保守者です。目的は、抽出設定とローカル表抽出ロジックが局所最適・過剰適合・非効率な分岐の山になっていないかを点検し、保守可能なアルゴリズムへ整理し、必要なコード・設定・テストを実装することです。

ユーザーはコード知識を前提にしません。コード妥当性の確認をユーザーに求めず、Codex/AIが監査、実装、テスト、差分説明まで担当してください。ユーザーに確認を求めるのは、業務上の採用判断、破壊的なデータ削除、外部API・課金・公開範囲に影響する変更だけです。

## 参照ファイル

まず `README.md` を読み、次に以下を確認してください。

- `field_algorithm_inventory.csv`
- `section_inventory.csv`
- `risk_flags.csv`
- `local_rule_samples.csv`
- `config/field_definition.csv`
- `config/extraction_sections.yml`
- `config/validation_rules.yml`
- `code/local_table_extractor.py`
- `code/section_locator.py`
- `reports/field_coverage.csv`
- `reports/run_report.md`

## 必ず守る前提

- 空欄を0扱いしない。
- standalone、consolidated、segment を混同しない。
- source_quote の根拠がない値を推定で埋めない。
- 1社1年度だけのレビュー証跡を、十分な根拠なく全社共通ルールに昇格しない。
- `review_resolved.csv` は保存済みレビューの監査証跡として扱い、勝手に削除・上書きしない。
- `review_queue.csv` や自動判定出力を、UIや自動処理から直接破壊的に上書きしない。
- 新しい抽出値は、source_quote、対象年度、単位、スコープを必ず確認できる形で残す。

## メンテナンス手順

1. 抽出セクションが増えすぎていないか。統合できる汎用パーサはあるか。
2. `field_definition.csv` の XBRL候補・同義語・セクションキーワードが広がりすぎて誤抽出リスクを上げていないか。
3. `LOCAL_RULE_TABLE` が増えている項目について、XBRL補完なのか、正式に表パーサへ昇格すべきなのか。
4. 低カバレッジ項目は、対象外・レビュー継続・汎用ルール追加のどれが妥当か。
5. 通常リスクの改善は、提案だけで止めずにコード・設定・テストへ反映する。
6. 高リスクまたは業務判断が必要な変更だけ、理由と選択肢を短く報告して停止する。

## 出力形式

以下の順で、短く具体的に報告してください。

1. 実装した改善: 変更ファイルと狙い。
2. レビュー由来のルール追加の整理結果: 統合、保留、維持の判断。
3. テスト結果: 実行したコマンドと結果。
4. 残リスク: source_quote不足、スコープ不明、低カバレッジなど。
5. 次に集めるべきレビュー材料: LOC/TABLE/LABEL/SCOPE/QUOTE の具体例。
"""


def _readme(files: Sequence[Dict[str, Any]]) -> str:
    file_lines = "\n".join(f"- `{row['file']}`: {row['description']}" for row in files)
    return f"""# Algorithm Audit Bundle

このフォルダは、有報抽出パイプラインのアルゴリズムをAI/Codexに定期メンテナンスさせるための資料です。分析用の `data/ai_bundle/` とは目的が違います。こちらは、レビュー由来ルール・設定・抽出コードが複雑化していないかを棚卸しし、通常リスクの改善を実装・テストするために使います。

## 使い方

1. この `data/algorithm_audit/` フォルダをCodex/AIに渡します。
2. `CODEX_MAINTENANCE_PROMPT.md` または互換用の `ALGORITHM_AUDIT_PROMPT.md` の内容を依頼文として貼ります。
3. Codex/AIが監査、実装、テスト、差分説明まで行います。ユーザーにコード妥当性の確認を求めません。
4. ユーザー確認が必要なのは、業務上の採用判断、破壊的なデータ削除、外部API・課金・公開範囲に影響する変更だけです。

## 同梱ファイル

{file_lines}

## 監査時の注意

- 空欄は0ではありません。
- standalone/consolidated/segment を混ぜないでください。
- レビュー1件だけで全社共通化された候補は、まず過剰適合リスクとして疑ってください。
- `risk_flags.csv` は機械的な赤旗です。実装判断では source_quote、対象年度、単位、スコープを確認してください。
- `review_resolved.csv` は監査証跡です。勝手に削除・上書きしないでください。
"""


def _write_table(path: Path, rows: Iterable[Dict[str, Any]], description: str) -> Dict[str, Any]:
    written = write_table(path, rows)
    return _file_row(path.parent, written, description)


def _copy_file(root: Path, audit_dir: Path, source_rel: str, target_rel: str, description: str) -> Dict[str, Any]:
    source = root / source_rel
    if not source.exists():
        return {}
    target = audit_dir / target_rel
    ensure_parent(target)
    shutil.copyfile(source, target)
    return _file_row(audit_dir, target, description, source=source_rel)


def _file_row(base_dir: Path, path: Path, description: str, source: str = "") -> Dict[str, Any]:
    return {
        "file": str(path.relative_to(base_dir)),
        "source": source,
        "description": description,
        "bytes": path.stat().st_size,
    }


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    return read_table(path) if path.exists() else []


def _split_values(value: Any) -> List[str]:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return []
    return [part.strip() for part in re.split(r"[;\n]+", text) if part.strip() and part.strip().lower() != "nan"]


def _as_int(value: Any) -> int:
    try:
        return int(float(str(value or "0")))
    except ValueError:
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(str(value or "0"))
    except ValueError:
        return 0.0


def _flag(severity: str, area: str, target: str, issue: str, evidence: str, suggested_action: str) -> Dict[str, Any]:
    return {
        "severity": severity,
        "area": area,
        "target": target,
        "issue": issue,
        "evidence": evidence,
        "suggested_action": suggested_action,
    }
