"""P7: 決定的アルゴリズム監査findings。

「継ぎ足し劣化」（重複タグ・矛盾マッピング・低カバレッジ概念・孤立概念・
review_*セクション残骸）を config/ semantics.db data/final/ から読み取り専用で
検出し、構造化findingsとして書き出す。

既存 services/algorithm_audit.py（build_algorithm_audit_bundle、外部AI/Codexに
丸投げするパック生成）とは目的が異なるため意図的に別モジュールとした。
このモジュールは read-only。config/*, data/final/*, semantics.db を一切変更しない。
実claudeは使用しない（全検出は決定的集計）。

出力:
  data/reports/algorithm_audit_findings.json — 全findings＋サマリ（機械可読）
  data/reports/algorithm_audit_findings.md   — 人間可読サマリ
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..io_utils import ensure_parent, read_yaml
from . import semantics_store

REPORTS_DIR = Path("data") / "reports"
JSON_FILENAME = "algorithm_audit_findings.json"
MD_FILENAME = "algorithm_audit_findings.md"

FIELD_DEFINITION_PATH = Path("config") / "field_definition.csv"
EXTRACTION_SECTIONS_PATH = Path("config") / "extraction_sections.yml"
FINAL_MASTER_LONG_PATH = Path("data") / "final" / "final_master_long.csv"

LOW_COVERAGE_THRESHOLD = 5
REVIEW_SECTION_COUNT_MEDIUM_THRESHOLD = 20


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_finding_id(kind: str, target: str, evidence: Dict[str, Any]) -> str:
    """finding_id の決定論的な生成。同一入力に対して常に同じIDを返す。"""
    raw = "|".join([kind or "", target or "", json.dumps(evidence, sort_keys=True, ensure_ascii=False, default=str)])
    return "aaf_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def make_finding(kind: str, severity: str, target: str, evidence: Dict[str, Any], suggested_action: str) -> Dict[str, Any]:
    return {
        "finding_id": make_finding_id(kind, target, evidence),
        "kind": kind,
        "severity": severity,
        "target": target,
        "evidence": evidence,
        "suggested_action": suggested_action,
    }


# ---------------------------------------------------------------------------
# 読み込みヘルパ（すべて読み取り専用）
# ---------------------------------------------------------------------------

def _read_field_definition_rows(root: Path) -> List[Dict[str, Any]]:
    path = root / FIELD_DEFINITION_PATH
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_final_master_long_rows(root: Path) -> List[Dict[str, Any]]:
    path = root / FINAL_MASTER_LONG_PATH
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_extraction_sections(root: Path) -> Dict[str, Any]:
    path = root / EXTRACTION_SECTIONS_PATH
    if not path.exists():
        return {}
    data = read_yaml(path)
    return data if isinstance(data, dict) else {}


def _open_readonly_semantics_connection(root: Path):
    """semantics.db を読み取り専用で開く。DBファイルが無ければ None を返す。"""
    db_path = semantics_store.semantics_db_path(root)
    if not db_path.exists():
        return None
    conn = semantics_store.connect(root)
    conn.execute("PRAGMA query_only=ON")
    return conn


# ---------------------------------------------------------------------------
# 検出器1: 重複/曖昧タグ
# ---------------------------------------------------------------------------

def detect_duplicate_tags(field_definition_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fields_by_id = {row["field_id"]: row for row in field_definition_rows if row.get("field_id")}

    token_to_fields: Dict[str, List[str]] = defaultdict(list)
    for row in field_definition_rows:
        field_id = row.get("field_id")
        if not field_id:
            continue
        tokens = [t.strip() for t in (row.get("xbrl_tag_candidates") or "").split(";") if t.strip()]
        for token in tokens:
            if field_id not in token_to_fields[token]:
                token_to_fields[token].append(field_id)

    findings: List[Dict[str, Any]] = []
    for token, field_ids in sorted(token_to_fields.items()):
        if len(field_ids) < 2:
            continue
        field_ids = sorted(field_ids)
        severity = _classify_duplicate_tag_severity(field_ids, fields_by_id)
        findings.append(
            make_finding(
                kind="duplicate_tag",
                severity=severity,
                target=token,
                evidence={"field_ids": field_ids, "token_count_in_fields": len(field_ids)},
                suggested_action=(
                    "候補が複数のfield_idに重複しています。context_filtersでの分離、または概念統合を検討してください。"
                ),
            )
        )
    return findings


def _classify_duplicate_tag_severity(field_ids: List[str], fields_by_id: Dict[str, Dict[str, Any]]) -> str:
    """2件重複かつ consolidated/standalone が context_filters のscope識別子
    （ConsolidatedMember / NonConsolidatedMember）で明確に分離されている場合は
    意図された設計なので low。それ以外（3件以上重複や分離されていない場合）は medium。

    period系フィルタ（CurrentYearDuration/CurrentYearInstant等）は連結/個別ペア間で
    共通することが多く、それだけを見ると isdisjoint にならない。scope識別子の有無で
    判定する。
    """
    if len(field_ids) != 2:
        return "medium"
    f1 = fields_by_id.get(field_ids[0], {})
    f2 = fields_by_id.get(field_ids[1], {})
    scopes = {f1.get("data_scope_required"), f2.get("data_scope_required")}
    if scopes != {"consolidated", "standalone"}:
        return "medium"
    cf1 = set((f1.get("context_filters") or "").split(";"))
    cf2 = set((f2.get("context_filters") or "").split(";"))
    scope_markers = {"ConsolidatedMember", "NonConsolidatedMember"}
    if (cf1 & scope_markers) and (cf2 & scope_markers) and (cf1 & scope_markers).isdisjoint(cf2 & scope_markers):
        return "low"
    return "medium"


# ---------------------------------------------------------------------------
# 検出器2: 矛盾マッピング
# ---------------------------------------------------------------------------

def detect_contradictory_mappings(conn) -> List[Dict[str, Any]]:
    """同一 observed_item_id が status='confirmed' で複数 concept_id にmapされているもの。

    重要: observed_item_id が空文字列/NULLの行（company_field_exclusions由来の
    company_scope単位ignore宣言）は observed_item同士の対応矛盾ではないため必ず除外する。
    これは実データ調査で踏んだ罠であり、回帰テストで固定すること。
    """
    if conn is None:
        return []
    rows = conn.execute(
        """
        select observed_item_id, group_concat(distinct concept_id) as concepts, count(distinct concept_id) as n
        from concept_mappings
        where status='confirmed'
          and concept_id is not null and concept_id != ''
          and observed_item_id is not null and observed_item_id != ''
        group by observed_item_id
        having n > 1
        """
    ).fetchall()

    findings: List[Dict[str, Any]] = []
    for row in rows:
        observed_item_id = row["observed_item_id"]
        concept_ids = sorted((row["concepts"] or "").split(","))
        findings.append(
            make_finding(
                kind="contradictory_mapping",
                severity="high",
                target=observed_item_id,
                evidence={"concept_ids": concept_ids},
                suggested_action="いずれか一方をsuperseded/rejectedに降格し、正しいconcept_idを決定してください。",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 検出器3: 低カバレッジ概念
# ---------------------------------------------------------------------------

def detect_low_coverage_concepts(
    field_definition_rows: List[Dict[str, Any]],
    final_long_rows: List[Dict[str, Any]],
    threshold: int = LOW_COVERAGE_THRESHOLD,
) -> List[Dict[str, Any]]:
    counts: Counter = Counter()
    for row in final_long_rows:
        field_id = row.get("field_id")
        value = str(row.get("value", "") or "").strip()
        if field_id and value not in ("", "nan", "None"):
            counts[field_id] += 1

    findings: List[Dict[str, Any]] = []
    for field in field_definition_rows:
        field_id = field.get("field_id")
        if not field_id:
            continue
        n = counts.get(field_id, 0)
        if n >= threshold:
            continue
        not_yet_extracted = field_id not in counts
        if not_yet_extracted:
            severity = "info"
        else:
            severity = "medium"
        findings.append(
            make_finding(
                kind="low_coverage_concept",
                severity=severity,
                target=field_id,
                evidence={
                    "filled_company_years": n,
                    "threshold": threshold,
                    "not_yet_extracted": not_yet_extracted,
                },
                suggested_action="XBRLタグ候補・同義語・セクション設定の拡充、または対象外化を検討してください。",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 検出器4: 孤立/未使用概念（+ オプションのunconfirmed_concept）
# ---------------------------------------------------------------------------

def detect_orphan_concepts(
    conn,
    field_definition_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """canonical_concepts にあるが field_definition にも confirmed mapping にも無い概念。

    副産物として、field_definitionにはあるがconfirmedマッピングがゼロの概念を
    kind='unconfirmed_concept' として別途返す（必須5種には含まれないオプション扱い）。
    """
    if conn is None:
        return []

    field_definition_ids = {row["field_id"] for row in field_definition_rows if row.get("field_id")}

    canonical_rows = conn.execute("select concept_id from canonical_concepts").fetchall()
    canonical_ids = {row["concept_id"] for row in canonical_rows if row["concept_id"]}

    confirmed_rows = conn.execute(
        """
        select distinct concept_id from concept_mappings
        where status='confirmed' and concept_id is not null and concept_id != ''
        """
    ).fetchall()
    confirmed_concept_ids = {row["concept_id"] for row in confirmed_rows if row["concept_id"]}

    findings: List[Dict[str, Any]] = []

    orphans = canonical_ids - field_definition_ids - confirmed_concept_ids
    for concept_id in sorted(orphans):
        findings.append(
            make_finding(
                kind="orphan_concept",
                severity="medium",
                target=concept_id,
                evidence={
                    "in_field_definition": concept_id in field_definition_ids,
                    "has_confirmed_mapping": concept_id in confirmed_concept_ids,
                },
                suggested_action="field_definitionへの追加、または概念の削除(status=retired)を検討してください。",
            )
        )

    proposed_counts: Counter = Counter()
    for row in conn.execute(
        "select concept_id, count(*) as n from concept_mappings where status='proposed' and concept_id is not null and concept_id != '' group by concept_id"
    ).fetchall():
        proposed_counts[row["concept_id"]] = row["n"]

    unconfirmed = field_definition_ids - confirmed_concept_ids
    for concept_id in sorted(unconfirmed):
        findings.append(
            make_finding(
                kind="unconfirmed_concept",
                severity="low",
                target=concept_id,
                evidence={"proposed_mapping_count": proposed_counts.get(concept_id, 0)},
                suggested_action="マッピングレビュー画面(P6)でproposed提案を確認・確定してください。",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# 検出器5: review_*セクション残骸
# ---------------------------------------------------------------------------

def detect_review_section_debt(sections: Dict[str, Any]) -> List[Dict[str, Any]]:
    review_sections = {
        name: section
        for name, section in sections.items()
        if isinstance(section, dict) and name.startswith("review_")
    }
    total = len(sections)

    findings: List[Dict[str, Any]] = []
    for name, section in sorted(review_sections.items()):
        target_fields = [str(item) for item in (section.get("target_fields") or []) if str(item).strip()]
        heading_keywords = [str(item) for item in (section.get("heading_keywords") or []) if str(item).strip()]
        findings.append(
            make_finding(
                kind="review_section_debt",
                severity="low",
                target=name,
                evidence={
                    "target_fields": target_fields,
                    "heading_keyword_count": len(heading_keywords),
                },
                suggested_action="対応するfield_idの本則セクションへ統合できないか確認してください。",
            )
        )

    findings.append(
        make_finding(
            kind="review_section_debt_summary",
            severity="medium" if len(review_sections) >= REVIEW_SECTION_COUNT_MEDIUM_THRESHOLD else "low",
            target="config/extraction_sections.yml",
            evidence={"review_section_count": len(review_sections), "total_section_count": total},
            suggested_action="review_*セクションが多い場合、共通パターンの汎用パーサへの統合を検討してください。",
        )
    )
    return findings


# ---------------------------------------------------------------------------
# ビルド本体
# ---------------------------------------------------------------------------

def build_audit_findings(root: Path) -> Dict[str, Any]:
    """5検出器を実行し、findings配列とサマリを返す（ファイル書き出しはしない）。"""
    field_definition_rows = _read_field_definition_rows(root)
    final_long_rows = _read_final_master_long_rows(root)
    sections = _read_extraction_sections(root)

    findings: List[Dict[str, Any]] = []
    findings.extend(detect_duplicate_tags(field_definition_rows))

    conn = _open_readonly_semantics_connection(root)
    try:
        findings.extend(detect_contradictory_mappings(conn))
        findings.extend(detect_low_coverage_concepts(field_definition_rows, final_long_rows))
        findings.extend(detect_orphan_concepts(conn, field_definition_rows))
    finally:
        if conn is not None:
            conn.close()

    findings.extend(detect_review_section_debt(sections))

    summary = _build_summary(findings)
    return {"generated_at_utc": _now_utc_iso(), "summary": summary, "findings": findings}


def _build_summary(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_kind: Counter = Counter()
    by_severity: Counter = Counter()
    for finding in findings:
        by_kind[finding["kind"]] += 1
        by_severity[finding["severity"]] += 1
    return {
        "total": len(findings),
        "by_kind": dict(sorted(by_kind.items())),
        "by_severity": dict(sorted(by_severity.items())),
    }


def build_algorithm_audit_findings(root: Path) -> Dict[str, Any]:
    """findingsを構築し、data/reports/ 配下にJSON/MDとして書き出す。読み取り専用。"""
    result = build_audit_findings(root)

    json_path = root / REPORTS_DIR / JSON_FILENAME
    ensure_parent(json_path)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    md_path = root / REPORTS_DIR / MD_FILENAME
    md_path.write_text(_render_markdown(result), encoding="utf-8")

    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "summary": result["summary"],
    }


def read_algorithm_audit_findings(root: Path) -> Dict[str, Any]:
    """GET APIから使う軽量読み出し。未生成なら status: not_built を返す。"""
    json_path = root / REPORTS_DIR / JSON_FILENAME
    if not json_path.exists():
        return {"status": "not_built"}
    return json.loads(json_path.read_text(encoding="utf-8"))


def _render_markdown(result: Dict[str, Any]) -> str:
    summary = result.get("summary", {})
    lines = [
        "# アルゴリズム監査findings サマリ (P7)",
        "",
        f"生成日時: {result.get('generated_at_utc', '')}",
        "",
        "## kind別件数",
    ]
    for kind, count in (summary.get("by_kind") or {}).items():
        lines.append(f"- {kind}: {count}")
    lines.append("")
    lines.append("## severity別件数")
    for severity, count in (summary.get("by_severity") or {}).items():
        lines.append(f"- {severity}: {count}")
    lines.append("")
    lines.append("## 上位findings（severity high/medium）")
    top_findings = [f for f in result.get("findings", []) if f.get("severity") in ("high", "medium")]
    if not top_findings:
        lines.append("")
        lines.append("該当なし。")
    else:
        for finding in top_findings:
            lines.append("")
            lines.append(f"### [{finding['severity']}] {finding['kind']}: {finding['target']}")
            lines.append(f"- evidence: {json.dumps(finding.get('evidence', {}), ensure_ascii=False, sort_keys=True)}")
            lines.append(f"- 提案: {finding.get('suggested_action', '')}")
    lines.append("")
    return "\n".join(lines)
