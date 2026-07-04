from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from yuho_auto_extract import __version__
from yuho_auto_extract.services import ai_prompt, algorithm_audit, algorithm_audit_findings, automation, company_factbooks, corroboration_report, datasets, field_admin, golden, mapping_review, market, pipeline, reviews
from yuho_auto_extract.web_api.jobs import JobAlreadyRunning, JobManager


PROJECT_ROOT = Path(os.getenv("YUHO_PROJECT_ROOT") or Path.cwd()).resolve()
WEB_DIR = PROJECT_ROOT / "web"
DIST_DIR = WEB_DIR / "dist"

app = FastAPI(title="yuho_auto_extract local web app")
jobs = JobManager(PROJECT_ROOT)
_scheduler_started = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8765",
        "http://127.0.0.1:8765",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReviewSaveRequest(BaseModel):
    reviews: List[Dict[str, Any]]


class ReviewDeleteRequest(BaseModel):
    reviews: List[Dict[str, Any]]


class NotApplicableRequest(BaseModel):
    company_id: str
    field_id: str
    note: str = ""
    start_year: Optional[int] = None
    end_year: Optional[int] = None


class FieldDefinitionUpdateRequest(BaseModel):
    updates: Dict[str, Any]


class FieldTermsAppendRequest(BaseModel):
    synonyms: List[str] = []
    xbrl_tags: List[str] = []
    section_keywords: List[str] = []
    note: str = ""


class AnnualRefreshRequest(BaseModel):
    fiscal_year: Optional[int] = None
    force: bool = False
    dry_run: bool = False


class StockRefreshRequest(BaseModel):
    force: bool = False
    dry_run: bool = False


class RegressionCheckRequest(BaseModel):
    mode: str = "light"


class FactbookRefreshRequest(BaseModel):
    force: bool = False
    dry_run: bool = False


class PromptRequest(BaseModel):
    theme: str = ""
    companies: List[str] = []
    fiscal_years: List[str] = []
    fields: List[str] = []
    extra_instruction: str = ""


class MappingReviewDecisionRequest(BaseModel):
    reviewer: str = ""
    note: str = ""


@app.get("/api/status")
def status() -> Dict[str, Any]:
    current = datasets.project_status(PROJECT_ROOT)
    current["app_version"] = __version__
    return current


@app.get("/api/automation/status")
def automation_status(fiscal_year: Optional[int] = None) -> Dict[str, Any]:
    return automation.automation_status(PROJECT_ROOT, fiscal_year=fiscal_year)


@app.get("/api/market/stock/status")
def stock_status() -> Dict[str, Any]:
    return market.stock_monthly_status(PROJECT_ROOT)


@app.get("/api/market/stock/options")
def stock_options() -> Dict[str, Any]:
    return market.stock_monthly_options(PROJECT_ROOT)


@app.get("/api/market/stock/monthly")
def stock_monthly(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    company: str = "",
    month: str = "",
) -> Dict[str, Any]:
    return market.read_stock_prices_page(PROJECT_ROOT, page=page, page_size=page_size, company=company, month=month)


@app.get("/api/company-factbooks/status")
def company_factbook_status() -> Dict[str, Any]:
    return company_factbooks.factbook_status(PROJECT_ROOT)


@app.get("/api/company-factbooks/options")
def company_factbook_options() -> Dict[str, Any]:
    return company_factbooks.factbook_options(PROJECT_ROOT)


@app.get("/api/company-factbooks/orders")
def company_factbook_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    company: str = "",
    fiscal_year: str = "",
    category_type: str = "",
    search: str = "",
) -> Dict[str, Any]:
    return company_factbooks.read_factbook_orders_page(
        PROJECT_ROOT,
        page=page,
        page_size=page_size,
        company=company,
        fiscal_year=fiscal_year,
        category_type=category_type,
        search=search,
    )


@app.get("/api/company-factbooks/documents")
def company_factbook_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    company: str = "",
    fiscal_year: str = "",
    search: str = "",
) -> Dict[str, Any]:
    return company_factbooks.read_factbook_documents_page(
        PROJECT_ROOT,
        page=page,
        page_size=page_size,
        company=company,
        fiscal_year=fiscal_year,
        search=search,
    )


@app.post("/api/jobs/run-all")
def start_run_all() -> Dict[str, Any]:
    return _start_job("run-all", pipeline.run_all)


@app.post("/api/jobs/reextract-with-review")
def start_reextract_with_review() -> Dict[str, Any]:
    return _start_job("reextract-with-review", pipeline.reextract_with_review)


@app.post("/api/jobs/annual-refresh")
def start_annual_refresh(request: AnnualRefreshRequest = Body(default=AnnualRefreshRequest())) -> Dict[str, Any]:
    return _start_job(
        "annual-refresh",
        lambda root, log: pipeline.annual_refresh(
            root,
            log=log,
            fiscal_year=request.fiscal_year,
            force=request.force,
            dry_run=request.dry_run,
        ),
    )


@app.post("/api/jobs/stock-refresh")
def start_stock_refresh(request: StockRefreshRequest = Body(default=StockRefreshRequest())) -> Dict[str, Any]:
    return _start_job(
        "stock-refresh",
        lambda root, log: pipeline.refresh_stock_prices(
            root,
            log=log,
            force=request.force,
            dry_run=request.dry_run,
        ),
    )


@app.post("/api/jobs/factbook-refresh")
def start_factbook_refresh(request: FactbookRefreshRequest = Body(default=FactbookRefreshRequest())) -> Dict[str, Any]:
    return _start_job(
        "factbook-refresh",
        lambda root, log: pipeline.refresh_company_factbooks(
            root,
            log=log,
            force=request.force,
            dry_run=request.dry_run,
        ),
    )


@app.post("/api/jobs/xbrl-fact-store")
def start_xbrl_fact_store() -> Dict[str, Any]:
    return _start_job("xbrl-fact-store", pipeline.build_xbrl_fact_store)


@app.post("/api/jobs/report")
def start_report() -> Dict[str, Any]:
    return _start_job("report", pipeline.rebuild_report)


@app.post("/api/jobs/algorithm-audit")
def start_algorithm_audit() -> Dict[str, Any]:
    return _start_job("algorithm-audit", pipeline.build_algorithm_audit)


@app.post("/api/jobs/corroboration-report")
def start_corroboration_report() -> Dict[str, Any]:
    return _start_job("corroboration-report", pipeline.build_corroboration_report)


@app.post("/api/jobs/algorithm-audit-findings")
def start_algorithm_audit_findings() -> Dict[str, Any]:
    return _start_job("algorithm-audit-findings", pipeline.build_algorithm_audit_findings)


@app.post("/api/jobs/golden-freeze")
def start_golden_freeze() -> Dict[str, Any]:
    return _start_job("golden-freeze", pipeline.golden_freeze)


@app.post("/api/jobs/regression-check")
def start_regression_check(request: RegressionCheckRequest = Body(default=RegressionCheckRequest())) -> Dict[str, Any]:
    return _start_job(
        "regression-check",
        lambda root, log: pipeline.regression_check(root, log=log, mode=request.mode),
    )


@app.post("/api/jobs/apply-review")
def start_apply_review() -> Dict[str, Any]:
    return _start_job("apply-review", pipeline.apply_review)


@app.get("/api/jobs/current")
def current_job() -> Dict[str, Any]:
    return jobs.current()


@app.get("/api/datasets/wide")
def wide(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    company: str = "",
    fiscal_year: str = "",
    fields: str = "",
) -> Dict[str, Any]:
    field_list = [field.strip() for field in fields.split(",") if field.strip()]
    return datasets.read_wide(PROJECT_ROOT, page=page, page_size=page_size, company=company, fiscal_year=fiscal_year, fields=field_list)


@app.get("/api/datasets/audit")
def audit(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    company_year_id: str = "",
    field_id: str = "",
    search: str = "",
) -> Dict[str, Any]:
    return datasets.read_audit(PROJECT_ROOT, page=page, page_size=page_size, company_year_id=company_year_id, field_id=field_id, search=search)


@app.get("/api/datasets/fields")
def fields() -> Dict[str, Any]:
    return datasets.read_fields(PROJECT_ROOT)


@app.get("/api/field-definitions")
def field_definitions(category: str = "", search: str = "") -> Dict[str, Any]:
    return field_admin.read_field_definitions(PROJECT_ROOT, category=category, search=search)


@app.post("/api/field-definitions/{field_id}")
def update_field_definition(field_id: str, request: FieldDefinitionUpdateRequest) -> Dict[str, Any]:
    try:
        return field_admin.update_field_definition(PROJECT_ROOT, field_id, request.updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/field-definitions/{field_id}/terms")
def append_field_terms(field_id: str, request: FieldTermsAppendRequest) -> Dict[str, Any]:
    try:
        return field_admin.append_field_terms(
            PROJECT_ROOT,
            field_id,
            synonyms=request.synonyms,
            xbrl_tags=request.xbrl_tags,
            section_keywords=request.section_keywords,
            note=request.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/charts/data")
def chart_data(
    source: str = "financial",
    companies: str = "",
    fiscal_years: str = "",
    fields: str = "",
    max_rows: int = Query(5000, ge=1, le=20000),
) -> Dict[str, Any]:
    if source == "stock":
        return market.read_stock_chart_data(
            PROJECT_ROOT,
            companies=_split_csv(companies),
            months=_split_csv(fiscal_years),
            fields=_split_csv(fields),
            max_rows=max_rows,
        )
    if source == "factbook_orders":
        return company_factbooks.read_factbook_chart_data(
            PROJECT_ROOT,
            companies=_split_csv(companies),
            fiscal_years=_split_csv(fiscal_years),
            fields=_split_csv(fields),
            max_rows=max_rows,
        )
    return datasets.read_chart_data(
        PROJECT_ROOT,
        companies=_split_csv(companies),
        fiscal_years=_split_csv(fiscal_years),
        fields=_split_csv(fields),
        max_rows=max_rows,
    )


@app.get("/api/datasets/cell-detail")
def cell_detail(company_year_id: str, field_id: str) -> Dict[str, Any]:
    if not company_year_id or not field_id:
        raise HTTPException(status_code=400, detail="company_year_id and field_id are required")
    return datasets.read_cell_detail(PROJECT_ROOT, company_year_id=company_year_id, field_id=field_id)


@app.get("/api/options")
def options() -> Dict[str, Any]:
    return datasets.read_options(PROJECT_ROOT)


@app.get("/api/reviews/queue")
def review_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    company: str = "",
    fiscal_year: str = "",
    field_id: str = "",
    search: str = "",
    review_status: str = "",
    review_category: str = "",
) -> Dict[str, Any]:
    return datasets.read_review_queue(
        PROJECT_ROOT,
        page=page,
        page_size=page_size,
        company=company,
        fiscal_year=fiscal_year,
        field_id=field_id,
        search=search,
        review_status=review_status,
        review_category=review_category,
    )


@app.get("/api/reviews/resolved")
def resolved_reviews() -> Dict[str, Any]:
    return datasets.read_resolved_reviews(PROJECT_ROOT)


@app.post("/api/reviews/resolved")
def save_resolved_reviews(request: ReviewSaveRequest) -> Dict[str, Any]:
    try:
        return reviews.upsert_resolved_reviews(PROJECT_ROOT, request.reviews)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/reviews/resolved/delete")
def delete_resolved_reviews(request: ReviewDeleteRequest) -> Dict[str, Any]:
    try:
        return reviews.delete_resolved_reviews(PROJECT_ROOT, request.reviews)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/reviews/not-applicable")
def mark_not_applicable(request: NotApplicableRequest) -> Dict[str, Any]:
    try:
        return reviews.mark_company_field_not_applicable(
            PROJECT_ROOT,
            company_id=request.company_id,
            field_id=request.field_id,
            note=request.note,
            start_year=request.start_year,
            end_year=request.end_year,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/mappings/proposals")
def mapping_proposals(
    action: str = "",
    decided_by_kind: str = "",
    min_confidence: Optional[float] = Query(default=None),
    limit: int = Query(200, ge=1, le=2000),
    verdict: str = "",
) -> Dict[str, Any]:
    return mapping_review.read_mapping_proposals(
        PROJECT_ROOT,
        action=action,
        decided_by_kind=decided_by_kind,
        min_confidence=min_confidence,
        limit=limit,
        verdict=verdict,
    )


@app.post("/api/mappings/bulk-reject-conflicts")
def bulk_reject_conflicts(request: MappingReviewDecisionRequest = Body(default=MappingReviewDecisionRequest())) -> Dict[str, Any]:
    return mapping_review.bulk_reject_conflicting_proposals(PROJECT_ROOT, reviewer=request.reviewer or "web")


@app.get("/api/mappings/conflict-summary")
def mapping_conflict_summary() -> Dict[str, Any]:
    return mapping_review.read_conflict_summary(PROJECT_ROOT)


@app.post("/api/mappings/{mapping_id}/confirm")
def confirm_mapping(mapping_id: str, request: MappingReviewDecisionRequest = Body(default=MappingReviewDecisionRequest())) -> Dict[str, Any]:
    result = mapping_review.confirm_mapping_proposal(PROJECT_ROOT, mapping_id, reviewer=request.reviewer)
    if not result.get("updated"):
        raise HTTPException(status_code=409, detail=f"mapping {mapping_id} is not in 'proposed' status or not found")
    return result


@app.post("/api/mappings/{mapping_id}/reject")
def reject_mapping(mapping_id: str, request: MappingReviewDecisionRequest = Body(default=MappingReviewDecisionRequest())) -> Dict[str, Any]:
    result = mapping_review.reject_mapping_proposal(PROJECT_ROOT, mapping_id, reviewer=request.reviewer, note=request.note)
    if not result.get("updated"):
        raise HTTPException(status_code=409, detail=f"mapping {mapping_id} is not in 'proposed' status or not found")
    return result


@app.get("/api/markdown/{name}")
def markdown(name: str) -> Dict[str, str]:
    return datasets.read_markdown(PROJECT_ROOT, name)


@app.post("/api/ai/prompt")
def build_prompt(request: PromptRequest) -> Dict[str, Any]:
    return ai_prompt.build_prompt(PROJECT_ROOT, request.dict())


@app.post("/api/algorithm-audit/build")
def build_algorithm_audit_bundle() -> Dict[str, Any]:
    return algorithm_audit.build_algorithm_audit_bundle(PROJECT_ROOT)


@app.get("/api/algorithm-audit/findings")
def get_algorithm_audit_findings() -> Dict[str, Any]:
    return algorithm_audit_findings.read_algorithm_audit_findings(PROJECT_ROOT)


@app.get("/api/corroboration/summary")
def corroboration_summary() -> Dict[str, Any]:
    return corroboration_report.read_summary(PROJECT_ROOT)


@app.get("/api/regression/summary")
def regression_summary() -> Dict[str, Any]:
    return golden.read_regression_summary(PROJECT_ROOT)


@app.get("/api/golden/summary")
def golden_summary() -> Dict[str, Any]:
    return golden.read_golden_summary(PROJECT_ROOT)


def _start_job(name: str, target: Any) -> Dict[str, Any]:
    try:
        return jobs.start(name, target)
    except JobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@app.on_event("startup")
def start_background_schedulers() -> None:
    global _scheduler_started
    if _scheduler_started or os.getenv("YUHO_DISABLE_MARKET_SCHEDULER") == "1":
        return
    cfg = automation.load_automation_config(PROJECT_ROOT).get("stock_price_monthly", {}) or {}
    if not bool(cfg.get("enabled", True)):
        return
    _scheduler_started = True
    thread = threading.Thread(target=_stock_scheduler_loop, daemon=True)
    thread.start()


def _stock_scheduler_loop() -> None:
    while True:
        cfg = automation.load_automation_config(PROJECT_ROOT).get("stock_price_monthly", {}) or {}
        interval = int(cfg.get("scheduler_check_interval_seconds") or 21600)
        try:
            state = jobs.current()
            status = market.stock_monthly_status(PROJECT_ROOT)
            if state.get("status") != "running" and status.get("due"):
                jobs.start("stock-monthly-auto", lambda root, log: pipeline.refresh_stock_prices(root, log=log))
        except JobAlreadyRunning:
            pass
        except Exception:
            pass
        time.sleep(max(300, interval))


if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        target = DIST_DIR / full_path
        if full_path and target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": "no-store"})
