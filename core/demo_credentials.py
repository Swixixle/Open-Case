"""Request-scoped API key overrides for public demo (power-user keys)."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

_demo_api_key_overrides: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "open_case_demo_api_key_overrides",
    default=None,
)


def get_demo_api_key_override(adapter_name: str) -> str | None:
    d = _demo_api_key_overrides.get()
    if not d:
        return None
    v = d.get(adapter_name)
    if v and str(v).strip():
        return str(v).strip()
    return None


@contextmanager
def demo_api_key_overrides(overrides: dict[str, str] | None) -> Iterator[None]:
    """Non-empty values override CredentialRegistry resolution for those adapters only."""
    if not overrides:
        yield
        return
    clean: dict[str, str] = {}
    for k, v in overrides.items():
        key = (k or "").strip()
        if not key:
            continue
        val = (v or "").strip()
        if val:
            clean[key] = val
    if not clean:
        yield
        return
    token = _demo_api_key_overrides.set(clean)
    try:
        yield
    finally:
        _demo_api_key_overrides.reset(token)
