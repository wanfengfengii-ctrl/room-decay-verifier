"""请求 / 响应模型。"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Optional, Union

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


def is_utf8_encodable(value: str) -> bool:
    """能否按 UTF-8 编码：孤立代理（如 \\ud800）无法编码，响应序列化会失败。"""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _require_json_str(value: object) -> object:
    """只接受可原样回传的 JSON 字符串：数字、布尔、null 视为类型错误；
    孤立代理等无法按 UTF-8 编码的字符串同样拒绝（响应无法携带该标识）。"""
    if not isinstance(value, str):
        raise ValueError("room_id 必须是字符串")
    if not is_utf8_encodable(value):
        raise ValueError("room_id 包含无法编码的孤立代理字符")
    return value


StrictJsonInt = Annotated[int, BeforeValidator(_require_json_int)]
StrictJsonNumber = Annotated[float, BeforeValidator(_require_json_number)]
StrictJsonStr = Annotated[str, BeforeValidator(_require_json_str)]


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


class WindowPointModel(BaseModel):
    """轨迹取样点：峰值后的秒数与 [-25, -5] dB 窗口内的 dB 值。"""

    time_seconds: float
    db: float


class FitLineModel(BaseModel):
    """完整窗口回归线在窗口首末 t 处的两个端点（非取样近似）。"""

    t_start: float
    db_start: float
    t_end: float
    db_end: float


class DecayTrailModel(BaseModel):
    """衰减轨迹：展示用取样点、完整取点数与回归线端点。

    斜率、R²、T20 一律使用 total_points 对应的完整窗口计算，
    sampled_points 仅用于前端画图，最多 200 个且首尾必留。
    """

    total_points: int
    sampled_points: list[WindowPointModel]
    fit_line: FitLineModel


class EvaluateSuccess(BaseModel):
    status: Literal["ok"] = "ok"
    t20_seconds: float
    points_used: int
    slope: float
    background: float
    peak_index: int
    limit_seconds: float
    passed: bool
    # 拟合优度证据：R² 已钳制到 [0, 1]；fit_quality 仅提示是否复查，不参与合格判定
    r_squared: float
    fit_quality: Literal["stable", "needs_review"]
    # 衰减轨迹仅可视化判定依据；旧客户端忽略该字段即可，契约向后兼容
    decay_trail: DecayTrailModel


class EvaluateRejected(BaseModel):
    status: Literal["rejected"] = "rejected"
    reason: str


EvaluateResponse = Union[EvaluateSuccess, EvaluateRejected]


# ---------- 批量复核 ----------


class BatchEvaluateItem(EvaluateRequest):
    """批量中的单个房间：复用单次三字段的全部约束，另加 room_id。

    room_id 只要求是非空非空白字符串，不设长度上限：
    超长标识照常复核并返回该房间结论，而不是判为字段错误。

    limit_seconds 在此放宽为可缺省：请求顶层提供 common_limit_seconds 时，
    条目限值整列忽略（残留的旧值不再校验，全批以顶层值为准）；
    未提供统一限值时，条目缺省限值由批量入口补判为该行的字段错误，
    显式给出的限值仍按原口径严格校验。
    """

    room_id: StrictJsonStr = Field(min_length=1)
    limit_seconds: Optional[StrictJsonNumber] = Field(default=None, ge=0.30, le=5.00)

    @field_validator("room_id")
    @classmethod
    def _room_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("room_id 不能为空白字符串")
        return value


class BatchItemSuccess(BaseModel):
    """正常项：证据字段与 /api/evaluate 的成功响应完全一致，仅多 room_id。"""

    room_id: str
    status: Literal["ok"] = "ok"
    t20_seconds: float
    points_used: int
    slope: float
    background: float
    peak_index: int
    limit_seconds: float
    passed: bool
    r_squared: float
    fit_quality: Literal["stable", "needs_review"]
    decay_trail: DecayTrailModel


class BatchItemRejected(BaseModel):
    """衰减异常项：仍然只暴露拒绝原因，不泄露任何计算字段。"""

    room_id: str
    status: Literal["rejected"] = "rejected"
    reason: str


class BatchFieldError(BaseModel):
    """可定位到具体字段的错误说明。"""

    field: str
    message: str


class BatchItemInvalid(BaseModel):
    """字段错误项：携带房间标识（room_id 本身缺失时为 null，用 index 定位）。"""

    status: Literal["invalid"] = "invalid"
    index: int
    room_id: Optional[str] = None
    errors: list[BatchFieldError]


BatchItemResponse = Annotated[
    Union[BatchItemSuccess, BatchItemRejected, BatchItemInvalid],
    Field(discriminator="status"),
]


class BatchSummary(BaseModel):
    """整批汇总：只有正常项（ok）计入合格统计。"""

    total: int
    ok: int
    passed: int
    failed: int
    rejected: int
    invalid: int


class BatchEvaluateResponse(BaseModel):
    items: list[BatchItemResponse]
    summary: BatchSummary
