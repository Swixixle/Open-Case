"""Shared HTTP retry helpers (exponential backoff + jitter)."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_SECONDS = 2.0


def backoff_sleep_seconds(attempt_index: int, base: float = DEFAULT_BASE_SECONDS) -> float:
    """attempt_index is 0-based; first retry waits ~base *2^0 + jitter."""
    return base * (2**attempt_index) + random.uniform(0, 0.5)


def sync_sleep_backoff(attempt_index: int, base: float = DEFAULT_BASE_SECONDS) -> None:
    time.sleep(backoff_sleep_seconds(attempt_index, base))


async def async_sleep_backoff(attempt_index: int, base: float = DEFAULT_BASE_SECONDS) -> None:
    await asyncio.sleep(backoff_sleep_seconds(attempt_index, base))


def http_request_with_retry(
    method: str,
    url: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_seconds: float = DEFAULT_BASE_SECONDS,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Exception | None = None
    timeout = float(kwargs.pop("timeout", 120.0))
    with httpx.Client(timeout=timeout) as client:
        for attempt in range(max_attempts):
            try:
                resp = client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except Exception as e:
                last_exc = e
                logger.warning(
                    "HTTP %s %s failed attempt %s/%s: %s",
                    method,
                    url,
                    attempt + 1,
                    max_attempts,
                    e,
                )
                if attempt + 1 < max_attempts:
                    sync_sleep_backoff(attempt, base_seconds)
    assert last_exc is not None
    raise last_exc


async def async_http_request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_seconds: float = DEFAULT_BASE_SECONDS,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            logger.warning(
                "HTTP %s %s failed attempt %s/%s: %s",
                method,
                url,
                attempt + 1,
                max_attempts,
                e,
            )
            if attempt + 1 < max_attempts:
                await async_sleep_backoff(attempt, base_seconds)
    assert last_exc is not None
    raise last_exc
