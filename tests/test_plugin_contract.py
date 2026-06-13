from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "astrbot_plugin_memory_cards"


def test_plugin_package_contract() -> None:
    expected_files = {
        "__init__.py",
        "main.py",
        "models.py",
        "store.py",
        "retrieval.py",
        "injection.py",
        "metadata.yaml",
        "_conf_schema.json",
        "requirements.txt",
        "pages/memory/index.html",
        "pages/memory/app.js",
        "pages/memory/style.css",
        ".astrbot-plugin/i18n/zh-CN.json",
        ".astrbot-plugin/i18n/en-US.json",
    }
    assert expected_files <= {
        str(path.relative_to(PLUGIN)).replace("\\", "/")
        for path in PLUGIN.rglob("*")
        if path.is_file()
    }


def test_metadata_and_configuration_contract() -> None:
    metadata = (PLUGIN / "metadata.yaml").read_text(encoding="utf-8")
    schema = json.loads((PLUGIN / "_conf_schema.json").read_text(encoding="utf-8"))

    assert "name: astrbot_plugin_memory_cards" in metadata
    assert "author: Lv_Tu" in metadata
    assert 'repo: "https://github.com/Hello-Lv-tu/astrbot-memory-cards"' in metadata
    assert 'astrbot_version: ">=4.25.5,<5"' in metadata
    assert {
        "enabled",
        "max_injected_notes",
        "max_injected_chars",
        "minimum_score",
        "recall_fallback_enabled",
    } <= schema.keys()
    assert schema["max_injected_notes"]["default"] == 5
    assert schema["max_injected_chars"]["default"] == 1500


def test_main_uses_version_verified_astrbot_contract() -> None:
    source = (PLUGIN / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "@register(" in source
    assert "@filter.on_llm_request()" in source
    assert "event.is_private_chat()" in source
    assert "event.get_platform_id()" in source
    assert "event.get_sender_id()" in source
    assert "StarTools.get_data_dir(PLUGIN_NAME)" in source
    assert "extra_user_content_parts.append" in source
    assert ".mark_as_temp()" in source
    assert "register_web_api" in source
    assert {"initialize", "terminate"} <= function_names
