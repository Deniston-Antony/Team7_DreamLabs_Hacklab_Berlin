from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from .reports import batch_html, task_html
from .store import SAFE_ID, EvaluationStore

SUMMARY_FIELDS = (
    "batch_id",
    "total_tasks",
    "successful",
    "partial",
    "failed",
    "unsafe",
    "timed_out",
    "not_evaluable",
    "batch_score",
    "score_coverage",
    "success_rate",
    "average_score",
    "median_score",
    "highest_score",
    "lowest_score",
    "total_penalties",
    "total_disturbances",
    "tasks",
    "sample_note",
)


def create_router(store: EvaluationStore) -> APIRouter:
    router = APIRouter()

    def valid(identifier: str) -> str:
        if not SAFE_ID.match(identifier):
            raise HTTPException(404, "not found")
        return identifier

    @router.post("/api/evaluation/tasks/{task_id}/evaluate")
    def evaluate(task_id: str):
        result = store.reevaluate(valid(task_id))
        if result is None:
            raise HTTPException(404, "No evidence is stored for this task.")
        return result.model_dump()

    @router.get("/api/evaluation/tasks/{task_id}/result")
    def result(task_id: str):
        item = store.result(valid(task_id))
        if item is None:
            raise HTTPException(404, "This task has not been evaluated.")
        return item.model_dump()

    @router.get("/api/evaluation/batches")
    def batches():
        return store.batches()

    @router.post("/api/evaluation/batches")
    def new_batch():
        return {"batch_id": store.new_batch()}

    @router.get("/api/reports/tasks/{task_id}")
    def task(task_id: str, format: Literal["json", "html"] = Query(default="json")):
        report = store.report(valid(task_id))
        if report is None:
            raise HTTPException(404, "No report exists for this task.")
        return HTMLResponse(task_html(report)) if format == "html" else report

    @router.get("/api/reports/batches/{batch_id}")
    def batch(batch_id: str, format: Literal["json", "html"] = Query(default="json")):
        report = store.batch_report(valid(batch_id))
        if report is None:
            raise HTTPException(404, "No report exists for this batch.")
        return HTMLResponse(batch_html(report)) if format == "html" else report

    @router.get("/api/reports/batches/{batch_id}/summary")
    def batch_summary(batch_id: str):
        report = store.batch_report(valid(batch_id))
        if report is None:
            raise HTTPException(404, "No report exists for this batch.")
        return {name: report[name] for name in SUMMARY_FIELDS}

    return router
