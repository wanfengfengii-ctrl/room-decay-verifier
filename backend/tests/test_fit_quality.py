"""拟合优度 R² 的公式与契约测试。

核心不变量：
- 用既有 [-25, -5] dB 取点与同一条最小二乘回归线计算决定系数；
- 理想指数衰减 R² = 1.0；带确定扰动的采样按独立 oracle 复算；
- R² 钳制到 [0, 1] 后分类：>= 0.9000 稳定，低于阈值只提示需复查；
- 提示不参与合格判定：需复查可以合格，稳定也可以不合格；
- 衰减拒绝与字段错误响应不携带 r_squared / fit_quality；
- 单间与批量正常项的新证据逐字段一致。
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.t20 import (
    RSQUARED_STABLE_THRESHOLD,
    _clamp_unit_interval,
    classify_fit_quality,
    evaluate_t20,
)

client = TestClient(app)


def make_wobbled_decay(
    wobble_amplitude: float,
    wobble_period_samples: int = 100,
    t60: float = 1.5,
    dt_ms: int = 1,
    n: int = 5000,
    amplitude: float = 1000.0,
    offset: float = 0.25,
) -> list[float]:
    """理想指数衰减乘上确定性正弦扰动：p_i = A·10^(-3t/T60)·(1 + a·sin(2πi/P)) + 背景。

    扰动幅度确定，R² 随之确定，可在测试内独立复算；
    幅度小于 1，声压始终为正且峰值仍在 0 号位。
    """
    dt = dt_ms / 1000.0
    return [
        amplitude
        * 10.0 ** (-3.0 * (i * dt) / t60)
        * (1.0 + wobble_amplitude * math.sin(2.0 * math.pi * i / wobble_period_samples))
        + offset
        for i in range(n)
    ]


def oracle_r_squared(pressure: list[float], dt_ms: int = 1) -> tuple[float, int]:
    """按复核口径独立复算窗口内 (R², 取点数)，不导入后端算法模块。"""
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
    window = [(t, db) for t, db in window if -25.0 <= db <= -5.0]
    m = len(window)
    x_bar = sum(t for t, _ in window) / m
    y_bar = sum(db for _, db in window) / m
    sxx = sum((t - x_bar) ** 2 for t, _ in window)
    sxy = sum((t - x_bar) * (db - y_bar) for t, db in window)
    slope = sxy / sxx
    intercept = y_bar - slope * x_bar
    ss_res = sum((db - (intercept + slope * t)) ** 2 for t, db in window)
    ss_tot = sum((db - y_bar) ** 2 for _, db in window)
    raw = 1.0 - ss_res / ss_tot
    return min(1.0, max(0.0, raw)), m


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


# ---------- 公式：理想衰减与确定扰动 ----------


def test_ideal_exponential_decay_has_perfect_fit():
    pressure = make_wobbled_decay(0.0)
    result = evaluate_t20(1, pressure, limit_seconds=5.0)
    assert result.accepted
    assert result.r_squared == 1.0
    assert result.fit_quality == "stable"
    # 新增证据不改变 T20 / 斜率 / 合格口径
    assert result.t20_seconds == pytest.approx(0.5, abs=1e-6)


def test_r_squared_matches_independent_oracle_for_perturbed_samples():
    for amplitude in (0.10, 0.25, 0.35, 0.50):
        pressure = make_wobbled_decay(amplitude)
        result = evaluate_t20(1, pressure, limit_seconds=5.0)
        expected_r2, expected_points = oracle_r_squared(pressure)
        assert result.accepted
        assert result.points_used == expected_points
        assert result.r_squared == pytest.approx(expected_r2, abs=1e-12), (
            f"扰动幅度 {amplitude} 的 R² 与 oracle 不一致"
        )


def test_threshold_labels_on_both_sides_via_deterministic_samples():
    # 两侧采样：未舍入 R² 分别约为 0.9003 与 0.8997，四位小数后仍分居阈值两侧
    stable_pressure = make_wobbled_decay(0.3087)
    review_pressure = make_wobbled_decay(0.3095)

    stable = evaluate_t20(1, stable_pressure, limit_seconds=5.0)
    review = evaluate_t20(1, review_pressure, limit_seconds=5.0)
    assert stable.r_squared == pytest.approx(0.9003, abs=5e-4)
    assert review.r_squared == pytest.approx(0.8997, abs=5e-4)
    assert stable.fit_quality == "stable"
    assert review.fit_quality == "needs_review"

    # 页面四位小数展示的阈值两侧标签
    assert f"{stable.r_squared:.4f}" == "0.9003"
    assert f"{review.r_squared:.4f}" == "0.8997"


def test_classification_uses_unrounded_value_and_equality_is_stable():
    # 恰好 0.9000 归稳定侧
    assert classify_fit_quality(RSQUARED_STABLE_THRESHOLD) == "stable"
    assert classify_fit_quality(0.9000) == "stable"
    # 任何低于阈值的值（即使四位小数会显示 0.8999）提示复查
    assert classify_fit_quality(0.89999) == "needs_review"
    assert classify_fit_quality(0.0) == "needs_review"
    assert classify_fit_quality(1.0) == "stable"


def test_r_squared_is_clamped_to_closed_interval_before_classification():
    # 浮点误差使残差比值略越过 0 / 1：先钳制再分类，不出现越界值或异常
    assert _clamp_unit_interval(1.0 + 1e-16) == 1.0
    assert _clamp_unit_interval(-1e-16) == 0.0
    assert _clamp_unit_interval(0.5) == 0.5
    assert classify_fit_quality(_clamp_unit_interval(1.0 + 1e-12)) == "stable"
    assert classify_fit_quality(_clamp_unit_interval(-1e-12)) == "needs_review"


def test_real_perturbed_r_squared_stays_within_closed_interval():
    for amplitude in (0.0, 0.25, 0.50):
        body = post(make_wobbled_decay(amplitude))
        assert 0.0 <= body["r_squared"] <= 1.0


# ---------- 提示不改变 T20 判定 ----------


def test_needs_review_can_still_pass():
    # 离散程度高（需复查）但 T20=0.564 在 1.0 上限内：仍判合格
    body = post(make_wobbled_decay(0.35), limit_seconds=1.0)
    assert body["fit_quality"] == "needs_review"
    assert body["passed"] is True


def test_stable_can_still_fail():
    # 理想衰减（稳定）但上限低于 T20：仍判不合格
    body = post(make_wobbled_decay(0.0), limit_seconds=0.3)
    assert body["fit_quality"] == "stable"
    assert body["passed"] is False


def test_fit_evidence_identical_across_limits():
    # 仅改上限：R²、fit_quality 与其它计算证据完全不变，只有 passed/limit 回显变化
    loose = post(make_wobbled_decay(0.35), limit_seconds=5.0)
    tight = post(make_wobbled_decay(0.35), limit_seconds=0.3)
    for key in ("t20_seconds", "points_used", "slope", "background", "peak_index",
                "r_squared", "fit_quality"):
        assert loose[key] == tight[key]
    assert loose["passed"] is True and tight["passed"] is False


# ---------- API 契约 ----------


def test_success_payload_carries_fit_evidence():
    body = post(make_wobbled_decay(0.0))
    assert set(body.keys()) == {
        "status",
        "t20_seconds",
        "points_used",
        "slope",
        "background",
        "peak_index",
        "limit_seconds",
        "passed",
        "r_squared",
        "fit_quality",
        "decay_trail",
    }
    assert isinstance(body["r_squared"], float)
    assert body["fit_quality"] in {"stable", "needs_review"}
    assert body["r_squared"] == 1.0


def test_rejected_payload_has_no_fit_evidence():
    resp = client.post(
        "/api/evaluate",
        json={
            "sample_interval_ms": 1,
            "pressure": [5.0] * 1000,
            "limit_seconds": 1.0,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "rejected",
        "reason": "衰减窗口 [-25, -5] dB 内有效采样点不足 30 个，无法进行最小二乘回归",
    }


def test_field_error_payload_has_no_fit_evidence():
    resp = client.post(
        "/api/evaluate",
        json={
            "sample_interval_ms": 1,
            "pressure": [1.0] * 199,
            "limit_seconds": 1.0,
        },
    )
    assert resp.status_code == 422
    text = resp.text
    assert "r_squared" not in text and "fit_quality" not in text


# ---------- 单间与批量证据一致 ----------


def test_batch_ok_items_carry_identical_fit_evidence():
    rooms = [
        {
            "room_id": "STABLE",
            "sample_interval_ms": 1,
            "pressure": make_wobbled_decay(0.25),
            "limit_seconds": 1.0,
        },
        {
            "room_id": "REVIEW",
            "sample_interval_ms": 1,
            "pressure": make_wobbled_decay(0.35),
            "limit_seconds": 1.0,
        },
    ]
    batch = client.post("/api/evaluate-batch", json={"items": rooms}).json()
    for room, item in zip(rooms, batch["items"]):
        single = client.post(
            "/api/evaluate",
            json={k: v for k, v in room.items() if k != "room_id"},
        ).json()
        assert item["status"] == "ok"
        for key, value in single.items():
            assert item[key] == value, f"{room['room_id']} 的 {key} 两入口不一致"
        assert item["r_squared"] == single["r_squared"]
        assert item["fit_quality"] == single["fit_quality"]


def test_batch_rejected_and_invalid_rows_omit_fit_evidence():
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
            "pressure": make_wobbled_decay(0.0),
            "limit_seconds": 1.0,
        },
        {
            "room_id": "OK",
            "sample_interval_ms": 1,
            "pressure": make_wobbled_decay(0.0),
            "limit_seconds": 1.0,
        },
    ]
    batch = client.post("/api/evaluate-batch", json={"items": rooms}).json()
    rejected, invalid, ok = batch["items"]
    assert set(rejected.keys()) == {"room_id", "status", "reason"}
    assert "r_squared" not in invalid and "fit_quality" not in invalid
    assert "r_squared" in ok and "fit_quality" in ok
    # 提示不影响合格汇总与房间顺序
    assert [i["room_id"] for i in batch["items"]] == ["REJECTED", "INVALID", "OK"]
    assert batch["summary"] == {
        "total": 3,
        "ok": 1,
        "passed": 1,
        "failed": 0,
        "rejected": 1,
        "invalid": 1,
    }
