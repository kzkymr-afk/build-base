from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from yuho_auto_extract.services import algorithm_audit_findings as aaf
from yuho_auto_extract.services import semantics_store


FIELD_DEFINITION_HEADER = [
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


def _write_field_definition(root: Path, rows: list[dict]) -> None:
    path = root / "config" / "field_definition.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELD_DEFINITION_HEADER)
        writer.writeheader()
        for row in rows:
            full = {key: row.get(key, "") for key in FIELD_DEFINITION_HEADER}
            writer.writerow(full)


def _field_row(
    field_id: str,
    *,
    xbrl_tag_candidates: str = "",
    data_scope_required: str = "consolidated",
    context_filters: str = "",
) -> dict:
    return {
        "field_id": field_id,
        "field_name_ja": field_id,
        "category": "performance",
        "target_unit": "百万円",
        "data_scope_required": data_scope_required,
        "period_type": "current_year",
        "preferred_method": "XBRL_CSV",
        "xbrl_tag_candidates": xbrl_tag_candidates,
        "context_filters": context_filters,
    }


def _write_extraction_sections(root: Path, sections: dict) -> None:
    path = root / "config" / "extraction_sections.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(sections, allow_unicode=True), encoding="utf-8")


def _write_final_master_long(root: Path, rows: list[dict]) -> None:
    path = root / "data" / "final" / "final_master_long.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["field_id", "value", "company_year_id"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _seed_observed_item(conn, observed_item_id: str, element_id: str) -> None:
    if not observed_item_id:
        return
    semantics_store.replace_observed_items(
        conn,
        [
            {
                "observed_item_id": observed_item_id,
                "item_kind": "xbrl",
                "element_id": element_id,
                "element_local_name": element_id,
                "label_ja": "",
                "normalized_scope": "consolidated",
                "taxonomy_kind": "jppfs",
                "unit": "円",
                "source": "metric_catalog",
                "sample_values": {},
            }
        ],
        delete_first=False,
    )


def _seed_concept(conn, concept_id: str) -> None:
    semantics_store.upsert_canonical_concepts(
        conn,
        [
            {
                "concept_id": concept_id,
                "concept_name_ja": concept_id,
                "category": "financial",
                "data_scope": "consolidated",
                "target_unit": "円",
                "status": "active",
            }
        ],
    )


def _seed_mapping(
    conn,
    mapping_id: str,
    *,
    observed_item_id: str,
    concept_id: str,
    action: str = "map",
    status: str = "confirmed",
    decided_by: str = "human:reviewer_a",
) -> None:
    semantics_store.replace_concept_mappings(
        conn,
        [
            {
                "mapping_id": mapping_id,
                "observed_item_id": observed_item_id,
                "concept_id": concept_id,
                "action": action,
                "status": status,
                "decided_by": decided_by,
                "confidence": 0.9,
                "evidence": {"rationale": "test"},
            }
        ],
        delete_first=False,
    )


class DuplicateTagTests(unittest.TestCase):
    def test_detects_multi_field_tokens(self):
        rows = [
            _field_row("field_a", xbrl_tag_candidates="SharedTag"),
            _field_row("field_b", xbrl_tag_candidates="SharedTag"),
            _field_row("field_c", xbrl_tag_candidates="SharedTag"),
        ]
        findings = aaf.detect_duplicate_tags(rows)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["kind"], "duplicate_tag")
        self.assertEqual(finding["target"], "SharedTag")
        self.assertEqual(sorted(finding["evidence"]["field_ids"]), ["field_a", "field_b", "field_c"])
        # 3件重複は2者ペア判定の対象外なので常に medium
        self.assertEqual(finding["severity"], "medium")

    def test_consolidated_standalone_pair_with_scope_markers_is_low_severity(self):
        rows = [
            _field_row(
                "operating_income_consolidated",
                xbrl_tag_candidates="OperatingIncome",
                data_scope_required="consolidated",
                context_filters="CurrentYearDuration;ConsolidatedMember",
            ),
            _field_row(
                "operating_income_standalone",
                xbrl_tag_candidates="OperatingIncome",
                data_scope_required="standalone",
                context_filters="CurrentYearDuration;NonConsolidatedMember",
            ),
        ]
        findings = aaf.detect_duplicate_tags(rows)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "low")

    def test_pair_without_scope_markers_is_medium_severity(self):
        rows = [
            _field_row(
                "new_concept_a",
                xbrl_tag_candidates="AmbiguousTag",
                data_scope_required="consolidated",
                context_filters="",
            ),
            _field_row(
                "new_concept_b",
                xbrl_tag_candidates="AmbiguousTag",
                data_scope_required="standalone",
                context_filters="",
            ),
        ]
        findings = aaf.detect_duplicate_tags(rows)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "medium")

    def test_no_duplicates_when_tokens_unique(self):
        rows = [
            _field_row("field_a", xbrl_tag_candidates="UniqueTagA"),
            _field_row("field_b", xbrl_tag_candidates="UniqueTagB"),
        ]
        self.assertEqual(aaf.detect_duplicate_tags(rows), [])


class ContradictoryMappingTests(unittest.TestCase):
    def test_ignores_empty_observed_item_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_concept(conn, "concept_a")
            _seed_concept(conn, "concept_b")
            # company_field_exclusions由来: observed_item_id='' の複数confirmed行
            _seed_mapping(conn, "cmap_1", observed_item_id="", concept_id="concept_a", action="ignore")
            _seed_mapping(conn, "cmap_2", observed_item_id="", concept_id="concept_b", action="ignore")
            findings = aaf.detect_contradictory_mappings(conn)
            conn.close()
            self.assertEqual(findings, [])

    def test_detects_real_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:NetSales")
            _seed_concept(conn, "concept_a")
            _seed_concept(conn, "concept_b")
            _seed_mapping(conn, "cmap_1", observed_item_id="xm_1", concept_id="concept_a")
            _seed_mapping(conn, "cmap_2", observed_item_id="xm_1", concept_id="concept_b")
            findings = aaf.detect_contradictory_mappings(conn)
            conn.close()
            self.assertEqual(len(findings), 1)
            finding = findings[0]
            self.assertEqual(finding["kind"], "contradictory_mapping")
            self.assertEqual(finding["severity"], "high")
            self.assertEqual(finding["target"], "xm_1")
            self.assertEqual(sorted(finding["evidence"]["concept_ids"]), ["concept_a", "concept_b"])

    def test_none_connection_returns_empty(self):
        self.assertEqual(aaf.detect_contradictory_mappings(None), [])


class LowCoverageConceptTests(unittest.TestCase):
    def test_below_and_above_threshold_boundary(self):
        field_rows = [_field_row("low_field"), _field_row("ok_field")]
        final_rows = [{"field_id": "low_field", "value": "1"}] * 3 + [{"field_id": "ok_field", "value": "1"}] * 5
        findings = aaf.detect_low_coverage_concepts(field_rows, final_rows, threshold=5)
        targets = {f["target"]: f for f in findings}
        self.assertIn("low_field", targets)
        self.assertNotIn("ok_field", targets)
        self.assertEqual(targets["low_field"]["evidence"]["filled_company_years"], 3)
        self.assertEqual(targets["low_field"]["severity"], "medium")
        self.assertFalse(targets["low_field"]["evidence"]["not_yet_extracted"])

    def test_marks_not_yet_extracted_as_info(self):
        field_rows = [_field_row("never_extracted_field")]
        final_rows: list[dict] = []
        findings = aaf.detect_low_coverage_concepts(field_rows, final_rows, threshold=5)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding["severity"], "info")
        self.assertTrue(finding["evidence"]["not_yet_extracted"])
        self.assertEqual(finding["evidence"]["filled_company_years"], 0)


class OrphanConceptTests(unittest.TestCase):
    def test_detects_true_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_concept(conn, "orphan_concept_x")
            field_rows = [_field_row("some_other_field")]
            findings = aaf.detect_orphan_concepts(conn, field_rows)
            conn.close()
            orphan_findings = [f for f in findings if f["kind"] == "orphan_concept"]
            self.assertEqual(len(orphan_findings), 1)
            self.assertEqual(orphan_findings[0]["target"], "orphan_concept_x")
            self.assertEqual(orphan_findings[0]["severity"], "medium")

    def test_concept_with_confirmed_mapping_is_not_orphan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            _seed_observed_item(conn, "xm_1", "jppfs_cor:NetSales")
            _seed_concept(conn, "mapped_concept")
            _seed_mapping(conn, "cmap_1", observed_item_id="xm_1", concept_id="mapped_concept")
            field_rows: list[dict] = []
            findings = aaf.detect_orphan_concepts(conn, field_rows)
            conn.close()
            orphan_findings = [f for f in findings if f["kind"] == "orphan_concept"]
            self.assertEqual(orphan_findings, [])

    def test_unconfirmed_concept_reported_when_field_definition_has_no_confirmed_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = semantics_store.connect(root)
            field_rows = [_field_row("unconfirmed_field")]
            findings = aaf.detect_orphan_concepts(conn, field_rows)
            conn.close()
            unconfirmed = [f for f in findings if f["kind"] == "unconfirmed_concept"]
            self.assertEqual(len(unconfirmed), 1)
            self.assertEqual(unconfirmed[0]["target"], "unconfirmed_field")


class ReviewSectionDebtTests(unittest.TestCase):
    def test_counts_review_prefixed_sections(self):
        sections = {
            "core_section": {"target_fields": ["a"], "heading_keywords": ["x"]},
            "review_field_a": {"target_fields": ["field_a"], "heading_keywords": ["y", "z"]},
            "review_field_b": {"target_fields": ["field_b"], "heading_keywords": []},
        }
        findings = aaf.detect_review_section_debt(sections)
        debt_findings = [f for f in findings if f["kind"] == "review_section_debt"]
        summary_findings = [f for f in findings if f["kind"] == "review_section_debt_summary"]
        self.assertEqual(len(debt_findings), 2)
        self.assertEqual({f["target"] for f in debt_findings}, {"review_field_a", "review_field_b"})
        self.assertEqual(len(summary_findings), 1)
        self.assertEqual(summary_findings[0]["evidence"]["review_section_count"], 2)
        self.assertEqual(summary_findings[0]["evidence"]["total_section_count"], 3)

    def test_summary_severity_escalates_above_threshold(self):
        sections = {f"review_field_{i}": {"target_fields": [], "heading_keywords": []} for i in range(20)}
        findings = aaf.detect_review_section_debt(sections)
        summary = next(f for f in findings if f["kind"] == "review_section_debt_summary")
        self.assertEqual(summary["severity"], "medium")


class FindingIdDeterminismTests(unittest.TestCase):
    def test_finding_id_is_deterministic(self):
        id1 = aaf.make_finding_id("duplicate_tag", "SomeTag", {"field_ids": ["a", "b"]})
        id2 = aaf.make_finding_id("duplicate_tag", "SomeTag", {"field_ids": ["a", "b"]})
        self.assertEqual(id1, id2)

    def test_finding_id_changes_with_evidence(self):
        id1 = aaf.make_finding_id("duplicate_tag", "SomeTag", {"field_ids": ["a", "b"]})
        id2 = aaf.make_finding_id("duplicate_tag", "SomeTag", {"field_ids": ["a", "c"]})
        self.assertNotEqual(id1, id2)


def _file_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


class BuildAlgorithmAuditFindingsIntegrationTests(unittest.TestCase):
    def _seed_project(self, root: Path) -> None:
        _write_field_definition(
            root,
            [
                _field_row(
                    "operating_income_consolidated",
                    xbrl_tag_candidates="OperatingIncome",
                    data_scope_required="consolidated",
                    context_filters="CurrentYearDuration;ConsolidatedMember",
                ),
                _field_row(
                    "operating_income_standalone",
                    xbrl_tag_candidates="OperatingIncome",
                    data_scope_required="standalone",
                    context_filters="CurrentYearDuration;NonConsolidatedMember",
                ),
                _field_row("low_field", xbrl_tag_candidates="LowFieldTag"),
            ],
        )
        _write_extraction_sections(
            root,
            {
                "core_section": {"target_fields": ["operating_income_consolidated"], "heading_keywords": ["x"]},
                "review_low_field": {"target_fields": ["low_field"], "heading_keywords": ["y"]},
            },
        )
        _write_final_master_long(
            root,
            [{"field_id": "operating_income_consolidated", "value": "100", "company_year_id": f"c{i}"} for i in range(6)],
        )
        conn = semantics_store.connect(root)
        conn.close()

    def test_build_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_project(root)

            field_def_path = root / "config" / "field_definition.csv"
            sections_path = root / "config" / "extraction_sections.yml"
            final_path = root / "data" / "final" / "final_master_long.csv"
            db_path = semantics_store.semantics_db_path(root)

            before = {
                str(p): _file_md5(p)
                for p in [field_def_path, sections_path, final_path, db_path]
            }

            aaf.build_algorithm_audit_findings(root)

            after = {
                str(p): _file_md5(p)
                for p in [field_def_path, sections_path, final_path, db_path]
            }
            self.assertEqual(before, after)

    def test_build_writes_json_and_markdown_with_expected_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_project(root)

            result = aaf.build_algorithm_audit_findings(root)
            json_path = Path(result["json_path"])
            md_path = Path(result["md_path"])
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())

            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertIn("generated_at_utc", data)
            self.assertIn("summary", data)
            self.assertIn("findings", data)
            for finding in data["findings"]:
                for key in ("finding_id", "kind", "severity", "target", "evidence", "suggested_action"):
                    self.assertIn(key, finding)

            # 集計されたfindingの中に、意図した2種が含まれる
            kinds = {f["kind"] for f in data["findings"]}
            self.assertIn("duplicate_tag", kinds)
            self.assertIn("review_section_debt", kinds)

    def test_read_algorithm_audit_findings_not_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = aaf.read_algorithm_audit_findings(root)
            self.assertEqual(result, {"status": "not_built"})

    def test_read_algorithm_audit_findings_after_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_project(root)
            aaf.build_algorithm_audit_findings(root)
            result = aaf.read_algorithm_audit_findings(root)
            self.assertNotEqual(result.get("status"), "not_built")
            self.assertIn("findings", result)

    def test_finding_ids_stable_across_repeated_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_project(root)
            aaf.build_algorithm_audit_findings(root)
            first = json.loads((root / "data" / "reports" / "algorithm_audit_findings.json").read_text(encoding="utf-8"))
            aaf.build_algorithm_audit_findings(root)
            second = json.loads((root / "data" / "reports" / "algorithm_audit_findings.json").read_text(encoding="utf-8"))
            first_ids = sorted(f["finding_id"] for f in first["findings"])
            second_ids = sorted(f["finding_id"] for f in second["findings"])
            self.assertEqual(first_ids, second_ids)


if __name__ == "__main__":
    unittest.main()
