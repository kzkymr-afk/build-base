"""BuildBase P2/P4a: semantics.db の薄い永続化層。

data/marts/semantics/semantics.db への接続・DDL初期化・upsert のみを行う。
ビジネスロジック（自動確定ポリシーの判定・バックフィル変換規則等）はここに置かない
（corroboration_policy.py / services/semantics_corroborate.py / services/semantics_backfill.py
の責務）。

絶対制約: semantics.db は必ず data/marts/semantics/semantics.db に置く。
data/intermediate/edinet.db には絶対に書かない
（edinet_db.py の build_edinet_db() が毎回 unlink する揮発DBのため）。

P4a: 対応層3テーブル（observed_items / canonical_concepts / concept_mappings）を追加。
この段階では field_definition.csv のビュー化・生成、パイプライン挙動変更は行わない
（P4b/P4c送り）。既存の corroborations/cell_resolutions/ai_calls/golden_values/
golden_negative の挙動・スキーマは変更しない。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..io_utils import ensure_parent, write_table

SEMANTICS_DB_RELATIVE_PATH = Path("data") / "marts" / "semantics" / "semantics.db"
CELL_RESOLUTIONS_CSV_NAME = "cell_resolutions.csv"
CORROBORATIONS_CSV_NAME = "corroborations.csv"
GOLDEN_VALUES_CSV_NAME = "golden_values.csv"
GOLDEN_NEGATIVE_CSV_NAME = "golden_negative.csv"
OBSERVED_ITEMS_CSV_NAME = "observed_items.csv"
CANONICAL_CONCEPTS_CSV_NAME = "canonical_concepts.csv"
CONCEPT_MAPPINGS_CSV_NAME = "concept_mappings.csv"

# backup_semantics_db() が残すタイムスタンプ付きフルコピーの保持世代数。
# backfill-semantics / freeze_golden のたびに ~42MB のバイナリコピーが増えるため、
# 最新 N 世代だけ残して古いものを自動削除する（放置で data/marts/semantics/ が肥大化）。
SEMANTICS_DB_BACKUP_KEEP = 5


def semantics_db_path(root: Path) -> Path:
    return root / SEMANTICS_DB_RELATIVE_PATH


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_corroboration_id(
    company_year_id: str,
    concept_id: str,
    check_kind: str,
    check_ref: str,
    discriminator: str = "",
) -> str:
    """corroborations.corroboration_id の決定論的な生成。

    sha1(company_year_id|field_id|check_kind|check_ref|discriminator)。

    discriminator は「同一セル・同一check_kind・同一check_refだが実際には別の証拠」
    （例: next_year_prior で同一elementの連結/個別スコープの2レコード、primary_valueが
    19,277 と 18,192 で異なる）を衝突させないための識別子。呼び出し側が
    primary_value / other_value / matched / detail 等から組み立てる。空文字なら
    従来と同じ4キーのみのID（後方互換）。真に同一の証拠は同じIDに畳まれ冪等性を保つ。
    """
    raw = "|".join([company_year_id or "", concept_id or "", check_kind or "", check_ref or "", discriminator or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _corroboration_discriminator(record: Dict[str, Any]) -> str:
    """1レコードを一意化するための識別子文字列（決定論的）。"""
    detail = record.get("detail") or {}
    return "|".join(
        [
            "" if record.get("primary_value") is None else repr(record.get("primary_value")),
            "" if record.get("other_value") is None else repr(record.get("other_value")),
            "1" if record.get("matched") else "0",
            "1" if record.get("restatement_suspected") else "0",
            json.dumps(detail, sort_keys=True, ensure_ascii=False, default=str),
        ]
    )


def connect(root: Path) -> sqlite3.Connection:
    """semantics.db に接続し、DDLを初期化してから返す。呼び出し側がcloseすること。"""
    db_path = semantics_db_path(root)
    ensure_parent(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """3テーブルを CREATE TABLE IF NOT EXISTS する。冪等。"""
    conn.execute(
        """
        create table if not exists corroborations (
            corroboration_id text primary key,
            company_year_id text not null,
            concept_id text not null,
            check_kind text not null,
            check_ref text,
            matched integer,
            primary_value real,
            other_value real,
            difference real,
            restatement_suspected integer,
            detail_json text,
            run_id text,
            created_at_utc text
        )
        """
    )
    conn.execute(
        """
        create table if not exists cell_resolutions (
            company_year_id text not null,
            concept_id text not null,
            value real,
            corroboration_count integer,
            conflict_count integer,
            independent_bucket_count integer,
            buckets_json text,
            resolution text,
            review_reason text,
            sources_json text,
            run_id text,
            decided_at_utc text,
            primary key (company_year_id, concept_id)
        )
        """
    )
    conn.execute(
        """
        create table if not exists ai_calls (
            call_id text primary key,
            created_at_utc text,
            purpose text,
            model text,
            tier text,
            input_ref text,
            input_tokens integer,
            output_tokens integer,
            duration_ms integer,
            exit_code integer,
            status text,
            output_json text
        )
        """
    )
    conn.execute(
        """
        create table if not exists golden_values (
            company_year_id text not null,
            concept_id text not null,
            value real,
            value_text text,
            unit text,
            origin text,
            locked integer,
            evidence_json text,
            run_id text,
            decided_at_utc text,
            primary key (company_year_id, concept_id)
        )
        """
    )
    conn.execute(
        """
        create table if not exists golden_negative (
            company_year_id text not null,
            concept_id text not null,
            origin text,
            evidence_json text,
            run_id text,
            decided_at_utc text,
            primary key (company_year_id, concept_id)
        )
        """
    )
    # --- P4a: 対応層3テーブル ---------------------------------------------
    # observed_items: 観測された「タグ・ローカル表セル・人手レビュー判断そのもの」の
    # カタログ。不変・非統合・会社ローカルラベル保持。item_kind で由来を区別する。
    conn.execute(
        """
        create table if not exists observed_items (
            observed_item_id text primary key,
            item_kind text not null,
            element_id text,
            element_local_name text,
            normalized_scope text,
            period_bucket text,
            taxonomy_kind text,
            section_name text,
            row_label text,
            company_scope text,
            label_ja text,
            unit text,
            first_fiscal_year text,
            last_fiscal_year text,
            sample_values_json text,
            source text not null,
            created_at_utc text,
            updated_at_utc text
        )
        """
    )
    # canonical_concepts: 正準概念。P4a時点ではconcept_id=field_definition.csvの
    # field_idをそのまま初期シードとして継承する（65件、無変更）。
    conn.execute(
        """
        create table if not exists canonical_concepts (
            concept_id text primary key,
            concept_name_ja text,
            category text,
            data_scope text,
            target_unit text,
            period_type text,
            definition_ja text,
            calculation_formula text,
            status text not null default 'active',
            merged_into_concept_id text,
            created_at_utc text,
            updated_at_utc text
        )
        """
    )
    # concept_mappings: observed_item -> concept の対応判断。company_field_exclusions
    # 由来のみ observed_item_id が空文字を許容する（該当observed_itemを特定できないため）。
    conn.execute(
        """
        create table if not exists concept_mappings (
            mapping_id text primary key,
            observed_item_id text,
            concept_id text,
            action text not null,
            status text not null,
            decided_by text not null,
            confidence real,
            evidence_json text,
            valid_from_year text,
            valid_to_year text,
            company_scope text,
            superseded_by text,
            created_at_utc text,
            updated_at_utc text
        )
        """
    )
    conn.execute(
        "create index if not exists idx_concept_mappings_observed_status "
        "on concept_mappings (observed_item_id, status)"
    )
    conn.execute(
        "create index if not exists idx_concept_mappings_concept_status "
        "on concept_mappings (concept_id, status)"
    )
    conn.commit()


def replace_corroborations(
    conn: sqlite3.Connection,
    records: Iterable[Dict[str, Any]],
    run_id: str,
) -> int:
    """corroborations テーブルへ upsert する（corroboration_id で冪等）。

    records は corroboration.py の build_corroboration_record 形式
    （company_year_id, field_id, check_kind, check_ref, matched, primary_value,
    other_value, difference, restatement_suspected, detail）を想定する。
    ここでは field_id を concept_id として保存する（P2時点ではfield_id=concept_id）。

    corroboration_id は内容依存（primary_value等を含む）のため、id生成規則の変更や
    ソースデータの変化で過去runの行が孤児化しうる。毎run「現在の全証拠」で完全置換
    するため、挿入前に全行を削除する（単一の最新状態のみを保持する設計）。
    """
    created_at = _now_utc_iso()
    conn.execute("delete from corroborations")
    rows_written = 0
    for record in records:
        company_year_id = str(record.get("company_year_id") or "")
        concept_id = str(record.get("field_id") or "")
        check_kind = str(record.get("check_kind") or "")
        check_ref = str(record.get("check_ref") or "")
        if not company_year_id or not concept_id or not check_kind:
            continue
        corroboration_id = make_corroboration_id(
            company_year_id, concept_id, check_kind, check_ref, _corroboration_discriminator(record)
        )
        conn.execute(
            """
            insert into corroborations (
                corroboration_id, company_year_id, concept_id, check_kind, check_ref,
                matched, primary_value, other_value, difference, restatement_suspected,
                detail_json, run_id, created_at_utc
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(corroboration_id) do update set
                matched=excluded.matched,
                primary_value=excluded.primary_value,
                other_value=excluded.other_value,
                difference=excluded.difference,
                restatement_suspected=excluded.restatement_suspected,
                detail_json=excluded.detail_json,
                run_id=excluded.run_id,
                created_at_utc=excluded.created_at_utc
            """,
            (
                corroboration_id,
                company_year_id,
                concept_id,
                check_kind,
                check_ref,
                1 if record.get("matched") else 0,
                record.get("primary_value"),
                record.get("other_value"),
                record.get("difference"),
                1 if record.get("restatement_suspected") else 0,
                json.dumps(record.get("detail") or {}, ensure_ascii=False, sort_keys=True),
                run_id,
                created_at,
            ),
        )
        rows_written += 1
    conn.commit()
    return rows_written


def replace_cell_resolutions(
    conn: sqlite3.Connection,
    resolutions: Iterable[Dict[str, Any]],
    run_id: str,
) -> int:
    """cell_resolutions テーブルへ upsert する（company_year_id, concept_id で冪等）。

    resolutions の各要素は少なくとも company_year_id, concept_id(またはfield_id),
    resolution を持つ辞書。value/corroboration_count/conflict_count/
    independent_bucket_count/buckets/review_reason/sources は任意。
    """
    decided_at = _now_utc_iso()
    rows_written = 0
    for entry in resolutions:
        company_year_id = str(entry.get("company_year_id") or "")
        concept_id = str(entry.get("concept_id") or entry.get("field_id") or "")
        if not company_year_id or not concept_id:
            continue
        conn.execute(
            """
            insert into cell_resolutions (
                company_year_id, concept_id, value, corroboration_count, conflict_count,
                independent_bucket_count, buckets_json, resolution, review_reason,
                sources_json, run_id, decided_at_utc
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(company_year_id, concept_id) do update set
                value=excluded.value,
                corroboration_count=excluded.corroboration_count,
                conflict_count=excluded.conflict_count,
                independent_bucket_count=excluded.independent_bucket_count,
                buckets_json=excluded.buckets_json,
                resolution=excluded.resolution,
                review_reason=excluded.review_reason,
                sources_json=excluded.sources_json,
                run_id=excluded.run_id,
                decided_at_utc=excluded.decided_at_utc
            """,
            (
                company_year_id,
                concept_id,
                entry.get("value"),
                int(entry.get("corroboration_count") or 0),
                int(entry.get("conflict_count") or 0),
                int(entry.get("independent_bucket_count") or 0),
                json.dumps(sorted(entry.get("buckets") or []), ensure_ascii=False),
                str(entry.get("resolution") or ""),
                str(entry.get("review_reason") or ""),
                json.dumps(entry.get("sources") or [], ensure_ascii=False),
                run_id,
                decided_at,
            ),
        )
        rows_written += 1
    conn.commit()
    return rows_written


def fetch_cell_resolutions(conn: sqlite3.Connection) -> Dict[Any, Dict[str, Any]]:
    """(company_year_id, concept_id) -> row dict のマップとして cell_resolutions 全件を返す。"""
    out: Dict[Any, Dict[str, Any]] = {}
    for row in conn.execute("select * from cell_resolutions"):
        d = dict(row)
        out[(d["company_year_id"], d["concept_id"])] = d
    return out


def replace_golden_values(
    conn: sqlite3.Connection,
    entries: Iterable[Dict[str, Any]],
    run_id: str,
) -> int:
    """golden_values テーブルを完全置換する（P3: ゴールデン回帰基盤）。

    entries の各要素は company_year_id, concept_id, value, origin, locked,
    evidence(任意dict) を持つ辞書。golden集合はfreeze_golden()実行のたびに
    「現在の正しい状態」を表すため、挿入前に全行を削除する完全置換方式とする
    （replace_corroborations と同じ設計判断）。
    """
    decided_at = _now_utc_iso()
    conn.execute("delete from golden_values")
    rows_written = 0
    for entry in entries:
        company_year_id = str(entry.get("company_year_id") or "")
        concept_id = str(entry.get("concept_id") or "")
        if not company_year_id or not concept_id:
            continue
        conn.execute(
            """
            insert into golden_values (
                company_year_id, concept_id, value, value_text, unit, origin,
                locked, evidence_json, run_id, decided_at_utc
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(company_year_id, concept_id) do update set
                value=excluded.value,
                value_text=excluded.value_text,
                unit=excluded.unit,
                origin=excluded.origin,
                locked=excluded.locked,
                evidence_json=excluded.evidence_json,
                run_id=excluded.run_id,
                decided_at_utc=excluded.decided_at_utc
            """,
            (
                company_year_id,
                concept_id,
                entry.get("value"),
                entry.get("value_text"),
                entry.get("unit"),
                str(entry.get("origin") or ""),
                1 if entry.get("locked") else 0,
                json.dumps(entry.get("evidence") or {}, ensure_ascii=False, sort_keys=True, default=str),
                run_id,
                decided_at,
            ),
        )
        rows_written += 1
    conn.commit()
    return rows_written


def replace_golden_negative(
    conn: sqlite3.Connection,
    entries: Iterable[Dict[str, Any]],
    run_id: str,
) -> int:
    """golden_negative テーブル（not_applicableのネガティブゴールデン）を完全置換する。"""
    decided_at = _now_utc_iso()
    conn.execute("delete from golden_negative")
    rows_written = 0
    for entry in entries:
        company_year_id = str(entry.get("company_year_id") or "")
        concept_id = str(entry.get("concept_id") or "")
        if not company_year_id or not concept_id:
            continue
        conn.execute(
            """
            insert into golden_negative (
                company_year_id, concept_id, origin, evidence_json, run_id, decided_at_utc
            ) values (?, ?, ?, ?, ?, ?)
            on conflict(company_year_id, concept_id) do update set
                origin=excluded.origin,
                evidence_json=excluded.evidence_json,
                run_id=excluded.run_id,
                decided_at_utc=excluded.decided_at_utc
            """,
            (
                company_year_id,
                concept_id,
                str(entry.get("origin") or ""),
                json.dumps(entry.get("evidence") or {}, ensure_ascii=False, sort_keys=True, default=str),
                run_id,
                decided_at,
            ),
        )
        rows_written += 1
    conn.commit()
    return rows_written


def fetch_golden_values(conn: sqlite3.Connection) -> Dict[Any, Dict[str, Any]]:
    """(company_year_id, concept_id) -> row dict のマップとして golden_values 全件を返す。"""
    out: Dict[Any, Dict[str, Any]] = {}
    for row in conn.execute("select * from golden_values"):
        d = dict(row)
        out[(d["company_year_id"], d["concept_id"])] = d
    return out


def fetch_golden_negative(conn: sqlite3.Connection) -> Dict[Any, Dict[str, Any]]:
    """(company_year_id, concept_id) -> row dict のマップとして golden_negative 全件を返す。"""
    out: Dict[Any, Dict[str, Any]] = {}
    for row in conn.execute("select * from golden_negative"):
        d = dict(row)
        out[(d["company_year_id"], d["concept_id"])] = d
    return out


# ---------------------------------------------------------------------------
# P4a: 対応層3テーブルの upsert / fetch
# ---------------------------------------------------------------------------

def replace_observed_items(
    conn: sqlite3.Connection,
    items: Iterable[Dict[str, Any]],
    *,
    delete_first: bool = True,
) -> int:
    """observed_items を書き込む。

    delete_first=True（既定）の場合は挿入前に全行を削除する完全置換方式
    （corroborations/golden_valuesと同じ設計判断。バックフィル元データの変化が
    反映されなくなる=孤児化を防ぐ）。observed_item_id で on conflict upsertも
    行うため、delete_first=False で部分更新にも使える。
    """
    now = _now_utc_iso()
    if delete_first:
        conn.execute("delete from observed_items")
    rows_written = 0
    for item in items:
        observed_item_id = str(item.get("observed_item_id") or "")
        if not observed_item_id:
            continue
        conn.execute(
            """
            insert into observed_items (
                observed_item_id, item_kind, element_id, element_local_name,
                normalized_scope, period_bucket, taxonomy_kind, section_name,
                row_label, company_scope, label_ja, unit, first_fiscal_year,
                last_fiscal_year, sample_values_json, source,
                created_at_utc, updated_at_utc
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(observed_item_id) do update set
                item_kind=excluded.item_kind,
                element_id=excluded.element_id,
                element_local_name=excluded.element_local_name,
                normalized_scope=excluded.normalized_scope,
                period_bucket=excluded.period_bucket,
                taxonomy_kind=excluded.taxonomy_kind,
                section_name=excluded.section_name,
                row_label=excluded.row_label,
                company_scope=excluded.company_scope,
                label_ja=excluded.label_ja,
                unit=excluded.unit,
                first_fiscal_year=excluded.first_fiscal_year,
                last_fiscal_year=excluded.last_fiscal_year,
                sample_values_json=excluded.sample_values_json,
                source=excluded.source,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                observed_item_id,
                str(item.get("item_kind") or ""),
                str(item.get("element_id") or ""),
                str(item.get("element_local_name") or ""),
                str(item.get("normalized_scope") or ""),
                str(item.get("period_bucket") or ""),
                str(item.get("taxonomy_kind") or ""),
                str(item.get("section_name") or ""),
                str(item.get("row_label") or ""),
                str(item.get("company_scope") or ""),
                str(item.get("label_ja") or ""),
                str(item.get("unit") or ""),
                str(item.get("first_fiscal_year") or ""),
                str(item.get("last_fiscal_year") or ""),
                json.dumps(item.get("sample_values") or {}, ensure_ascii=False, sort_keys=True, default=str),
                str(item.get("source") or ""),
                item.get("created_at_utc") or now,
                now,
            ),
        )
        rows_written += 1
    conn.commit()
    return rows_written


def fetch_observed_items(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """observed_item_id -> row dict のマップとして observed_items 全件を返す。"""
    out: Dict[str, Dict[str, Any]] = {}
    for row in conn.execute("select * from observed_items"):
        d = dict(row)
        out[d["observed_item_id"]] = d
    return out


def upsert_canonical_concepts(
    conn: sqlite3.Connection,
    concepts: Iterable[Dict[str, Any]],
) -> int:
    """canonical_concepts を upsert する（フルupsert、既存concept_idも属性列を更新する）。

    P4a時点では field_definition.csv 65件が唯一の投入源であり、人間による
    status（merged/retired）編集はまだ発生しない（P4b以降の概念管理UIの守備範囲）。
    そのため今回は挿入も更新も同じ完全属性上書きでよい。
    """
    now = _now_utc_iso()
    rows_written = 0
    for concept in concepts:
        concept_id = str(concept.get("concept_id") or "")
        if not concept_id:
            continue
        conn.execute(
            """
            insert into canonical_concepts (
                concept_id, concept_name_ja, category, data_scope, target_unit,
                period_type, definition_ja, calculation_formula, status,
                merged_into_concept_id, created_at_utc, updated_at_utc
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(concept_id) do update set
                concept_name_ja=excluded.concept_name_ja,
                category=excluded.category,
                data_scope=excluded.data_scope,
                target_unit=excluded.target_unit,
                period_type=excluded.period_type,
                definition_ja=excluded.definition_ja,
                calculation_formula=excluded.calculation_formula,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                concept_id,
                str(concept.get("concept_name_ja") or ""),
                str(concept.get("category") or ""),
                str(concept.get("data_scope") or ""),
                str(concept.get("target_unit") or ""),
                str(concept.get("period_type") or ""),
                str(concept.get("definition_ja") or ""),
                str(concept.get("calculation_formula") or ""),
                str(concept.get("status") or "active"),
                str(concept.get("merged_into_concept_id") or ""),
                now,
                now,
            ),
        )
        rows_written += 1
    conn.commit()
    return rows_written


def fetch_canonical_concepts(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """concept_id -> row dict のマップとして canonical_concepts 全件を返す。"""
    out: Dict[str, Dict[str, Any]] = {}
    for row in conn.execute("select * from canonical_concepts"):
        d = dict(row)
        out[d["concept_id"]] = d
    return out


def replace_concept_mappings(
    conn: sqlite3.Connection,
    mappings: Iterable[Dict[str, Any]],
    *,
    delete_first: bool = True,
) -> int:
    """concept_mappings を書き込む。

    delete_first=True（既定）の場合は挿入前に全行を削除する完全置換方式
    （バックフィルの再実行を冪等にするため）。observed_item_id は
    company_field_exclusions由来のみ空文字を許容する。
    """
    now = _now_utc_iso()
    if delete_first:
        conn.execute("delete from concept_mappings")
    rows_written = 0
    for mapping in mappings:
        mapping_id = str(mapping.get("mapping_id") or "")
        action = str(mapping.get("action") or "")
        status = str(mapping.get("status") or "")
        decided_by = str(mapping.get("decided_by") or "")
        if not mapping_id or not action or not status or not decided_by:
            continue
        conn.execute(
            """
            insert into concept_mappings (
                mapping_id, observed_item_id, concept_id, action, status,
                decided_by, confidence, evidence_json, valid_from_year,
                valid_to_year, company_scope, superseded_by,
                created_at_utc, updated_at_utc
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(mapping_id) do update set
                observed_item_id=excluded.observed_item_id,
                concept_id=excluded.concept_id,
                action=excluded.action,
                status=excluded.status,
                decided_by=excluded.decided_by,
                confidence=excluded.confidence,
                evidence_json=excluded.evidence_json,
                valid_from_year=excluded.valid_from_year,
                valid_to_year=excluded.valid_to_year,
                company_scope=excluded.company_scope,
                superseded_by=excluded.superseded_by,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                mapping_id,
                str(mapping.get("observed_item_id") or ""),
                str(mapping.get("concept_id") or "") or None,
                action,
                status,
                decided_by,
                mapping.get("confidence"),
                json.dumps(mapping.get("evidence") or {}, ensure_ascii=False, sort_keys=True, default=str),
                str(mapping.get("valid_from_year") or "") or None,
                str(mapping.get("valid_to_year") or "") or None,
                str(mapping.get("company_scope") or ""),
                str(mapping.get("superseded_by") or "") or None,
                mapping.get("created_at_utc") or now,
                now,
            ),
        )
        rows_written += 1
    conn.commit()
    return rows_written


def fetch_concept_mappings(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """concept_mappings 全件をリストで返す。"""
    return [dict(row) for row in conn.execute("select * from concept_mappings")]


def update_concept_mapping_status(
    conn: sqlite3.Connection,
    mapping_id: str,
    *,
    new_status: str,
    new_decided_by: str,
    evidence_patch: Optional[Dict[str, Any]] = None,
    new_action: Optional[str] = None,
    new_concept_id: Optional[str] = None,
    expected_current_status: str = "proposed",
) -> bool:
    """既存1行の status/decided_by（任意でaction/concept_id）を更新する（確定への昇格専用）。

    安全ガード: expected_current_status と一致する行のみ更新する
    （既定'proposed'）。すでにconfirmed/rejected/supersededの行や、
    人間/deterministic判断の行（status='confirmed'）は
    where mapping_id = ? and status = ? の条件に一致しないため更新されない
    （P5cの絶対制約「既存confirmedを絶対に上書きしない」の二重防御）。

    evidence_patch は既存evidence_jsonへのマージ（キー追加。既存キーは
    evidence_patch側のキーで上書きされる。呼び出し側が意図的に上書きしたい
    キーのみを渡すこと）。

    戻り値: 実際に更新した場合True、対象行が無い/status不一致でNoopならFalse
    （冪等性の判定に使う——同じ呼び出しを2回してもFalseで実害なし）。
    """
    row = conn.execute(
        "select evidence_json from concept_mappings where mapping_id = ? and status = ?",
        (mapping_id, expected_current_status),
    ).fetchone()
    if row is None:
        return False
    evidence = json.loads(row["evidence_json"] or "{}") if isinstance(row, sqlite3.Row) else json.loads(row[0] or "{}")
    if evidence_patch:
        evidence.update(evidence_patch)
    now = _now_utc_iso()
    if new_action is not None or new_concept_id is not None:
        set_clauses = ["status = ?", "decided_by = ?", "evidence_json = ?", "updated_at_utc = ?"]
        params: List[Any] = [new_status, new_decided_by, json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str), now]
        if new_action is not None:
            set_clauses.append("action = ?")
            params.append(new_action)
        if new_concept_id is not None:
            set_clauses.append("concept_id = ?")
            params.append(new_concept_id)
        params.extend([mapping_id, expected_current_status])
        conn.execute(
            f"update concept_mappings set {', '.join(set_clauses)} where mapping_id = ? and status = ?",
            params,
        )
    else:
        conn.execute(
            """
            update concept_mappings
            set status = ?, decided_by = ?, evidence_json = ?, updated_at_utc = ?
            where mapping_id = ? and status = ?
            """,
            (
                new_status,
                new_decided_by,
                json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str),
                now,
                mapping_id,
                expected_current_status,
            ),
        )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# P5: ai_calls 追記ログ（コスト監査用。DELETEは行わない）
# ---------------------------------------------------------------------------

def insert_ai_call(conn: sqlite3.Connection, record: Dict[str, Any]) -> None:
    """ai_calls に1行追加する（追記のみ、upsertしない。呼び出し履歴は不変ログ）。

    corroborations/golden_values等の「現在の状態のみ保持」する完全置換方式とは
    異なり、ai_callsはコスト監査ログのため過去の呼び出し記録を消してはならない。
    """
    conn.execute(
        """
        insert into ai_calls (
            call_id, created_at_utc, purpose, model, tier, input_ref,
            input_tokens, output_tokens, duration_ms, exit_code, status, output_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["call_id"],
            record.get("created_at_utc") or _now_utc_iso(),
            record.get("purpose"),
            record.get("model"),
            record.get("tier"),
            record.get("input_ref"),
            record.get("input_tokens"),
            record.get("output_tokens"),
            record.get("duration_ms"),
            record.get("exit_code"),
            record.get("status"),
            json.dumps(record.get("output") or {}, ensure_ascii=False, sort_keys=True, default=str),
        ),
    )
    conn.commit()


def fetch_ai_calls(conn: sqlite3.Connection, purpose: Optional[str] = None) -> List[Dict[str, Any]]:
    """ai_calls を返す（budgetチェック・監査用）。purpose指定時はフィルタ。"""
    if purpose:
        rows = conn.execute("select * from ai_calls where purpose = ? order by created_at_utc", (purpose,))
    else:
        rows = conn.execute("select * from ai_calls order by created_at_utc")
    return [dict(row) for row in rows]


def _prune_semantics_backups(root: Path, keep: int = SEMANTICS_DB_BACKUP_KEEP) -> List[Path]:
    """semantics.db のタイムスタンプ付きバックアップを最新 keep 世代だけ残して古いものを削除する。

    バックアップ名は `{stem}.{YYYYMMDDTHHMMSSZ}.bak{suffix}`（例: semantics.20260704T183700Z.bak.db）。
    タイムスタンプはゼロ埋め固定幅なので、ファイル名の辞書順ソートが作成時刻順と一致する。
    そのため mtime に依存せず名前順で「古い順」を決定できる（テストが決定論的になる）。

    ライブの semantics.db やCSVミラーはグロブ `{stem}.*.bak{suffix}` に一致しないため削除対象外。
    keep 以下の世代数なら何も削除しない。keep=0 なら全バックアップを削除する。
    削除に失敗したファイル（権限・競合等）はスキップし、致命的エラーにはしない
    （次回実行で再試行される）。実際に削除したPathのリストを返す（監査・テスト用）。
    """
    keep = max(keep, 0)
    db_path = semantics_db_path(root)
    semantics_dir = db_path.parent
    if not semantics_dir.exists():
        return []
    pattern = f"{db_path.stem}.*.bak{db_path.suffix}"
    backups = sorted(semantics_dir.glob(pattern), key=lambda p: p.name)
    if len(backups) <= keep:
        return []
    deleted: List[Path] = []
    for path in backups[: len(backups) - keep]:
        try:
            path.unlink()
            deleted.append(path)
        except OSError:
            continue
    return deleted


def backup_semantics_db(root: Path) -> Optional[Path]:
    """semantics.db が既に存在する場合、タイムスタンプ付きでバイナリコピーしてからNoneではなくPathを返す。

    既存の _backup_file パターン（services/field_admin.py, services/rule_candidates.py）を踏襲。
    存在しない場合は None を返す（初回実行時等）。

    コピー後に _prune_semantics_backups() で古い世代を刈り取る（最新 SEMANTICS_DB_BACKUP_KEEP
    世代のみ保持）。返り値の契約は変えず、今作成したバックアップのPath（新規なら常に保持対象）を返す。
    """
    db_path = semantics_db_path(root)
    if not db_path.exists():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.{timestamp}.bak{db_path.suffix}")
    shutil.copyfile(db_path, backup_path)
    _prune_semantics_backups(root)
    return backup_path


def write_csv_mirrors(root: Path, conn: sqlite3.Connection) -> Dict[str, Path]:
    """cell_resolutions / corroborations / 対応層3表をCSVミラーとして
    data/marts/semantics/ に書き出す。

    Git追跡・目視確認用。semantics.db自体が正であり、CSVは派生物。
    """
    semantics_dir = (root / SEMANTICS_DB_RELATIVE_PATH).parent
    ensure_parent(semantics_dir / "placeholder")

    cell_rows = [dict(row) for row in conn.execute("select * from cell_resolutions order by company_year_id, concept_id")]
    corroboration_rows = [
        dict(row) for row in conn.execute("select * from corroborations order by company_year_id, concept_id, check_kind, check_ref")
    ]
    golden_rows = [dict(row) for row in conn.execute("select * from golden_values order by company_year_id, concept_id")]
    golden_negative_rows = [
        dict(row) for row in conn.execute("select * from golden_negative order by company_year_id, concept_id")
    ]
    observed_item_rows = [
        dict(row) for row in conn.execute("select * from observed_items order by observed_item_id")
    ]
    canonical_concept_rows = [
        dict(row) for row in conn.execute("select * from canonical_concepts order by concept_id")
    ]
    concept_mapping_rows = [
        dict(row) for row in conn.execute("select * from concept_mappings order by mapping_id")
    ]

    cell_path = semantics_dir / CELL_RESOLUTIONS_CSV_NAME
    corroboration_path = semantics_dir / CORROBORATIONS_CSV_NAME
    golden_path = semantics_dir / GOLDEN_VALUES_CSV_NAME
    golden_negative_path = semantics_dir / GOLDEN_NEGATIVE_CSV_NAME
    observed_items_path = semantics_dir / OBSERVED_ITEMS_CSV_NAME
    canonical_concepts_path = semantics_dir / CANONICAL_CONCEPTS_CSV_NAME
    concept_mappings_path = semantics_dir / CONCEPT_MAPPINGS_CSV_NAME
    write_table(cell_path, cell_rows)
    write_table(corroboration_path, corroboration_rows)
    write_table(golden_path, golden_rows)
    write_table(golden_negative_path, golden_negative_rows)
    write_table(observed_items_path, observed_item_rows)
    write_table(canonical_concepts_path, canonical_concept_rows)
    write_table(concept_mappings_path, concept_mapping_rows)
    return {
        "cell_resolutions_csv": cell_path,
        "corroborations_csv": corroboration_path,
        "golden_values_csv": golden_path,
        "golden_negative_csv": golden_negative_path,
        "observed_items_csv": observed_items_path,
        "canonical_concepts_csv": canonical_concepts_path,
        "concept_mappings_csv": concept_mappings_path,
    }
