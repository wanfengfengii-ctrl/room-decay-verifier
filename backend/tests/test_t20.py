"""T20 公式与 API 的 pytest 覆盖。

所有期望值均由测试内独立构造的指数衰减样本推出，不依赖任何固定响应。
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.t20 import (
    REASON_INSUFFICIENT_POINTS,
    REASON_NON_NEGATIVE_SLOPE,
    evaluate_t20,
)

client = TestClient(app)


def make_decay(
    t60: float,
    dt_ms: int = 1,
    n: int = 5000,
    amplitude: float = 1000.0,
    offset: float = 0.0,
) -> list[float]:
    """理想指数衰减声压：p(t) = amplitude * 10^(-3 t / T60) + offset。

    其 dB 曲线斜率恰为 -60 / T60 dB/s，故 T20 期望值为 T60 / 3。
    """
    dt = dt_ms / 1000.0
    return [amplitude * 10.0 ** (-3.0 * (i * dt) / t60) + offset for i in range(n)]


def make_rising() -> list[float]:
    """峰值在 0 号位、其后 dB 线性上升、尾部贴近零的序列（斜率为正）。"""
    rising = [10.0 ** ((-30.0 + 28.0 * i / 999.0) / 20.0) for i in range(1000)]
    return [1.0] + rising + [1e-9] * 200


def post(payload: dict) -> dict:
    resp = client.post("/api/evaluate", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- 公式单元测试 ----------


def test_ideal_decay_recovers_t20():
    t60 = 1.8
    result = evaluate_t20(1, make_decay(t60), limit_seconds=5.0)
    assert result.accepted
    assert result.t20_seconds == pytest.approx(t60 / 3.0, abs=1e-6)
    assert result.slope == pytest.approx(-60.0 / t60, abs=1e-4)
    assert result.points_used >= 30


def test_background_is_mean_of_last_ceil_10_percent():
    # 末尾 ceil(N*10%) 个样本的算术平均作为背景值
    n = 250  # ceil(250 * 0.1) = 25
    pressure = make_decay(1.5, dt_ms=1, n=n, offset=2.5)
    result = evaluate_t20(1, pressure, limit_seconds=5.0)
    expected_bg = sum(pressure[-25:]) / 25
    assert result.background == pytest.approx(expected_bg)


def test_background_offset_is_subtracted_before_db():
    # 同一衰减叠加恒定背景后，T20 结论应基本一致
    plain = evaluate_t20(1, make_decay(1.5), limit_seconds=5.0)
    shifted = evaluate_t20(1, make_decay(1.5, offset=3.0), limit_seconds=5.0)
    assert plain.accepted and shifted.accepted
    assert shifted.t20_seconds == pytest.approx(plain.t20_seconds, abs=1e-3)


def test_first_maximum_is_used_when_max_repeats():
    pressure = make_decay(1.5)
    pressure.insert(0, pressure[0])  # 两个相同最大值，首个在 0 号位
    result = evaluate_t20(1, pressure, limit_seconds=5.0)
    assert result.peak_index == 0


def test_window_selects_only_minus_25_to_minus_5_db():
    # 窗口点数应等于 [-25,-5] dB 区间覆盖的采样数：
    # 斜率 -40 dB/s、dt=1ms 时区间宽 20 dB -> 约 500 点
    result = evaluate_t20(1, make_decay(1.5), limit_seconds=5.0)
    assert result.accepted
    assert 490 <= result.points_used <= 510


def test_non_positive_corrected_values_are_dropped():
    # 背景高于尾部采样时，尾部被丢弃，仅正值参与
    pressure = make_decay(1.5, offset=0.5)
    result = evaluate_t20(1, pressure, limit_seconds=5.0)
    assert result.accepted
    assert result.t20_seconds == pytest.approx(0.5, abs=1e-3)


def test_insufficient_points_rejected():
    # 极快衰减：[-25,-5] dB 窗口内不足 30 点
    pressure = make_decay(0.06, dt_ms=5, n=2000)
    result = evaluate_t20(5, pressure, limit_seconds=5.0)
    assert not result.accepted
    assert result.reason == REASON_INSUFFICIENT_POINTS
    assert result.t20_seconds is None


def test_non_negative_slope_rejected():
    # 峰值之后 dB 持续上升：窗口内斜率为正
    result = evaluate_t20(1, make_rising(), limit_seconds=5.0)
    assert not result.accepted
    assert result.reason == REASON_NON_NEGATIVE_SLOPE


def test_all_samples_below_background_rejected():
    # 峰值之后全部不高于背景 -> 无有效点
    pressure = [100.0] + [1.0] * 299
    result = evaluate_t20(1, pressure, limit_seconds=5.0)
    assert not result.accepted
    assert result.reason == REASON_INSUFFICIENT_POINTS


def test_limit_comparison_uses_unrounded_value_and_equality_passes():
    payload = {
        "sample_interval_ms": 1,
        "pressure": make_decay(1.5),
        "limit_seconds": 5.0,
    }
    t20 = post(payload)["t20_seconds"]

    equal = post({**payload, "limit_seconds": t20})
    assert equal["passed"] is True  # 相等算合格

    below = post({**payload, "limit_seconds": t20 - 1e-9})
    assert below["passed"] is False

    above = post({**payload, "limit_seconds": t20 + 1e-9})
    assert above["passed"] is True


def test_determinism_same_input_same_output():
    payload = {
        "sample_interval_ms": 2,
        "pressure": make_decay(2.1, dt_ms=2, n=4000, offset=0.25),
        "limit_seconds": 1.0,
    }
    first = client.post("/api/evaluate", json=payload).json()
    second = client.post("/api/evaluate", json=payload).json()
    assert first == second


# ---------- API 校验与契约 ----------


def test_success_payload_shape():
    body = post(
        {
            "sample_interval_ms": 1,
            "pressure": make_decay(1.5),
            "limit_seconds": 1.0,
        }
    )
    assert body["status"] == "ok"
    assert body["passed"] is True
    assert body["points_used"] >= 30
    assert body["slope"] < 0
    assert isinstance(body["t20_seconds"], float)


def test_rejection_payload_contains_only_reason():
    body = post(
        {
            "sample_interval_ms": 1,
            "pressure": make_rising(),
            "limit_seconds": 1.0,
        }
    )
    assert body == {"status": "rejected", "reason": REASON_NON_NEGATIVE_SLOPE}


@pytest.mark.parametrize(
    "override",
    [
        {"sample_interval_ms": 0},
        {"sample_interval_ms": 101},
        {"sample_interval_ms": 1.5},
        {"sample_interval_ms": True},
        {"sample_interval_ms": False},
        {"sample_interval_ms": "5"},
        {"limit_seconds": 0.29},
        {"limit_seconds": 5.01},
        {"limit_seconds": True},
        {"limit_seconds": "1.0"},
        {"pressure": [1.0] * 199},
        {"pressure": [1.0] * 20001},
        {"pressure": [1.0] * 199 + [0.0] + [1.0]},
        {"pressure": [1.0] * 199 + [-2.0] + [1.0]},
        {"pressure": ["5.0"] * 200},
        {"pressure": [True] * 200},
        {"pressure": [1.0] * 199 + ["3.0"]},
        {"pressure": [1.0] * 199 + [False]},
        {"pressure": [None] * 200},
    ],
)
def test_invalid_payloads_return_422(override):
    payload = {
        "sample_interval_ms": 1,
        "pressure": make_decay(1.5),
        "limit_seconds": 1.0,
    }
    payload.update(override)
    resp = client.post("/api/evaluate", json=payload)
    assert resp.status_code == 422


def test_integer_json_numbers_are_accepted():
    # JSON 整数是合法数字，不应被严格类型校验误伤
    payload = {
        "sample_interval_ms": 1,
        "pressure": [int(p) for p in make_decay(1.5, offset=1.0)],
        "limit_seconds": 1,
    }
    resp = client.post("/api/evaluate", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_type_error_message_is_explicit():
    payload = {
        "sample_interval_ms": True,
        "pressure": make_decay(1.5),
        "limit_seconds": 1.0,
    }
    resp = client.post("/api/evaluate", json=payload)
    assert resp.status_code == 422
    assert "整数" in resp.text


def test_health_endpoint():
    assert client.get("/api/health").json() == {"status": "up"}
