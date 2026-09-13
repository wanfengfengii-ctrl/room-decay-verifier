"""FastAPI 入口：T20 混响复核台后端。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .schemas import EvaluateRejected, EvaluateRequest, EvaluateResponse, EvaluateSuccess
from .t20 import evaluate_t20

app = FastAPI(title="T20 混响复核台", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "up"}


@app.post("/api/evaluate", response_model=EvaluateResponse)
def evaluate(payload: EvaluateRequest) -> EvaluateResponse:
    result = evaluate_t20(
        sample_interval_ms=payload.sample_interval_ms,
        pressure=payload.pressure,
        limit_seconds=payload.limit_seconds,
    )
    if not result.accepted:
        return EvaluateRejected(reason=result.reason or "未知拒绝原因")
    return EvaluateSuccess(
        t20_seconds=result.t20_seconds,
        points_used=result.points_used,
        slope=result.slope,
        background=result.background,
        peak_index=result.peak_index,
        limit_seconds=payload.limit_seconds,
        passed=result.passed,
    )
