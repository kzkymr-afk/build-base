import tempfile
import unittest
from pathlib import Path

from yuho_auto_extract.io_utils import read_table, write_table
from yuho_auto_extract.services import field_definition_enrich, semantics_store


FIELD_DEFINITION_COLUMNS = [
    "field_id",
    "field_name_ja",
    "category",
    "target_unit",
    "data_scope_required",
    "period_type",
    "preferred_method",
    "xbrl_tag_candidates",
    "context_filters",
    "section_keywords",
    "synonyms_ja",
    "calculation_formula",
    "validation_rule_ids",
    "review_threshold",
    "notes",
]


def _write_field_definition(root: Path) -> None:
    write_table(
        root / "config" / "field_definition.csv",
        [
            {
                "field_id": "operating_income_consolidated",
                "field_name_ja": "営業利益_連結",
                "category": "performance",
                "target_unit": "百万円",
                "data_scope_required": "consolidated",
                "period_type": "current_year",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "OperatingIncome;ProfitLossFromOperatingActivities",
                "context_filters": "CurrentYearDuration;ConsolidatedMember",
                "section_keywords": "",
                "synonyms_ja": "手キュレーション済み同義語",
                "calculation_formula": "",
                "validation_rule_ids": "",
                "review_threshold": "0.95",
                "notes": "IFRS/日本基準でタグ差異あり",
            },
            {
                "field_id": "total_assets_consolidated",
                "field_name_ja": "総資産_連結",
                "category": "financial_position",
                "target_unit": "百万円",
                "data_scope_required": "consolidated",
                "period_type": "current_year",
                "preferred_method": "XBRL_CSV",
                "xbrl_tag_candidates": "TotalAssets;TotalAssetsSummaryOfBusinessResults",
                "context_filters": "",
                "section_keywords": "貸借対照表",
                "synonyms_ja": "総資産",
                "calculation_formula": "",
                "validation_rule_ids": "vr_001",
                "review_threshold": "0.90",
                "notes": "既存メモ",
            },
        ],
    )


def _seed_semantics_db(root: Path) -> None:
    conn = semantics_store.connect(root)
    try:
        now = "2026-07-04T00:00:00+00:00"

        # observed_items
        observed = [
            ("xm_1", "Assets", "consolidated", "jppfs"),
            ("xm_2", "NewTagXyz", "standalone", "jppfs"),  # 既に含まれない新規タグ -> 追記対象
            ("xm_3", "AverageNumberOfTemporaryWorkers", "", "jppfs"),  # 新概念用
        ]
        for observed_item_id, element_local_name, scope, taxonomy_kind in observed:
            conn.execute(
                """
                insert into observed_items
                (observed_item_id, item_kind, element_id, element_local_name, normalized_scope,
                 period_bucket, taxonomy_kind, section_name, row_label, company_scope, label_ja,
                 unit, first_fiscal_year, last_fiscal_year, sample_values_json, source,
                 created_at_utc, updated_at_utc)
                values (?, 'xbrl_metric', ?, ?, ?, '', ?, '', '', '', '', '円', '', '', '{}', 'test', ?, ?)
                """,
                (observed_item_id, element_local_name, element_local_name, scope, taxonomy_kind, now, now),
            )

        # canonical_concepts: 1件は既存field_id、1件は新概念
        conn.execute(
            """
            insert into canonical_concepts
            (concept_id, concept_name_ja, category, data_scope, target_unit, period_type,
             definition_ja, calculation_formula, status, merged_into_concept_id,
             created_at_utc, updated_at_utc)
            values ('total_assets_consolidated', '総資産_連結', 'financial_position', 'consolidated',
                    '百万円', 'current_year', '', '', 'active', '', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            insert into canonical_concepts
            (concept_id, concept_name_ja, category, data_scope, target_unit, period_type,
             definition_ja, calculation_formula, status, merged_into_concept_id,
             created_at_utc, updated_at_utc)
            values ('nc_new_concept_1', '平均臨時雇用人員', 'human_capital', '', '', 'current_year',
                    '', '', 'active', '', ?, ?)
            """,
            (now, now),
        )
        # マージ済み概念（採用対象外になることを確認するため）
        conn.execute(
            """
            insert into canonical_concepts
            (concept_id, concept_name_ja, category, data_scope, target_unit, period_type,
             definition_ja, calculation_formula, status, merged_into_concept_id,
             created_at_utc, updated_at_utc)
            values ('nc_merged_concept', 'マージ済み概念', 'performance', '', '', 'current_year',
                    '', '', 'merged', 'operating_income_consolidated', ?, ?)
            """,
            (now, now),
        )

        # concept_mappings: 既存概念への確定corroboration map（新タグ）
        conn.execute(
            """
            insert into concept_mappings
            (mapping_id, observed_item_id, concept_id, action, status, decided_by, confidence,
             evidence_json, valid_from_year, valid_to_year, company_scope, superseded_by,
             created_at_utc, updated_at_utc)
            values ('cmap_1', 'xm_1', 'total_assets_consolidated', 'map', 'confirmed',
                    'ai:claude-haiku-4-5+corroboration', 0.9, '{}', '', '', '', '', ?, ?)
            """,
            (now, now),
        )
        # 既存タグと同じ要素名（大文字小文字違い）の corroboration map -> no-op になるはず
        conn.execute(
            """
            insert into concept_mappings
            (mapping_id, observed_item_id, concept_id, action, status, decided_by, confidence,
             evidence_json, valid_from_year, valid_to_year, company_scope, superseded_by,
             created_at_utc, updated_at_utc)
            values ('cmap_2', 'xm_2', 'operating_income_consolidated', 'map', 'confirmed',
                    'ai:claude-haiku-4-5+corroboration', 0.9, '{}', '', '', '', '', ?, ?)
            """,
            (now, now),
        )
        # 新概念への確定 human_adopt map
        conn.execute(
            """
            insert into concept_mappings
            (mapping_id, observed_item_id, concept_id, action, status, decided_by, confidence,
             evidence_json, valid_from_year, valid_to_year, company_scope, superseded_by,
             created_at_utc, updated_at_utc)
            values ('cmap_3', 'xm_3', 'nc_new_concept_1', 'map', 'confirmed',
                    'ai:claude-haiku-4-5+human_adopt', 0.9, '{}', '', '', '', '', ?, ?)
            """,
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()


class FieldDefinitionEnrichTests(unittest.TestCase):
    def test_build_enrichment_plan_identifies_append_and_new_rows_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_field_definition(root)
            _seed_semantics_db(root)

            csv_before = (root / "config" / "field_definition.csv").read_text(encoding="utf-8")

            plan = field_definition_enrich.build_enrichment_plan(root)

            csv_after = (root / "config" / "field_definition.csv").read_text(encoding="utf-8")
            self.assertEqual(csv_before, csv_after)
            self.assertFalse((root / "config" / "field_definition.xlsx").exists())

            append_field_ids = {item["field_id"]: item for item in plan["appends"]}
            self.assertIn("total_assets_consolidated", append_field_ids)
            self.assertEqual(append_field_ids["total_assets_consolidated"]["tags_to_add"], ["Assets"])

            # operating_income_consolidated への corroboration map は既存タグに無いので追記対象になる
            self.assertIn("operating_income_consolidated", append_field_ids)
            self.assertEqual(append_field_ids["operating_income_consolidated"]["tags_to_add"], ["NewTagXyz"])

            self.assertEqual(plan["new_row_count"], 1)
            self.assertEqual(plan["new_rows"][0]["field_id"], "nc_new_concept_1")
            self.assertEqual(plan["new_rows"][0]["xbrl_tag_candidates"], "AverageNumberOfTemporaryWorkers")
            self.assertEqual(plan["new_rows"][0]["preferred_method"], "XBRL_CSV")
            self.assertEqual(plan["new_rows"][0]["review_threshold"], "0.85")

    def test_apply_enrichment_appends_tag_and_preserves_hand_curated_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_field_definition(root)
            _seed_semantics_db(root)

            result = field_definition_enrich.apply_enrichment(root)

            rows = read_table(root / "config" / "field_definition.csv")
            by_id = {row["field_id"]: row for row in rows}

            self.assertIn("Assets", by_id["total_assets_consolidated"]["xbrl_tag_candidates"].split(";"))
            self.assertIn("TotalAssets", by_id["total_assets_consolidated"]["xbrl_tag_candidates"].split(";"))
            # 手キュレーション列は不変
            self.assertEqual(by_id["total_assets_consolidated"]["synonyms_ja"], "総資産")
            self.assertEqual(by_id["total_assets_consolidated"]["section_keywords"], "貸借対照表")
            self.assertEqual(by_id["total_assets_consolidated"]["validation_rule_ids"], "vr_001")
            self.assertEqual(by_id["total_assets_consolidated"]["review_threshold"], "0.90")

            self.assertEqual(by_id["operating_income_consolidated"]["synonyms_ja"], "手キュレーション済み同義語")

            self.assertIn("nc_new_concept_1", by_id)
            self.assertEqual(by_id["nc_new_concept_1"]["category"], "human_capital")

            self.assertEqual(result["appended_count"], 2)
            self.assertEqual(result["added_count"], 1)

            self.assertTrue((root / "config" / "field_definition.xlsx").exists())
            xlsx_rows = read_table(root / "config" / "field_definition.xlsx")
            xlsx_by_id = {row["field_id"]: row for row in xlsx_rows}
            self.assertIn("nc_new_concept_1", xlsx_by_id)
            self.assertIn("Assets", xlsx_by_id["total_assets_consolidated"]["xbrl_tag_candidates"].split(";"))

    def test_apply_enrichment_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_field_definition(root)
            _seed_semantics_db(root)

            field_definition_enrich.apply_enrichment(root)
            rows_after_first = read_table(root / "config" / "field_definition.csv")

            second_result = field_definition_enrich.apply_enrichment(root)
            rows_after_second = read_table(root / "config" / "field_definition.csv")

            self.assertEqual(rows_after_first, rows_after_second)
            self.assertEqual(second_result["appended_count"], 0)
            self.assertEqual(second_result["added_count"], 0)

    def test_dry_run_does_not_write_files_or_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_field_definition(root)
            _seed_semantics_db(root)

            field_definition_enrich.build_enrichment_plan(root)

            config_files = sorted(p.name for p in (root / "config").iterdir())
            self.assertEqual(config_files, ["field_definition.csv"])

    def test_merged_concept_is_not_adopted_as_new_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_field_definition(root)
            _seed_semantics_db(root)

            plan = field_definition_enrich.build_enrichment_plan(root)
            new_ids = {row["field_id"] for row in plan["new_rows"]}
            self.assertNotIn("nc_merged_concept", new_ids)


if __name__ == "__main__":
    unittest.main()
