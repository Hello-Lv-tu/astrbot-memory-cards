from __future__ import annotations

import asyncio

import pytest

from astrbot_plugin_memory_cards.models import MAX_CONTENT_LENGTH
from astrbot_plugin_memory_cards.store import MemoryStore


@pytest.fixture
async def store(tmp_path):
    instance = MemoryStore(tmp_path / "memory.db")
    await instance.open()
    yield instance
    await instance.close()


@pytest.mark.asyncio
async def test_notes_persist_and_remain_scoped(tmp_path) -> None:
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    await store.open()
    await store.upsert_user("p1\x1fu1", "p1", "u1", "Alice")
    note = await store.create_note("p1\x1fu1", "偏好", "喜欢简洁回答")

    notes, total = await store.list_notes("p1\x1fu1")
    assert total == 1
    assert [item.content for item in notes] == ["喜欢简洁回答"]
    assert await store.list_notes("p1\x1fu2") == ([], 0)
    await store.close()

    reopened = MemoryStore(path)
    await reopened.open()
    persisted = await reopened.get_note("p1\x1fu1", note.id)
    assert persisted is not None
    assert persisted.content == "喜欢简洁回答"
    await reopened.close()


@pytest.mark.asyncio
async def test_user_list_includes_note_counts_and_recent_order(store) -> None:
    await store.upsert_user("p\x1fu1", "p", "u1", "Alice")
    await store.upsert_user("p\x1fu2", "p", "u2", "Bob")
    await store.create_note("p\x1fu1", "目标", "完成项目")
    await store.create_note("p\x1fu1", "偏好", "喜欢中文")

    users = await store.list_users()

    by_scope = {user.scope_key: user for user in users}
    assert by_scope["p\x1fu1"].note_count == 2
    assert by_scope["p\x1fu2"].note_count == 0


@pytest.mark.asyncio
async def test_search_category_and_pagination(store) -> None:
    scope = "p\x1fu"
    await store.upsert_user(scope, "p", "u", "Alice")
    await store.create_note(scope, "偏好", "喜欢简洁回答")
    await store.create_note(scope, "目标", "完成 AstrBot 插件")
    await store.create_note(scope, "偏好", "喜欢浅色界面")

    notes, total = await store.list_notes(
        scope,
        keyword="喜欢",
        category="偏好",
        limit=1,
        offset=1,
    )

    assert total == 2
    assert len(notes) == 1
    assert notes[0].category == "偏好"


@pytest.mark.asyncio
async def test_update_and_delete_require_matching_scope(store) -> None:
    await store.upsert_user("p\x1fu1", "p", "u1", "Alice")
    await store.upsert_user("p\x1fu2", "p", "u2", "Bob")
    note = await store.create_note("p\x1fu1", "待办", "旧内容")

    assert await store.update_note("p\x1fu2", note.id, "目标", "越权") is None
    assert await store.delete_note("p\x1fu2", note.id) is False

    updated = await store.update_note("p\x1fu1", note.id, "目标", "新内容")
    assert updated is not None
    assert updated.category == "目标"
    assert updated.content == "新内容"
    assert await store.delete_note("p\x1fu1", note.id) is True
    assert await store.get_note("p\x1fu1", note.id) is None


@pytest.mark.asyncio
async def test_content_validation_and_category_fallback(store) -> None:
    scope = "p\x1fu"
    await store.upsert_user(scope, "p", "u", "Alice")

    with pytest.raises(ValueError, match="不能为空"):
        await store.create_note(scope, "偏好", "   ")
    with pytest.raises(ValueError, match="2000"):
        await store.create_note(scope, "偏好", "x" * (MAX_CONTENT_LENGTH + 1))

    note = await store.create_note(scope, "未知分类", "有效内容")
    assert note.category == "其他"
    assert note.content == "有效内容"


@pytest.mark.asyncio
async def test_unknown_user_cannot_receive_note(store) -> None:
    with pytest.raises(ValueError, match="用户不存在"):
        await store.create_note("missing\x1fuser", "其他", "内容")


@pytest.mark.asyncio
async def test_closed_store_rejects_operations(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    with pytest.raises(RuntimeError, match="未就绪"):
        await store.list_users()


@pytest.mark.asyncio
async def test_close_waits_for_in_flight_read(store) -> None:
    await store.upsert_user("p\x1fu", "p", "u", "Alice")
    await store._lock.acquire()
    read_task = asyncio.create_task(store.list_users())
    close_task = asyncio.create_task(store.close())
    await asyncio.sleep(0)

    assert not read_task.done()
    assert not close_task.done()

    store._lock.release()
    users = await read_task
    await close_task
    assert users[0].scope_key == "p\x1fu"


@pytest.mark.asyncio
async def test_retrieval_reads_more_than_ui_page_limit(store) -> None:
    scope = "p\x1fu"
    await store.upsert_user(scope, "p", "u", "Alice")
    for index in range(105):
        await store.create_note(scope, "其他", f"普通便签 {index}")

    notes = await store.list_notes_for_retrieval(scope)

    assert len(notes) == 105
