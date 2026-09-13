"""衰减轨迹 decay_trail 的公式与契约测试。

核心不变量：
- 轨迹与 T20 在同一次计算中产出，复用背景扣除、峰值定位与 [-25,-5] dB 窗口；
- 窗口点超过 200 时首尾必留、等距索引取样，索引不重复；
- 斜率、R²、T20 仍使用完整窗口，轨迹只可视化、不改变任何计算口径；
- 拟合线端点取自完整（未取样）回归线在窗口首末 t 处的值；
- 衰减拒绝与字段错误响应不携带 decay_trail；
- 单间与批量正常项的轨迹逐字段一致。
"""

from __future__ import annotations

import math

from fastapi.testclient import TestClient

from app.main import app
from app.t20 import TRAIL_MAX_PLOT_POINTS, evaluate_t20, sample_trail_indices

client = TestClient(app)


def make_decay(t60: float, dt_ms: int = 1, n: int = 5000, offset: float = 0.25) -> list[float]:
    """理想指数衰减声压：p(t) = 1000·10^(-3t/T60) + offset，dB 斜率 -60/T60。"""
    dt = dt_ms / 1000.0
    return [1000.0 * 10.0 ** (-3.0 * (i * dt) / t60) + offset for i in range(n)]


def oracle_window(pressure: list[float], dt_ms: int = 1) -> list[tuple[float, float]]:
    """按复核口径独立复算 [-25, -5] dB 窗口的 (峰值后秒数, dB)，不导入算法模块。"""
    n = len(pressure)
    dt = dt_ms / 1000.0
    tail = math.ceil(n * 0.10)
    background = sum(pressure[n - tail:]) / tail
    peak = max(range(n), key=lambda i: pressure[i])
    corrected = [
        ((i - peak) * dt, pressure[i] - background)
        for i in range(peak, n)
        if pressure[i] - background > 0.0
    ]
    reference = max(v for _, v in corrected)
    window = [(t, 20.0 * math.log10(v / reference)) for t, v in corrected]
    return [(t, db) for t, db in window if -25.0 <= db <= -5.0]


def post(pressure: list[float], limit_seconds: float = 1.0) -> dict:
    resp = client.post(
        "/api/evaluate",
        json={
            "sample_interval_ms": 1,
            "pressure": pressure,
            "limit_seconds": limit_seconds,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- 等距取样索引 ----------


def test_sample_indices_under_limit_keeps_every_point():
    assert sample_trail_indices(1) == [0]
    assert sample_trail_indices(30) == list(range(30))
    assert sample_trail_indices(TRAIL_MAX_PLOT_POINTS) == list(range(TRAIL_MAX_PLOT_POINTS))


def test_sample_indices_over_limit_keep_ends_and_stay_unique_equidistant():
    for total in (201, 500, 501, 1999, 20000):
        indices = sample_trail_indices(total)
        # 恰好取到上限个点
        assert len(indices) == TRAIL_MAX_PLOT_POINTS
        # 首尾必留
        assert indices[0] == 0
        assert indices[-1] == total - 1
        # 索引不重复且严格升序
        assert len(set(indices)) == len(indices)
        assert indices == sorted(indices)
        assert all(0 <= idx < total for idx in indices)
        # 等距口径：k 号位对应 round(k*(total-1)/(limit-1))
        expected = [
            round(k * (total - 1) / (TRAIL_MAX_PLOT_POINTS - 1))
            for k in range(TRAIL_MAX_PLOT_POINTS)
        ]
        assert indices == expected


# ---------- 轨迹内容与完整窗口一致 ----------


def test_trail_over_200_points_samples_full_window_with_endpoints():
    pressure = make_decay(1.5)  # 窗口约 500 点，超过 200
    window = oracle_window(pressure)
    assert len(window) > TRAIL_MAX_PLOT_POINTS

    result = evaluate_t20(1, pressure, limit_seconds=1.0)
    assert result.accepted
    assert result.trail is not None
    trail = result.trail

    # 完整取点数与窗口一致；展示点恰好 200
    assert trail.total_points == len(window) == result.points_used
    assert len(trail.sampled_points) == TRAIL_MAX_PLOT_POINTS

    indices = sample_trail_indices(len(window))
    for point, idx in zip(trail.sampled_points, indices):
        full_t, full_db = window[idx]
        assert point.time_seconds == full_t
        assert point.db == full_db

    # 首末展示点就是窗口首末点
    assert trail.sampled_points[0].time_seconds == window[0][0]
    assert trail.sampled_points[0].db == window[0][1]
    assert trail.sampled_points[-1].time_seconds == window[-1][0]
    assert trail.sampled_points[-1].db == window[-1][1]


def test_trail_under_200_points_keeps_all_window_points():
    # t60=15s 时 dB 斜率 -4/s，dt=100ms：20 dB 窗口约覆盖 5s -> 约 50 点
    pressure = make_decay(15.0, dt_ms=100, n=2000)
    window = oracle_window(pressure, dt_ms=100)
    assert 30 <= len(window) <= TRAIL_MAX_PLOT_POINTS

    resp = client.post(
        "/api/evaluate",
        json={
            "sample_interval_ms": 100,
            "pressure": pressure,
            "limit_seconds": 1.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    trail = body["decay_trail"]
    assert trail["total_points"] == len(window) == body["points_used"]
    assert len(trail["sampled_points"]) == len(window)
    for point, (t, db) in zip(trail["sampled_points"], window):
        assert point["time_seconds"] == t
        assert point["db"] == db


def test_fit_line_endpoints_come_from_full_regression_line():
    pressure = make_decay(1.5)
    window = oracle_window(pressure)
    xs = [t for t, _ in window]
    ys = [db for _, db in window]
    x_bar = sum(xs) / len(xs)
    y_bar = sum(ys) / len(ys)
    sxx = sum((x - x_bar) ** 2 for x in xs)
    sxy = sum((x - x_bar) * (y - y_bar) for x, y in window)
    slope = sxy / sxx
    intercept = y_bar - slope * x_bar

    body = post(pressure)
    line = body["decay_trail"]["fit_line"]

    # 端点的 t 是窗口首末 t，db 是完整回归线（非取样折线）在该处的值
    assert line["t_start"] == xs[0]
    assert line["t_end"] == xs[-1]
    assert line["db_start"] == intercept + slope * xs[0]
    assert line["db_end"] == intercept + slope * xs[-1]
    # 与成功响应中的完整窗口斜率同一条线
    assert body["slope"] == slope
    assert (line["db_end"] - line["db_start"]) / (line["t_end"] - line["t_start"]) == slope


def test_trail_does_not_change_slope_r2_t20_full_window_metrics():
    pressure = make_decay(1.5)
    window = oracle_window(pressure)
    xs = [t for t, _ in window]
    ys = [db for _, db in window]
    x_bar, y_bar = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - x_bar) ** 2 for x in xs)
    sxy = sum((x - x_bar) * (y - y_bar) for x, y in window)
    slope = sxy / sxx
    intercept = y_bar - slope * x_bar
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in window)
    ss_tot = sum((y - y_bar) ** 2 for y in ys)
    expected_r2 = min(1.0, max(0.0, 1.0 - ss_res / ss_tot))
    expected_t20 = -20.0 / slope

    body = post(pressure)
    assert body["slope"] == slope
    assert body["t20_seconds"] == expected_t20
    assert abs(body["r_squared"] - expected_r2) < 1e-12
    assert body["points_used"] == len(window)


def test_trail_is_deterministic_across_requests():
    payload = {
        "sample_interval_ms": 1,
        "pressure": make_decay(2.1, n=4000),
        "limit_seconds": 1.0,
    }
    first = client.post("/api/evaluate", json=payload).json()
    second = client.post("/api/evaluate", json=payload).json()
    assert first["decay_trail"] == second["decay_trail"]


# ---------- API 契约 ----------


def test_success_payload_carries_trail():
    body = post(make_decay(1.5))
    trail = body["decay_trail"]
    assert trail["total_points"] == body["points_used"]
    assert 1 <= len(trail["sampled_points"]) <= TRAIL_MAX_PLOT_POINTS
    point = trail["sampled_points"][0]
    assert set(point.keys()) == {"time_seconds", "db"}
    assert set(trail["fit_line"].keys()) == {"t_start", "db_start", "t_end", "db_end"}


def test_rejected_payload_has_no_trail():
    resp = client.post(
        "/api/evaluate",
        json={
            "sample_interval_ms": 1,
            "pressure": [5.0] * 1000,
            "limit_seconds": 1.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert "decay_trail" not in body


def test_field_error_payload_has_no_trail():
    resp = client.post(
        "/api/evaluate",
        json={
            "sample_interval_ms": 1,
            "pressure": [1.0] * 199,
            "limit_seconds": 1.0,
        },
    )
    assert resp.status_code == 422
    assert "decay_trail" not in resp.text


# ---------- 单间与批量轨迹一致 ----------


def test_batch_ok_items_carry_identical_trail():
    pressure = make_decay(1.5)
    single = post(pressure)
    batch = client.post(
        "/api/evaluate-batch",
        json={
            "items": [
                {"room_id": "TRAIL-1", "sample_interval_ms": 1,
                 "pressure": pressure, "limit_seconds": 1.0}
            ]
        },
    ).json()
    item = batch["items"][0]
    assert item["status"] == "ok"
    assert item["decay_trail"] == single["decay_trail"]


def test_batch_rejected_and_invalid_rows_omit_trail():
    rooms = [
        {
            "room_id": "REJECTED",
            "sample_interval_ms": 1,
            "pressure": [5.0] * 1000,
            "limit_seconds": 1.0,
        },
        {
            "room_id": "INVALID",
            "sample_interval_ms": 0,
            "pressure": make_decay(1.5),
            "limit_seconds": 1.0,
        },
    ]
    batch = client.post("/api/evaluate-batch", json={"items": rooms}).json()
    for item in batch["items"]:
        assert "decay_trail" not in item
