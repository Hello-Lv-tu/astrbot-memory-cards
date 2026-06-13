from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from tests.astrbot_stubs import (
    FakeContext,
    install_astrbot_stubs,
    unload_plugin_main,
)


class FakeEvent:
    def __init__(
        self,
        *,
        private: bool = True,
        platform_id: str = "platform",
        sender_id: str = "user",
        sender_name: str = "Alice",
        message: str = "回答简洁一点",
    ) -> None:
        self.private = private
        self.platform_id = platform_id
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.message = message
        self.unified_msg_origin = f"{platform_id}:{sender_id}"

    def is_private_chat(self) -> bool:
        return self.private

    def get_platform_id(self) -> str:
        return self.platform_id

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_sender_name(self) -> str:
        return self.sender_name

    def get_message_str(self) -> str:
        return self.message


class FakeRequest:
    def __init__(self, prompt: str = "回答简洁一点") -> None:
        self.prompt = prompt
        self.system_prompt = "stable"
        self.contexts = [{"role": "user", "content": "old"}]
        self.extra_user_content_parts = []


@pytest.fixture
async def plugin(tmp_path):
    install_astrbot_stubs(tmp_path)
    unload_plugin_main()
    module = importlib.import_module("astrbot_plugin_memory_cards.main")
    instance = module.MemoryCardsPlugin(
        FakeContext(),
        {
            "enabled": True,
            "max_injected_notes": 5,
            "max_injected_chars": 1500,
            "minimum_score": 3,
            "recall_fallback_enabled": True,
            "auto_extract_enabled": True,
            "auto_extract_message_threshold": 1,
            "auto_extract_idle_minutes": 30,
            "auto_extract_retry_minutes": 10,
            "auto_extract_max_notes": 5,
            "auto_extract_provider_id": "",
        },
    )
    await instance.initialize()
    yield instance
    await instance.terminate()


@pytest.mark.asyncio
async def test_private_request_gets_temporary_memory(plugin) -> None:
    event = FakeEvent()
    request = FakeRequest()
    await plugin.observe_private_user(event)
    await plugin.store.create_note(
        "platform\x1fuser",
        "偏好",
        "用户喜欢简洁直接的回答",
    )

    original_contexts = list(request.contexts)
    await plugin.inject_memory(event, request)

    assert request.system_prompt == "stable"
    assert request.contexts == original_contexts
    assert len(request.extra_user_content_parts) == 1
    assert request.extra_user_content_parts[0]._no_save is True
    assert "[偏好]" in request.extra_user_content_parts[0].text


@pytest.mark.asyncio
async def test_group_chat_is_ignored(plugin) -> None:
    event = FakeEvent(private=False)
    request = FakeRequest()

    await plugin.observe_private_user(event)
    await plugin.inject_memory(event, request)

    assert await plugin.store.list_users() == []
    assert request.extra_user_content_parts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform_id", "sender_id"),
    [("", "user"), ("platform", "")],
)
async def test_missing_identity_fails_closed(
    plugin,
    platform_id: str,
    sender_id: str,
) -> None:
    event = FakeEvent(platform_id=platform_id, sender_id=sender_id)
    request = FakeRequest()

    await plugin.observe_private_user(event)
    await plugin.inject_memory(event, request)

    assert await plugin.store.list_users() == []
    assert request.extra_user_content_parts == []


@pytest.mark.asyncio
async def test_disabled_or_unrelated_request_is_not_injected(plugin) -> None:
    event = FakeEvent(message="今天天气怎么样")
    await plugin.observe_private_user(event)
    await plugin.store.create_note("platform\x1fuser", "偏好", "喜欢简洁回答")

    unrelated = FakeRequest(prompt="今天天气怎么样")
    await plugin.inject_memory(event, unrelated)
    assert unrelated.extra_user_content_parts == []

    plugin.config["enabled"] = False
    matching = FakeRequest()
    await plugin.inject_memory(FakeEvent(), matching)
    assert matching.extra_user_content_parts == []


@pytest.mark.asyncio
async def test_store_failure_does_not_block_request(plugin) -> None:
    class BrokenStore:
        async def list_notes_for_retrieval(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

    await plugin.store.close()
    plugin.store = BrokenStore()
    request = FakeRequest()

    await plugin.inject_memory(FakeEvent(), request)

    assert request.extra_user_content_parts == []


@pytest.mark.asyncio
async def test_old_matching_note_beyond_first_hundred_is_injected(plugin) -> None:
    event = FakeEvent(message="我那只叫月饼的猫怎么样")
    await plugin.observe_private_user(event)
    scope = "platform\x1fuser"
    await plugin.store.create_note(scope, "人物", "用户的猫叫月饼")
    for index in range(105):
        await plugin.store.create_note(scope, "其他", f"最近普通便签 {index}")
    request = FakeRequest(prompt="我那只叫月饼的猫怎么样")

    await plugin.inject_memory(event, request)

    assert len(request.extra_user_content_parts) == 1
    assert "用户的猫叫月饼" in request.extra_user_content_parts[0].text


@pytest.mark.asyncio
async def test_private_messages_and_final_reply_are_buffered(plugin) -> None:
    event = FakeEvent(message="我喜欢安静")
    await plugin.observe_private_user(event)
    await plugin.buffer_final_reply(
        event,
        SimpleNamespace(),
        SimpleNamespace(role="assistant", completion_text="好的"),
    )

    status = await plugin.store.get_extraction_status("platform\x1fuser")
    assert status.pending_count == 2


@pytest.mark.asyncio
async def test_extraction_creates_auto_note_and_skips_duplicate(plugin) -> None:
    event = FakeEvent(message="我喜欢安静")
    await plugin.observe_private_user(event)
    await plugin.buffer_final_reply(
        event,
        SimpleNamespace(),
        SimpleNamespace(role="assistant", completion_text="好的"),
    )
    plugin.context.llm_responses.append(
        SimpleNamespace(
            completion_text=(
                '{"memories":[{"action":"create","category":"偏好",'
                '"content":"用户喜欢安静"}]}'
            )
        )
    )

    await plugin.process_extraction_scope("platform\x1fuser")
    notes, _ = await plugin.store.list_notes("platform\x1fuser")
    assert len(notes) == 1
    assert notes[0].source == "auto"

    await plugin.store.append_buffer_message(
        "platform\x1fuser", "user", "我还是喜欢安静", "provider-a"
    )
    plugin.context.llm_responses.append(
        SimpleNamespace(
            completion_text=(
                '{"memories":[{"action":"create","category":"偏好",'
                '"content":"用户喜欢安静"}]}'
            )
        )
    )
    await plugin.process_extraction_scope("platform\x1fuser")
    notes, _ = await plugin.store.list_notes("platform\x1fuser")
    assert len(notes) == 1


@pytest.mark.asyncio
async def test_extraction_uses_configured_provider_and_retries_failure(plugin) -> None:
    event = FakeEvent(message="我在准备考试")
    await plugin.observe_private_user(event)
    plugin.config["auto_extract_provider_id"] = "cheap-provider"
    plugin.context.llm_responses.append(RuntimeError("provider unavailable"))

    await plugin.process_extraction_scope("platform\x1fuser")

    status = await plugin.store.get_extraction_status("platform\x1fuser")
    assert plugin.context.llm_calls[0]["chat_provider_id"] == "cheap-provider"
    assert status.pending_count == 1
    assert status.next_retry_at is not None
