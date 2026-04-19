"""TestClient + lifespan must not touch APScheduler when testing mode is active."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def _reset_testing_mode():
    import main

    main.set_testing_mode(False)
    yield
    main.set_testing_mode(False)


def test_testclient_does_not_call_scheduler_add_job(_reset_testing_mode):
    """Explicit set_testing_mode(True) before TestClient — add_job must never run."""
    import main
    from fastapi.testclient import TestClient

    with patch.object(main.scheduler, "add_job") as add_job:
        main.set_testing_mode(True)
        try:
            with patch.object(main, "init_db", lambda: None):
                with TestClient(main.app) as c:
                    assert c.get("/health").json() == {"status": "ok"}
        finally:
            main.set_testing_mode(False)
        add_job.assert_not_called()


def test_open_case_testing_env_disables_scheduler(monkeypatch):
    import main

    monkeypatch.setenv("OPEN_CASE_TESTING", "1")
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    main.set_testing_mode(False)
    assert main._env_open_case_testing() is True
    assert main._scheduler_disabled() is True
