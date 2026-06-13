from __future__ import annotations

from astrbot_plugin_memory_cards.models import MemoryNote
from astrbot_plugin_memory_cards.retrieval import select_relevant_notes


def note(
    note_id: int,
    category: str,
    content: str,
    updated_at: str = "2026-06-13T10:00:00+00:00",
) -> MemoryNote:
    return MemoryNote(
        id=note_id,
        scope_key="p\x1fu",
        category=category,
        content=content,
        created_at=updated_at,
        updated_at=updated_at,
    )


def test_chinese_relevance_prefers_matching_memory() -> None:
    notes = [
        note(1, "偏好", "用户喜欢简洁直接的回答"),
        note(2, "事件", "用户下周参加英语考试"),
    ]

    result = select_relevant_notes(
        "回答能简洁一点吗",
        notes,
        minimum_score=3,
    )

    assert [item.id for item in result] == [1]


def test_english_words_and_category_names_are_searchable() -> None:
    notes = [
        note(1, "偏好", "The user prefers Python for automation"),
        note(2, "目标", "完成毕业设计"),
    ]

    assert [item.id for item in select_relevant_notes("Python automation", notes)] == [
        1
    ]
    assert [item.id for item in select_relevant_notes("我的目标是什么", notes)] == [2]


def test_unrelated_message_does_not_inject_recent_notes() -> None:
    notes = [
        note(1, "偏好", "喜欢简洁回答"),
        note(2, "目标", "完成 AstrBot 插件"),
    ]

    assert select_relevant_notes("今天天气怎么样", notes) == []


def test_limits_note_count_and_character_budget() -> None:
    notes = [
        note(index, "事件", f"AstrBot 项目事项 {index}" + "x" * 20)
        for index in range(1, 8)
    ]

    result = select_relevant_notes(
        "AstrBot 项目",
        notes,
        max_notes=3,
        max_chars=70,
    )

    assert len(result) == 2
    assert sum(len(item.content) for item in result) <= 70


def test_ties_use_recent_update_then_id() -> None:
    notes = [
        note(1, "偏好", "喜欢 Python", "2026-06-11T10:00:00+00:00"),
        note(2, "偏好", "喜欢 Python", "2026-06-13T10:00:00+00:00"),
        note(3, "偏好", "喜欢 Python", "2026-06-13T10:00:00+00:00"),
    ]

    result = select_relevant_notes("Python", notes)

    assert [item.id for item in result] == [3, 2, 1]


def test_explicit_recall_intent_falls_back_to_recent_notes() -> None:
    notes = [
        note(1, "偏好", "喜欢浅色界面", "2026-06-11T10:00:00+00:00"),
        note(2, "目标", "完成插件", "2026-06-13T10:00:00+00:00"),
        note(3, "人物", "小明是同学", "2026-06-12T10:00:00+00:00"),
        note(4, "事件", "下周考试", "2026-06-10T10:00:00+00:00"),
    ]

    result = select_relevant_notes(
        "你还记得关于我的事情吗",
        notes,
        recall_fallback_enabled=True,
    )

    assert [item.id for item in result] == [2, 3, 1]


def test_recall_fallback_can_be_disabled() -> None:
    notes = [note(1, "偏好", "喜欢浅色界面")]
    assert (
        select_relevant_notes(
            "你还记得我吗",
            notes,
            recall_fallback_enabled=False,
        )
        == []
    )
