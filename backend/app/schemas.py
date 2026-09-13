"""请求 / 响应模型。"""

from __future__ import annotations

import math
from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator


class EvaluateRequest(BaseModel):
    sample_interval_ms: int = Field(ge=1, le=100)
    pressure: list[float] = Field(min_length=200, max_length=20000)
    limit_seconds: float = Field(ge=0.30, le=5.00)

    @field_validator("pressure")
    @classmethod
    def _pressure_must_be_positive(cls, values: list[float]) -> list[float]:
        for v in values:
            if not math.isfinite(v) or v <= 0.0:
                raise ValueError("pressure 必须全部为大于零的有限数")
        return values


class EvaluateSuccess(BaseModel):
    status: Literal["ok"] = "ok"
    t20_seconds: float
    points_used: int
    slope: float
    background: float
    peak_index: int
    limit_seconds: float
    passed: bool


class EvaluateRejected(BaseModel):
    status: Literal["rejected"] = "rejected"
    reason: str


EvaluateResponse = Union[EvaluateSuccess, EvaluateRejected]
