from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from astrbot_plugin_memory_cards.models import ExtractionStatus
from astrbot_plugin_memory_cards.scheduler import ExtractionScheduler


class FakeStore:
    def __init__(self, statuses):
        self.statuses = statuses

    async def list_extraction_statuses_with_pending(self):
        return self.statuses


@pytest.mark.asyncio
async def test_scheduler_only_processes_eligible_users() -> None:
    now = datetime.now(UTC)
    statuses = [
        ExtractionStatus("count", 20, now.isoformat(), None, None, None, None),
        ExtractionStatus(
            "idle",
            2,
            (now - timedelta(minutes=31)).isoformat(),
            None,
            None,
            None,
            None,
        ),
        ExtractionStatus("young", 2, now.isoformat(), None, None, None, None),
        ExtractionStatus(
            "retry",
            30,
            now.isoformat(),
            (now + timedelta(minutes=5)).isoformat(),
            None,
            None,
            None,
        ),
    ]
    processed = []
    scheduler = ExtractionScheduler(
        FakeStore(statuses),
        processed.append,
        message_threshold=lambda: 20,
        idle_minutes=lambda: 30,
        now=lambda: now,
    )

    await scheduler.check_once()

    assert processed == ["count", "idle"]


@pytest.mark.asyncio
async def test_scheduler_with_no_pending_statuses_does_nothing() -> None:
    processed = []
    scheduler = ExtractionScheduler(
        FakeStore([]),
        processed.append,
        message_threshold=lambda: 20,
        idle_minutes=lambda: 30,
    )
    await scheduler.check_once()
    assert processed == []
