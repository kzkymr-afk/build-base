from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from yuho_auto_extract import __version__
from yuho_auto_extract.services import ai_prompt, algorithm_audit, automation, datasets, pipeline, reviews, rule_candidates
from yuho_auto_extract.web_api.jobs import JobAlreadyRunning, JobManager


PROJECT_ROOT = Path(os.getenv("YUHO_PROJECT_ROOT") or Path.cwd()).resolve()
WEB_DIR = PROJECT_ROOT / "web"
DIST_DIR = WEB_DIR / "dist"

app = FastAPI(title="yuho_auto_extract local web app")
jobs = JobManager(PROJECT_ROOT)

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


class RuleCandidateApplyRequest(BaseModel):
    field_ids: List[str] = []


class AnnualRefreshRequest(BaseModel):
    fiscal_year: Optional[int] = None
    force: bool = False
    dry_run: bool = False


class PromptRequest(BaseModel):
    theme: str = ""
    companies: List[str] = []
    fiscal_years: List[str] = []
    fields: List[str] = []
    extra_instruction: str = ""


@app.get("/api/status")
def status() -> Dict[str, Any]:
    current = datasets.project_status(PROJECT_ROOT)
    current["app_version"] = __version__
    return current


@app.get("/api/automation/status")
def automation_status(fiscal_year: Optional[int] = None) -> Dict[str, Any]:
    return automation.automation_status(PROJECT_ROOT, fiscal_year=fiscal_year)


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


@app.post("/api/jobs/report")
def start_report() -> Dict[str, Any]:
    return _start_job("report", pipeline.rebuild_report)


@app.post("/api/jobs/algorithm-audit")
def start_algorithm_audit() -> Dict[str, Any]:
    return _start_job("algorithm-audit", pipeline.build_algorithm_audit)


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


@app.get("/api/charts/data")
def chart_data(
    companies: str = "",
    fiscal_years: str = "",
    fields: str = "",
    max_rows: int = Query(5000, ge=1, le=20000),
) -> Dict[str, Any]:
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


@app.get("/api/reviews/rule-candidates")
def review_rule_candidates(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    candidate_status: str = "active",
) -> Dict[str, Any]:
    result = datasets.paginate(rule_candidates.read_rule_candidates(PROJECT_ROOT, candidate_status=candidate_status), page, page_size)
    result["status_counts"] = rule_candidates.rule_candidate_status_counts(PROJECT_ROOT)
    return result


@app.post("/api/reviews/rule-candidates/generate")
def generate_review_rule_candidates() -> Dict[str, Any]:
    return rule_candidates.generate_rule_candidates(PROJECT_ROOT)


@app.post("/api/reviews/rule-candidates/apply")
def apply_review_rule_candidates(request: RuleCandidateApplyRequest) -> Dict[str, Any]:
    try:
        return rule_candidates.apply_rule_candidates(PROJECT_ROOT, request.field_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/markdown/{name}")
def markdown(name: str) -> Dict[str, str]:
    return datasets.read_markdown(PROJECT_ROOT, name)


@app.post("/api/ai/prompt")
def build_prompt(request: PromptRequest) -> Dict[str, Any]:
    return ai_prompt.build_prompt(PROJECT_ROOT, request.dict())


@app.post("/api/algorithm-audit/build")
def build_algorithm_audit_bundle() -> Dict[str, Any]:
    return algorithm_audit.build_algorithm_audit_bundle(PROJECT_ROOT)


def _start_job(name: str, target: Any) -> Dict[str, Any]:
    try:
        return jobs.start(name, target)
    except JobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        target = DIST_DIR / full_path
        if full_path and target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST_DIR / "index.html")
