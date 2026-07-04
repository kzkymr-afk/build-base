from __future__ import annotations

import json
import time
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from yuho_auto_extract.io_utils import is_blankish, prefer_existing_table, read_table, write_table
from yuho_auto_extract.services.automation import load_automation_config, merge_records_by_key


FetchChart = Callable[[str, date, date, Dict[str, Any]], Dict[str, Any]]

PRICE_COLUMNS = [
    "listed_company_id",
    "operating_company_id",
    "operating_company_name",
    "stock_code",
    "ticker",
    "exchange",
    "date",
    "month",
    "fiscal_year",
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
    "monthly_return",
    "monthly_return_pct",
    "dividend",
    "split_factor",
    "shares_outstanding",
    "market_cap",
    "currency",
    "source",
    "source_symbol",
    "fetched_at_utc",
]

NUMERIC_PRICE_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
    "adjusted_close",
    "volume",
    "monthly_return",
    "monthly_return_pct",
    "dividend",
    "split_factor",
    "shares_outstanding",
    "market_cap",
    "fiscal_year",
}

STOCK_CHART_FIELDS = [
    {"id": "adjusted_close", "name": "調整後終値", "category": "market_price", "unit": "円"},
    {"id": "close", "name": "終値", "category": "market_price", "unit": "円"},
    {"id": "open", "name": "始値", "category": "market_price", "unit": "円"},
    {"id": "high", "name": "高値", "category": "market_price", "unit": "円"},
    {"id": "low", "name": "安値", "category": "market_price", "unit": "円"},
    {"id": "volume", "name": "出来高", "category": "market_volume", "unit": "株"},
    {"id": "monthly_return_pct", "name": "月次リターン", "category": "market_return", "unit": "%"},
    {"id": "dividend", "name": "配当", "category": "market_event", "unit": "円"},
    {"id": "split_factor", "name": "分割係数", "category": "market_event", "unit": "倍"},
    {"id": "market_cap", "name": "時価総額", "category": "market_value", "unit": "円"},
]

DEFAULT_STOCK_CHART_FIELDS = ["adjusted_close", "monthly_return_pct", "volume"]


def stock_monthly_status(root: Path, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    cfg = _market_config(root)
    securities = read_security_master(root)
    rows = _read_optional(root / "data" / "marts" / "market" / "stock_price_monthly.csv")
    latest_month = _latest_month(rows)
    last = _read_json(root / "data" / "automation" / "stock_monthly_last.json")
    last_successful_month = str(last.get("last_successful_month") or latest_month or "")
    target_month_end = _target_month_end(today, include_current_month=bool(cfg.get("include_current_month", False)))
    target_month = target_month_end.strftime("%Y-%m")
    run_day = int(cfg.get("run_day_of_month") or 5)
    due = (
        bool(cfg.get("enabled", True))
        and bool(securities)
        and _month_key(last_successful_month) < _month_key(target_month)
        and today.day >= run_day
    )
    enabled_securities = [row for row in securities if _bool(row.get("enabled"), True)]
    active_errors, ignored_listing_errors = _split_errors_by_listing_period(last.get("errors", []) or [], securities, target_month_end)
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "provider": cfg.get("provider", "yahoo_finance_chart"),
        "cadence": "monthly",
        "as_of": today.isoformat(),
        "run_day_of_month": run_day,
        "due": due,
        "target_month": target_month,
        "latest_month": latest_month,
        "last_run_at_utc": last.get("run_at_utc", ""),
        "last_status": last.get("status", ""),
        "last_successful_month": last_successful_month,
        "last_error_count": len(active_errors),
        "last_ignored_listing_error_count": len(ignored_listing_errors),
        "price_rows": len(rows),
        "total_securities": len(securities),
        "enabled_securities": len(enabled_securities),
        "output_path": str(root / "data" / "marts" / "market" / "stock_price_monthly.csv"),
        "security_master_path": str(root / "data" / "marts" / "market" / "security_master.csv"),
        "message": _stock_status_message(due, latest_month, target_month, last, len(active_errors), len(ignored_listing_errors)),
    }


def stock_monthly_options(root: Path) -> Dict[str, Any]:
    rows = _read_optional(root / "data" / "marts" / "market" / "stock_price_monthly.csv")
    securities = read_security_master(root)
    companies_by_id = {
        str(row.get("operating_company_id") or ""): {
            "id": str(row.get("operating_company_id") or ""),
            "name": str(row.get("operating_company_name") or ""),
            "label": _company_label(row),
        }
        for row in securities
        if row.get("operating_company_id")
    }
    for row in rows:
        company_id = str(row.get("operating_company_id") or "")
        if company_id and company_id not in companies_by_id:
            companies_by_id[company_id] = {
                "id": company_id,
                "name": str(row.get("operating_company_name") or ""),
                "label": _company_label(row),
            }
    return {
        "companies": sorted(companies_by_id.values(), key=lambda item: item["id"]),
        "months": sorted({str(row.get("month") or "") for row in rows if row.get("month")}),
        "fields": [_stock_field_option(row) for row in STOCK_CHART_FIELDS],
        "default_result_fields": DEFAULT_STOCK_CHART_FIELDS,
        "field_presets": [
            {"id": "price", "name": "株価", "fields": ["adjusted_close", "close"]},
            {"id": "return", "name": "リターン", "fields": ["monthly_return_pct"]},
            {"id": "volume", "name": "出来高", "fields": ["volume"]},
            {"id": "events", "name": "配当・分割", "fields": ["dividend", "split_factor"]},
        ],
    }


def read_stock_prices_page(
    root: Path,
    page: int = 1,
    page_size: int = 100,
    company: str = "",
    month: str = "",
) -> Dict[str, Any]:
    rows = _read_optional(root / "data" / "marts" / "market" / "stock_price_monthly.csv")
    filtered = []
    for row in rows:
        if company and str(row.get("operating_company_id") or "") != company:
            continue
        if month and str(row.get("month") or "") != month:
            continue
        filtered.append(row)
    filtered = sorted(filtered, key=lambda item: (str(item.get("date", "")), str(item.get("operating_company_id", ""))), reverse=True)
    total = len(filtered)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    visible = filtered[start:end]
    return {
        "rows": visible,
        "columns": PRICE_COLUMNS,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if page_size else 1,
    }


def read_stock_chart_data(
    root: Path,
    companies: Optional[Sequence[str]] = None,
    months: Optional[Sequence[str]] = None,
    fields: Optional[Sequence[str]] = None,
    max_rows: int = 5000,
) -> Dict[str, Any]:
    rows = _read_optional(root / "data" / "marts" / "market" / "stock_price_monthly.csv")
    company_filter = {str(item) for item in companies or [] if str(item)}
    month_filter = {str(item) for item in months or [] if str(item)}
    field_definitions = {str(row["id"]): row for row in STOCK_CHART_FIELDS}
    selected_fields = [field for field in (fields or []) if field in field_definitions]
    if not selected_fields:
        selected_fields = [field for field in DEFAULT_STOCK_CHART_FIELDS if field in field_definitions]

    filtered = []
    for row in rows:
        company_id = str(row.get("operating_company_id") or "")
        month = str(row.get("month") or "")
        if company_filter and company_id not in company_filter:
            continue
        if month_filter and month not in month_filter:
            continue
        chart_row = {
            "company_year_id": f"{company_id}_{month}",
            "fiscal_year": month,
            "fiscal_year_end": row.get("date", ""),
            "month": month,
            "date": row.get("date", ""),
            "operating_company_id": company_id,
            "operating_company_name": row.get("operating_company_name", ""),
            "stock_code": row.get("stock_code", ""),
            "ticker": row.get("ticker", ""),
        }
        for field_id in selected_fields:
            value = _safe_float(row.get(field_id))
            chart_row[field_id] = value
            chart_row[f"{field_id}__raw"] = "" if value is None else str(row.get(field_id, ""))
        filtered.append(chart_row)

    filtered = sorted(filtered, key=lambda row: (str(row.get("fiscal_year", "")), str(row.get("operating_company_id", ""))))
    omitted = max(0, len(filtered) - max_rows)
    filtered = filtered[:max_rows]
    return {
        "rows": filtered,
        "columns": _columns(filtered),
        "total": len(filtered) + omitted,
        "omitted_rows": omitted,
        "fields": [_stock_field_option(field_definitions[field_id]) for field_id in selected_fields],
        "companies": _chart_companies(filtered),
        "years": sorted({str(row.get("fiscal_year", "")) for row in filtered if row.get("fiscal_year")}),
    }


def refresh_stock_prices_if_due(
    root: Path,
    force: bool = False,
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
    fetcher: Optional[FetchChart] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    status = stock_monthly_status(root, today=today)
    if not force and not status.get("due"):
        if log:
            log(f"[stock-monthly] skip: {status.get('message', '')}")
        summary = {
            "status": "skipped",
            "reason": "not_due",
            "plan": status,
            "run_at_utc": _now_utc(),
        }
        _write_stock_summary(root, summary)
        return summary
    return refresh_stock_prices(root, force=True, dry_run=dry_run, log=log, fetcher=fetcher, today=today)


def refresh_stock_prices(
    root: Path,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    force: bool = False,
    dry_run: bool = False,
    log: Optional[Callable[[str], None]] = None,
    fetcher: Optional[FetchChart] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    today = today or date.today()
    cfg = _market_config(root)
    if not bool(cfg.get("enabled", True)) and not force:
        summary = {"status": "blocked", "reason": "stock_price_monthly_disabled", "run_at_utc": _now_utc()}
        _write_stock_summary(root, summary)
        return summary

    securities = read_security_master(root)
    enabled_securities = [row for row in securities if _bool(row.get("enabled"), True)]
    out_dir = root / "data" / "marts" / "market"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_table(out_dir / "security_master.csv", securities)

    existing_rows = _read_optional(out_dir / "stock_price_monthly.csv")
    target_end = end_date or _target_month_end(today, include_current_month=bool(cfg.get("include_current_month", False)))
    target_start = start_date or _default_start_date(existing_rows, cfg)
    if target_start > target_end:
        target_start = date(target_end.year, target_end.month, 1)

    summary: Dict[str, Any] = {
        "status": "dry_run" if dry_run else "running",
        "provider": cfg.get("provider", "yahoo_finance_chart"),
        "run_at_utc": _now_utc(),
        "start_date": target_start.isoformat(),
        "end_date": target_end.isoformat(),
        "target_month": target_end.strftime("%Y-%m"),
        "total_securities": len(securities),
        "enabled_securities": len(enabled_securities),
        "fetched_securities": 0,
        "skipped_securities": len(securities) - len(enabled_securities),
        "failed_securities": 0,
        "existing_rows": len(existing_rows),
        "new_rows": 0,
        "merged_rows": len(existing_rows),
        "errors": [],
        "skipped_listing_period": [],
        "dry_run": dry_run,
    }

    if log:
        log(
            "[stock-monthly] plan "
            f"provider={summary['provider']} securities={len(enabled_securities)} "
            f"range={target_start.isoformat()}..{target_end.isoformat()} dry_run={dry_run}"
        )

    if dry_run:
        _write_stock_summary(root, summary)
        return summary

    fetch = fetcher or fetch_yahoo_chart_monthly
    fetched_rows: List[Dict[str, Any]] = []
    raw_dir = root / "data" / "raw" / "market" / "yahoo_chart"
    raw_dir.mkdir(parents=True, exist_ok=True)
    sleep_seconds = float(cfg.get("polite_sleep_seconds") or 0)
    fetched_at = _now_utc()

    for security in enabled_securities:
        ticker = str(security.get("ticker") or "").strip()
        if not ticker:
            summary["skipped_securities"] = int(summary["skipped_securities"]) + 1
            continue
        security_start, security_end = _security_fetch_range(security, target_start, target_end)
        if security_end < security_start:
            summary["skipped_securities"] = int(summary["skipped_securities"]) + 1
            skipped = {
                "ticker": ticker,
                "listed_company_id": security.get("listed_company_id", ""),
                "operating_company_name": security.get("operating_company_name", ""),
                "listed_from": security.get("listed_from", ""),
                "listed_to": security.get("listed_to", ""),
                "requested_start": target_start.isoformat(),
                "requested_end": target_end.isoformat(),
            }
            summary["skipped_listing_period"].append(skipped)
            if log:
                log(
                    "[stock-monthly] skip "
                    f"{ticker} {security.get('operating_company_name', '')} "
                    f"outside listing period {security.get('listed_from', '') or '-'}..{security.get('listed_to', '') or '-'}"
                )
            continue
        try:
            if log:
                log(
                    "[stock-monthly] fetch "
                    f"{ticker} {security.get('operating_company_name', '')} "
                    f"{security_start.isoformat()}..{security_end.isoformat()}"
                )
            payload = fetch(ticker, security_start, security_end, cfg)
            (raw_dir / f"{ticker}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            rows = parse_yahoo_chart_monthly(payload, security, security_start, security_end, fetched_at)
            fetched_rows.extend(rows)
            summary["fetched_securities"] = int(summary["fetched_securities"]) + 1
            summary["new_rows"] = int(summary["new_rows"]) + len(rows)
            if log:
                log(f"[stock-monthly] fetched {ticker} rows={len(rows)}")
        except Exception as exc:
            summary["failed_securities"] = int(summary["failed_securities"]) + 1
            error = {"ticker": ticker, "listed_company_id": security.get("listed_company_id", ""), "error": str(exc)}
            summary["errors"].append(error)
            if log:
                log(f"[stock-monthly] ERROR {ticker}: {exc}")
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    merged_rows = merge_records_by_key(existing_rows, fetched_rows, ["listed_company_id", "date"])
    merged_rows = _recalculate_monthly_returns(merged_rows)
    merged_rows = _normalize_price_columns(merged_rows)
    write_table(out_dir / "stock_price_monthly.csv", merged_rows)
    write_table(out_dir / "stock_price_monthly.parquet", merged_rows)

    summary["merged_rows"] = len(merged_rows)
    summary["latest_month"] = _latest_month(merged_rows)
    summary["last_successful_month"] = summary["latest_month"] if fetched_rows else _latest_month(existing_rows)
    if int(summary["failed_securities"]) and int(summary["fetched_securities"]) == 0:
        summary["status"] = "failed"
    elif int(summary["failed_securities"]):
        summary["status"] = "partial_success"
    else:
        summary["status"] = "succeeded"
    _write_stock_summary(root, summary)
    if log:
        log(
            "[stock-monthly] done "
            f"status={summary['status']} new_rows={summary['new_rows']} merged_rows={summary['merged_rows']} "
            f"errors={len(summary['errors'])}"
        )
    return summary


def read_security_master(root: Path) -> List[Dict[str, Any]]:
    configured = root / "config" / "security_master.csv"
    if configured.exists():
        return _normalize_security_rows(read_table(configured))
    return _derive_security_master(root)


def fetch_yahoo_chart_monthly(ticker: str, start: date, end: date, cfg: Dict[str, Any]) -> Dict[str, Any]:
    period1 = _unix_utc(start)
    period2 = _unix_utc(end + timedelta(days=1))
    base_url = str(cfg.get("yahoo_chart_url") or "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}")
    url = base_url.format(ticker=ticker)
    params = {
        "period1": str(period1),
        "period2": str(period2),
        "interval": "1mo",
        "events": "div,splits",
    }
    headers = {"User-Agent": str(cfg.get("user_agent") or "Mozilla/5.0 BuildBase/stock-monthly")}
    timeout = int(cfg.get("request_timeout_seconds") or 30)
    retry_count = int(cfg.get("retry_count") or 2)
    backoff = float(cfg.get("retry_backoff_seconds") or 2)
    last_error: Optional[Exception] = None
    for attempt in range(retry_count + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            error = (payload.get("chart") or {}).get("error")
            if error:
                raise RuntimeError(json.dumps(error, ensure_ascii=False))
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < retry_count:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Yahoo Finance chart request failed for {ticker}: {last_error}")


def parse_yahoo_chart_monthly(
    payload: Dict[str, Any],
    security: Dict[str, Any],
    start: date,
    end: date,
    fetched_at_utc: str,
) -> List[Dict[str, Any]]:
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        return []
    result = results[0]
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quotes = indicators.get("quote") or [{}]
    quote = quotes[0] if quotes else {}
    adjclose_items = indicators.get("adjclose") or [{}]
    adjclose = adjclose_items[0].get("adjclose") if adjclose_items else []
    dividends = _monthly_dividends(result.get("events", {}).get("dividends") or {})
    splits = _monthly_splits(result.get("events", {}).get("splits") or {})
    rows: List[Dict[str, Any]] = []

    for index, ts in enumerate(timestamps):
        period_start = _jst_date(int(ts))
        month_end = _month_end(period_start.year, period_start.month)
        if month_end < start or month_end > end:
            continue
        close = _array_value(quote.get("close"), index)
        adjusted_close = _array_value(adjclose, index)
        if is_blankish(close) and is_blankish(adjusted_close):
            continue
        ticker = str(security.get("ticker") or meta.get("symbol") or "")
        rows.append(
            {
                "listed_company_id": security.get("listed_company_id", ""),
                "operating_company_id": security.get("operating_company_id", ""),
                "operating_company_name": security.get("operating_company_name", ""),
                "stock_code": security.get("stock_code", ""),
                "ticker": ticker,
                "exchange": security.get("exchange", meta.get("exchangeName", "")),
                "date": month_end.isoformat(),
                "month": month_end.strftime("%Y-%m"),
                "fiscal_year": _fiscal_year_for_month(month_end, int(security.get("fiscal_year_end_month") or 3)),
                "open": _clean_number(_array_value(quote.get("open"), index)),
                "high": _clean_number(_array_value(quote.get("high"), index)),
                "low": _clean_number(_array_value(quote.get("low"), index)),
                "close": _clean_number(close),
                "adjusted_close": _clean_number(adjusted_close),
                "volume": _clean_int(_array_value(quote.get("volume"), index)),
                "monthly_return": "",
                "monthly_return_pct": "",
                "dividend": _clean_number(dividends.get(month_end.strftime("%Y-%m"), "")),
                "split_factor": _clean_number(splits.get(month_end.strftime("%Y-%m"), "")),
                "shares_outstanding": "",
                "market_cap": "",
                "currency": meta.get("currency", ""),
                "source": "yahoo_finance_chart",
                "source_symbol": meta.get("symbol", ticker),
                "fetched_at_utc": fetched_at_utc,
            }
        )
    return rows


def _derive_security_master(root: Path) -> List[Dict[str, Any]]:
    companies = _read_optional(root / "config" / "company_master.csv")
    rows: List[Dict[str, Any]] = []
    for company in companies:
        code = str(company.get("securities_code") or "").strip()
        if not code:
            continue
        company_id = str(company.get("operating_company_id") or "").strip()
        suffix = ".T"
        rows.append(
            {
                "listed_company_id": company_id,
                "operating_company_id": company_id,
                "operating_company_name": company.get("operating_company_name", ""),
                "stock_code": code,
                "ticker": f"{code}{suffix}",
                "exchange": "JPX",
                "currency": "JPY",
                "fiscal_year_end_month": company.get("fiscal_year_end_month", "3"),
                "listed_from": _year_start(company.get("valid_from_year")),
                "listed_to": "",
                "successor_listed_company_id": "",
                "enabled": "true",
                "source": "company_master",
                "note": company.get("notes", ""),
            }
        )
    return _normalize_security_rows(rows)


def _normalize_security_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        stock_code = str(row.get("stock_code") or row.get("securities_code") or "").strip()
        ticker = str(row.get("ticker") or "").strip()
        if not ticker and stock_code:
            ticker = f"{stock_code}.T"
        normalized.append(
            {
                "listed_company_id": str(row.get("listed_company_id") or row.get("operating_company_id") or stock_code).strip(),
                "operating_company_id": str(row.get("operating_company_id") or row.get("listed_company_id") or "").strip(),
                "operating_company_name": row.get("operating_company_name", ""),
                "stock_code": stock_code,
                "ticker": ticker,
                "exchange": row.get("exchange", "JPX"),
                "currency": row.get("currency", "JPY"),
                "fiscal_year_end_month": row.get("fiscal_year_end_month", "3"),
                "listed_from": row.get("listed_from", ""),
                "listed_to": row.get("listed_to", ""),
                "successor_listed_company_id": row.get("successor_listed_company_id", ""),
                "enabled": str(row.get("enabled", "true") or "true").lower(),
                "source": row.get("source", ""),
                "note": row.get("note", row.get("notes", "")),
            }
        )
    return sorted(normalized, key=lambda item: str(item.get("listed_company_id", "")))


def _recalculate_monthly_returns(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_company: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_company.setdefault(str(row.get("listed_company_id") or ""), []).append(dict(row))
    output: List[Dict[str, Any]] = []
    for company_id in sorted(by_company):
        previous: Optional[float] = None
        for row in sorted(by_company[company_id], key=lambda item: str(item.get("date", ""))):
            current = _safe_float(row.get("adjusted_close"))
            if previous and current is not None and previous != 0:
                monthly_return = current / previous - 1
                row["monthly_return"] = round(monthly_return, 8)
                row["monthly_return_pct"] = round(monthly_return * 100, 6)
            else:
                row["monthly_return"] = ""
                row["monthly_return_pct"] = ""
            if current is not None:
                previous = current
            output.append(row)
    return output


def _normalize_price_columns(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (str(item.get("listed_company_id", "")), str(item.get("date", "")))):
        copied = {}
        for column in PRICE_COLUMNS:
            value = row.get(column, "")
            copied[column] = None if column in NUMERIC_PRICE_COLUMNS and is_blankish(value) else value
        normalized.append(copied)
    return normalized


def _market_config(root: Path) -> Dict[str, Any]:
    cfg = load_automation_config(root)
    return cfg.get("stock_price_monthly", {}) or {}


def _default_start_date(existing_rows: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> date:
    if existing_rows:
        latest = max((str(row.get("date") or "") for row in existing_rows), default="")
        if latest:
            parsed = date.fromisoformat(latest)
            return date(parsed.year, parsed.month, 1)
    return date.fromisoformat(str(cfg.get("initial_start_date") or "2015-01-01"))


def _security_fetch_range(security: Dict[str, Any], start: date, end: date) -> Tuple[date, date]:
    listed_from = _date_or_none(security.get("listed_from"))
    listed_to = _date_or_none(security.get("listed_to"))
    security_start = max(start, listed_from) if listed_from else start
    security_end = min(end, _month_end(listed_to.year, listed_to.month)) if listed_to else end
    return security_start, security_end


def _target_month_end(today: date, include_current_month: bool) -> date:
    if include_current_month:
        return _month_end(today.year, today.month)
    first_this_month = date(today.year, today.month, 1)
    previous = first_this_month - timedelta(days=1)
    return _month_end(previous.year, previous.month)


def _write_stock_summary(root: Path, summary: Dict[str, Any]) -> None:
    out_dir = root / "data" / "automation"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "stock_monthly_last.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    with (out_dir / "stock_monthly_runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")


def _stock_status_message(
    due: bool,
    latest_month: str,
    target_month: str,
    last: Dict[str, Any],
    active_error_count: Optional[int] = None,
    ignored_listing_error_count: int = 0,
) -> str:
    error_count = len(last.get("errors", []) or []) if active_error_count is None else active_error_count
    if due:
        return f"月次株価が未更新です。対象月: {target_month}、最新データ: {latest_month or 'なし'}。"
    if last.get("status") == "partial_success" and error_count:
        return f"月次株価は{latest_month or '直近月'}まで更新済みですが、前回取得エラーが{error_count}件あります。"
    if last.get("status") == "partial_success" and ignored_listing_error_count:
        return f"月次株価は{latest_month or '直近月'}まで更新済みです。上場期間外になった履歴銘柄の前回エラー{ignored_listing_error_count}件は現在の自動更新対象外です。"
    if last.get("status") == "failed":
        return f"前回取得は失敗しました。最新データ: {latest_month or 'なし'}。"
    return f"月次株価は更新済みです。最新データ: {latest_month or 'なし'}。"


def _stock_field_option(row: Dict[str, Any]) -> Dict[str, str]:
    field_id = str(row.get("id") or "")
    name = str(row.get("name") or field_id)
    unit = str(row.get("unit") or "")
    return {
        "id": field_id,
        "name": name,
        "category": str(row.get("category") or ""),
        "unit": unit,
        "label": f"{name} ({field_id})",
    }


def _company_label(row: Dict[str, Any]) -> str:
    name = str(row.get("operating_company_name") or "")
    company_id = str(row.get("operating_company_id") or row.get("listed_company_id") or "")
    return f"{name}（{company_id}）" if name else company_id


def _split_errors_by_listing_period(
    errors: Sequence[Dict[str, Any]],
    securities: Sequence[Dict[str, Any]],
    target_end: date,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    by_company = {str(row.get("listed_company_id") or ""): row for row in securities if row.get("listed_company_id")}
    by_ticker = {str(row.get("ticker") or ""): row for row in securities if row.get("ticker")}
    active: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []
    for error in errors:
        security = by_company.get(str(error.get("listed_company_id") or "")) or by_ticker.get(str(error.get("ticker") or ""))
        if security:
            security_start, security_end = _security_fetch_range(security, target_end, target_end)
            if security_end < security_start:
                ignored.append(error)
                continue
        active.append(error)
    return active, ignored


def _chart_companies(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    by_id: Dict[str, Dict[str, str]] = {}
    for row in rows:
        company_id = str(row.get("operating_company_id", ""))
        if not company_id or company_id in by_id:
            continue
        name = str(row.get("operating_company_name", ""))
        by_id[company_id] = {
            "id": company_id,
            "name": name,
            "label": f"{name}（{company_id}）" if name else company_id,
        }
    return list(by_id.values())


def _columns(rows: Iterable[Dict[str, Any]]) -> List[str]:
    columns: List[str] = []
    for row in rows:
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    return columns


def _monthly_dividends(events: Dict[str, Any]) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for event in events.values():
        ts = event.get("date")
        if is_blankish(ts):
            continue
        event_date = _jst_date(int(ts))
        key = event_date.strftime("%Y-%m")
        result[key] = result.get(key, 0.0) + float(event.get("amount") or 0)
    return result


def _monthly_splits(events: Dict[str, Any]) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for event in events.values():
        ts = event.get("date")
        if is_blankish(ts):
            continue
        event_date = _jst_date(int(ts))
        key = event_date.strftime("%Y-%m")
        numerator = _safe_float(event.get("numerator"))
        denominator = _safe_float(event.get("denominator"))
        if numerator is None or denominator in (None, 0):
            factor = _split_ratio(event.get("splitRatio"))
        else:
            factor = numerator / denominator
        if factor:
            result[key] = result.get(key, 1.0) * factor
    return result


def _split_ratio(value: Any) -> Optional[float]:
    if is_blankish(value):
        return None
    text = str(value)
    if ":" not in text:
        return _safe_float(text)
    left, right = text.split(":", 1)
    numerator = _safe_float(left)
    denominator = _safe_float(right)
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _array_value(values: Any, index: int) -> Any:
    if not isinstance(values, list) or index >= len(values):
        return ""
    return values[index]


def _clean_number(value: Any) -> Any:
    number = _safe_float(value)
    if number is None:
        return ""
    return round(number, 6)


def _clean_int(value: Any) -> Any:
    number = _safe_float(value)
    if number is None:
        return ""
    return int(number)


def _safe_float(value: Any) -> Optional[float]:
    if is_blankish(value):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _fiscal_year_for_month(month_end: date, fiscal_year_end_month: int) -> int:
    if fiscal_year_end_month == 12:
        return month_end.year
    return month_end.year if month_end.month > fiscal_year_end_month else month_end.year - 1


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _date_or_none(value: Any) -> Optional[date]:
    if is_blankish(value):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _latest_month(rows: Sequence[Dict[str, Any]]) -> str:
    months = [str(row.get("month") or "") for row in rows if row.get("month")]
    return max(months) if months else ""


def _month_key(value: str) -> Tuple[int, int]:
    if not value:
        return (0, 0)
    text = value[:7]
    try:
        year, month = text.split("-", 1)
        return (int(year), int(month))
    except ValueError:
        return (0, 0)


def _jst_date(timestamp: int) -> date:
    jst = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(jst).date()


def _unix_utc(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _year_start(value: Any) -> str:
    if is_blankish(value):
        return ""
    try:
        return f"{int(float(str(value))):04d}-01-01"
    except ValueError:
        return ""


def _bool(value: Any, default: bool = False) -> bool:
    if is_blankish(value):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "有効"}


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_optional(path: Path) -> List[Dict[str, Any]]:
    actual = prefer_existing_table(path)
    return read_table(actual) if actual.exists() else []
