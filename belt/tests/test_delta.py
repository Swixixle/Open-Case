"""Hermetic tests for belt/delta.py.

Each test builds a throwaway git repo in a tmpdir, exercises delta against it
as a subprocess (cwd = temp repo, so it resolves that repo, never the real
one), and asserts on the --json facts. No network, no dependence on the real
Open-Case repo state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

DELTA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "delta.py")

# An anchor comfortably in the past so "now" commits always fall after it.
OLD_SESSION = "session-2020-01-01T00-00-00Z.md"


def _git(repo: str, *args: str, env: dict | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return proc.stdout


def _init_repo(repo: str) -> dict:
    """Init a git repo with deterministic identity/branch. Returns env."""
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    _git(repo, "init", "-q", "-b", "main", env=env)
    return env


def _commit(repo: str, env: dict, name: str, content: str, message: str) -> None:
    with open(os.path.join(repo, name), "w") as fh:
        fh.write(content)
    _git(repo, "add", name, env=env)
    _git(repo, "commit", "-q", "-m", message, env=env)


def _run_delta(repo: str) -> dict:
    proc = subprocess.run(
        [sys.executable, DELTA, "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_delta_reports_commits_dirty_and_anchor(tmp_path):
    repo = str(tmp_path)
    env = _init_repo(repo)

    # Two commits, both "now" so they land after the ancient anchor.
    _commit(repo, env, "a.txt", "one", "first commit")
    _commit(repo, env, "b.txt", "two", "second commit")

    # A fake prior session log => the anchor.
    sessions = os.path.join(repo, ".memory", "sessions")
    os.makedirs(sessions)
    with open(os.path.join(sessions, OLD_SESSION), "w") as fh:
        fh.write("# old session\n")

    # One uncommitted (untracked) file: the working-tree gap.
    with open(os.path.join(repo, "dirty.txt"), "w") as fh:
        fh.write("wip")

    report = _run_delta(repo)

    assert report["commits"]["count"] == 2
    subjects = {c["subject"] for c in report["commits"]["list"]}
    assert subjects == {"first commit", "second commit"}

    # dirty.txt untracked. (.memory/sessions/... is also untracked -> counted.)
    assert report["working_tree"]["untracked"] >= 1
    dirty_total = report["working_tree"]["total"]
    assert dirty_total == report["working_tree"]["untracked"]  # nothing tracked-but-modified

    # Anchor names the fake session and used the session source.
    assert report["anchor"]["source"] == "session"
    assert report["anchor"]["session_file"] == OLD_SESSION
    assert report["anchor"]["since"] == "2020-01-01T00:00:00Z"


def test_delta_no_session_falls_back_to_7_days(tmp_path):
    repo = str(tmp_path)
    env = _init_repo(repo)
    _commit(repo, env, "a.txt", "one", "only commit")
    # No .memory/sessions dir at all.

    report = _run_delta(repo)

    assert report["anchor"]["source"] == "fallback-7d"
    assert report["anchor"]["session_file"] is None
    assert "fallback" in report["anchor"]["note"]


def test_delta_picks_newest_session_and_flags_memory_and_deps(tmp_path):
    repo = str(tmp_path)
    env = _init_repo(repo)
    _commit(repo, env, "seed.txt", "x", "seed")

    sessions = os.path.join(repo, ".memory", "sessions")
    os.makedirs(sessions)
    # Two anchors, both ancient; newest wins. Mix both filename styles.
    for name in (OLD_SESSION, "session-2020-06-15-some-slug.md"):
        with open(os.path.join(sessions, name), "w") as fh:
            fh.write("s\n")

    # Commit changes to constraints.md and pyproject.toml after the anchor.
    os.makedirs(os.path.join(repo, ".memory"), exist_ok=True)
    _commit(repo, env, ".memory/constraints.md", "rules", "edit constraints")
    _commit(repo, env, "pyproject.toml", "[project]\n", "add pyproject")

    report = _run_delta(repo)

    # Newest of the two anchors (2020-06-15) is chosen.
    assert report["anchor"]["session_file"] == "session-2020-06-15-some-slug.md"
    assert report["anchor"]["since"] == "2020-06-15T00:00:00Z"

    assert report["memory"]["constraints_changed"] is True
    assert report["dependencies"]["changed"] is True
    assert "pyproject.toml" in report["dependencies"]["files"]


def test_delta_fails_loud_outside_git_repo(tmp_path):
    # tmp_path is not a git repo.
    proc = subprocess.run(
        [sys.executable, DELTA, "--json"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "not a git repository" in proc.stderr
