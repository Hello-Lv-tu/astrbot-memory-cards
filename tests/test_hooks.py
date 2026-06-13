from __future__ import annotations

import importlib

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
        async def list_notes(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

    plugin.store = BrokenStore()
    request = FakeRequest()

    await plugin.inject_memory(FakeEvent(), request)

    assert request.extra_user_content_parts == []
