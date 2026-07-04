from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from yuho_auto_extract.io_utils import is_blankish, read_table, write_table


NOTE_RELATIVE_PATH = Path("Obsidian") / "Kzky-works" / "ゼネコン各社の技術者数.md"
LONG_OUTPUT = Path("data") / "intermediate" / "manual_technician_extracted_long.csv"
MART_DIR = Path("data") / "marts" / "manual_technicians"
MART_LONG = MART_DIR / "architecture_engineers_long.csv"
MART_WIDE = MART_DIR / "architecture_engineers_wide.csv"
UNMATCHED_OUTPUT = MART_DIR / "unmatched_rows.csv"
SUMMARY_OUTPUT = MART_DIR / "import_summary.json"

FIELD_COLUMNS = [
    {
        "field_id": "architecture_engineers_1st_class",
        "source_column": "建築技術者数（一級）",
        "field_name_ja": "建築一式_技術職員数_一級",
    },
    {
        "field_id": "architecture_engineers_1st_class_training",
        "source_column": "建築監理技術者数",
        "field_name_ja": "建築一式_技術職員数_一級_講習受講",
    },
]

COMPANY_ALIASES = {
    "安藤ハザマ": "ANDO_HAZAMA",
    "長谷工": "HASEKO",
    "銭高組": "ZENITAKA",
}


def import_manual_technicians(root: Path, note_path: Optional[Path] = None) -> Dict[str, Any]:
    note = note_path or default_note_path(root)
    if not note.exists():
        raise FileNotFoundError(f"manual technician note not found: {note}")

    note_text = note.read_text(encoding="utf-8")
    company_master = read_table(root / "config" / "company_master.csv")
    company_year_master = read_table(root / "config" / "company_year_master.csv")
    company_aliases = _company_aliases(company_master)
    company_years = _company_year_index(company_year_master)
    run_id = "manual-technicians-" + hashlib.sha256(note_text.encode("utf-8")).hexdigest()[:12]

    long_rows: List[Dict[str, Any]] = []
    wide_rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    unmatched: List[Dict[str, Any]] = []
    parsed_rows = parse_markdown_table(note_text)

    for parsed in parsed_rows:
        raw_company = str(parsed.get("会社名") or "").strip()
        company_id = company_aliases.get(_normalize_company_name(raw_company))
        if not company_id:
            unmatched.append(_unmatched_row(parsed, "company_not_in_master", note))
            continue

        period = _parse_period(str(parsed.get("年度") or ""))
        if not period:
            unmatched.append(_unmatched_row(parsed, "period_parse_failed", note, company_id=company_id))
            continue

        year_end_year, explicit_month = period
        fiscal_month = explicit_month or _company_fiscal_month(company_master, company_id) or 3
        company_year = company_years.get((company_id, year_end_year, fiscal_month))
        if not company_year:
            unmatched.append(
                _unmatched_row(
                    parsed,
                    "company_year_not_in_master",
                    note,
                    company_id=company_id,
                    expected_fiscal_year_end=f"{year_end_year:04d}-{fiscal_month:02d}",
                )
            )
            continue

        base = {
            "run_id": run_id,
            "company_year_id": company_year.get("company_year_id"),
            "operating_company_id": company_id,
            "fiscal_year": company_year.get("fiscal_year"),
            "source_doc_id": "obsidian_manual_technicians",
            "source_file": str(note),
            "source_heading": "ゼネコン各社の技術者数.md",
            "source_quote": parsed.get("_raw_line", ""),
            "unit_raw": "人",
            "data_scope": "permit_entity",
            "extraction_method": "MANUAL_OBSIDIAN",
            "confidence": 1.0,
            "review_required": False,
            "period_label": parsed.get("年度"),
            "source_segment_label": "建築一式",
            "normalized_segment_key": "architecture_construction",
            "segment_taxonomy_status": "canonical_keishin_trade",
            "applies_to_company_id": company_id,
            "status": "found",
            "notes": "Obsidianで手整理した経営事項審査結果通知の技術職員数。ノートの年はfiscal_year_end年として突合。",
        }
        wide_key = (str(company_year.get("company_year_id") or ""), company_id)
        wide = wide_rows.setdefault(
            wide_key,
            {
                "company_year_id": company_year.get("company_year_id"),
                "operating_company_id": company_id,
                "fiscal_year": company_year.get("fiscal_year"),
                "fiscal_year_end": company_year.get("fiscal_year_end"),
                "source_period_label": parsed.get("年度"),
                "source_company_name": raw_company,
                "source_file": str(note),
            },
        )
        for field in FIELD_COLUMNS:
            value_raw = parsed.get(field["source_column"])
            value = _parse_int(value_raw)
            if value is None:
                if not is_blankish(value_raw):
                    unmatched.append(
                        _unmatched_row(
                            parsed,
                            f"value_parse_failed:{field['source_column']}",
                            note,
                            company_id=company_id,
                            company_year_id=str(company_year.get("company_year_id") or ""),
                        )
                    )
                continue
            row = {
                **base,
                "field_id": field["field_id"],
                "field_name_ja": field["field_name_ja"],
                "value_raw": value,
            }
            long_rows.append(row)
            wide[field["field_id"]] = value

    long_rows = sorted(long_rows, key=lambda row: (str(row.get("company_year_id")), str(row.get("field_id"))))
    wide_output = [wide_rows[key] for key in sorted(wide_rows)]
    write_table(root / LONG_OUTPUT, long_rows)
    write_table(root / MART_LONG, long_rows)
    write_table(root / MART_WIDE, wide_output)
    write_table(root / UNMATCHED_OUTPUT, unmatched)
    summary = {
        "status": "succeeded",
        "source_note": str(note),
        "parsed_rows": len(parsed_rows),
        "imported_long_rows": len(long_rows),
        "imported_company_year_rows": len(wide_output),
        "unmatched_rows": len(unmatched),
        "output": str(root / LONG_OUTPUT),
        "mart_long": str(root / MART_LONG),
        "mart_wide": str(root / MART_WIDE),
        "unmatched_output": str(root / UNMATCHED_OUTPUT),
    }
    _write_summary(root / SUMMARY_OUTPUT, summary)
    return summary


def default_note_path(root: Path) -> Path:
    for base in [root, *root.parents]:
        candidate = base / NOTE_RELATIVE_PATH
        if candidate.exists():
            return candidate
    return root / NOTE_RELATIVE_PATH


def parse_markdown_table(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    headers: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        if not headers and cells[0] == "年度":
            headers = cells
            continue
        if cells and set(cells[0].replace(" ", "")) <= {"-"}:
            continue
        if not headers or len(cells) < len(headers):
            continue
        row = {headers[index]: cells[index] for index in range(len(headers))}
        row["_raw_line"] = stripped
        rows.append(row)
    return rows


def _company_aliases(company_master: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for company in company_master:
        company_id = str(company.get("operating_company_id") or "").strip()
        name = str(company.get("operating_company_name") or "").strip()
        if not company_id or not name:
            continue
        aliases[_normalize_company_name(name)] = company_id
    for alias, company_id in COMPANY_ALIASES.items():
        aliases[_normalize_company_name(alias)] = company_id
    return aliases


def _company_fiscal_month(company_master: Iterable[Dict[str, Any]], company_id: str) -> Optional[int]:
    for company in company_master:
        if str(company.get("operating_company_id") or "") != company_id:
            continue
        try:
            return int(company.get("fiscal_year_end_month") or 0) or None
        except (TypeError, ValueError):
            return None
    return None


def _company_year_index(company_year_master: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, int, int], Dict[str, Any]]:
    index: Dict[Tuple[str, int, int], Dict[str, Any]] = {}
    for row in company_year_master:
        company_id = str(row.get("operating_company_id") or "").strip()
        fiscal_year_end = str(row.get("fiscal_year_end") or "").strip()
        match = re.match(r"^(\d{4})-(\d{2})-\d{2}$", fiscal_year_end)
        if not company_id or not match:
            continue
        index[(company_id, int(match.group(1)), int(match.group(2)))] = row
    return index


def _parse_period(value: str) -> Optional[Tuple[int, Optional[int]]]:
    match = re.fullmatch(r"\s*(\d{4})(?:\((\d{1,2})\))?\s*", value)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else None
    return year, month


def _parse_int(value: Any) -> Optional[int]:
    if is_blankish(value):
        return None
    text = str(value).strip().replace(",", "")
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def _normalize_company_name(value: str) -> str:
    text = value.strip()
    for token in ["株式会社", "（株）", "(株)", "㈱", " ", "　", "・", ".", "．"]:
        text = text.replace(token, "")
    return text


def _unmatched_row(
    parsed: Dict[str, Any],
    reason: str,
    note: Path,
    company_id: str = "",
    company_year_id: str = "",
    expected_fiscal_year_end: str = "",
) -> Dict[str, Any]:
    return {
        "reason": reason,
        "raw_period": parsed.get("年度", ""),
        "raw_company_name": parsed.get("会社名", ""),
        "company_id": company_id,
        "company_year_id": company_year_id,
        "expected_fiscal_year_end": expected_fiscal_year_end,
        "architecture_engineers_1st_class": parsed.get("建築技術者数（一級）", ""),
        "architecture_engineers_1st_class_training": parsed.get("建築監理技術者数", ""),
        "source_file": str(note),
        "source_quote": parsed.get("_raw_line", ""),
    }


def _write_summary(path: Path, summary: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
