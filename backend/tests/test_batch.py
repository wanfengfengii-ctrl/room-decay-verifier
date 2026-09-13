"""批量复核 /api/evaluate-batch 契约测试。

核心不变量：
- 正常项证据与 /api/evaluate 单独提交完全一致；
- items 严格保持输入顺序；
- 衰减异常项只暴露拒绝原因；
- 字段错误项携带 room_id（缺失时为 null）与可定位字段说明；
- 无法解析 / 结构不符 / room_id 重复 / 数量越界一律请求级 400。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app
from app.t20 import REASON_NON_NEGATIVE_SLOPE

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


# ---------- 正常项与单次入口一致 ----------


def test_normal_item_evidence_identical_to_single_submission():
    room = valid_room("R-001")
    batch_resp = post_batch({"items": [room]})
    assert batch_resp.status_code == 200, batch_resp.text
    batch_item = batch_resp.json()["items"][0]

    single = client.post("/api/evaluate", json={k: v for k, v in room.items() if k != "room_id"})
    assert single.status_code == 200
    single_body = single.json()

    assert batch_item["status"] == "ok"
    for key, value in single_body.items():
        assert batch_item[key] == value, f"{key} 与单次提交不一致"
    assert batch_item["room_id"] == "R-001"
    # 成功项证据字段集合固定，不允许多余字段
    assert set(batch_item.keys()) == {"room_id", *single_body.keys()}


def test_every_normal_item_matches_its_own_single_submission():
    rooms = [
        valid_room("A", limit_seconds=1.0),
        valid_room("B", limit_seconds=0.3),  # 同一采样、更低上限 -> 不合格
        valid_room("C", **{"sample_interval_ms": 2, "pressure": make_decay(2.1, dt_ms=2, n=4000)}),
    ]
    body = post_batch({"items": rooms}).json()
    for room, item in zip(rooms, body["items"]):
        single = client.post(
            "/api/evaluate",
            json={k: v for k, v in room.items() if k != "room_id"},
        ).json()
        assert item["status"] == "ok"
        assert item["room_id"] == room["room_id"]
        for key, value in single.items():
            assert item[key] == value
    assert [i["passed"] for i in body["items"]] == [True, False, True]


# ---------- 顺序与混合批次 ----------


def test_mixed_batch_order_statuses_and_summary():
    rooms = [
        valid_room("PASS-1"),
        valid_room("REJECT-1", pressure=make_rising()),
        valid_room("BAD-1", sample_interval_ms=0),
        valid_room("PASS-2", limit_seconds=0.3),
        valid_room("REJECT-2", pressure=[5.0] * 1000),
        valid_room("BAD-2", pressure=[1.0] * 199),
    ]
    resp = post_batch({"items": rooms})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert [item["room_id"] for item in body["items"]] == [
        "PASS-1",
        "REJECT-1",
        "BAD-1",
        "PASS-2",
        "REJECT-2",
        "BAD-2",
    ]
    assert [item["status"] for item in body["items"]] == [
        "ok",
        "rejected",
        "invalid",
        "ok",
        "rejected",
        "invalid",
    ]
    assert body["summary"] == {
        "total": 6,
        "ok": 2,
        "passed": 1,
        "failed": 1,
        "rejected": 2,
        "invalid": 2,
    }


def test_rejected_item_contains_only_reason():
    body = post_batch({"items": [valid_room("R-X", pressure=make_rising())]}).json()
    item = body["items"][0]
    assert item == {"room_id": "R-X", "status": "rejected", "reason": REASON_NON_NEGATIVE_SLOPE}
    for leaked in ("t20_seconds", "slope", "points_used", "background", "peak_index"):
        assert leaked not in item


def test_invalid_items_carry_locatable_field_errors():
    body = post_batch(
        {
            "items": [
                valid_room("E-INTERVAL", sample_interval_ms=101),
                valid_room("E-LIMIT", limit_seconds=5.01),
                valid_room("E-PRESSURE-TYPE", pressure=["x"] * 200),
                valid_room("E-PRESSURE-NEG", pressure=[1.0] * 199 + [-1.0]),
            ]
        }
    ).json()
    fields = [[e["field"] for e in item["errors"]] for item in body["items"]]
    assert fields == [
        ["sample_interval_ms"],
        ["limit_seconds"],
        ["pressure"],
        ["pressure"],
    ]
    for item in body["items"]:
        assert item["status"] == "invalid"
        assert item["room_id"].startswith("E-")
        assert all(e["message"] for e in item["errors"])
    assert [item["index"] for item in body["items"]] == [0, 1, 2, 3]


def test_missing_room_id_marked_invalid_with_null_room_id_but_others_still_run():
    rooms = [
        {k: v for k, v in valid_room("OK-1").items()},
        {k: v for k, v in valid_room("MISSING-ID").items() if k != "room_id"},
        valid_room("OK-2"),
    ]
    body = post_batch({"items": rooms}).json()
    assert [i["status"] for i in body["items"]] == ["ok", "invalid", "ok"]
    missing = body["items"][1]
    assert missing["room_id"] is None
    assert missing["index"] == 1
    assert any(e["field"] == "room_id" for e in missing["errors"])
    # 错误行不得遮蔽其余正常结果
    assert body["items"][0]["room_id"] == "OK-1"
    assert body["items"][2]["room_id"] == "OK-2"
    assert body["summary"]["ok"] == 2


def test_blank_and_non_string_room_ids_are_invalid_not_request_errors():
    rooms = [
        valid_room("   "),
        {**valid_room("ignored"), "room_id": 42},
    ]
    body = post_batch({"items": rooms}).json()
    assert [i["status"] for i in body["items"]] == ["invalid", "invalid"]
    assert body["items"][1]["room_id"] is None
    assert any("room_id" in e["field"] for e in body["items"][1]["errors"])


# ---------- 房间标识错误定位 ----------


def test_room_id_over_100_chars_still_evaluated():
    """超过 100 字符的非空标识不是字段错误：照常复核并返回该房间结论。"""
    long_id = "R" * 150
    room = valid_room(long_id)
    resp = post_batch({"items": [room]})
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["status"] == "ok"
    assert item["room_id"] == long_id
    single = client.post(
        "/api/evaluate", json={k: v for k, v in room.items() if k != "room_id"}
    ).json()
    for key, value in single.items():
        assert item[key] == value, f"{key} 与单次提交不一致"


def test_room_id_length_has_no_upper_bound():
    rooms = [valid_room("B" * 100), valid_room("B" * 101)]
    body = post_batch({"items": rooms}).json()
    assert [i["status"] for i in body["items"]] == ["ok", "ok"]


def test_non_string_room_id_is_type_error_with_null_room_id_and_index():
    """数字标识：room_id 为 null，凭 index 定位，字段错误明确指出类型不符。"""
    body = post_batch(
        {"items": [valid_room("OK-1"), {**valid_room("ignored"), "room_id": 42}]}
    ).json()
    assert [i["status"] for i in body["items"]] == ["ok", "invalid"]
    bad = body["items"][1]
    assert bad["room_id"] is None
    assert bad["index"] == 1
    id_errors = [e for e in bad["errors"] if e["field"] == "room_id"]
    assert id_errors and "字符串" in id_errors[0]["message"]
    # 错误行不遮蔽其余房间
    assert body["items"][0]["room_id"] == "OK-1"


def test_blank_room_id_invalid_item_keeps_position_and_blank_detail():
    """空白标识：逐项 invalid，凭 index 定位，字段错误指出空白原因。"""
    body = post_batch({"items": [valid_room("OK-1"), valid_room("   ")]}).json()
    assert [i["status"] for i in body["items"]] == ["ok", "invalid"]
    bad = body["items"][1]
    assert bad["index"] == 1
    # 原始空白标识原样回传，便于界面区分“空白”与“缺失/类型错误”
    assert bad["room_id"] == "   "
    id_errors = [e for e in bad["errors"] if e["field"] == "room_id"]
    assert id_errors and "空白" in id_errors[0]["message"]


def test_non_object_array_element_is_invalid():
    body = post_batch({"items": [42, "oops", None, valid_room("OK")]}).json()
    assert [i["status"] for i in body["items"]] == ["invalid", "invalid", "invalid", "ok"]
    assert [i["index"] for i in body["items"][:3]] == [0, 1, 2]
    for bad in body["items"][:3]:
        assert bad["room_id"] is None
        assert bad["errors"]


# ---------- 请求级错误 ----------


def test_unparseable_json_is_request_error():
    resp = post_batch(b'{"items": [')
    assert resp.status_code == 400
    assert "JSON" in resp.json()["detail"]


def test_missing_items_array_is_request_error():
    resp = post_batch({"rooms": []})
    assert resp.status_code == 400
    resp = post_batch([valid_room("X")])
    assert resp.status_code == 400


def test_empty_and_oversized_batches_are_request_errors():
    assert post_batch({"items": []}).status_code == 400
    assert post_batch({"items": [valid_room(f"R{i}") for i in range(21)]}).status_code == 400


def test_boundary_sizes_accepted():
    assert post_batch({"items": [valid_room("ONE")]}).status_code == 200
    rooms = [valid_room(f"R{i:02d}", limit_seconds=5.0) for i in range(20)]
    assert post_batch({"items": rooms}).status_code == 200


def test_duplicate_room_id_rejects_whole_batch():
    resp = post_batch({"items": [valid_room("DUP"), valid_room("DUP")]})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "DUP" in detail and "重复" in detail


def test_duplicate_room_id_rejected_even_when_other_rows_broken():
    # 重复检测先于逐项校验：重复即整批拒绝，不产生任何逐项结论
    resp = post_batch(
        {"items": [valid_room("DUP", sample_interval_ms=0), valid_room("DUP")]}
    )
    assert resp.status_code == 400


def test_duplicate_check_does_not_mistake_non_string_ids():
    # 非字符串 room_id 走逐项 invalid，不参与重复判定
    rooms = [{**valid_room("x"), "room_id": 1}, {**valid_room("y"), "room_id": 1}]
    body = post_batch({"items": rooms}).json()
    assert [i["status"] for i in body["items"]] == ["invalid", "invalid"]


def test_duplicate_check_does_not_mistake_blank_ids():
    # 两个空白 ID 是各自格式错误，不构成“真实房间重名”的整批拒绝
    body = post_batch({"items": [valid_room(" "), valid_room("  ")]}).json()
    assert [i["status"] for i in body["items"]] == ["invalid", "invalid"]


# ---------- 既有入口不受影响 ----------


def test_single_endpoint_payload_shape_unchanged():
    resp = client.post(
        "/api/evaluate",
        json={
            "sample_interval_ms": 1,
            "pressure": make_decay(),
            "limit_seconds": 1.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
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
    }
    assert 0.0 <= body["r_squared"] <= 1.0
    assert body["fit_quality"] in {"stable", "needs_review"}
