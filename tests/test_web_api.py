from __future__ import annotations

import importlib
from urllib.parse import urlencode

import pytest
from quart import Quart

from tests.astrbot_stubs import (
    FakeContext,
    install_astrbot_stubs,
    unload_plugin_main,
)


@pytest.fixture
async def api_plugin(tmp_path):
    install_astrbot_stubs(tmp_path)
    unload_plugin_main()
    module = importlib.import_module("astrbot_plugin_memory_cards.main")
    context = FakeContext()
    plugin = module.MemoryCardsPlugin(context, {"enabled": True})
    await plugin.initialize()
    await plugin.store.upsert_user("p\x1fu1", "p", "u1", "Alice")
    await plugin.store.upsert_user("p\x1fu2", "p", "u2", "Bob")
    yield plugin, context
    await plugin.terminate()


async def call_handler(
    app: Quart,
    handler,
    *,
    path: str = "/",
    method: str = "GET",
    json: dict | None = None,
) -> tuple[int, dict]:
    async with app.test_request_context(path, method=method, json=json):
        response = await app.make_response(await handler())
        return response.status_code, await response.get_json()


def test_all_web_api_routes_are_namespaced(api_plugin) -> None:
    _, context = api_plugin
    routes = {route for route, _, _, _ in context.routes}
    assert routes == {
        "/astrbot_plugin_memory_cards/memory/users",
        "/astrbot_plugin_memory_cards/memory/notes",
        "/astrbot_plugin_memory_cards/memory/notes/create",
        "/astrbot_plugin_memory_cards/memory/notes/update",
        "/astrbot_plugin_memory_cards/memory/notes/delete",
    }


@pytest.mark.asyncio
async def test_users_and_filtered_notes(api_plugin) -> None:
    plugin, _ = api_plugin
    app = Quart(__name__)
    await plugin.store.create_note("p\x1fu1", "偏好", "喜欢简洁回答")
    await plugin.store.create_note("p\x1fu1", "目标", "完成插件")

    status, users = await call_handler(app, plugin.api_users)
    assert status == 200
    assert users["ok"] is True
    assert "data" not in users
    assert users["items"][0]["scope_key"] in {"p\x1fu1", "p\x1fu2"}
    assert users["items"][0]["pending_message_count"] == 0

    status, notes = await call_handler(
        app,
        plugin.api_notes,
        path="/?"
        + urlencode(
            {
                "scope_key": "p\x1fu1",
                "category": "偏好",
                "keyword": "简洁",
            }
        ),
    )
    assert status == 200
    assert notes["total"] == 1
    assert "data" not in notes
    assert notes["items"][0]["category"] == "偏好"
    assert notes["items"][0]["source"] == "manual"


@pytest.mark.asyncio
async def test_create_update_and_delete_note(api_plugin) -> None:
    plugin, _ = api_plugin
    app = Quart(__name__)

    status, created = await call_handler(
        app,
        plugin.api_create_note,
        method="POST",
        json={"scope_key": "p\x1fu1", "category": "待办", "content": "完成测试"},
    )
    assert status == 200
    assert "data" not in created
    note_id = created["note"]["id"]

    status, updated = await call_handler(
        app,
        plugin.api_update_note,
        method="POST",
        json={
            "scope_key": "p\x1fu1",
            "id": note_id,
            "category": "目标",
            "content": "完成发布",
        },
    )
    assert status == 200
    assert updated["note"]["content"] == "完成发布"

    status, deleted = await call_handler(
        app,
        plugin.api_delete_note,
        method="POST",
        json={"scope_key": "p\x1fu1", "id": note_id},
    )
    assert status == 200
    assert deleted["ok"] is True


@pytest.mark.asyncio
async def test_api_rejects_invalid_and_cross_scope_requests(api_plugin) -> None:
    plugin, _ = api_plugin
    app = Quart(__name__)
    note = await plugin.store.create_note("p\x1fu1", "待办", "私有内容")

    status, invalid = await call_handler(
        app,
        plugin.api_create_note,
        method="POST",
        json={"scope_key": "p\x1fu1", "category": "偏好", "content": " "},
    )
    assert status == 200
    assert invalid["status"] == "error"
    assert invalid["code"] == 400
    assert "不能为空" in invalid["message"]

    status, missing = await call_handler(
        app,
        plugin.api_create_note,
        method="POST",
        json={"scope_key": "missing", "category": "其他", "content": "内容"},
    )
    assert status == 200
    assert missing["status"] == "error"
    assert missing["code"] == 404
    assert missing["message"] == "用户不存在"

    status, crossed = await call_handler(
        app,
        plugin.api_update_note,
        method="POST",
        json={
            "scope_key": "p\x1fu2",
            "id": note.id,
            "category": "目标",
            "content": "越权修改",
        },
    )
    assert status == 200
    assert crossed["status"] == "error"
    assert crossed["code"] == 404
    assert "不存在" in crossed["message"]


@pytest.mark.asyncio
async def test_inactive_store_returns_503(api_plugin) -> None:
    plugin, _ = api_plugin
    app = Quart(__name__)
    plugin._active = False

    status, body = await call_handler(app, plugin.api_users)

    assert status == 200
    assert body["ok"] is False
    assert body["status"] == "error"
    assert body["code"] == 503
    assert "未就绪" in body["message"]
