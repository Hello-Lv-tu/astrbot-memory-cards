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

QUALITY_REVIEW_SYSTEM_PROMPT = """你是长期记忆质量审查器。只整理已有便签。
合并必须语义相近；明确冲突应更新旧便签；可纠正分类但禁止新增事实。
禁止输出 create 或 delete。只输出严格 JSON，不要解释，不要 Markdown。"""

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
    note_ids: tuple[int, ...] = ()
    reason: str = ""


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
            "从下面对话中提取最多几条长期便签。只能基于已有便签 ID "
            "输出 create、update、merge、noop，禁止 delete。",
            '输出格式：{"memories":['
            '{"action":"create","category":"偏好","content":"..."},'
            '{"action":"update","note_id":1,"category":"偏好",'
            '"content":"...","reason":"..."},'
            '{"action":"merge","note_ids":[1,2],"category":"偏好",'
            '"content":"...","reason":"..."},'
            '{"action":"noop","reason":"..."}]}',
            "已有便签：",
            *(note_lines or ["- 无"]),
            "对话：",
            *conversation,
        ]
    )


def build_quality_review_prompt(existing_notes: list[MemoryNote]) -> str:
    note_lines = [
        f"- ID {note.id} [{note.category}] {note.content}" for note in existing_notes
    ]
    return "\n".join(
        [
            "审查下面的已有便签。只输出 update、merge、noop，禁止 create 和 delete。",
            "仅合并语义相近的便签；同分类但主题不同的便签必须保持独立。",
            "可用 update 修正分类、补全表述或处理明确冲突。",
            '输出格式：{"memories":['
            '{"action":"update","note_id":1,"category":"偏好",'
            '"content":"...","reason":"..."},'
            '{"action":"merge","note_ids":[1,2],"category":"偏好",'
            '"content":"...","reason":"..."},'
            '{"action":"noop","reason":"无需整理"}]}',
            "已有便签：",
            *(note_lines or ["- 无"]),
        ]
    )


def parse_candidates(
    text: str,
    *,
    max_notes: int,
    allowed_note_ids: set[int] | None = None,
    reject_invalid: bool = False,
) -> list[MemoryCandidate]:
    raw = str(text or "").strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        if reject_invalid:
            raise ValueError("模型输出包含非法 JSON")
        return []
    memories = payload.get("memories") if isinstance(payload, dict) else None
    if not isinstance(memories, list):
        if reject_invalid:
            raise ValueError("模型输出缺少 memories 列表")
        return []

    candidates: list[MemoryCandidate] = []
    for item in memories:
        if len(candidates) >= max(0, int(max_notes)):
            break
        if not isinstance(item, dict):
            if reject_invalid:
                raise ValueError("模型输出包含非法操作")
            continue
        action = str(item.get("action", "")).strip()
        category = str(item.get("category", "")).strip()
        if action == "noop":
            candidates.append(
                MemoryCandidate(
                    "noop",
                    "其他",
                    "",
                    reason=str(item.get("reason", ""))[:500],
                )
            )
            continue
        if action not in {"create", "update", "merge"} or category not in CATEGORIES:
            if reject_invalid:
                raise ValueError("模型输出包含非法操作")
            continue
        try:
            content = normalize_content(str(item.get("content", "")))
        except ValueError:
            if reject_invalid:
                raise ValueError("模型输出包含非法内容")
            continue
        if contains_credential_material(content):
            if reject_invalid:
                raise ValueError("模型输出包含敏感内容")
            continue
        note_id = None
        if action == "update":
            try:
                note_id = int(item.get("note_id"))
            except (TypeError, ValueError):
                if reject_invalid:
                    raise ValueError("模型输出包含非法候选 ID")
                continue
            if note_id <= 0:
                if reject_invalid:
                    raise ValueError("模型输出包含非法候选 ID")
                continue
            if allowed_note_ids is not None and note_id not in allowed_note_ids:
                if reject_invalid:
                    raise ValueError("模型输出包含非候选 ID")
                continue
        note_ids: tuple[int, ...] = ()
        if action == "merge":
            raw_note_ids = item.get("note_ids")
            if not isinstance(raw_note_ids, list):
                if reject_invalid:
                    raise ValueError("模型输出包含非法候选 ID")
                continue
            parsed_ids: list[int] = []
            for raw_id in raw_note_ids:
                try:
                    parsed_id = int(raw_id)
                except (TypeError, ValueError):
                    if reject_invalid:
                        raise ValueError("模型输出包含非法候选 ID")
                    parsed_ids = []
                    break
                if parsed_id <= 0:
                    if reject_invalid:
                        raise ValueError("模型输出包含非法候选 ID")
                    parsed_ids = []
                    break
                parsed_ids.append(parsed_id)
            if len(set(parsed_ids)) < 2:
                if reject_invalid:
                    raise ValueError("模型输出包含非法候选 ID")
                continue
            if allowed_note_ids is not None and any(
                parsed_id not in allowed_note_ids for parsed_id in parsed_ids
            ):
                if reject_invalid:
                    raise ValueError("模型输出包含非候选 ID")
                continue
            note_ids = tuple(parsed_ids)
        candidates.append(
            MemoryCandidate(
                action,
                category,
                content,
                note_id,
                note_ids,
                str(item.get("reason", ""))[:500],
            )
        )
    return candidates
