"""DISABLE_SCHEDULER gating for test-safe FastAPI lifespan (see main.py)."""

from __future__ import annotations

import main


def test_env_scheduler_disabled_parsing(monkeypatch):
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    assert main._env_scheduler_disabled() is True
    monkeypatch.setenv("DISABLE_SCHEDULER", "true")
    assert main._env_scheduler_disabled() is True
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    assert main._env_scheduler_disabled() is False


def test_scheduler_disabled_true_under_pytest():
    """Pytest is always in sys.modules here; combined gate must skip the scheduler."""
    assert main._scheduler_disabled() is True
