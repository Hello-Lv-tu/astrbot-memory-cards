from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
PAGE = ROOT / "astrbot_plugin_memory_cards" / "pages" / "memory"


def test_page_uses_astrbot_bridge_and_relative_endpoints() -> None:
    script = (PAGE / "app.js").read_text(encoding="utf-8")

    assert "window.AstrBotPluginPage" in script
    for endpoint in (
        "memory/users",
        "memory/notes",
        "memory/notes/create",
        "memory/notes/update",
        "memory/notes/delete",
    ):
        assert f'"{endpoint}"' in script
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "result.items" in script
    assert "notesRequestVersion" in script
    assert "requestVersion !== state.notesRequestVersion" in script
    assert "自动生成" in script
    assert "pending_message_count" in script


def test_page_contains_management_controls_and_states() -> None:
    html = (PAGE / "index.html").read_text(encoding="utf-8")

    for element_id in (
        "user-select",
        "search-input",
        "category-list",
        "new-note",
        "note-grid",
        "note-dialog",
        "note-form",
        "delete-dialog",
        "status",
        "empty-state",
    ):
        assert f'id="{element_id}"' in html
    for category in ("偏好", "习惯", "人物", "事件", "雷区", "目标", "待办", "其他"):
        assert category in html


def test_page_is_responsive_and_theme_aware() -> None:
    styles = (PAGE / "style.css").read_text(encoding="utf-8")

    assert "repeat(auto-fit" in styles
    assert "@media" in styles
    assert '[data-theme="dark"]' in styles
    assert "dialog" in styles
