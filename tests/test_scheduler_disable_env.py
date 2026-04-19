"""DISABLE_SCHEDULER / OPEN_CASE_TESTING gating for lifespan (see main.py)."""

from __future__ import annotations

import main


def test_env_scheduler_disabled_parsing(monkeypatch):
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    assert main._env_scheduler_disabled() is True
    monkeypatch.setenv("DISABLE_SCHEDULER", "true")
    assert main._env_scheduler_disabled() is True
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    assert main._env_scheduler_disabled() is False


def test_scheduler_disabled_true_when_set_testing_mode():
    main.set_testing_mode(True)
    try:
        assert main._scheduler_disabled() is True
    finally:
        main.set_testing_mode(False)


def test_scheduler_disabled_true_under_pytest():
    """Pytest is in sys.modules here; combined gate must skip the scheduler."""
    assert main._scheduler_disabled() is True
