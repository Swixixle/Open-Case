#!/usr/bin/env python3
"""delta — "what changed since I last worked here?"

The first belt trust tool. Deterministic, read-only, fail-loud. Pure git +
filesystem; no LLM, no hand-maintained ledger. Every fact is computed from
data that updates itself (git history, working tree, the .memory/ files).

Anchor ("since when?"): the most recent session log in .memory/sessions/,
ranked by an *effective* timestamp = max(timestamp parsed from filename,
file mtime). Date-only /park filenames (session-YYYY-MM-DD-slug.md) parse to
midnight — a lower bound on when that session actually ended — so a same-day
full-timestamp log would otherwise outrank a later date-only park. Folding in
the file's mtime recovers the real "last worked" instant; full-timestamp
filenames are already precise, so max() leaves them unchanged. That same
effective timestamp is used as --since. If no session log exists, fall back
to the last 7 days and say so. The anchor used is always printed.

Usage:
  python belt/delta.py           compact human report (### Delta block)
  python belt/delta.py --json    machine-readable object, same facts

Exits non-zero if not in a git repository or git is unavailable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch

FALLBACK_DAYS = 7
COMMIT_DISPLAY_CAP = 15

# session-2026-07-10T01-35-13Z.md            (full timestamp)
# session-2026-07-09-park-verify-...-.md     (date + slug, written by /park)
_SESSION_RE = re.compile(
    r"^session-(\d{4})-(\d{2})-(\d{2})(?:T(\d{2})-(\d{2})-(\d{2})Z)?"
)

# Files whose change since the anchor is worth flagging.
_CANON = ".memory/canon.md"
_CONSTRAINTS = ".memory/constraints.md"
_CURRENT_STATE = ".memory/current-state.md"
_DEP_EXACT = ("pyproject.toml", "uv.lock")
_DEP_GLOBS = ("requirements*.txt",)


class DeltaError(Exception):
    """Fatal, fail-loud condition (no git repo, git missing)."""


def _run_git(root: str, args: list[str]) -> tuple[int, str]:
    """Run a git command read-only; return (returncode, stdout).

    Raises DeltaError if the git binary itself is unavailable.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:  # git not installed / not on PATH
        raise DeltaError("git executable not found on PATH") from exc
    return proc.returncode, proc.stdout


def resolve_repo_root(start: str) -> str:
    """Repo root via `git rev-parse --show-toplevel`, resolved from *start*.

    Resolved from the current working directory (not the script location) so
    the tool reports on the repo it is invoked in — which is what lets the
    hermetic tests point it at a throwaway temp repo.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", start, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise DeltaError("git executable not found on PATH") from exc
    if proc.returncode != 0:
        raise DeltaError(f"not a git repository (or git failed) at: {start}")
    return proc.stdout.strip()


def parse_session_dt(filename: str) -> datetime | None:
    """UTC datetime parsed from a session filename, or None if unparseable.

    Date-only names (no T..Z component) anchor at 00:00:00 UTC.
    """
    m = _SESSION_RE.match(filename)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if m.group(4) is not None:
        hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
    else:
        hour = minute = second = 0
    try:
        return datetime(
            year, month, day, hour, minute, second, tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _file_mtime_utc(path: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    except OSError:
        return None


def _effective_dt(filename_dt: datetime, mtime_dt: datetime | None) -> datetime:
    """Real "last worked" instant: max(filename timestamp, file mtime).

    A date-only filename parses to midnight — a *lower bound* on the session's
    end — so mtime (when the log was actually written) refines it. A precise
    full-timestamp filename is left unchanged when mtime ~= it.
    """
    if mtime_dt is None:
        return filename_dt
    return max(filename_dt, mtime_dt)


def resolve_anchor(root: str, now: datetime) -> dict:
    """Pick the anchor: most recent session log by effective timestamp.

    Effective timestamp = max(filename timestamp, mtime) so date-only /park
    logs don't undersort beneath a same-day full-timestamp log. That same
    value becomes --since. Falls back to a 7-day window if no log exists.
    """
    sessions_dir = os.path.join(root, ".memory", "sessions")
    best_name: str | None = None
    best_dt: datetime | None = None
    if os.path.isdir(sessions_dir):
        for name in os.listdir(sessions_dir):
            if not (name.startswith("session-") and name.endswith(".md")):
                continue
            filename_dt = parse_session_dt(name)
            if filename_dt is None:
                continue
            mtime_dt = _file_mtime_utc(os.path.join(sessions_dir, name))
            eff = _effective_dt(filename_dt, mtime_dt)
            if best_dt is None or eff > best_dt:
                best_dt, best_name = eff, name

    if best_dt is not None:
        return {
            "source": "session",
            "session_file": best_name,
            "since": best_dt,
            "note": None,
        }
    return {
        "source": "fallback-7d",
        "session_file": None,
        "since": now - timedelta(days=FALLBACK_DAYS),
        "note": f"no prior session log found; window is a {FALLBACK_DAYS}-day fallback",
    }


def _git_iso(dt: datetime) -> str:
    """Format a UTC datetime the way git --since understands unambiguously."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def commits_since(root: str, since: datetime) -> list[dict]:
    """Commits on HEAD with commit date >= anchor, newest first."""
    code, out = _run_git(
        root,
        ["log", f"--since={_git_iso(since)}", "--pretty=format:%h%x09%s"],
    )
    if code != 0 or not out.strip():
        return []
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        commits.append({"sha": sha, "subject": subject})
    return commits


def branch_position(root: str) -> dict:
    """Current branch and ahead/behind counts vs main."""
    _, cur = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    current = cur.strip() or None

    code, out = _run_git(
        root, ["rev-list", "--left-right", "--count", "main...HEAD"]
    )
    if code != 0:
        return {
            "current": current,
            "on_main": current == "main",
            "ahead": None,
            "behind": None,
            "compare_error": "cannot compare to 'main' (branch missing?)",
        }
    parts = out.split()
    behind = int(parts[0]) if len(parts) == 2 else None
    ahead = int(parts[1]) if len(parts) == 2 else None
    return {
        "current": current,
        "on_main": current == "main",
        "ahead": ahead,
        "behind": behind,
        "compare_error": None,
    }


def _porcelain_path(entry: str) -> str:
    """Extract the (possibly renamed) path from a porcelain v1 status line."""
    path = entry[3:]
    if " -> " in path:  # rename/copy: keep the destination
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


def working_tree(root: str) -> tuple[dict, list[str]]:
    """Working-tree dirtiness. Returns (summary, dirty_paths)."""
    _, out = _run_git(root, ["status", "--porcelain"])
    modified = untracked = 0
    dirty: list[str] = []
    for line in out.splitlines():
        if not line:
            continue
        if line.startswith("??"):
            untracked += 1
        else:
            modified += 1
        dirty.append(_porcelain_path(line))
    summary = {
        "modified": modified,
        "untracked": untracked,
        "total": modified + untracked,
    }
    return summary, dirty


def changed_files_since(root: str, since: datetime, dirty: list[str]) -> set[str]:
    """Union of files touched by commits since the anchor and files dirty now.

    This is what makes memory/dependency detection catch both committed and
    still-uncommitted changes.
    """
    changed: set[str] = set(dirty)
    code, out = _run_git(
        root,
        ["log", f"--since={_git_iso(since)}", "--pretty=format:", "--name-only"],
    )
    if code == 0:
        for line in out.splitlines():
            line = line.strip()
            if line:
                changed.add(line)
    return changed


def _is_dep(path: str) -> bool:
    base = os.path.basename(path)
    if base in _DEP_EXACT:
        return True
    return any(fnmatch(base, g) for g in _DEP_GLOBS)


def build_report(root: str, now: datetime) -> dict:
    anchor = resolve_anchor(root, now)
    since = anchor["since"]

    commits = commits_since(root, since)
    branch = branch_position(root)
    wt_summary, dirty = working_tree(root)
    changed = changed_files_since(root, since, dirty)

    memory = {
        "canon_changed": _CANON in changed,
        "constraints_changed": _CONSTRAINTS in changed,
        "current_state_changed": _CURRENT_STATE in changed,
    }
    dep_files = sorted(p for p in changed if _is_dep(p))

    return {
        "anchor": {
            "source": anchor["source"],
            "session_file": anchor["session_file"],
            "since": _git_iso(since),
            "note": anchor["note"],
        },
        "branch": branch,
        "commits": {"count": len(commits), "list": commits},
        "working_tree": wt_summary,
        "memory": memory,
        "dependencies": {"changed": bool(dep_files), "files": dep_files},
    }


def render_human(report: dict) -> str:
    a = report["anchor"]
    lines = ["### Delta"]
    if a["source"] == "session":
        lines.append(f"Since: {a['since']}  (anchor: {a['session_file']})")
    else:
        lines.append(f"Since: {a['since']}  (fallback: {a['note']})")
    lines.append("")

    commits = report["commits"]
    lines.append(f"Commits: {commits['count']}")
    for c in commits["list"][:COMMIT_DISPLAY_CAP]:
        lines.append(f"  - {c['sha']}  {c['subject']}")
    extra = commits["count"] - COMMIT_DISPLAY_CAP
    if extra > 0:
        lines.append(f"  … and {extra} more")

    b = report["branch"]
    if b["compare_error"]:
        lines.append(f"Branch: {b['current']} — {b['compare_error']}")
    else:
        pos = f"{b['ahead']} ahead, {b['behind']} behind main"
        tail = "" if b["on_main"] else "  (not on main)"
        lines.append(f"Branch: {b['current']} — {pos}{tail}")

    wt = report["working_tree"]
    lines.append(
        f"Working tree: {wt['total']} changed "
        f"({wt['modified']} modified, {wt['untracked']} untracked)"
    )

    m = report["memory"]
    notes = []
    if m["canon_changed"]:
        notes.append("canon.md changed (!)")
    if m["constraints_changed"]:
        notes.append("constraints.md changed (!)")
    if m["current_state_changed"]:
        notes.append("current-state.md changed")
    lines.append("Memory: " + (", ".join(notes) if notes else "no changes"))

    dep = report["dependencies"]
    lines.append(
        "Dependencies: "
        + (", ".join(dep["files"]) + " changed" if dep["changed"] else "no changes")
    )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    as_json = "--json" in argv[1:]
    try:
        root = resolve_repo_root(os.getcwd())
        now = datetime.now(timezone.utc)
        report = build_report(root, now)
    except DeltaError as exc:
        print(f"delta: {exc}", file=sys.stderr)
        return 2

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
