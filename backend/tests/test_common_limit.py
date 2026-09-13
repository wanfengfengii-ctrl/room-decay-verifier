"""批量复核顶层统一限值 common_limit_seconds 的契约测试。

核心不变量：
- 统一限值存在时条目可省略 limit_seconds，两处同时存在以顶层值为准；
- 同一批采样以逐房间限值与等值统一限值提交，逐行证据、顺序与汇总一致；
- 统一限值非法（字符串 / 布尔 / 非有限数 / 越界）整批 400，消息固定；
- 未启用统一限值时缺少条目限值仍是该行字段错误；
- 统一限值只改变有效房间的限值与判定，衰减异常与字段错误行原地不动。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import COMMON_LIMIT_ERROR_MESSAGE, app

client = TestClient(app)


def make_decay(
    t60: float = 1.5,
    dt_ms: int = 1,
    n: int = 5000,
    offset: float = 0.25,
) -> list[float]:
    dt = dt_ms / 1000.0
    return [1000.0 * 10.0 ** (-3.0 * (i * dt) / t60) + offset for i in range(n)]


def make_rising() -> list[float]:
    rising = [10.0 ** ((-30.0 + 28.0 * i / 999.0) / 20.0) for i in range(1000)]
    return [1.0] + rising + [1e-9] * 200


def valid_room(room_id: str, **overrides) -> dict:
    payload = {
        "room_id": room_id,
        "sample_interval_ms": 1,
        "pressure": make_decay(),
        "limit_seconds": 1.0,
    }
    payload.update(overrides)
    return payload


def without_limit(room: dict) -> dict:
    return {k: v for k, v in room.items() if k != "limit_seconds"}


def post_batch(raw_body: bytes | dict | list):
    if isinstance(raw_body, bytes):
        content = raw_body
    else:
        content = json.dumps(raw_body).encode()
    return client.post(
        "/api/evaluate-batch",
        content=content,
        headers={"Content-Type": "application/json"},
    )


def mixed_rooms() -> list[dict]:
    """正常 / 衰减异常 / 字段错误各一间，限值均为 1.0。"""
    return [
        valid_room("U-OK"),
        valid_room("U-REJECTED", pressure=make_rising()),
        valid_room("U-INVALID", sample_interval_ms=0),
    ]


# ---------- 统一限值与逐房间限值等效 ----------


def test_common_limit_equals_per_room_limits_byte_for_byte():
    """同一批采样：逐房间限值与等值统一限值的响应逐字节一致（证据、顺序、汇总）。"""
    rooms = mixed_rooms()
    per_room = post_batch({"items": rooms})
    assert per_room.status_code == 200, per_room.text

    omitted = [without_limit(r) for r in rooms]
    common = post_batch({"items": omitted, "common_limit_seconds": 1.0})
    assert common.status_code == 200, common.text

    assert common.content == per_room.content


def test_common_limit_allows_items_to_omit_limit_seconds():
    rooms = [without_limit(valid_room("A101")), without_limit(valid_room("A102"))]
    resp = post_batch({"items": rooms, "common_limit_seconds": 0.3})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [i["status"] for i in body["items"]] == ["ok", "ok"]
    # 逐行回显实际采用的限值（顶层统一限值）
    assert [i["limit_seconds"] for i in body["items"]] == [0.3, 0.3]
    # T20 为 0.5，超过 0.3，全批不合格
    assert [i["passed"] for i in body["items"]] == [False, False]
    assert body["summary"] == {
        "total": 2,
        "ok": 2,
        "passed": 0,
        "failed": 2,
        "rejected": 0,
        "invalid": 0,
    }


def test_common_limit_ignores_stale_invalid_item_limits():
    """启用统一限值时，条目残留的无效旧限值不再判为字段错误，按全批限值正常复核。"""
    rooms = [
        {**valid_room("STALE-STR"), "limit_seconds": "去年填的"},
        {**valid_room("STALE-RANGE"), "limit_seconds": 99.0},
        {**valid_room("STALE-BOOL"), "limit_seconds": True},
        {**valid_room("STALE-NULL"), "limit_seconds": None},
        without_limit(valid_room("CLEAN")),
    ]
    resp = post_batch({"items": rooms, "common_limit_seconds": 1.0})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [i["status"] for i in body["items"]] == ["ok"] * 5
    assert [i["limit_seconds"] for i in body["items"]] == [1.0] * 5
    assert body["summary"] == {
        "total": 5,
        "ok": 5,
        "passed": 5,
        "failed": 0,
        "rejected": 0,
        "invalid": 0,
    }
    # 与干净条目（省略限值）的响应逐字节一致：旧限值不留任何痕迹
    clean = post_batch(
        {"items": [without_limit(r) for r in rooms], "common_limit_seconds": 1.0}
    )
    assert resp.content == clean.content


def test_stale_invalid_item_limits_still_error_without_common_limit():
    """未启用统一限值时，同样的无效限值仍是该行字段错误（行为不回归）。"""
    rooms = [
        {**valid_room("STALE-STR"), "limit_seconds": "去年填的"},
        {**valid_room("STALE-RANGE"), "limit_seconds": 99.0},
        valid_room("OK"),
    ]
    body = post_batch({"items": rooms}).json()
    assert [i["status"] for i in body["items"]] == ["invalid", "invalid", "ok"]
    for item in body["items"][:2]:
        assert any(e["field"] == "limit_seconds" for e in item["errors"])
    assert body["summary"]["ok"] == 1 and body["summary"]["invalid"] == 2


def test_top_level_common_limit_wins_when_item_also_has_limit():
    """两处同时存在限值时，明确以顶层统一限值为准。"""
    rooms = [valid_room("R-ITEM-LOW", limit_seconds=0.3)]  # 条目限值本会导致不合格
    resp = post_batch({"items": rooms, "common_limit_seconds": 5.0})
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["status"] == "ok"
    assert item["limit_seconds"] == 5.0
    assert item["passed"] is True

    # 与单独提交 5.0 限值的证据逐字段一致
    single = client.post(
        "/api/evaluate",
        json={
            "sample_interval_ms": 1,
            "pressure": rooms[0]["pressure"],
            "limit_seconds": 5.0,
        },
    ).json()
    for key, value in single.items():
        assert item[key] == value


def test_common_limit_change_only_alters_ok_rows():
    """统一限值改变时，只改变有效房间的限值与判定；异常与错误行原地不动。"""
    rooms = [without_limit(r) for r in mixed_rooms()]
    low = post_batch({"items": rooms, "common_limit_seconds": 0.3}).json()
    high = post_batch({"items": rooms, "common_limit_seconds": 5.0}).json()

    ok_low, ok_high = low["items"][0], high["items"][0]
    assert ok_low["limit_seconds"] == 0.3 and ok_low["passed"] is False
    assert ok_high["limit_seconds"] == 5.0 and ok_high["passed"] is True
    # 限值之外的计算证据不随统一限值改变
    for key in ("t20_seconds", "points_used", "slope", "background", "peak_index"):
        assert ok_low[key] == ok_high[key]

    # 衰减异常与字段错误行完全一致（状态、位置、内容均不变）
    assert low["items"][1] == high["items"][1]
    assert low["items"][1]["status"] == "rejected"
    assert low["items"][2] == high["items"][2]
    assert low["items"][2]["status"] == "invalid"

    # 汇总随有效房间的判定更新
    assert low["summary"] == {
        "total": 3,
        "ok": 1,
        "passed": 0,
        "failed": 1,
        "rejected": 1,
        "invalid": 1,
    }
    assert high["summary"] == {
        "total": 3,
        "ok": 1,
        "passed": 1,
        "failed": 0,
        "rejected": 1,
        "invalid": 1,
    }


def test_common_limit_boundary_values_accepted():
    for limit in (0.3, 5.0):
        resp = post_batch(
            {"items": [without_limit(valid_room("BOUND"))], "common_limit_seconds": limit}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"][0]["limit_seconds"] == limit


def test_common_limit_null_means_not_enabled():
    """显式 null 视为未启用：完全按各条目原值处理。"""
    resp = post_batch({"items": [valid_room("R", limit_seconds=1.0)], "common_limit_seconds": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["limit_seconds"] == 1.0
    # 缺少条目限值仍按该行字段错误处理
    resp = post_batch(
        {"items": [without_limit(valid_room("R"))], "common_limit_seconds": None}
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["status"] == "invalid"
    assert any(e["field"] == "limit_seconds" for e in item["errors"])


# ---------- 非法统一限值：整批 400 ----------


def test_invalid_common_limits_reject_whole_batch():
    for bad in ("1.0", True, False, 0.29, 5.01, -1.0, [1.0], {"v": 1.0}):
        resp = post_batch({"items": [valid_room("R")], "common_limit_seconds": bad})
        assert resp.status_code == 400, f"{bad!r} 应整批拒绝：{resp.text}"
        assert resp.json()["detail"] == COMMON_LIMIT_ERROR_MESSAGE
        assert resp.json()["detail"] == "统一限值必须是0.30至5.00的JSON数字"


def test_non_finite_common_limits_reject_whole_batch():
    """NaN / Infinity 以原始字节提交（标准 JSON 之外的数值）。"""
    room = json.dumps(valid_room("R"))
    for token in (b"NaN", b"Infinity", b"-Infinity"):
        body = b'{"items": [' + room.encode() + b'], "common_limit_seconds": ' + token + b"}"
        resp = post_batch(body)
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == "统一限值必须是0.30至5.00的JSON数字"


def test_invalid_common_limit_produces_no_item_results():
    """整批拒绝：响应不含任何逐项结论，即使条目本身合法。"""
    resp = post_batch(
        {"items": [valid_room("R1"), valid_room("R2")], "common_limit_seconds": "oops"}
    )
    assert resp.status_code == 400
    assert "items" not in resp.json()


# ---------- 未启用统一限值时的条目限值缺失 ----------


def test_missing_item_limit_without_common_limit_is_row_field_error():
    rooms = [valid_room("OK-1"), without_limit(valid_room("NO-LIMIT")), valid_room("OK-2")]
    resp = post_batch({"items": rooms})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [i["status"] for i in body["items"]] == ["ok", "invalid", "ok"]
    bad = body["items"][1]
    assert bad["room_id"] == "NO-LIMIT"
    assert bad["index"] == 1
    assert bad["errors"] == [{"field": "limit_seconds", "message": "字段缺失"}]
    # 错误行不遮蔽其余房间
    assert body["summary"]["ok"] == 2


def test_missing_item_limit_alongside_other_field_errors():
    """缺少限值且另有字段错误时，两类错误同时可定位。"""
    room = without_limit(valid_room("MULTI", sample_interval_ms=0))
    body = post_batch({"items": [room]}).json()
    item = body["items"][0]
    assert item["status"] == "invalid"
    fields = [e["field"] for e in item["errors"]]
    assert "sample_interval_ms" in fields
    assert "limit_seconds" in fields


def test_explicit_null_item_limit_treated_as_missing():
    room = {**valid_room("NULL-LIMIT"), "limit_seconds": None}
    body = post_batch({"items": [room]}).json()
    item = body["items"][0]
    assert item["status"] == "invalid"
    assert any(e["field"] == "limit_seconds" for e in item["errors"])
    # 启用统一限值时，显式 null 与缺省同等由顶层值兜底
    body = post_batch({"items": [room], "common_limit_seconds": 1.0}).json()
    item = body["items"][0]
    assert item["status"] == "ok"
    assert item["limit_seconds"] == 1.0


# ---------- 旧请求兼容 ----------


def test_request_without_common_limit_unchanged():
    """不含 common_limit_seconds 的旧请求：完全按各条目原值处理。"""
    rooms = [valid_room("OLD-1", limit_seconds=1.0), valid_room("OLD-2", limit_seconds=0.3)]
    resp = post_batch({"items": rooms})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [i["limit_seconds"] for i in body["items"]] == [1.0, 0.3]
    assert [i["passed"] for i in body["items"]] == [True, False]
    assert body["summary"]["passed"] == 1 and body["summary"]["failed"] == 1
