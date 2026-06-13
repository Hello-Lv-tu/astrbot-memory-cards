from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

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


@pytest.mark.asyncio
async def test_v1_database_migrates_notes_to_manual_source(tmp_path) -> None:
    path = tmp_path / "memory.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (version INTEGER NOT NULL);
        INSERT INTO schema_meta(version) VALUES (1);
        CREATE TABLE users (
            scope_key TEXT PRIMARY KEY,
            platform_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_key TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO users VALUES ('p' || char(31) || 'u', 'p', 'u', 'Alice', 'now');
        INSERT INTO notes(scope_key, category, content, created_at, updated_at)
        VALUES ('p' || char(31) || 'u', '偏好', '喜欢安静', 'now', 'now');
        """
    )
    connection.commit()
    connection.close()

    from astrbot_plugin_memory_cards.store import MemoryStore

    migrated = MemoryStore(path)
    await migrated.open()
    notes, _ = await migrated.list_notes("p\x1fu")

    assert notes[0].source == "manual"
    assert notes[0].source_batch_id is None
    await migrated.close()


@pytest.mark.asyncio
async def test_buffer_claim_complete_and_new_messages(store) -> None:
    scope = "p\x1fu"
    await store.upsert_user(scope, "p", "u", "Alice")
    now = datetime.now(UTC)
    await store.append_buffer_message(scope, "user", "我喜欢安静", "provider-a", now=now)
    await store.append_buffer_message(
        scope,
        "assistant",
        "我记住了",
        "provider-a",
        now=now + timedelta(seconds=1),
    )

    status = await store.get_extraction_status(scope)
    assert status.pending_count == 2

    batch = await store.claim_extraction_batch(
        scope,
        message_threshold=2,
        idle_before=None,
        now=now + timedelta(seconds=2),
    )
    assert batch is not None
    assert [message.role for message in batch.messages] == ["user", "assistant"]
    assert await store.claim_extraction_batch(
        scope,
        message_threshold=2,
        idle_before=None,
        now=now + timedelta(seconds=2),
    ) is None

    await store.append_buffer_message(
        scope,
        "user",
        "这是下一轮",
        "provider-a",
        now=now + timedelta(seconds=3),
    )
    await store.complete_extraction_batch(scope, batch.batch_id)
    status = await store.get_extraction_status(scope)
    assert status.pending_count == 1


@pytest.mark.asyncio
async def test_failed_batch_is_released_with_retry(store) -> None:
    scope = "p\x1fu"
    await store.upsert_user(scope, "p", "u", "Alice")
    now = datetime.now(UTC)
    await store.append_buffer_message(scope, "user", "记住这个", "provider-a", now=now)
    batch = await store.claim_extraction_batch(
        scope,
        message_threshold=1,
        idle_before=None,
        now=now,
    )
    assert batch is not None

    retry_at = now + timedelta(minutes=10)
    await store.fail_extraction_batch(scope, batch.batch_id, "模型失败", retry_at)
    status = await store.get_extraction_status(scope)

    assert status.pending_count == 1
    assert status.next_retry_at == retry_at.isoformat(timespec="microseconds")
    assert status.last_error == "模型失败"
