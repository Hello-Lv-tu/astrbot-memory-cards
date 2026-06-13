"""Domain records and validation for memory cards."""

from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = ("偏好", "习惯", "人物", "事件", "雷区", "目标", "待办", "其他")
MAX_CONTENT_LENGTH = 2000


def normalize_category(value: str) -> str:
    category = str(value or "").strip()
    return category if category in CATEGORIES else "其他"


def normalize_content(value: str) -> str:
    content = str(value or "").strip()
    if not content:
        raise ValueError("便签内容不能为空")
    if len(content) > MAX_CONTENT_LENGTH:
        raise ValueError(f"便签内容不能超过 {MAX_CONTENT_LENGTH} 个字符")
    return content


@dataclass(frozen=True, slots=True)
class UserSummary:
    scope_key: str
    platform_id: str
    user_id: str
    display_name: str
    last_seen_at: str
    note_count: int = 0


@dataclass(frozen=True, slots=True)
class MemoryNote:
    id: int
    scope_key: str
    category: str
    content: str
    created_at: str
    updated_at: str
    source: str = "manual"
    source_batch_id: str | None = None


@dataclass(frozen=True, slots=True)
class BufferedMessage:
    id: int
    scope_key: str
    role: str
    content: str
    provider_id: str
    created_at: str
    batch_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionBatch:
    batch_id: str
    scope_key: str
    messages: tuple[BufferedMessage, ...]


@dataclass(frozen=True, slots=True)
class ExtractionStatus:
    scope_key: str
    pending_count: int
    last_message_at: str | None
    next_retry_at: str | None
    processing_batch_id: str | None
    last_error: str | None
    last_extracted_at: str | None
