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


def make_wobbled_decay(
    amplitude: float, period: int = 100, t60: float = 1.5, n: int = 5000
) -> list[float]:
    """完全指数衰减乘确定性正弦扰动：R² 由扰动幅度确定，可独立复算。

    p_i = 1000·10^(-3t/T60)·(1 + a·sin(2πi/P)) + 0.25，幅度 < 1 保证声压恒正。
    """
    return [
        1000.0
        * 10.0 ** (-3.0 * (i / 1000.0) / t60)
        * (1.0 + amplitude * math.sin(2.0 * math.pi * i / period))
        + 0.25
        for i in range(n)
    ]


def oracle_t20(dt_ms: int, pressure: list[float]) -> tuple[float, float, int, float]:
    """按复核口径独立计算 (t20, slope, points, r_squared)。"""
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
    sxx = sum((x - x_bar) ** 2 for x in xs)
    sxy = sum((x - x_bar) * (y - y_bar) for x, y in window)
    slope = sxy / sxx
    intercept = y_bar - slope * x_bar
    assert slope < 0.0, "oracle 样本斜率应为负"
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in window)
    ss_tot = sum((y - y_bar) ** 2 for y in ys)
    r_squared = min(1.0, max(0.0, 1.0 - ss_res / ss_tot))
    return -20.0 / slope, slope, len(window), r_squared


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
    exp_t20, exp_slope, exp_points, exp_r2 = oracle_t20(1, payload["pressure"])
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
    # 理想指数衰减：R² 与独立 oracle 一致（=1.0），钳制后落在闭区间，标记稳定
    check("R² 与独立 oracle 一致", abs(data["r_squared"] - exp_r2) < 1e-12, body)
    check("理想衰减 R² 为 1.0000", data["r_squared"] == 1.0, body)
    check("四位小数展示为 1.0000", f"{data['r_squared']:.4f}" == "1.0000", body)
    check("理想衰减拟合质量为 stable", data["fit_quality"] == "stable", body)

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

    # 3b. 拟合优度：确定扰动样本的 R² 与独立 oracle 一致，四舍五入与阈值两侧标签
    fit_cases = [
        # (扰动幅度, 期望未舍入 R² 近似, 四位小数, 期望 fit_quality)
        (0.3087, 0.9003, "0.9003", "stable"),        # 阈值上方：稳定
        (0.3095, 0.8997, "0.8997", "needs_review"),  # 阈值下方：需复查
        (0.35, 0.8697, "0.8697", "needs_review"),    # 明显离散
    ]
    for amp, approx_r2, text_r2, quality in fit_cases:
        wobbled = {
            "sample_interval_ms": 1,
            "pressure": make_wobbled_decay(amp),
            "limit_seconds": 1.0,
        }
        _, _, _, oracle_r2 = oracle_t20(1, wobbled["pressure"])
        status, wbody = http("POST", f"{API_URL}/api/evaluate", wobbled)
        wdata = json.loads(wbody)
        check(f"扰动 {amp} 返回 200 且为正常项", status == 200 and wdata["status"] == "ok", wbody)
        check(
            f"扰动 {amp} 的 R² 与独立 oracle 一致",
            abs(wdata["r_squared"] - oracle_r2) < 1e-12,
            f"got {wdata['r_squared']} expect {oracle_r2}",
        )
        check(
            f"扰动 {amp} 的 R² 位于预期区间且四位小数为 {text_r2}",
            abs(wdata["r_squared"] - approx_r2) < 5e-4
            and f"{wdata['r_squared']:.4f}" == text_r2,
            f"got {wdata['r_squared']!r}",
        )
        check(
            f"扰动 {amp} 的拟合标签为 {quality}",
            wdata["fit_quality"] == quality,
            wbody,
        )
        check(
            f"扰动 {amp} 的 R² 钳制在闭区间 [0, 1]",
            0.0 <= wdata["r_squared"] <= 1.0,
            wbody,
        )

    # 阈值两侧各一个真实采样（0.9003 稳定 / 0.8997 需复查），相等归稳定侧由单元测试保证。

    # 需复查只是复查提示：R²≈0.8697 的采样 T20≈0.564 仍在 1.0 上限内 -> 合格
    review_payload = {
        "sample_interval_ms": 1,
        "pressure": make_wobbled_decay(0.35),
        "limit_seconds": 1.0,
    }
    review_data = json.loads(http("POST", f"{API_URL}/api/evaluate", review_payload)[1])
    check(
        "需复查不改变合格判定（离散但在上限内仍合格）",
        review_data["fit_quality"] == "needs_review" and review_data["passed"] is True,
        json.dumps(review_data, ensure_ascii=False),
    )
    # 同采样压低上限：稳定与否不随限值变化，只有 passed 改变
    tight = json.loads(
        http("POST", f"{API_URL}/api/evaluate", {**review_payload, "limit_seconds": 0.3})[1]
    )
    check(
        "改变上限不改变拟合证据，只改变合格判定",
        tight["r_squared"] == review_data["r_squared"]
        and tight["fit_quality"] == review_data["fit_quality"]
        and tight["passed"] is False,
        json.dumps(tight, ensure_ascii=False),
    )

    # 3c. 两入口对同一采样的拟合证据逐字段一致（在下方混合批次中一并逐字段比对）

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

    # 5c. 批量复核：混合批次顺序稳定、逐项证据与单独提交完全一致、汇总口径正确
    rooms = [
        {
            "room_id": "ROOM-OK-1",
            "sample_interval_ms": 1,
            "pressure": make_decay(1.5),
            "limit_seconds": 1.0,
        },
        {
            "room_id": "ROOM-OK-2",
            "sample_interval_ms": 1,
            "pressure": make_decay(1.5),
            "limit_seconds": 0.3,  # 同一采样更低上限 -> 不合格
        },
        {
            "room_id": "ROOM-REJECTED",
            "sample_interval_ms": 1,
            "pressure": [5.0] * 1000,
            "limit_seconds": 1.0,
        },
        {
            "room_id": "ROOM-INVALID",
            "sample_interval_ms": 0,  # 越界
            "pressure": make_decay(1.5),
            "limit_seconds": 1.0,
        },
        {
            "room_id": "ROOM-OK-3",
            "sample_interval_ms": 2,
            "pressure": make_decay(2.1, dt_ms=2, n=4000),
            "limit_seconds": 5.0,
        },
    ]
    status, body = http("POST", f"{API_URL}/api/evaluate-batch", {"items": rooms})
    check("混合批次返回 200", status == 200, body)
    batch = json.loads(body)
    check(
        "items 保持输入顺序",
        [i.get("room_id") for i in batch["items"]]
        == ["ROOM-OK-1", "ROOM-OK-2", "ROOM-REJECTED", "ROOM-INVALID", "ROOM-OK-3"],
        body,
    )
    statuses = [i["status"] for i in batch["items"]]
    check("逐项状态为 ok/ok/rejected/invalid/ok", statuses == ["ok", "ok", "rejected", "invalid", "ok"], body)

    # 每个正常项与单独提交所得证据完全一致（逐字段对比）
    normal_indices = [i for i, s in enumerate(statuses) if s == "ok"]
    single_bodies = {}
    for idx in normal_indices:
        room = rooms[idx]
        single_body = http(
            "POST",
            f"{API_URL}/api/evaluate",
            {k: v for k, v in room.items() if k != "room_id"},
        )[1]
        single_bodies[idx] = json.loads(single_body)
        item = batch["items"][idx]
        check(
            f"房间 {room['room_id']} 批量证据与单独提交逐字段一致",
            all(item[k] == v for k, v in single_bodies[idx].items()),
            f"batch={item} single={single_body}",
        )

    # 首间正常项同时与独立 oracle 一致
    first_ok = batch["items"][0]
    check("批量首间 T20 与独立 oracle 一致", abs(first_ok["t20_seconds"] - exp_t20) < 1e-9)
    check("批量首间取点数与 oracle 一致", first_ok["points_used"] == exp_points)
    check("批量首间合格", first_ok["passed"] is True)
    check("批量第二间超上限不合格", batch["items"][1]["passed"] is False)
    # 正常项携带拟合证据：理想衰减 R²=1.0000 且 stable；逐字段比对已含这两个新字段
    check(
        "批量正常项携带 R² 与拟合标签",
        first_ok["r_squared"] == 1.0 and first_ok["fit_quality"] == "stable",
        json.dumps(first_ok, ensure_ascii=False),
    )

    # 3c. 两入口对同一扰动采样的拟合证据一致：稳定 / 需复查各一间，均合格，顺序不变
    fit_rooms = [
        {
            "room_id": "FIT-STABLE",
            "sample_interval_ms": 1,
            "pressure": make_wobbled_decay(0.3087),
            "limit_seconds": 1.0,
        },
        {
            "room_id": "FIT-REVIEW",
            "sample_interval_ms": 1,
            "pressure": make_wobbled_decay(0.35),
            "limit_seconds": 1.0,
        },
    ]
    fit_batch = json.loads(
        http("POST", f"{API_URL}/api/evaluate-batch", {"items": fit_rooms})[1]
    )
    check(
        "拟合批次顺序与状态稳定（均为正常项）",
        [i["room_id"] for i in fit_batch["items"]] == ["FIT-STABLE", "FIT-REVIEW"]
        and [i["status"] for i in fit_batch["items"]] == ["ok", "ok"],
        json.dumps(fit_batch, ensure_ascii=False),
    )
    for room, item in zip(fit_rooms, fit_batch["items"]):
        single = json.loads(
            http(
                "POST",
                f"{API_URL}/api/evaluate",
                {k: v for k, v in room.items() if k != "room_id"},
            )[1]
        )
        check(
            f"{room['room_id']} 两入口拟合证据逐字段一致",
            item["r_squared"] == single["r_squared"]
            and item["fit_quality"] == single["fit_quality"]
            and all(item[k] == v for k, v in single.items()),
            f"batch={item} single={single}",
        )
    check(
        "阈值两侧标签：稳定 / 需复查，且需复查不影响合格汇总",
        [i["fit_quality"] for i in fit_batch["items"]] == ["stable", "needs_review"]
        and f"{fit_batch['items'][0]['r_squared']:.4f}" == "0.9003"
        and f"{fit_batch['items'][1]['r_squared']:.4f}" == "0.8697"
        and [i["passed"] for i in fit_batch["items"]] == [True, True]
        and fit_batch["summary"]
        == {"total": 2, "ok": 2, "passed": 2, "failed": 0, "rejected": 0, "invalid": 0},
        json.dumps(fit_batch, ensure_ascii=False),
    )
    # 经 Web 反代的单间拟合证据一致
    proxied_fit = json.loads(
        http(
            "POST",
            f"{WEB_URL}/api/evaluate",
            {k: v for k, v in fit_rooms[1].items() if k != "room_id"},
        )[1]
    )
    check(
        "经 Web 反代的拟合证据一致",
        proxied_fit["r_squared"] == fit_batch["items"][1]["r_squared"]
        and proxied_fit["fit_quality"] == "needs_review"
        and proxied_fit["passed"] is True,
        json.dumps(proxied_fit, ensure_ascii=False),
    )

    # 衰减异常项仍只含拒绝原因（多一个 room_id）
    rejected_item = batch["items"][2]
    check(
        "批量异常项只暴露 room_id/status/reason",
        set(rejected_item.keys()) == {"room_id", "status", "reason"} and bool(rejected_item["reason"]),
        json.dumps(rejected_item, ensure_ascii=False),
    )
    # 字段错误项不携带任何拟合证据
    check(
        "字段错误项不携带拟合证据",
        "r_squared" not in batch["items"][3] and "fit_quality" not in batch["items"][3],
        json.dumps(batch["items"][3], ensure_ascii=False),
    )

    # 字段错误项携带房间标识与可定位说明
    invalid_item = batch["items"][3]
    check(
        "字段错误项携带房间标识与字段定位",
        invalid_item["room_id"] == "ROOM-INVALID"
        and invalid_item["index"] == 3
        and any(e.get("field") == "sample_interval_ms" and e.get("message") for e in invalid_item["errors"]),
        json.dumps(invalid_item, ensure_ascii=False),
    )

    check(
        "汇总只统计正常项",
        batch["summary"] == {"total": 5, "ok": 3, "passed": 2, "failed": 1, "rejected": 1, "invalid": 1},
        json.dumps(batch["summary"], ensure_ascii=False),
    )

    # 部分失败批次的原始字节与再次请求一致（确定性）
    _, body_batch_again = http("POST", f"{API_URL}/api/evaluate-batch", {"items": rooms})
    check("混合批次两次响应完全一致", body == body_batch_again)

    # room_id 重复：整批请求级拒绝
    status, body = http(
        "POST",
        f"{API_URL}/api/evaluate-batch",
        {"items": [rooms[0], {**rooms[1], "room_id": "ROOM-OK-1"}]},
    )
    check("重复 room_id 整批拒绝为 400", status == 400 and "ROOM-OK-1" in body, f"got {status}: {body}")

    # 无法解析的批次：请求级 400，而不是逐项错误
    req = urllib.request.Request(
        f"{API_URL}/api/evaluate-batch",
        data=b'{"items": [',
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            bad_status, bad_body = resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        bad_status, bad_body = exc.code, exc.read().decode()
    check("整批无法解析返回 400", bad_status == 400 and "JSON" in bad_body, f"got {bad_status}: {bad_body}")

    # 空批次 / 超过 20 间：请求级 400
    check("空批次返回 400", http("POST", f"{API_URL}/api/evaluate-batch", {"items": []})[0] == 400)
    too_many = {"items": [{**{k: v for k, v in rooms[0].items() if k != "room_id"}, "room_id": f"R{i}"} for i in range(21)]}
    check("21 间批次返回 400", http("POST", f"{API_URL}/api/evaluate-batch", too_many)[0] == 400)

    # 5d. 房间标识错误定位：超长标识照常复核；数字 / 空白标识逐项 invalid 且可定位
    long_room_id = "LONG-" + "R" * 120
    status, body = http(
        "POST",
        f"{API_URL}/api/evaluate-batch",
        {
            "items": [
                {**rooms[0], "room_id": long_room_id},
                {**rooms[0], "room_id": 42},
                {**rooms[0], "room_id": "   "},
            ]
        },
    )
    check("标识定位批次返回 200", status == 200, body)
    locate = json.loads(body)
    long_item = locate["items"][0]
    check(
        "超过 100 字符的非空标识照常复核并返回结论",
        long_item["status"] == "ok"
        and long_item["room_id"] == long_room_id
        and abs(long_item["t20_seconds"] - exp_t20) < 1e-9,
        json.dumps(long_item, ensure_ascii=False),
    )
    numeric_item = locate["items"][1]
    check(
        "数字标识逐项 invalid：room_id 为 null、凭 index 定位、错误指出类型不符",
        numeric_item["status"] == "invalid"
        and numeric_item["room_id"] is None
        and numeric_item["index"] == 1
        and any(
            e.get("field") == "room_id" and "字符串" in e.get("message", "")
            for e in numeric_item["errors"]
        ),
        json.dumps(numeric_item, ensure_ascii=False),
    )
    blank_item = locate["items"][2]
    check(
        "空白标识逐项 invalid：凭 index 定位、字段错误指出空白",
        blank_item["status"] == "invalid"
        and blank_item["index"] == 2
        and any(
            e.get("field") == "room_id" and "空白" in e.get("message", "")
            for e in blank_item["errors"]
        ),
        json.dumps(blank_item, ensure_ascii=False),
    )
    check(
        "标识定位批次汇总只计正常项",
        locate["summary"]
        == {"total": 3, "ok": 1, "passed": 1, "failed": 0, "rejected": 0, "invalid": 2},
        json.dumps(locate["summary"], ensure_ascii=False),
    )

    # 5e. 统一限值：同一批采样分别提交逐房间限值与等值统一限值，结论逐字节一致
    uniform_rooms = [
        {
            "room_id": "U-OK",
            "sample_interval_ms": 1,
            "pressure": make_decay(1.5),
            "limit_seconds": 1.0,
        },
        {
            "room_id": "U-REJECTED",
            "sample_interval_ms": 1,
            "pressure": [5.0] * 1000,
            "limit_seconds": 1.0,
        },
        {
            "room_id": "U-INVALID",
            "sample_interval_ms": 0,
            "pressure": make_decay(1.5),
            "limit_seconds": 1.0,
        },
    ]
    status, per_room_body = http("POST", f"{API_URL}/api/evaluate-batch", {"items": uniform_rooms})
    check("逐房间限值批次返回 200", status == 200, per_room_body)

    omitted = [{k: v for k, v in r.items() if k != "limit_seconds"} for r in uniform_rooms]
    status, common_body = http(
        "POST",
        f"{API_URL}/api/evaluate-batch",
        {"items": omitted, "common_limit_seconds": 1.0},
    )
    check("等值统一限值批次返回 200", status == 200, common_body)
    check(
        "统一限值与逐房间限值的逐行证据、顺序与汇总一致",
        common_body == per_room_body,
        f"common={common_body} per-room={per_room_body}",
    )

    # 两处同时存在限值时以顶层统一限值为准
    both = [dict(r, limit_seconds=0.3) for r in uniform_rooms]
    status, body = http(
        "POST",
        f"{API_URL}/api/evaluate-batch",
        {"items": both, "common_limit_seconds": 1.0},
    )
    both_batch = json.loads(body)
    check(
        "条目与顶层同时给出限值时以顶层值为准",
        status == 200
        and both_batch["items"][0]["limit_seconds"] == 1.0
        and both_batch["items"][0]["passed"] is True,
        body,
    )

    # 启用统一限值时，条目残留的无效旧限值被忽略，按全批限值正常复核
    stale = [
        {**omitted[0], "limit_seconds": "残留旧值"},
        {**omitted[0], "room_id": "U-STALE-RANGE", "limit_seconds": 99.0},
        {**omitted[0], "room_id": "U-STALE-BOOL", "limit_seconds": True},
    ]
    status, body = http(
        "POST",
        f"{API_URL}/api/evaluate-batch",
        {"items": stale, "common_limit_seconds": 1.0},
    )
    stale_batch = json.loads(body)
    check(
        "残留无效旧限值的房间按全批限值正常复核",
        status == 200
        and [i["status"] for i in stale_batch["items"]] == ["ok", "ok", "ok"]
        and [i["limit_seconds"] for i in stale_batch["items"]] == [1.0, 1.0, 1.0]
        and stale_batch["summary"]["ok"] == 3
        and stale_batch["summary"]["invalid"] == 0,
        body,
    )

    # 统一限值改变时，只改变有效房间的限值与判定
    status, low_body = http(
        "POST", f"{API_URL}/api/evaluate-batch", {"items": omitted, "common_limit_seconds": 0.3}
    )
    status, high_body = http(
        "POST", f"{API_URL}/api/evaluate-batch", {"items": omitted, "common_limit_seconds": 5.0}
    )
    low, high = json.loads(low_body), json.loads(high_body)
    ok_low, ok_high = low["items"][0], high["items"][0]
    check(
        "统一限值改变只更新有效房间的限值与判定",
        ok_low["limit_seconds"] == 0.3
        and ok_low["passed"] is False
        and ok_high["limit_seconds"] == 5.0
        and ok_high["passed"] is True
        and ok_low["t20_seconds"] == ok_high["t20_seconds"]
        and ok_low["slope"] == ok_high["slope"]
        and ok_low["points_used"] == ok_high["points_used"],
        f"low={ok_low} high={ok_high}",
    )
    check(
        "衰减异常与字段错误行不随统一限值改变",
        low["items"][1] == high["items"][1] and low["items"][2] == high["items"][2],
        f"low={low['items']} high={high['items']}",
    )
    check(
        "合格汇总随统一限值更新",
        low["summary"] == {"total": 3, "ok": 1, "passed": 0, "failed": 1, "rejected": 1, "invalid": 1}
        and high["summary"] == {"total": 3, "ok": 1, "passed": 1, "failed": 0, "rejected": 1, "invalid": 1},
        f"low={low['summary']} high={high['summary']}",
    )

    # 非法统一限值：字符串 / 布尔 / 越界一律整批 400，消息固定
    for bad in ("1.0", True, 0.29, 5.01):
        status, body = http(
            "POST",
            f"{API_URL}/api/evaluate-batch",
            {"items": [uniform_rooms[0]], "common_limit_seconds": bad},
        )
        check(
            f"非法统一限值 {bad!r} 整批 400",
            status == 400 and "统一限值必须是0.30至5.00的JSON数字" in body,
            f"got {status}: {body}",
        )
    # 非有限数（NaN / Infinity）以原始字节提交
    room_json = json.dumps(uniform_rooms[0]).encode()
    for token in (b"NaN", b"Infinity"):
        req = urllib.request.Request(
            f"{API_URL}/api/evaluate-batch",
            data=b'{"items": [' + room_json + b'], "common_limit_seconds": ' + token + b"}",
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                nf_status, nf_body = resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            nf_status, nf_body = exc.code, exc.read().decode()
        check(
            f"非有限统一限值 {token.decode()} 整批 400",
            nf_status == 400 and "统一限值必须是0.30至5.00的JSON数字" in nf_body,
            f"got {nf_status}: {nf_body}",
        )

    # 未启用统一限值时，缺少条目限值仍是该行字段错误，不遮蔽其余房间
    status, body = http(
        "POST",
        f"{API_URL}/api/evaluate-batch",
        {"items": [uniform_rooms[0], {**omitted[0], "room_id": "U-NO-LIMIT"}]},
    )
    no_common = json.loads(body)
    missing_item = no_common["items"][1]
    check(
        "未启用统一限值时缺少条目限值按该行字段错误处理",
        status == 200
        and no_common["items"][0]["status"] == "ok"
        and missing_item["status"] == "invalid"
        and missing_item["index"] == 1
        and any(
            e.get("field") == "limit_seconds" and e.get("message")
            for e in missing_item["errors"]
        ),
        json.dumps(no_common, ensure_ascii=False),
    )

    # 经 Web 反代的统一限值链路同样可用
    status, body = http(
        "POST",
        f"{WEB_URL}/api/evaluate-batch",
        {"items": omitted, "common_limit_seconds": 1.0},
    )
    check("经 Web 反代统一限值结论一致", status == 200 and body == per_room_body, body)
    status, body = http(
        "POST",
        f"{WEB_URL}/api/evaluate-batch",
        {"items": omitted, "common_limit_seconds": 9.9},
    )
    check("经 Web 反代非法统一限值同样整批 400", status == 400, f"got {status}")

    # 经 Web 反代的批量链路可用，结论一致
    status, body = http("POST", f"{WEB_URL}/api/evaluate-batch", {"items": [rooms[0]]})
    proxied = json.loads(body)
    check(
        "经 Web 反代批量结论一致",
        status == 200 and proxied["items"][0]["t20_seconds"] == exp_t20,
        body,
    )
    status, body = http(
        "POST",
        f"{WEB_URL}/api/evaluate-batch",
        {"items": [rooms[0], {**rooms[1], "room_id": "ROOM-OK-1"}]},
    )
    check("经 Web 反代重复 room_id 同样整批拒绝", status == 400, f"got {status}")

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
