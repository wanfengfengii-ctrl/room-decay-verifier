"""FastAPI 入口：T20 混响复核台后端。"""

from __future__ import annotations

import json
import math
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .schemas import (
    BatchEvaluateItem,
    BatchEvaluateResponse,
    BatchFieldError,
    BatchItemInvalid,
    BatchItemRejected,
    BatchItemSuccess,
    BatchSummary,
    EvaluateRejected,
    EvaluateRequest,
    EvaluateResponse,
    EvaluateSuccess,
)
from .t20 import evaluate_t20

app = FastAPI(title="T20 混响复核台", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BATCH_MIN_ITEMS = 1
BATCH_MAX_ITEMS = 20

# 统一限值：与条目级 limit_seconds 同一验收口径
COMMON_LIMIT_MIN = 0.30
COMMON_LIMIT_MAX = 5.00
COMMON_LIMIT_ERROR_MESSAGE = "统一限值必须是0.30至5.00的JSON数字"


class BatchRequestError(Exception):
    """整批无法受理的请求级错误（无法解析 / 结构不符 / room_id 重复）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@app.exception_handler(BatchRequestError)
def _batch_request_error_handler(_request: Request, exc: BatchRequestError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": exc.message})


@app.get("/api/health")
def health() -> dict:
    return {"status": "up"}


def _run_evaluation(
    sample_interval_ms: int,
    pressure: list[float],
    limit_seconds: float,
) -> EvaluateResponse:
    """单次复核的唯一计算出口：批量接口也复用这里，不改写任何公式。

    limit_seconds 为该行实际采用的限值（批量场景下可能来自顶层统一限值），
    既参与合格判定，也原样回显在证据字段中。
    """
    result = evaluate_t20(
        sample_interval_ms=sample_interval_ms,
        pressure=pressure,
        limit_seconds=limit_seconds,
    )
    if not result.accepted:
        return EvaluateRejected(reason=result.reason or "未知拒绝原因")
    return EvaluateSuccess(
        t20_seconds=result.t20_seconds,
        points_used=result.points_used,
        slope=result.slope,
        background=result.background,
        peak_index=result.peak_index,
        limit_seconds=limit_seconds,
        passed=result.passed,
    )


@app.post("/api/evaluate", response_model=EvaluateResponse)
def evaluate(payload: EvaluateRequest) -> EvaluateResponse:
    return _run_evaluation(
        sample_interval_ms=payload.sample_interval_ms,
        pressure=payload.pressure,
        limit_seconds=payload.limit_seconds,
    )


# ---------- 批量复核 ----------


_ZH_TYPE_MESSAGES = {
    "missing": "字段缺失",
    "model_type": "必须是 JSON 对象",
    "dict_type": "必须是 JSON 对象",
    "list_type": "必须是数组",
    "int_type": "必须是整数",
    "float_type": "必须是数字",
    "number_type": "必须是数字",
    "string_type": "必须是字符串",
    "bool_type": "必须是布尔值",
    "greater_than_equal": "小于允许的最小值",
    "greater_than": "小于允许的最小值",
    "less_than_equal": "超过允许的最大值",
    "less_than": "超过允许的最大值",
    "too_short": "长度不足",
    "too_long": "长度超限",
    "string_too_short": "字符串长度不足",
    "string_too_long": "字符串长度超限",
    "finite_number": "必须是有限数",
}


def _zh_error_message(error: dict) -> str:
    """把 pydantic 错误转成可定位的中文说明，自定义校验器原文优先保留。"""
    err_type = error.get("type", "")
    if err_type == "value_error":
        # BeforeValidator / field_validator 抛出的 ValueError 已带中文信息
        message = error.get("msg", "")
        return message.removeprefix("Value error, ").strip() or "字段值不合法"
    base = _ZH_TYPE_MESSAGES.get(err_type, error.get("msg", "字段值不合法"))
    ctx = error.get("ctx") or {}
    extras = []
    # 数值边界的上下限（ge/le）与长度边界（min_length/max_length）
    for key, label in (
        ("ge", "最小允许"),
        ("gt", "最小允许"),
        ("le", "最大允许"),
        ("lt", "最大允许"),
        ("min_length", "最小长度"),
        ("max_length", "最大长度"),
    ):
        if key in ctx:
            extras.append(f"{label}：{ctx[key]}")
    if extras:
        return f"{base}（{'，'.join(extras)}）"
    return base


def _item_field_errors(exc: ValidationError) -> list[BatchFieldError]:
    """按字段聚合 pydantic 错误。

    列表元素逐个报错时（如 pressure 中有上百个非法元素）只保留一条，
    消息中附带首个出错下标，既不淹没其他字段，又可定位。
    """
    errors: list[BatchFieldError] = []
    seen: dict[tuple[str, str], BatchFieldError] = {}
    for error in exc.errors():
        loc_parts = [part for part in error.get("loc", ()) if part != ""]
        field = str(loc_parts[0]) if loc_parts else "(item)"
        message = _zh_error_message(error)

        # pressure.0 / pressure.3 这类列表内错误折叠到字段本身
        subscripts = [f"[{p}]" for p in loc_parts[1:] if isinstance(p, int)]
        if subscripts:
            located = f"{message}（首个出错位置：{field}{''.join(subscripts[:1])}）"
        else:
            located = message

        key = (field, message)
        if key in seen:
            continue
        entry = BatchFieldError(field=field, message=located)
        seen[key] = entry
        errors.append(entry)
    return errors


def _parse_common_limit(body: dict) -> Optional[float]:
    """解析顶层可选统一限值 common_limit_seconds。

    缺省（或显式 null）视为未启用，返回 None；一旦提供，必须是
    0.30 至 5.00 的有限 JSON 数字，否则整批 400，不产生任何逐项结论。
    """
    if "common_limit_seconds" not in body or body["common_limit_seconds"] is None:
        return None
    value = body["common_limit_seconds"]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not (COMMON_LIMIT_MIN <= value <= COMMON_LIMIT_MAX)
    ):
        raise BatchRequestError(COMMON_LIMIT_ERROR_MESSAGE)
    return float(value)


@app.post("/api/evaluate-batch", response_model=BatchEvaluateResponse)
async def evaluate_batch(request: Request) -> BatchEvaluateResponse:
    # 手动解析：整批无法解析属于请求级错误，不能下沉为逐项错误。
    try:
        body = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BatchRequestError("请求体不是合法 JSON，整批无法解析")

    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise BatchRequestError('请求体必须是包含 "items" 数组的 JSON 对象')

    # 统一限值是请求级字段：非法即整批拒绝，先于任何逐项校验。
    common_limit = _parse_common_limit(body)

    raw_items = body["items"]
    if not (BATCH_MIN_ITEMS <= len(raw_items) <= BATCH_MAX_ITEMS):
        raise BatchRequestError(
            f"每批仅支持 {BATCH_MIN_ITEMS} 至 {BATCH_MAX_ITEMS} 个房间，"
            f"当前为 {len(raw_items)} 个"
        )

    # room_id 重复在整批层面拒绝；非字符串 / 空白等不合法标识不参与重复判定，
    # 留给逐项校验（两个空 ID 是各自格式错误，而非真实房间重名）。
    seen_room_ids: set[str] = set()
    for raw in raw_items:
        if isinstance(raw, dict) and isinstance(raw.get("room_id"), str):
            room_id = raw["room_id"]
            if not room_id.strip():
                continue
            if room_id in seen_room_ids:
                raise BatchRequestError(f'room_id 重复："{room_id}"，整批已拒绝')
            seen_room_ids.add(room_id)

    items = []
    summary = BatchSummary(
        total=len(raw_items), ok=0, passed=0, failed=0, rejected=0, invalid=0
    )

    # 逐项校验并调用现有 T20 计算；items 严格保持输入顺序。
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            items.append(
                BatchItemInvalid(
                    index=index,
                    errors=[
                        BatchFieldError(
                            field="(item)", message="该数组元素必须是包含房间字段的 JSON 对象"
                        )
                    ],
                )
            )
            summary.invalid += 1
            continue

        # 未启用统一限值时，条目缺少 limit_seconds 仍是该行的字段错误；
        # 显式 null 与缺省同等处理。
        limit_missing = common_limit is None and raw.get("limit_seconds") is None

        try:
            item = BatchEvaluateItem.model_validate(raw)
        except ValidationError as exc:
            room_id = raw.get("room_id")
            errors = _item_field_errors(exc)
            if limit_missing:
                errors.append(BatchFieldError(field="limit_seconds", message="字段缺失"))
            items.append(
                BatchItemInvalid(
                    index=index,
                    room_id=room_id if isinstance(room_id, str) else None,
                    errors=errors,
                )
            )
            summary.invalid += 1
            continue

        if limit_missing:
            items.append(
                BatchItemInvalid(
                    index=index,
                    room_id=item.room_id,
                    errors=[BatchFieldError(field="limit_seconds", message="字段缺失")],
                )
            )
            summary.invalid += 1
            continue

        # 条目与顶层同时给出限值时，以顶层统一限值为准
        effective_limit = common_limit if common_limit is not None else item.limit_seconds
        outcome = _run_evaluation(
            sample_interval_ms=item.sample_interval_ms,
            pressure=item.pressure,
            limit_seconds=effective_limit,
        )
        if isinstance(outcome, EvaluateSuccess):
            items.append(BatchItemSuccess(room_id=item.room_id, **outcome.model_dump()))
            summary.ok += 1
            if outcome.passed:
                summary.passed += 1
            else:
                summary.failed += 1
        else:
            items.append(BatchItemRejected(room_id=item.room_id, reason=outcome.reason))
            summary.rejected += 1

    return BatchEvaluateResponse(items=items, summary=summary)
