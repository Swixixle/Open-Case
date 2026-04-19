"""DISABLE_SCHEDULER gating for test-safe FastAPI lifespan (see main.py)."""

from __future__ import annotations

import main


def test_scheduler_disabled_env_parsing(monkeypatch):
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    assert main._scheduler_disabled() is True
    monkeypatch.setenv("DISABLE_SCHEDULER", "true")
    assert main._scheduler_disabled() is True
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    assert main._scheduler_disabled() is False
