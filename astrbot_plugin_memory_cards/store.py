"""Persistent SQLite storage for memory cards."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from .models import (
    MemoryNote,
    UserSummary,
    normalize_category,
    normalize_content,
)

SCHEMA_VERSION = 1


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
                elif int(row["version"]) != SCHEMA_VERSION:
                    raise RuntimeError(
                        f"不支持的数据库版本: {row['version']}"
                    )
                await connection.commit()
            except Exception:
                await connection.close()
                raise
            self._connection = connection

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
        connection = self._require_connection()
        cursor = await connection.execute(
            """
            SELECT
                u.scope_key,
                u.platform_id,
                u.user_id,
                u.display_name,
                u.last_seen_at,
                COUNT(n.id) AS note_count
            FROM users AS u
            LEFT JOIN notes AS n ON n.scope_key = u.scope_key
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
            )
            for row in rows
        ]

    async def user_exists(self, scope_key: str) -> bool:
        connection = self._require_connection()
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
    ) -> MemoryNote:
        normalized_content = normalize_content(content)
        normalized_category = normalize_category(category)
        timestamp = _now()
        async with self._lock:
            connection = self._require_connection()
            if not await self.user_exists(scope_key):
                raise ValueError("用户不存在")
            try:
                cursor = await connection.execute(
                    """
                    INSERT INTO notes(
                        scope_key, category, content, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        scope_key,
                        normalized_category,
                        normalized_content,
                        timestamp,
                        timestamp,
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
        )

    async def get_note(
        self,
        scope_key: str,
        note_id: int,
    ) -> MemoryNote | None:
        connection = self._require_connection()
        cursor = await connection.execute(
            """
            SELECT id, scope_key, category, content, created_at, updated_at
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
        connection = self._require_connection()
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

        count_cursor = await connection.execute(
            f"SELECT COUNT(*) AS total FROM notes WHERE {where}",
            params,
        )
        count_row = await count_cursor.fetchone()
        await count_cursor.close()

        cursor = await connection.execute(
            f"""
            SELECT id, scope_key, category, content, created_at, updated_at
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
        return await self.get_note(scope_key, note_id)

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

    @staticmethod
    def _note_from_row(row: aiosqlite.Row) -> MemoryNote:
        return MemoryNote(
            id=int(row["id"]),
            scope_key=row["scope_key"],
            category=row["category"],
            content=row["content"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
