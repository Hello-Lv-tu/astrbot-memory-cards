from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from astrbot_plugin_memory_cards.models import MAX_CONTENT_LENGTH
from astrbot_plugin_memory_cards.store import MemoryStore, PreviewExpiredError


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
async def test_v2_database_migrates_to_v3_without_rewriting_notes(tmp_path) -> None:
    path = tmp_path / "memory.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_meta (version INTEGER NOT NULL);
        INSERT INTO schema_meta(version) VALUES (2);
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
            updated_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            source_batch_id TEXT
        );
        INSERT INTO users VALUES ('p' || char(31) || 'u', 'p', 'u', 'Alice', 'seen');
        INSERT INTO notes(
            id, scope_key, category, content, created_at, updated_at,
            source, source_batch_id
        )
        VALUES (
            42, 'p' || char(31) || 'u', '偏好', '用户喜欢低噪音环境',
            'created', 'updated', 'manual', NULL
        );
        """
    )
    connection.commit()
    connection.close()

    migrated = MemoryStore(path)
    await migrated.open()

    notes, total = await migrated.list_notes("pu")
    revisions = await migrated.list_note_revisions("pu")

    assert total == 1
    assert notes[0].id == 42
    assert notes[0].created_at == "created"
    assert notes[0].updated_at == "updated"
    assert notes[0].content == "用户喜欢低噪音环境"
    assert revisions == []
    await migrated.close()


@pytest.mark.asyncio
async def test_apply_update_records_revision_and_preserves_note_id(store) -> None:
    scope = "pu"
    await store.upsert_user(scope, "p", "u", "Alice")
    note = await store.create_note(scope, "偏好", "用户喜欢茶")

    updated = await store.apply_memory_operations(
        scope,
        [
            {
                "action": "update",
                "note_id": note.id,
                "category": "偏好",
                "content": "用户喜欢乌龙茶",
                "reason": "新信息更具体",
            }
        ],
        source_batch_id="batch-a",
    )

    assert [item.id for item in updated] == [note.id]
    assert updated[0].content == "用户喜欢乌龙茶"
    revisions = await store.list_note_revisions(scope)
    assert len(revisions) == 1
    assert revisions[0].note_id == note.id
    assert revisions[0].merged_note_id is None
    assert revisions[0].change_type == "update"
    assert revisions[0].before_content == "用户喜欢茶"
    assert revisions[0].source_batch_id == "batch-a"


@pytest.mark.asyncio
async def test_apply_merge_keeps_earliest_note_and_records_removed_note(store) -> None:
    scope = "pu"
    await store.upsert_user(scope, "p", "u", "Alice")
    first = await store.create_note(scope, "目标", "用户在准备考试")
    second = await store.create_note(scope, "目标", "用户备考英语")

    updated = await store.apply_memory_operations(
        scope,
        [
            {
                "action": "merge",
                "note_ids": [second.id, first.id],
                "category": "目标",
                "content": "用户在准备英语考试",
                "reason": "近义合并",
            }
        ],
        source_batch_id="batch-b",
    )

    notes, total = await store.list_notes(scope)
    assert total == 1
    assert notes[0].id == first.id
    assert updated[0].id == first.id
    assert notes[0].content == "用户在准备英语考试"
    revisions = await store.list_note_revisions(scope)
    revision_keys = {
        (item.note_id, item.merged_note_id, item.change_type)
        for item in revisions
    }
    assert revision_keys == {
        (first.id, None, "merge"),
        (first.id, second.id, "merge"),
    }


@pytest.mark.asyncio
async def test_apply_operations_rolls_back_on_invalid_cross_scope_note(store) -> None:
    await store.upsert_user("pu1", "p", "u1", "Alice")
    await store.upsert_user("pu2", "p", "u2", "Bob")
    own = await store.create_note("pu1", "偏好", "用户喜欢绿茶")
    other = await store.create_note("pu2", "偏好", "其他用户喜欢咖啡")

    with pytest.raises(ValueError, match="候选便签无效"):
        await store.apply_memory_operations(
            "pu1",
            [
                {
                    "action": "update",
                    "note_id": own.id,
                    "category": "偏好",
                    "content": "用户喜欢红茶",
                },
                {
                    "action": "update",
                    "note_id": other.id,
                    "category": "偏好",
                    "content": "越权内容",
                },
            ],
            source_batch_id="batch-c",
        )

    assert (await store.get_note("pu1", own.id)).content == "用户喜欢绿茶"
    assert await store.list_note_revisions("pu1") == []


@pytest.mark.asyncio
async def test_quality_preview_fingerprint_rejects_stale_apply(store) -> None:
    scope = "pu"
    await store.upsert_user(scope, "p", "u", "Alice")
    note = await store.create_note(scope, "偏好", "用户喜欢热咖啡")
    preview = await store.create_quality_preview(
        scope,
        [
            {
                "action": "update",
                "note_id": note.id,
                "category": "偏好",
                "content": "用户喜欢冰咖啡",
                "reason": "冲突更新",
            }
        ],
    )
    await store.create_note(scope, "目标", "用户计划学习 Python")

    with pytest.raises(PreviewExpiredError):
        await store.apply_quality_preview(scope, preview.preview_id)

    assert (await store.get_note(scope, note.id)).content == "用户喜欢热咖啡"


@pytest.mark.asyncio
async def test_buffer_claim_complete_and_new_messages(store) -> None:
    scope = "p\x1fu"
    await store.upsert_user(scope, "p", "u", "Alice")
    now = datetime.now(UTC)
    await store.append_buffer_message(
        scope, "user", "我喜欢安静", "provider-a", now=now
    )
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
