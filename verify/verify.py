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

    # 衰减异常项仍只含拒绝原因（多一个 room_id）
    rejected_item = batch["items"][2]
    check(
        "批量异常项只暴露 room_id/status/reason",
        set(rejected_item.keys()) == {"room_id", "status", "reason"} and bool(rejected_item["reason"]),
        json.dumps(rejected_item, ensure_ascii=False),
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
