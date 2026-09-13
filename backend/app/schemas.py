"""请求 / 响应模型。"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, BeforeValidator, Field, field_validator


def _require_json_int(value: object) -> object:
    """只接受 JSON 整数：布尔与字符串一律视为类型错误。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("sample_interval_ms 必须是整数，不接受布尔或字符串")
    return value


def _require_json_number(value: object) -> object:
    """只接受 JSON 数字：布尔与字符串一律视为类型错误。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("必须是数字，不接受布尔或字符串")
    return value


StrictJsonInt = Annotated[int, BeforeValidator(_require_json_int)]
StrictJsonNumber = Annotated[float, BeforeValidator(_require_json_number)]


class EvaluateRequest(BaseModel):
    sample_interval_ms: StrictJsonInt = Field(ge=1, le=100)
    pressure: list[StrictJsonNumber] = Field(min_length=200, max_length=20000)
    limit_seconds: StrictJsonNumber = Field(ge=0.30, le=5.00)

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
