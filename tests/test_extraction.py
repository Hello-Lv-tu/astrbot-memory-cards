from __future__ import annotations

from astrbot_plugin_memory_cards.extraction import (
    build_extraction_prompt,
    contains_credential_material,
    parse_candidates,
)
from astrbot_plugin_memory_cards.models import BufferedMessage, MemoryNote


def test_parse_empty_and_fenced_json() -> None:
    assert parse_candidates('{"memories": []}', max_notes=5) == []
    assert parse_candidates("```json\n{\"memories\": []}\n```", max_notes=5) == []


def test_parse_valid_candidates_and_limit() -> None:
    text = """
    {"memories": [
      {"action": "create", "category": "偏好", "content": "用户喜欢安静"},
      {"action": "update", "note_id": 12, "category": "目标", "content": "准备考试"},
      {"action": "create", "category": "事件", "content": "多余内容"}
    ]}
    """
    candidates = parse_candidates(text, max_notes=2)
    assert [(item.action, item.note_id) for item in candidates] == [
        ("create", None),
        ("update", 12),
    ]


def test_invalid_json_actions_and_credentials_are_rejected() -> None:
    assert parse_candidates("not json", max_notes=5) == []
    text = """
    {"memories": [
      {"action": "delete", "category": "偏好", "content": "删除"},
      {"action": "create", "category": "偏好", "content": "密码是 abc123"},
      {"action": "update", "note_id": -1, "category": "目标", "content": "准备考试"}
    ]}
    """
    assert parse_candidates(text, max_notes=5) == []
    assert contains_credential_material("API key: sk-secret")
    assert contains_credential_material("验证码 123456")


def test_prompt_contains_roles_and_existing_note_ids() -> None:
    messages = (
        BufferedMessage(1, "scope", "user", "我喜欢安静", "p", "now"),
        BufferedMessage(2, "scope", "assistant", "好的", "p", "now"),
    )
    notes = [
        MemoryNote(7, "scope", "偏好", "用户喜欢简洁回答", "now", "now")
    ]
    prompt = build_extraction_prompt(messages, notes)
    assert "用户：我喜欢安静" in prompt
    assert "助手：好的" in prompt
    assert "ID 7" in prompt
