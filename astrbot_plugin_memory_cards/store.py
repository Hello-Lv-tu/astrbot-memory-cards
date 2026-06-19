"""Persistent SQLite storage for memory cards."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import aiosqlite

from .models import (
    BufferedMessage,
    ExtractionBatch,
    ExtractionStatus,
    MemoryNote,
    NoteRevision,
    QualityPreview,
    UserSummary,
    normalize_category,
    normalize_content,
)

SCHEMA_VERSION = 3


class PreviewExpiredError(RuntimeError):
    """Raised when a quality preview no longer matches current notes."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return self._connection is not None

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("便签存储未就绪")
        return self._connection

    async def open(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self.path)
            connection.row_factory = aiosqlite.Row
            try:
                await connection.execute("PRAGMA journal_mode=WAL")
                await connection.execute("PRAGMA foreign_keys=ON")
                await connection.execute("PRAGMA busy_timeout=5000")
                await connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        version INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS users (
                        scope_key TEXT PRIMARY KEY,
                        platform_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scope_key TEXT NOT NULL,
                        category TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'manual',
                        source_batch_id TEXT,
                        FOREIGN KEY(scope_key) REFERENCES users(scope_key)
                            ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_notes_scope_updated
                        ON notes(scope_key, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_notes_scope_category
                        ON notes(scope_key, category);
                    CREATE INDEX IF NOT EXISTS idx_users_last_seen
                        ON users(last_seen_at DESC);
                    """
                )
                cursor = await connection.execute(
                    "SELECT version FROM schema_meta LIMIT 1"
                )
                row = await cursor.fetchone()
                await cursor.close()
                if row is None:
                    await connection.execute(
                        "INSERT INTO schema_meta(version) VALUES (?)",
                        (SCHEMA_VERSION,),
                    )
                    row_version = SCHEMA_VERSION
                elif int(row["version"]) == 1:
                    await connection.execute(
                        "ALTER TABLE notes ADD COLUMN source TEXT "
                        "NOT NULL DEFAULT 'manual'"
                    )
                    await connection.execute(
                        "ALTER TABLE notes ADD COLUMN source_batch_id TEXT"
                    )
                    await connection.execute(
                        "UPDATE schema_meta SET version = ?",
                        (2,),
                    )
                    row_version = 2
                else:
                    row_version = int(row["version"])
                if row_version == 2:
                    await self._ensure_v3_schema(connection)
                    await connection.execute(
                        "UPDATE schema_meta SET version = ?",
                        (SCHEMA_VERSION,),
                    )
                elif row_version != SCHEMA_VERSION:
                    raise RuntimeError(
                        f"不支持的数据库版本: {row_version}"
                    )
                else:
                    await self._ensure_v3_schema(connection)
                await connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS message_buffer (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scope_key TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        provider_id TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        batch_id TEXT,
                        FOREIGN KEY(scope_key) REFERENCES users(scope_key)
                            ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS extraction_state (
                        scope_key TEXT PRIMARY KEY,
                        last_message_at TEXT,
                        next_retry_at TEXT,
                        processing_batch_id TEXT,
                        last_error TEXT,
                        last_extracted_at TEXT,
                        FOREIGN KEY(scope_key) REFERENCES users(scope_key)
                            ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_buffer_scope_batch
                        ON message_buffer(scope_key, batch_id, created_at, id);
                    """
                )
                await connection.commit()
            except Exception:
                await connection.close()
                raise
            self._connection = connection

    @staticmethod
    async def _ensure_v3_schema(connection: aiosqlite.Connection) -> None:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS note_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_key TEXT NOT NULL,
                note_id INTEGER NOT NULL,
                merged_note_id INTEGER,
                before_category TEXT NOT NULL,
                before_content TEXT NOT NULL,
                change_type TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                source_batch_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(scope_key) REFERENCES users(scope_key)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_revisions_scope_created
                ON note_revisions(scope_key, created_at DESC, id DESC);
            CREATE TABLE IF NOT EXISTS quality_previews (
                preview_id TEXT PRIMARY KEY,
                scope_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                operations_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(scope_key) REFERENCES users(scope_key)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_previews_scope_created
                ON quality_previews(scope_key, created_at DESC);
            """
        )

    async def close(self) -> None:
        async with self._lock:
            if self._connection is None:
                return
            await self._connection.close()
            self._connection = None

    async def upsert_user(
        self,
        scope_key: str,
        platform_id: str,
        user_id: str,
        display_name: str,
    ) -> UserSummary:
        if not scope_key or not platform_id or not user_id:
            raise ValueError("用户身份不能为空")
        timestamp = _now()
        name = str(display_name or user_id).strip() or user_id
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                """
                INSERT INTO users(
                    scope_key, platform_id, user_id, display_name, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    platform_id=excluded.platform_id,
                    user_id=excluded.user_id,
                    display_name=excluded.display_name,
                    last_seen_at=excluded.last_seen_at
                """,
                (scope_key, platform_id, user_id, name, timestamp),
            )
            await connection.commit()
        return UserSummary(
            scope_key=scope_key,
            platform_id=platform_id,
            user_id=user_id,
            display_name=name,
            last_seen_at=timestamp,
        )

    async def list_users(self) -> list[UserSummary]:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT
                    u.scope_key,
                    u.platform_id,
                    u.user_id,
                    u.display_name,
                    u.last_seen_at,
                    COUNT(n.id) AS note_count,
                    (
                        SELECT COUNT(*) FROM message_buffer AS b
                        WHERE b.scope_key = u.scope_key AND b.batch_id IS NULL
                    ) AS pending_message_count,
                    s.last_message_at,
                    s.last_extracted_at,
                    s.last_error
                FROM users AS u
                LEFT JOIN notes AS n ON n.scope_key = u.scope_key
                LEFT JOIN extraction_state AS s ON s.scope_key = u.scope_key
                GROUP BY u.scope_key
                ORDER BY u.last_seen_at DESC, u.scope_key ASC
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [
            UserSummary(
                scope_key=row["scope_key"],
                platform_id=row["platform_id"],
                user_id=row["user_id"],
                display_name=row["display_name"],
                last_seen_at=row["last_seen_at"],
                note_count=int(row["note_count"]),
                pending_message_count=int(row["pending_message_count"]),
                last_message_at=row["last_message_at"],
                last_extracted_at=row["last_extracted_at"],
                last_error=row["last_error"],
            )
            for row in rows
        ]

    async def user_exists(self, scope_key: str) -> bool:
        async with self._lock:
            connection = self._require_connection()
            return await self._user_exists(connection, scope_key)

    @staticmethod
    async def _user_exists(
        connection: aiosqlite.Connection,
        scope_key: str,
    ) -> bool:
        cursor = await connection.execute(
            "SELECT 1 FROM users WHERE scope_key = ?",
            (scope_key,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def create_note(
        self,
        scope_key: str,
        category: str,
        content: str,
        *,
        source: str = "manual",
        source_batch_id: str | None = None,
    ) -> MemoryNote:
        normalized_content = normalize_content(content)
        normalized_category = normalize_category(category)
        timestamp = _now()
        async with self._lock:
            connection = self._require_connection()
            if not await self._user_exists(connection, scope_key):
                raise ValueError("用户不存在")
            try:
                cursor = await connection.execute(
                    """
                    INSERT INTO notes(
                        scope_key, category, content, created_at, updated_at,
                        source, source_batch_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope_key,
                        normalized_category,
                        normalized_content,
                        timestamp,
                        timestamp,
                        "auto" if source == "auto" else "manual",
                        source_batch_id,
                    ),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
            note_id = int(cursor.lastrowid or 0)
            await cursor.close()
        return MemoryNote(
            id=note_id,
            scope_key=scope_key,
            category=normalized_category,
            content=normalized_content,
            created_at=timestamp,
            updated_at=timestamp,
            source="auto" if source == "auto" else "manual",
            source_batch_id=source_batch_id,
        )

    async def get_note(
        self,
        scope_key: str,
        note_id: int,
    ) -> MemoryNote | None:
        async with self._lock:
            connection = self._require_connection()
            return await self._get_note(connection, scope_key, note_id)

    async def _get_note(
        self,
        connection: aiosqlite.Connection,
        scope_key: str,
        note_id: int,
    ) -> MemoryNote | None:
        cursor = await connection.execute(
            """
            SELECT id, scope_key, category, content, created_at, updated_at,
                   source, source_batch_id
            FROM notes
            WHERE scope_key = ? AND id = ?
            """,
            (scope_key, int(note_id)),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return self._note_from_row(row) if row else None

    async def list_notes(
        self,
        scope_key: str,
        *,
        keyword: str = "",
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MemoryNote], int]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        clauses = ["scope_key = ?"]
        params: list[object] = [scope_key]
        cleaned_keyword = str(keyword or "").strip()
        if cleaned_keyword:
            clauses.append("content LIKE ? ESCAPE '\\'")
            escaped = (
                cleaned_keyword.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            params.append(f"%{escaped}%")
        if category and category != "全部":
            clauses.append("category = ?")
            params.append(normalize_category(category))
        where = " AND ".join(clauses)

        async with self._lock:
            connection = self._require_connection()
            count_cursor = await connection.execute(
                f"SELECT COUNT(*) AS total FROM notes WHERE {where}",
                params,
            )
            count_row = await count_cursor.fetchone()
            await count_cursor.close()

            cursor = await connection.execute(
                f"""
                SELECT id, scope_key, category, content, created_at, updated_at,
                       source, source_batch_id
                FROM notes
                WHERE {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return (
            [self._note_from_row(row) for row in rows],
            int(count_row["total"] if count_row else 0),
        )

    async def list_notes_for_retrieval(
        self,
        scope_key: str,
    ) -> list[MemoryNote]:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT id, scope_key, category, content, created_at, updated_at,
                       source, source_batch_id
                FROM notes
                WHERE scope_key = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (scope_key,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._note_from_row(row) for row in rows]

    async def update_note(
        self,
        scope_key: str,
        note_id: int,
        category: str,
        content: str,
    ) -> MemoryNote | None:
        normalized_content = normalize_content(content)
        normalized_category = normalize_category(category)
        timestamp = _now()
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                UPDATE notes
                SET category = ?, content = ?, updated_at = ?
                WHERE scope_key = ? AND id = ?
                """,
                (
                    normalized_category,
                    normalized_content,
                    timestamp,
                    scope_key,
                    int(note_id),
                ),
            )
            await connection.commit()
            changed = cursor.rowcount > 0
            await cursor.close()
            if not changed:
                return None
            return await self._get_note(connection, scope_key, note_id)

    async def apply_memory_operations(
        self,
        scope_key: str,
        operations: list[dict],
        *,
        source_batch_id: str | None = None,
    ) -> list[MemoryNote]:
        async with self._lock:
            connection = self._require_connection()
            try:
                await connection.execute("BEGIN")
                notes = await self._apply_memory_operations_locked(
                    connection,
                    scope_key,
                    operations,
                    source_batch_id=source_batch_id,
                )
                await connection.commit()
                return notes
            except Exception:
                await connection.rollback()
                raise

    async def _apply_memory_operations_locked(
        self,
        connection: aiosqlite.Connection,
        scope_key: str,
        operations: list[dict] | tuple[dict, ...],
        *,
        source_batch_id: str | None = None,
    ) -> list[MemoryNote]:
        if not await self._user_exists(connection, scope_key):
            raise ValueError("用户不存在")
        updated: list[MemoryNote] = []
        for raw_operation in operations:
            if not isinstance(raw_operation, dict):
                raise ValueError("操作无效")
            action = str(raw_operation.get("action", "")).strip()
            if action == "noop":
                continue
            if action == "create":
                note = await self._insert_note_locked(
                    connection,
                    scope_key,
                    str(raw_operation.get("category", "其他")),
                    str(raw_operation.get("content", "")),
                    source="auto",
                    source_batch_id=source_batch_id,
                )
                updated.append(note)
            elif action == "update":
                note_id = self._positive_int(raw_operation.get("note_id"))
                if note_id is None:
                    raise ValueError("候选便签无效")
                old = await self._get_note(connection, scope_key, note_id)
                if old is None:
                    raise ValueError("候选便签无效")
                timestamp = _now()
                await self._record_revision(
                    connection,
                    old,
                    note_id=old.id,
                    merged_note_id=None,
                    change_type="update",
                    reason=str(raw_operation.get("reason", ""))[:500],
                    source_batch_id=source_batch_id,
                    timestamp=timestamp,
                )
                await connection.execute(
                    """
                    UPDATE notes
                    SET category = ?, content = ?, updated_at = ?,
                        source = 'auto', source_batch_id = ?
                    WHERE scope_key = ? AND id = ?
                    """,
                    (
                        normalize_category(str(raw_operation.get("category", "其他"))),
                        normalize_content(str(raw_operation.get("content", ""))),
                        timestamp,
                        source_batch_id,
                        scope_key,
                        old.id,
                    ),
                )
                note = await self._get_note(connection, scope_key, old.id)
                if note is not None:
                    updated.append(note)
            elif action == "merge":
                ids = raw_operation.get("note_ids")
                if not isinstance(ids, list) or len(ids) < 2:
                    raise ValueError("候选便签无效")
                note_ids = [self._positive_int(item) for item in ids]
                if any(item is None for item in note_ids):
                    raise ValueError("候选便签无效")
                distinct_ids = sorted(
                    {int(item) for item in note_ids if item is not None}
                )
                if len(distinct_ids) < 2:
                    raise ValueError("候选便签无效")
                notes = [
                    await self._get_note(connection, scope_key, note_id)
                    for note_id in distinct_ids
                ]
                if any(note is None for note in notes):
                    raise ValueError("候选便签无效")
                existing = [note for note in notes if note is not None]
                keep = min(existing, key=lambda item: (item.created_at, item.id))
                timestamp = _now()
                for old in existing:
                    await self._record_revision(
                        connection,
                        old,
                        note_id=keep.id,
                        merged_note_id=None if old.id == keep.id else old.id,
                        change_type="merge",
                        reason=str(raw_operation.get("reason", ""))[:500],
                        source_batch_id=source_batch_id,
                        timestamp=timestamp,
                    )
                await connection.execute(
                    """
                    UPDATE notes
                    SET category = ?, content = ?, updated_at = ?,
                        source = 'auto', source_batch_id = ?
                    WHERE scope_key = ? AND id = ?
                    """,
                    (
                        normalize_category(str(raw_operation.get("category", "其他"))),
                        normalize_content(str(raw_operation.get("content", ""))),
                        timestamp,
                        source_batch_id,
                        scope_key,
                        keep.id,
                    ),
                )
                remove_ids = [note.id for note in existing if note.id != keep.id]
                placeholders = ",".join("?" for _ in remove_ids)
                await connection.execute(
                    f"DELETE FROM notes WHERE scope_key = ? AND id IN ({placeholders})",
                    [scope_key, *remove_ids],
                )
                note = await self._get_note(connection, scope_key, keep.id)
                if note is not None:
                    updated.append(note)
            else:
                raise ValueError("操作无效")
        return updated

    async def _insert_note_locked(
        self,
        connection: aiosqlite.Connection,
        scope_key: str,
        category: str,
        content: str,
        *,
        source: str,
        source_batch_id: str | None,
    ) -> MemoryNote:
        normalized_content = normalize_content(content)
        normalized_category = normalize_category(category)
        timestamp = _now()
        cursor = await connection.execute(
            """
            INSERT INTO notes(
                scope_key, category, content, created_at, updated_at,
                source, source_batch_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope_key,
                normalized_category,
                normalized_content,
                timestamp,
                timestamp,
                "auto" if source == "auto" else "manual",
                source_batch_id,
            ),
        )
        note_id = int(cursor.lastrowid or 0)
        await cursor.close()
        note = await self._get_note(connection, scope_key, note_id)
        if note is None:
            raise RuntimeError("新增便签失败")
        return note

    @staticmethod
    async def _record_revision(
        connection: aiosqlite.Connection,
        old: MemoryNote,
        *,
        note_id: int,
        merged_note_id: int | None,
        change_type: str,
        reason: str,
        source_batch_id: str | None,
        timestamp: str,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO note_revisions(
                scope_key, note_id, merged_note_id, before_category,
                before_content, change_type, reason, source_batch_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                old.scope_key,
                note_id,
                merged_note_id,
                old.category,
                old.content,
                change_type,
                reason,
                source_batch_id,
                timestamp,
            ),
        )

    async def list_note_revisions(
        self,
        scope_key: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NoteRevision]:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT id, scope_key, note_id, merged_note_id, before_category,
                       before_content, change_type, reason, source_batch_id,
                       created_at
                FROM note_revisions
                WHERE scope_key = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (scope_key, max(1, min(int(limit), 200)), max(0, int(offset))),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._revision_from_row(row) for row in rows]

    async def create_quality_preview(
        self,
        scope_key: str,
        operations: list[dict],
        *,
        ttl_minutes: int = 30,
    ) -> QualityPreview:
        created_at = _now()
        expires_at = datetime.fromisoformat(created_at) + timedelta(
            minutes=max(1, int(ttl_minutes))
        )
        async with self._lock:
            connection = self._require_connection()
            if not await self._user_exists(connection, scope_key):
                raise ValueError("用户不存在")
            fingerprint = await self._fingerprint_locked(connection, scope_key)
            preview_id = uuid4().hex
            normalized_ops = tuple(dict(item) for item in operations)
            await connection.execute(
                """
                INSERT INTO quality_previews(
                    preview_id, scope_key, fingerprint, operations_json,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    preview_id,
                    scope_key,
                    fingerprint,
                    json.dumps(normalized_ops, ensure_ascii=False, sort_keys=True),
                    created_at,
                    expires_at.isoformat(timespec="microseconds"),
                ),
            )
            await connection.commit()
        return QualityPreview(
            preview_id,
            scope_key,
            fingerprint,
            normalized_ops,
            created_at,
            expires_at.isoformat(timespec="microseconds"),
        )

    async def apply_quality_preview(
        self,
        scope_key: str,
        preview_id: str,
    ) -> list[MemoryNote]:
        async with self._lock:
            connection = self._require_connection()
            try:
                await connection.execute("BEGIN")
                cursor = await connection.execute(
                    """
                    SELECT preview_id, scope_key, fingerprint, operations_json,
                           created_at, expires_at
                    FROM quality_previews
                    WHERE preview_id = ? AND scope_key = ?
                    """,
                    (str(preview_id), scope_key),
                )
                row = await cursor.fetchone()
                await cursor.close()
                if row is None:
                    raise PreviewExpiredError("整理预览不存在或已过期")
                if datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC):
                    raise PreviewExpiredError("整理预览已过期")
                current = await self._fingerprint_locked(connection, scope_key)
                if current != row["fingerprint"]:
                    raise PreviewExpiredError("便签已变化，请重新生成预览")
                operations = json.loads(row["operations_json"])
                notes = await self._apply_memory_operations_locked(
                    connection,
                    scope_key,
                    operations,
                    source_batch_id=f"preview:{preview_id}",
                )
                await connection.execute(
                    "DELETE FROM quality_previews WHERE preview_id = ?",
                    (str(preview_id),),
                )
                await connection.commit()
                return notes
            except Exception:
                await connection.rollback()
                raise

    async def _fingerprint_locked(
        self,
        connection: aiosqlite.Connection,
        scope_key: str,
    ) -> str:
        cursor = await connection.execute(
            """
            SELECT id, category, content, created_at, updated_at, source,
                   source_batch_id
            FROM notes
            WHERE scope_key = ?
            ORDER BY id ASC
            """,
            (scope_key,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        payload = [
            [
                int(row["id"]),
                row["category"],
                row["content"],
                row["created_at"],
                row["updated_at"],
                row["source"],
                row["source_batch_id"],
            ]
            for row in rows
        ]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _positive_int(value) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    async def delete_note(self, scope_key: str, note_id: int) -> bool:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                "DELETE FROM notes WHERE scope_key = ? AND id = ?",
                (scope_key, int(note_id)),
            )
            await connection.commit()
            changed = cursor.rowcount > 0
            await cursor.close()
            return changed

    async def append_buffer_message(
        self,
        scope_key: str,
        role: str,
        content: str,
        provider_id: str = "",
        *,
        now: datetime | None = None,
    ) -> BufferedMessage:
        cleaned = str(content or "").strip()
        if role not in {"user", "assistant"} or not cleaned:
            raise ValueError("缓冲消息无效")
        timestamp = (now or datetime.now(UTC)).isoformat(timespec="microseconds")
        async with self._lock:
            connection = self._require_connection()
            if not await self._user_exists(connection, scope_key):
                raise ValueError("用户不存在")
            cursor = await connection.execute(
                """
                INSERT INTO message_buffer(
                    scope_key, role, content, provider_id, created_at, batch_id
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (scope_key, role, cleaned, str(provider_id or ""), timestamp),
            )
            await connection.execute(
                """
                INSERT INTO extraction_state(scope_key, last_message_at)
                VALUES (?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    last_message_at=excluded.last_message_at,
                    last_error=NULL
                """,
                (scope_key, timestamp),
            )
            await connection.commit()
            message_id = int(cursor.lastrowid or 0)
            await cursor.close()
        return BufferedMessage(
            id=message_id,
            scope_key=scope_key,
            role=role,
            content=cleaned,
            provider_id=str(provider_id or ""),
            created_at=timestamp,
        )

    async def get_extraction_status(self, scope_key: str) -> ExtractionStatus:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT
                    s.scope_key, s.last_message_at, s.next_retry_at,
                    s.processing_batch_id, s.last_error, s.last_extracted_at,
                    COUNT(b.id) AS pending_count
                FROM extraction_state AS s
                LEFT JOIN message_buffer AS b
                    ON b.scope_key = s.scope_key AND b.batch_id IS NULL
                WHERE s.scope_key = ?
                GROUP BY s.scope_key
                """,
                (scope_key,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return ExtractionStatus(scope_key, 0, None, None, None, None, None)
        return self._status_from_row(row)

    async def list_extraction_statuses_with_pending(
        self,
    ) -> list[ExtractionStatus]:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT
                    s.scope_key, s.last_message_at, s.next_retry_at,
                    s.processing_batch_id, s.last_error, s.last_extracted_at,
                    COUNT(b.id) AS pending_count
                FROM extraction_state AS s
                JOIN message_buffer AS b
                    ON b.scope_key = s.scope_key AND b.batch_id IS NULL
                GROUP BY s.scope_key
                HAVING COUNT(b.id) > 0
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._status_from_row(row) for row in rows]

    async def claim_extraction_batch(
        self,
        scope_key: str,
        *,
        message_threshold: int,
        idle_before: datetime | None,
        now: datetime | None = None,
    ) -> ExtractionBatch | None:
        current = now or datetime.now(UTC)
        async with self._lock:
            connection = self._require_connection()
            state_cursor = await connection.execute(
                """
                SELECT last_message_at, next_retry_at, processing_batch_id
                FROM extraction_state WHERE scope_key = ?
                """,
                (scope_key,),
            )
            state = await state_cursor.fetchone()
            await state_cursor.close()
            if state is None or state["processing_batch_id"]:
                return None
            if state["next_retry_at"]:
                retry_at = datetime.fromisoformat(state["next_retry_at"])
                if retry_at > current:
                    return None

            cursor = await connection.execute(
                """
                SELECT id, scope_key, role, content, provider_id, created_at,
                       batch_id
                FROM message_buffer
                WHERE scope_key = ? AND batch_id IS NULL
                ORDER BY created_at ASC, id ASC
                """,
                (scope_key,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            enough_messages = len(rows) >= max(1, int(message_threshold))
            idle_reached = bool(
                rows
                and idle_before is not None
                and datetime.fromisoformat(rows[-1]["created_at"]) <= idle_before
            )
            if not rows or not (enough_messages or idle_reached):
                return None

            batch_id = uuid4().hex
            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            await connection.execute(
                f"UPDATE message_buffer SET batch_id = ? WHERE id IN ({placeholders})",
                [batch_id, *ids],
            )
            await connection.execute(
                """
                UPDATE extraction_state
                SET processing_batch_id = ?, next_retry_at = NULL
                WHERE scope_key = ?
                """,
                (batch_id, scope_key),
            )
            await connection.commit()
        return ExtractionBatch(
            batch_id=batch_id,
            scope_key=scope_key,
            messages=tuple(self._buffer_from_row(row, batch_id) for row in rows),
        )

    async def complete_extraction_batch(
        self,
        scope_key: str,
        batch_id: str,
    ) -> None:
        timestamp = _now()
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                "DELETE FROM message_buffer WHERE scope_key = ? AND batch_id = ?",
                (scope_key, batch_id),
            )
            await connection.execute(
                """
                UPDATE extraction_state
                SET processing_batch_id = NULL, next_retry_at = NULL,
                    last_error = NULL, last_extracted_at = ?
                WHERE scope_key = ? AND processing_batch_id = ?
                """,
                (timestamp, scope_key, batch_id),
            )
            await connection.commit()

    async def fail_extraction_batch(
        self,
        scope_key: str,
        batch_id: str,
        error: str,
        retry_at: datetime,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                """
                UPDATE message_buffer SET batch_id = NULL
                WHERE scope_key = ? AND batch_id = ?
                """,
                (scope_key, batch_id),
            )
            await connection.execute(
                """
                UPDATE extraction_state
                SET processing_batch_id = NULL, next_retry_at = ?,
                    last_error = ?
                WHERE scope_key = ? AND processing_batch_id = ?
                """,
                (
                    retry_at.isoformat(timespec="microseconds"),
                    str(error)[:500],
                    scope_key,
                    batch_id,
                ),
            )
            await connection.commit()

    @staticmethod
    def _note_from_row(row: aiosqlite.Row) -> MemoryNote:
        return MemoryNote(
            id=int(row["id"]),
            scope_key=row["scope_key"],
            category=row["category"],
            content=row["content"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            source=row["source"],
            source_batch_id=row["source_batch_id"],
        )

    @staticmethod
    def _buffer_from_row(
        row: aiosqlite.Row,
        batch_id: str | None = None,
    ) -> BufferedMessage:
        return BufferedMessage(
            id=int(row["id"]),
            scope_key=row["scope_key"],
            role=row["role"],
            content=row["content"],
            provider_id=row["provider_id"],
            created_at=row["created_at"],
            batch_id=batch_id if batch_id is not None else row["batch_id"],
        )

    @staticmethod
    def _status_from_row(row: aiosqlite.Row) -> ExtractionStatus:
        return ExtractionStatus(
            scope_key=row["scope_key"],
            pending_count=int(row["pending_count"]),
            last_message_at=row["last_message_at"],
            next_retry_at=row["next_retry_at"],
            processing_batch_id=row["processing_batch_id"],
            last_error=row["last_error"],
            last_extracted_at=row["last_extracted_at"],
        )

    @staticmethod
    def _revision_from_row(row: aiosqlite.Row) -> NoteRevision:
        merged_note_id = row["merged_note_id"]
        return NoteRevision(
            id=int(row["id"]),
            scope_key=row["scope_key"],
            note_id=int(row["note_id"]),
            merged_note_id=int(merged_note_id) if merged_note_id is not None else None,
            before_category=row["before_category"],
            before_content=row["before_content"],
            change_type=row["change_type"],
            reason=row["reason"],
            source_batch_id=row["source_batch_id"],
            created_at=row["created_at"],
        )
