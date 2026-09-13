"""T20 混响时间复核核心算法。

计算流程（与验收口径一致）：
1. 背景值 = 末尾 ceil(N * 10%) 个样本的算术平均；
2. 从全序列首个最大值的下标开始，逐项减去背景值，仅保留正值；
3. 以最大校正量为基准，计算 20 * log10(校正量 / 基准量) dB；
4. 选取落在闭区间 [-25, -5] dB 的点，以峰值后的秒数为横轴做普通最小二乘；
5. 有效点不足 30 个或斜率不为负即拒绝；
6. T20 = -20 / 斜率，使用未舍入值与上限比较，相等算合格；
7. 用同一组 [-25, -5] dB 取点与已求得的回归线计算决定系数 R²，
   残差比值因浮点误差略越过 [0, 1] 时先钳制到闭区间再分类。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

MIN_WINDOW_POINTS = 30
DB_LOWER = -25.0
DB_UPPER = -5.0
BACKGROUND_TAIL_RATIO = 0.10

# 衰减轨迹图最多展示的取样点数：超过后首尾必留、等距取样，
# 仅用于可视化；斜率、R²、T20 始终用完整窗口计算。
TRAIL_MAX_PLOT_POINTS = 200

# 拟合优度阈值：R² >= 0.9000 视为衰减点离散程度可接受（稳定），
# 低于阈值只提示复查，不参与合格判定。
RSQUARED_STABLE_THRESHOLD = 0.9000

REASON_INSUFFICIENT_POINTS = (
    "衰减窗口 [-25, -5] dB 内有效采样点不足 30 个，无法进行最小二乘回归"
)
REASON_NON_NEGATIVE_SLOPE = "衰减曲线斜率非负，不符合混响衰减特征"


@dataclass(frozen=True)
class WindowPoint:
    """[-25, -5] dB 窗口内的一个取样点：峰值后的秒数与对应 dB 值。"""

    time_seconds: float
    db: float


@dataclass(frozen=True)
class FitLine:
    """完整窗口（未取样）最小二乘回归线在窗口两端 t 处的端点。"""

    t_start: float
    db_start: float
    t_end: float
    db_end: float


@dataclass(frozen=True)
class DecayTrail:
    """单间成功结论附带的衰减轨迹（仅可视化，不参与任何计算口径）。

    sampled_points 为展示用取样点（首尾必留、等距索引，最多
    TRAIL_MAX_PLOT_POINTS 个）；total_points 是完整窗口取点数；
    fit_line 是以完整窗口回归的斜率与截距在窗口两端 t 处的取值。
    """

    total_points: int
    sampled_points: list[WindowPoint]
    fit_line: FitLine


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
    r_squared: Optional[float] = None
    fit_quality: Optional[str] = None
    trail: Optional[DecayTrail] = None


def _first_max_index(values: list[float]) -> int:
    """全序列首个最大值的下标。"""
    best = 0
    for i in range(1, len(values)):
        if values[i] > values[best]:
            best = i
    return best


def _ols_slope_intercept(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """普通最小二乘斜率与截距。xs 互不相同，斜率分母恒正。"""
    n = len(xs)
    x_bar = sum(xs) / n
    y_bar = sum(ys) / n
    sxx = sum((x - x_bar) ** 2 for x in xs)
    sxy = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, y_bar - slope * x_bar


def _r_squared(xs: list[float], ys: list[float], slope: float, intercept: float) -> float:
    """决定系数：1 - 残差平方和 / 总平方和，复用既有取点与已求得的回归线。

    理论上 R² ∈ [0, 1]；浮点误差可能使 1 - SSE/SST 略越过 0 或 1，
    先钳制到闭区间，阈值分类只面对合法取值。
    """
    y_bar = sum(ys) / len(ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - y_bar) ** 2 for y in ys)
    return _clamp_unit_interval(1.0 - ss_res / ss_tot)


def _clamp_unit_interval(value: float) -> float:
    """把决定系数钳制到闭区间 [0, 1]，挡掉残差比值的浮点越界。"""
    return min(1.0, max(0.0, value))


def classify_fit_quality(r_squared: float) -> str:
    """R² >= 0.9000 为 stable，否则 needs_review；相等归稳定侧。

    标签只提示工程师是否值得现场复查，绝不参与 T20 合格判定。
    """
    return "stable" if r_squared >= RSQUARED_STABLE_THRESHOLD else "needs_review"


def sample_trail_indices(total: int, limit: int = TRAIL_MAX_PLOT_POINTS) -> list[int]:
    """展示用取样索引：不超过 limit 时原样返回；超过时首尾必留、等距取样。

    等距口径：在 [0, total-1] 上取 limit 个等距位置并四舍五入到最近索引。
    total > limit 时相邻位置间距 (total-1)/(limit-1) 恒大于 1，
    故四舍五入后索引必不重复；seen 仅作防御性去重，不改变等距结果。
    """
    if total <= limit:
        return list(range(total))
    indices: list[int] = []
    seen: set[int] = set()
    for k in range(limit):
        idx = round(k * (total - 1) / (limit - 1))
        if idx not in seen:
            seen.add(idx)
            indices.append(idx)
    # 等距四舍五入序列天然升序且首项为 0、末项为 total-1
    return indices


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
    slope, intercept = _ols_slope_intercept(xs, ys)

    if slope >= 0.0:
        return T20Result(
            accepted=False,
            reason=REASON_NON_NEGATIVE_SLOPE,
            points_used=len(window),
            background=background,
            peak_index=peak_index,
        )

    # 拟合优度只复用既有取点与同一条回归线，不改变 T20 与合格口径
    r_squared = _r_squared(xs, ys, slope, intercept)
    fit_quality = classify_fit_quality(r_squared)
    t20 = -20.0 / slope

    # 衰减轨迹仅用于现场可视化：展示点等距取样（斜率/R²/T20 仍用完整窗口），
    # 拟合线端点取完整回归线在窗口首末 t 处的值。
    sampled = [
        WindowPoint(time_seconds=xs[i], db=ys[i])
        for i in sample_trail_indices(len(window))
    ]
    trail = DecayTrail(
        total_points=len(window),
        sampled_points=sampled,
        fit_line=FitLine(
            t_start=xs[0],
            db_start=intercept + slope * xs[0],
            t_end=xs[-1],
            db_end=intercept + slope * xs[-1],
        ),
    )
    return T20Result(
        accepted=True,
        t20_seconds=t20,
        slope=slope,
        points_used=len(window),
        background=background,
        peak_index=peak_index,
        # 未舍入值与上限比较，相等算合格
        passed=t20 <= limit_seconds,
        r_squared=r_squared,
        fit_quality=fit_quality,
        trail=trail,
    )
