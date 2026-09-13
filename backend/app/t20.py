"""T20 混响时间复核核心算法。

计算流程（与验收口径一致）：
1. 背景值 = 末尾 ceil(N * 10%) 个样本的算术平均；
2. 从全序列首个最大值的下标开始，逐项减去背景值，仅保留正值；
3. 以最大校正量为基准，计算 20 * log10(校正量 / 基准量) dB；
4. 选取落在闭区间 [-25, -5] dB 的点，以峰值后的秒数为横轴做普通最小二乘；
5. 有效点不足 30 个或斜率不为负即拒绝；
6. T20 = -20 / 斜率，使用未舍入值与上限比较，相等算合格。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

MIN_WINDOW_POINTS = 30
DB_LOWER = -25.0
DB_UPPER = -5.0
BACKGROUND_TAIL_RATIO = 0.10

REASON_INSUFFICIENT_POINTS = (
    "衰减窗口 [-25, -5] dB 内有效采样点不足 30 个，无法进行最小二乘回归"
)
REASON_NON_NEGATIVE_SLOPE = "衰减曲线斜率非负，不符合混响衰减特征"


@dataclass(frozen=True)
class T20Result:
    """复核结论。accepted=False 时仅携带拒绝原因。"""

    accepted: bool
    reason: Optional[str] = None
    t20_seconds: Optional[float] = None
    slope: Optional[float] = None
    points_used: int = 0
    background: float = 0.0
    peak_index: int = 0
    passed: Optional[bool] = None


def _first_max_index(values: list[float]) -> int:
    """全序列首个最大值的下标。"""
    best = 0
    for i in range(1, len(values)):
        if values[i] > values[best]:
            best = i
    return best


def _ols_slope(xs: list[float], ys: list[float]) -> float:
    """普通最小二乘斜率。xs 互不相同，分母恒正。"""
    n = len(xs)
    x_bar = sum(xs) / n
    y_bar = sum(ys) / n
    sxx = sum((x - x_bar) ** 2 for x in xs)
    sxy = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys))
    return sxy / sxx


def evaluate_t20(
    sample_interval_ms: int,
    pressure: list[float],
    limit_seconds: float,
) -> T20Result:
    """对线性声压采样做 T20 复核，返回结构化结论。"""
    n = len(pressure)
    dt = sample_interval_ms / 1000.0

    tail = math.ceil(n * BACKGROUND_TAIL_RATIO)
    background = sum(pressure[n - tail :]) / tail

    peak_index = _first_max_index(pressure)

    # 从首个最大值起逐项减背景，仅保留正值；横轴保留峰值后的真实秒数。
    corrected: list[tuple[float, float]] = []
    for i in range(peak_index, n):
        value = pressure[i] - background
        if value > 0.0:
            corrected.append(((i - peak_index) * dt, value))

    if not corrected:
        return T20Result(
            accepted=False,
            reason=REASON_INSUFFICIENT_POINTS,
            background=background,
            peak_index=peak_index,
        )

    reference = max(value for _, value in corrected)
    window = [
        (t, 20.0 * math.log10(value / reference))
        for t, value in corrected
    ]
    window = [(t, db) for t, db in window if DB_LOWER <= db <= DB_UPPER]

    if len(window) < MIN_WINDOW_POINTS:
        return T20Result(
            accepted=False,
            reason=REASON_INSUFFICIENT_POINTS,
            points_used=len(window),
            background=background,
            peak_index=peak_index,
        )

    xs = [t for t, _ in window]
    ys = [db for _, db in window]
    slope = _ols_slope(xs, ys)

    if slope >= 0.0:
        return T20Result(
            accepted=False,
            reason=REASON_NON_NEGATIVE_SLOPE,
            points_used=len(window),
            background=background,
            peak_index=peak_index,
        )

    t20 = -20.0 / slope
    return T20Result(
        accepted=True,
        t20_seconds=t20,
        slope=slope,
        points_used=len(window),
        background=background,
        peak_index=peak_index,
        # 未舍入值与上限比较，相等算合格
        passed=t20 <= limit_seconds,
    )
