"""Prompt and validation helpers for automatic memory extraction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .models import (
    CATEGORIES,
    BufferedMessage,
    MemoryNote,
    normalize_content,
)

EXTRACTION_SYSTEM_PROMPT = """你是长期记忆整理器。只提取用户明确表达且长期有用的信息。
禁止保存密码、验证码、Cookie、令牌、API 密钥等凭据。
只输出严格 JSON，不要解释，不要 Markdown。"""

_CREDENTIAL_PATTERN = re.compile(
    r"(密码|验证码|cookie|token|令牌|api[\s_-]*key|secret|私钥)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    action: str
    category: str
    content: str
    note_id: int | None = None


def contains_credential_material(text: str) -> bool:
    return bool(_CREDENTIAL_PATTERN.search(str(text or "")))


def normalize_for_duplicate_check(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").casefold())


def build_extraction_prompt(
    messages: tuple[BufferedMessage, ...],
    existing_notes: list[MemoryNote],
) -> str:
    note_lines = [
        f"- ID {note.id} [{note.category}] {note.content}" for note in existing_notes
    ]
    conversation = [
        f"{'用户' if message.role == 'user' else '助手'}：{message.content}"
        for message in messages
    ]
    return "\n".join(
        [
            "从下面对话中提取最多几条长期便签。已有便签应优先 update，禁止 delete。",
            '输出格式：{"memories":[{"action":"create","category":"偏好","content":"..."}]}',
            "已有便签：",
            *(note_lines or ["- 无"]),
            "对话：",
            *conversation,
        ]
    )


def parse_candidates(text: str, *, max_notes: int) -> list[MemoryCandidate]:
    raw = str(text or "").strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    memories = payload.get("memories") if isinstance(payload, dict) else None
    if not isinstance(memories, list):
        return []

    candidates: list[MemoryCandidate] = []
    for item in memories:
        if len(candidates) >= max(0, int(max_notes)):
            break
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        category = str(item.get("category", "")).strip()
        if action not in {"create", "update"} or category not in CATEGORIES:
            continue
        try:
            content = normalize_content(str(item.get("content", "")))
        except ValueError:
            continue
        if contains_credential_material(content):
            continue
        note_id = None
        if action == "update":
            try:
                note_id = int(item.get("note_id"))
            except (TypeError, ValueError):
                continue
            if note_id <= 0:
                continue
        candidates.append(MemoryCandidate(action, category, content, note_id))
    return candidates
