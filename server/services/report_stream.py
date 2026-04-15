"""
In-process SSE fan-out for report pattern-alert refresh after background FEC ingest.

Suitable for single-worker deployments. If the API is scaled horizontally (e.g. multiple
Render workers), subscribers and publishers may land on different processes and events will
not cross instances; swap `get_pattern_event_bus()` to a Redis-backed `PatternEventBus`
implementation in this module when that matters.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


def _key(case_id: UUID) -> str:
    return str(case_id)


class PatternEventBus(ABC):
    """Publish/subscribe for pattern-refresh SSE. Replace implementation for Redis pub/sub."""

    @abstractmethod
    async def subscribe(self, case_id: UUID) -> asyncio.Queue:
        ...

    @abstractmethod
    async def unsubscribe(self, case_id: UUID, q: asyncio.Queue) -> None:
        ...

    @abstractmethod
    async def publish(self, case_id: UUID, message: dict[str, Any]) -> None:
        ...


class InMemoryPatternEventBus(PatternEventBus):
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    async def subscribe(self, case_id: UUID) -> asyncio.Queue:
        async with self._lock:
            q: asyncio.Queue = asyncio.Queue()
            self._subscribers.setdefault(_key(case_id), []).append(q)
            return q

    async def unsubscribe(self, case_id: UUID, q: asyncio.Queue) -> None:
        async with self._lock:
            lst = self._subscribers.get(_key(case_id))
            if not lst:
                return
            if q in lst:
                lst.remove(q)
            if not lst:
                self._subscribers.pop(_key(case_id), None)

    async def publish(self, case_id: UUID, message: dict[str, Any]) -> None:
        async with self._lock:
            qs = list(self._subscribers.get(_key(case_id), []))
        for q in qs:
            await q.put(message)


_bus: PatternEventBus | None = None


def get_pattern_event_bus() -> PatternEventBus:
    global _bus
    if _bus is None:
        _bus = InMemoryPatternEventBus()
    return _bus


def set_pattern_event_bus(bus: PatternEventBus | None) -> None:
    """Tests or future Redis wiring: replace the process-wide bus instance."""
    global _bus
    _bus = bus


async def subscribe_pattern_events(case_id: UUID) -> asyncio.Queue:
    return await get_pattern_event_bus().subscribe(case_id)


async def unsubscribe_pattern_events(case_id: UUID, q: asyncio.Queue) -> None:
    await get_pattern_event_bus().unsubscribe(case_id, q)


async def publish_pattern_event(case_id: UUID, message: dict[str, Any]) -> None:
    await get_pattern_event_bus().publish(case_id, message)


async def report_pattern_refresh_task(case_id: UUID) -> None:
    """Run lazy FEC ingest + pattern engine, then push alerts to SSE subscribers."""
    from database import SessionLocal
    from engines.pattern_engine import pattern_alerts_for_case, run_pattern_engine
    from services.case_auto_ingest import maybe_auto_ingest_case

    db = SessionLocal()
    bus = get_pattern_event_bus()
    try:
        await maybe_auto_ingest_case(db, case_id, background_tasks=None)
        alerts = run_pattern_engine(db)
        rows = pattern_alerts_for_case(case_id, alerts)
        await bus.publish(
            case_id,
            {
                "type": "pattern_alerts",
                "pattern_alerts": rows,
                "pattern_alerts_refresh_pending": False,
            },
        )
    except Exception as e:
        logger.exception("report pattern refresh failed case_id=%s", case_id)
        await bus.publish(
            case_id,
            {"type": "error", "message": str(e)},
        )
    finally:
        db.close()
