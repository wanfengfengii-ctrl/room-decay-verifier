"""一次性验收服务：对 API 与 Web 做真实 HTTP 验收，失败即非零退出。

验收 oracle 为本文件内按复核口径独立实现的计算，不调用后端算法模块，
确保“同一采样稳定得到相同证据、异常衰减只能看到拒绝原因”。
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

API_URL = os.environ.get("API_URL", "http://api:8000").rstrip("/")
WEB_URL = os.environ.get("WEB_URL", "http://web").rstrip("/")


def log(msg: str) -> None:
    print(f"[verify] {msg}", flush=True)


def http(method: str, url: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def wait_ready() -> None:
    for attempt in range(60):
        try:
            status, _ = http("GET", f"{API_URL}/api/health")
            if status == 200:
                log("API 健康检查通过")
                return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("[verify] API 在 60 秒内未就绪")


def make_decay(t60: float, dt_ms: int = 1, n: int = 5000, offset: float = 0.25) -> list[float]:
    return [
        1000.0 * 10.0 ** (-3.0 * (i * dt_ms / 1000.0) / t60) + offset
        for i in range(n)
    ]


def oracle_t20(dt_ms: int, pressure: list[float]) -> tuple[float, float, int]:
    """按复核口径独立计算 (t20, slope, points)。"""
    n = len(pressure)
    tail = math.ceil(n * 0.10)
    background = sum(pressure[n - tail:]) / tail
    peak = max(range(n), key=lambda i: pressure[i])
    corrected = [
        ((i - peak) * dt_ms / 1000.0, pressure[i] - background)
        for i in range(peak, n)
        if pressure[i] - background > 0.0
    ]
    reference = max(v for _, v in corrected)
    window = [
        (t, 20.0 * math.log10(v / reference))
        for t, v in corrected
    ]
    window = [(t, db) for t, db in window if -25.0 <= db <= -5.0]
    assert len(window) >= 30, "oracle 样本构造有误"
    xs = [t for t, _ in window]
    ys = [db for _, db in window]
    x_bar = sum(xs) / len(xs)
    y_bar = sum(ys) / len(ys)
    slope = sum((x - x_bar) * (y - y_bar) for x, y in window) / sum(
        (x - x_bar) ** 2 for x in xs
    )
    assert slope < 0.0, "oracle 样本斜率应为负"
    return -20.0 / slope, slope, len(window)


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise SystemExit(f"[verify] 失败: {name} {detail}")
    log(f"通过: {name}")


def main() -> None:
    wait_ready()

    # 1. 公式正确性：理想指数衰减，T20 应复现 T60/3
    payload = {
        "sample_interval_ms": 1,
        "pressure": make_decay(1.5),
        "limit_seconds": 1.0,
    }
    exp_t20, exp_slope, exp_points = oracle_t20(1, payload["pressure"])
    status, body = http("POST", f"{API_URL}/api/evaluate", payload)
    check("有效采样返回 200", status == 200, body)
    data = json.loads(body)
    check("状态为 ok", data.get("status") == "ok", body)
    check(
        "T20 与独立 oracle 一致",
        abs(data["t20_seconds"] - exp_t20) < 1e-9,
        f"got {data['t20_seconds']} expect {exp_t20}",
    )
    check("T20 复现 T60/3", abs(data["t20_seconds"] - 0.5) < 1e-3, str(data["t20_seconds"]))
    check("斜率与 oracle 一致", abs(data["slope"] - exp_slope) < 1e-9)
    check("取点数与 oracle 一致", data["points_used"] == exp_points)
    check("上限内判定合格", data["passed"] is True)

    # 2. 相等算合格（未舍入值比较）
    status, body = http(
        "POST",
        f"{API_URL}/api/evaluate",
        {**payload, "limit_seconds": data["t20_seconds"]},
    )
    check("T20 等于上限仍合格", json.loads(body)["passed"] is True, body)
    status, body = http(
        "POST",
        f"{API_URL}/api/evaluate",
        {**payload, "limit_seconds": data["t20_seconds"] - 1e-9},
    )
    check("T20 略超上限不合格", json.loads(body)["passed"] is False, body)

    # 3. 同一采样重复提交得到完全相同的证据
    _, body_a = http("POST", f"{API_URL}/api/evaluate", payload)
    _, body_b = http("POST", f"{API_URL}/api/evaluate", payload)
    check("同一采样两次响应完全一致", body_a == body_b)

    # 4. 异常衰减只能看到拒绝原因
    bad = {
        "sample_interval_ms": 1,
        "pressure": [5.0] * 1000,
        "limit_seconds": 1.0,
    }
    status, body = http("POST", f"{API_URL}/api/evaluate", bad)
    data = json.loads(body)
    check("异常衰减返回 rejected", status == 200 and data.get("status") == "rejected", body)
    check("拒绝原因非空", bool(data.get("reason")))
    check(
        "拒绝响应不泄露任何计算字段",
        set(data.keys()) == {"status", "reason"},
        body,
    )

    # 5. 非法输入返回 422
    status, _ = http(
        "POST",
        f"{API_URL}/api/evaluate",
        {"sample_interval_ms": 1, "pressure": [1.0, 2.0], "limit_seconds": 1.0},
    )
    check("过短采样返回 422", status == 422, f"got {status}")

    # 5b. 类型错误（字符串压力 / 布尔间隔）必须直接拒绝，不得强转
    for name, bad_payload in [
        ("字符串压力", {**payload, "pressure": ["5.0"] * 200}),
        ("布尔采样间隔", {**payload, "sample_interval_ms": True}),
        ("字符串上限", {**payload, "limit_seconds": "1.0"}),
    ]:
        status, body = http("POST", f"{API_URL}/api/evaluate", bad_payload)
        check(f"{name}返回 422 类型错误", status == 422, f"got {status}: {body}")

    # 6. Web 前端可达，且经 Web 反代的 API 可用
    status, body = http("GET", f"{WEB_URL}/")
    check("Web 首页 200", status == 200 and "root" in body, f"got {status}")
    status, body = http("GET", f"{WEB_URL}/api/health")
    check("Web 反代 /api 可达", status == 200 and "up" in body, f"got {status}")
    status, body = http("POST", f"{WEB_URL}/api/evaluate", payload)
    data = json.loads(body)
    check(
        "经 Web 反代复核结论一致",
        status == 200 and abs(data["t20_seconds"] - exp_t20) < 1e-9,
        body,
    )

    log("VERIFY OK：全部验收项通过")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] 未预期的错误: {exc!r}", file=sys.stderr)
        sys.exit(1)
