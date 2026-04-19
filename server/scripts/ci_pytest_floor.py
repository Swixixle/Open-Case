#!/usr/bin/env python3
"""Run pytest and enforce the repository regression floor (minimum passed tests)."""
from __future__ import annotations

import os

# Ensure the pytest child process inherits these before any test imports ``main`` (lifespan scheduler gate).
os.environ.setdefault("OPEN_CASE_TESTING", "1")
os.environ.setdefault("DISABLE_SCHEDULER", "1")

import re
import subprocess
import sys
from pathlib import Path

# server/scripts/ → repository root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

REGRESSION_FLOOR = 201


def main() -> int:
    env = os.environ.copy()
    env.setdefault("OPEN_CASE_TESTING", "1")
    env.setdefault("DISABLE_SCHEDULER", "1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    print(proc.stdout, end="")
    print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode != 0:
        return proc.returncode
    m = re.search(r"(\d+)\s+passed", out)
    if not m:
        print("ci_pytest_floor: could not parse passed count from pytest output", file=sys.stderr)
        return 1
    n = int(m.group(1))
    if n < REGRESSION_FLOOR:
        print(
            f"ci_pytest_floor: {n} passed < regression floor {REGRESSION_FLOOR}",
            file=sys.stderr,
        )
        return 1
    print(f"ci_pytest_floor: OK ({n} passed, floor {REGRESSION_FLOOR})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
