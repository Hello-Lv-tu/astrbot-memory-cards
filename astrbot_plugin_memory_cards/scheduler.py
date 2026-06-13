"""Background trigger scheduler for automatic memory extraction."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable

from .store import MemoryStore


class ExtractionScheduler:
    def __init__(
        self,
        store: MemoryStore,
        processor: Callable[[str], Awaitable[None] | None],
        *,
        message_threshold: Callable[[], int],
        idle_minutes: Callable[[], int],
        now: Callable[[], datetime] | None = None,
        poll_seconds: float = 30,
    ) -> None:
        self.store = store
        self.processor = processor
        self.message_threshold = message_threshold
        self.idle_minutes = idle_minutes
        self.now = now or (lambda: datetime.now(UTC))
        self.poll_seconds = poll_seconds
        self._loop_task: asyncio.Task | None = None
        self._active: set[str] = set()

    async def start(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._loop_task is None:
            return
        self._loop_task.cancel()
        await asyncio.gather(self._loop_task, return_exceptions=True)
        self._loop_task = None

    async def _run(self) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(self.poll_seconds)

    async def check_once(self) -> None:
        current = self.now()
        threshold = max(1, int(self.message_threshold()))
        idle_before = current - timedelta(
            minutes=max(1, int(self.idle_minutes()))
        )
        statuses = await self.store.list_extraction_statuses_with_pending()
        for status in statuses:
            if status.scope_key in self._active or status.processing_batch_id:
                continue
            if status.next_retry_at:
                retry_at = datetime.fromisoformat(status.next_retry_at)
                if retry_at > current:
                    continue
            count_ready = status.pending_count >= threshold
            idle_ready = bool(
                status.last_message_at
                and datetime.fromisoformat(status.last_message_at) <= idle_before
            )
            if not (count_ready or idle_ready):
                continue
            self._active.add(status.scope_key)
            try:
                result = self.processor(status.scope_key)
                if inspect.isawaitable(result):
                    await result
            finally:
                self._active.discard(status.scope_key)
