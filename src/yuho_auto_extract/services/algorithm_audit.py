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
    ("data/review/rule_candidates.csv", "review/rule_candidates.csv", "レビューから作られた抽出ルール候補。"),
    ("data/review/review_resolved.csv", "review/review_resolved.csv", "人間が保存したレビュー判断。"),
    ("src/yuho_auto_extract/section_locator.py", "code/section_locator.py", "候補ブロック探索ロジック。"),
    ("src/yuho_auto_extract/local_table_extractor.py", "code/local_table_extractor.py", "ローカル表抽出ロジック。レビュー由来ルールの受け皿。"),
    ("src/yuho_auto_extract/edinet_db.py", "code/edinet_db.py", "XBRL DB抽出とローカル抽出の統合箇所。"),
    ("src/yuho_auto_extract/normalizer.py", "code/normalizer.py", "数値・単位・スコープ正規化。"),
    ("src/yuho_auto_extract/services/rule_candidates.py", "code/rule_candidates.py", "レビュー文から候補ルールを作る処理。"),
]

GENERATED_FILES = [
    ("README.md", "監査パックの使い方。"),
    ("ALGORITHM_AUDIT_PROMPT.md", "AIに渡す固定監査プロンプト。"),
    ("manifest.json", "生成日時と同梱ファイル一覧。"),
    ("field_algorithm_inventory.csv", "項目別の抽出方式・カバレッジ・レビュー由来状況。"),
    ("section_inventory.csv", "抽出セクション別のキーワードと対象項目。"),
    ("review_learning_inventory.csv", "レビュー由来ルール候補の一覧。"),
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
    candidate_rows = _read_rows(root / "data" / "review" / "rule_candidates.csv")

    field_inventory = _field_inventory(field_rows, final_long_rows, coverage_rows, resolved_rows, candidate_rows)
    section_inventory = _section_inventory(sections)
    review_inventory = _review_learning_inventory(candidate_rows, sections)
    risk_flags = _risk_flags(field_inventory, section_inventory, review_inventory, resolved_rows)
    local_samples = _local_rule_samples(source_audit_rows)

    generated = [
        _write_table(audit_dir / "field_algorithm_inventory.csv", field_inventory, "項目別の抽出方式・カバレッジ・レビュー由来状況。"),
        _write_table(audit_dir / "section_inventory.csv", section_inventory, "抽出セクション別のキーワードと対象項目。"),
        _write_table(audit_dir / "review_learning_inventory.csv", review_inventory, "レビュー由来ルール候補の一覧。"),
        _write_table(audit_dir / "risk_flags.csv", risk_flags, "複雑化・過剰適合・未反映の自動検出フラグ。"),
        _write_table(audit_dir / "local_rule_samples.csv", local_samples, "LOCAL_RULE_TABLE由来の根拠サンプル。"),
    ]

    copied = [_copy_file(root, audit_dir, source, target, description) for source, target, description in COPY_FILES]
    copied = [row for row in copied if row]

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prompt = _audit_prompt()
    readme = _readme(generated + copied)
    (audit_dir / "ALGORITHM_AUDIT_PROMPT.md").write_text(prompt, encoding="utf-8")
    (audit_dir / "README.md").write_text(readme, encoding="utf-8")

    generated.extend(
        [
            _file_row(audit_dir, audit_dir / "README.md", "監査パックの使い方。"),
            _file_row(audit_dir, audit_dir / "ALGORITHM_AUDIT_PROMPT.md", "AIに渡す固定監査プロンプト。"),
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
            "review_derived_sections": sum(1 for row in section_inventory if row.get("rule_type") == "review_derived"),
            "review_learning_candidates": len(review_inventory),
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
    candidate_rows: Sequence[Dict[str, Any]],
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

    candidates_by_field = {str(row.get("field_id", "")): row for row in candidate_rows if row.get("field_id")}
    out: List[Dict[str, Any]] = []
    for field in field_rows:
        field_id = str(field.get("field_id", "")).strip()
        if not field_id:
            continue
        counts = extracted_by_field.get(field_id, Counter())
        coverage = coverage_by_field.get(field_id, {})
        candidate = candidates_by_field.get(field_id, {})
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
                "rule_candidate_evidence_count": candidate.get("evidence_count", ""),
                "rule_candidate_needs_manual_check": candidate.get("needs_manual_check", ""),
                "rule_candidate_action": candidate.get("recommended_action", ""),
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
                "rule_type": "review_derived" if section_name.startswith("review_") else "core",
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


def _review_learning_inventory(candidate_rows: Sequence[Dict[str, Any]], sections: Dict[str, Any]) -> List[Dict[str, Any]]:
    section_names = set(sections.keys())
    out: List[Dict[str, Any]] = []
    for row in candidate_rows:
        field_id = str(row.get("field_id", "")).strip()
        if not field_id:
            continue
        section_name = f"review_{field_id}"
        out.append(
            {
                "field_id": field_id,
                "field_name_ja": row.get("field_name_ja", ""),
                "evidence_count": row.get("evidence_count", ""),
                "company_year_ids": row.get("company_year_ids", ""),
                "generality": row.get("generality", ""),
                "needs_manual_check": row.get("needs_manual_check", ""),
                "recommended_action": row.get("recommended_action", ""),
                "has_config_section": "yes" if section_name in section_names else "no",
                "proposed_xbrl_tags": row.get("proposed_xbrl_tags", ""),
                "proposed_section_keywords": row.get("proposed_section_keywords", ""),
                "proposed_tables": row.get("proposed_tables", ""),
                "proposed_row_labels": row.get("proposed_row_labels", ""),
                "reviewed_value_examples": row.get("reviewed_value_examples", ""),
            }
        )
    return out


def _risk_flags(
    field_inventory: Sequence[Dict[str, Any]],
    section_inventory: Sequence[Dict[str, Any]],
    review_inventory: Sequence[Dict[str, Any]],
    resolved_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    field_by_id = {str(row.get("field_id", "")): row for row in field_inventory}

    for row in review_inventory:
        evidence_count = _as_int(row.get("evidence_count"))
        field_id = str(row.get("field_id", ""))
        if evidence_count < 2 and "全社共通" in str(row.get("generality", "")):
            flags.append(
                _flag(
                    "medium",
                    "review_learning",
                    field_id,
                    "single_evidence_global_rule",
                    f"証跡{evidence_count}件で全社共通候補になっています。",
                    "別会社・別年度の証跡を追加するか、汎用ルールではなく保留候補にしてください。",
                )
            )
        if str(row.get("needs_manual_check", "")).lower() == "yes":
            flags.append(
                _flag(
                    "medium",
                    "review_learning",
                    field_id,
                    "candidate_needs_manual_check",
                    "rule_candidates.csv が要確認フラグを出しています。",
                    "設定反映前に source_quote と対象スコープを確認してください。",
                )
            )

    for section in section_inventory:
        section_name = str(section.get("section_name", ""))
        if section.get("rule_type") == "review_derived":
            field_ids = _split_values(section.get("target_fields", ""))
            evidence = max((_as_int(field_by_id.get(field_id, {}).get("rule_candidate_evidence_count")) for field_id in field_ids), default=0)
            if evidence < 2:
                flags.append(
                    _flag(
                        "medium",
                        "section_config",
                        section_name,
                        "review_section_low_evidence",
                        f"review_* セクションですが、紐づく証跡が{evidence}件です。",
                        "同じ表構造で複数社・複数年度に効くことを確認してから汎用化してください。",
                    )
                )
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
    return """# 有報抽出アルゴリズム定期監査

あなたは財務データ抽出パイプラインの監査者です。目的は、レビュー由来のルール追加が局所最適・過剰適合・非効率な分岐の山になっていないかを点検し、保守可能なアルゴリズムへ整理することです。

## 参照ファイル

まず `README.md` を読み、次に以下を確認してください。

- `field_algorithm_inventory.csv`
- `section_inventory.csv`
- `review_learning_inventory.csv`
- `risk_flags.csv`
- `local_rule_samples.csv`
- `config/field_definition.csv`
- `config/extraction_sections.yml`
- `config/validation_rules.yml`
- `code/local_table_extractor.py`
- `code/section_locator.py`
- `code/rule_candidates.py`
- `reports/field_coverage.csv`
- `reports/run_report.md`

## 必ず守る前提

- 空欄を0扱いしない。
- standalone、consolidated、segment を混同しない。
- source_quote の根拠がない値を推定で埋めない。
- 1社1年度だけのレビュー証跡を、十分な根拠なく全社共通ルールに昇格しない。
- 人間が保存したレビュー値は監査証跡として扱い、勝手に削除・上書き前提にしない。

## 監査してほしい観点

1. `review_*` セクションが増えすぎていないか。統合できる汎用パーサはあるか。
2. `rule_candidates.csv` の証跡数が少ないのに全社共通化されているものはないか。
3. `field_definition.csv` の XBRL候補・同義語・セクションキーワードが広がりすぎて誤抽出リスクを上げていないか。
4. `LOCAL_RULE_TABLE` が増えている項目について、XBRL補完なのか、正式に表パーサへ昇格すべきなのか。
5. 低カバレッジ項目は、対象外・人間レビュー継続・汎用ルール追加のどれが妥当か。
6. テストで固定すべき代表パターンと、削除または保留すべきルールは何か。

## 出力形式

以下の順で、短く具体的に提案してください。

1. 高リスク事項: すぐ直すべき順に列挙。
2. 統合候補: 個別 `review_*` から汎用パーサへ移すべきもの。
3. 保留/削除候補: 証跡不足、過剰適合、誤抽出リスクがあるもの。
4. 設定整理案: `field_definition.csv` / `extraction_sections.yml` / `validation_rules.yml` の修正方針。
5. テスト追加案: どの表パターンをユニットテスト化すべきか。
6. 次回の人間レビューで集めるべきメモ: LOC/TABLE/LABEL/SCOPE/QUOTE の具体例。
"""


def _readme(files: Sequence[Dict[str, Any]]) -> str:
    file_lines = "\n".join(f"- `{row['file']}`: {row['description']}" for row in files)
    return f"""# Algorithm Audit Bundle

このフォルダは、有報抽出パイプラインのアルゴリズムをAIに監査させるための資料です。分析用の `data/ai_bundle/` とは目的が違います。こちらは、レビュー由来ルール・設定・抽出コードが複雑化していないかを棚卸しするために使います。

## 使い方

1. この `data/algorithm_audit/` フォルダをAIに渡します。
2. `ALGORITHM_AUDIT_PROMPT.md` の内容を依頼文として貼ります。
3. AIの提案をそのまま自動反映せず、人間が妥当性を確認してから実装します。

## 同梱ファイル

{file_lines}

## 監査時の注意

- 空欄は0ではありません。
- standalone/consolidated/segment を混ぜないでください。
- レビュー1件だけで全社共通化された候補は、まず過剰適合リスクとして疑ってください。
- `risk_flags.csv` は機械的な赤旗です。最終判断では source_quote と対象年度を確認してください。
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
