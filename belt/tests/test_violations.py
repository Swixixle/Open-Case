"""Tests for belt/violations.py.

The rule logic is a pure function (delta-json dict -> findings), so most of
this exercises it with no git at all. One integration test runs the real
subprocess path against a throwaway temp git repo (reusing delta's harness
pattern). Hermetic: no network, no dependence on the real Open-Case repo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_BELT_DIR = os.path.dirname(os.path.dirname(__file__))
if _BELT_DIR not in sys.path:
    sys.path.insert(0, _BELT_DIR)

import violations  # noqa: E402  (path injected above)

VIOLATIONS = os.path.join(_BELT_DIR, "violations.py")


def _delta_json(**overrides) -> dict:
    """A 'clean' delta --json object; override individual facts per test."""
    base = {
        "anchor": {"source": "session", "session_file": "s.md", "since": "x", "note": None},
        "branch": {"current": "main", "on_main": True, "ahead": 0, "behind": 0, "compare_error": None},
        "commits": {"count": 0, "list": []},
        "working_tree": {"modified": 0, "untracked": 0, "total": 0},
        "memory": {"canon_changed": False, "constraints_changed": False, "current_state_changed": False},
        "dependencies": {"changed": False, "files": []},
    }
    for key, value in overrides.items():
        base[key] = {**base[key], **value} if isinstance(value, dict) else value
    return base


# --- pure rule function ----------------------------------------------------

def test_constraints_changed_yields_one_warn_naming_constraints():
    findings = violations.delta_findings(_delta_json(memory={"constraints_changed": True}))
    assert len(findings) == 1
    assert findings[0]["severity"] == "warn"
    assert "constraints.md" in findings[0]["message"]
    assert findings[0]["source"] == "delta"


def test_deps_changed_yields_warn():
    findings = violations.delta_findings(
        _delta_json(dependencies={"changed": True, "files": ["uv.lock"]})
    )
    assert len(findings) == 1
    assert findings[0]["severity"] == "warn"
    assert "uv.lock" in findings[0]["message"]


def test_current_state_change_is_ignored():
    # current-state.md changes every park; must NOT surface.
    findings = violations.delta_findings(_delta_json(memory={"current_state_changed": True}))
    assert findings == []


def test_clean_delta_yields_no_findings():
    assert violations.delta_findings(_delta_json()) == []


def test_uncommitted_and_ahead_yield_notices():
    dj = _delta_json(
        working_tree={"modified": 1, "untracked": 2, "total": 3},
        branch={"current": "feature/x", "on_main": False, "ahead": 4, "behind": 0, "compare_error": None},
    )
    findings = violations.delta_findings(dj)
    sevs = [f["severity"] for f in findings]
    assert sevs == ["notice", "notice"]
    assert any("3 uncommitted files" in f["message"] for f in findings)
    assert any("4 commits on feature/x" in f["message"] for f in findings)


# --- feeder-down resilience (no crash, operational note recorded) ----------

def test_feeder_error_records_operational_note_and_still_returns(monkeypatch):
    def boom():
        return None, "delta feeder unavailable: simulated failure"

    monkeypatch.setitem(violations.FEEDERS, "delta", {"run": boom, "rules": violations.delta_findings})
    report = violations.aggregate()
    assert report["findings"] == []
    assert report["operational_notes"] == ["delta feeder unavailable: simulated failure"]


# --- integration: real subprocess path against a temp git repo -------------

def _git(repo, *args, env=None):
    subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env, check=True)


def test_integration_real_subprocess_flags_uncommitted(tmp_path):
    repo = str(tmp_path)
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
    })
    _git(repo, "init", "-q", "-b", "main", env=env)
    with open(os.path.join(repo, "seed.txt"), "w") as fh:
        fh.write("x")
    _git(repo, "add", "seed.txt", env=env)
    _git(repo, "commit", "-q", "-m", "seed", env=env)
    # Leave an untracked file so delta reports a dirty working tree.
    with open(os.path.join(repo, "wip.txt"), "w") as fh:
        fh.write("wip")

    proc = subprocess.run(
        [sys.executable, VIOLATIONS, "--json"],
        cwd=repo, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["operational_notes"] == []  # delta feeder ran fine
    assert any("uncommitted files" in f["message"] for f in report["findings"])
