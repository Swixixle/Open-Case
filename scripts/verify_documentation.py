#!/usr/bin/env python3
"""
Verify documentation-aligned facts against the repository (no network).

Use for CI, pre-release, or external doc-quality tools (e.g. Debrief DCI).
Exit 0 if all checks pass, 1 otherwise.

Run from repository root:
  python3 scripts/verify_documentation.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fail(msg: str) -> None:
    print(f"VERIFY FAIL: {msg}", file=sys.stderr)


def check_pattern_rule_count() -> bool:
    sys.path.insert(0, str(REPO_ROOT))
    from engines.pattern_engine import PATTERN_RULE_IDS  # noqa: PLC0415

    n = len(PATTERN_RULE_IDS)
    if n != 18:
        _fail(f"PATTERN_RULE_IDS has {n} rules, README documents 18")
        return False
    print(f"OK: PATTERN_RULE_IDS count = {n}")
    return True


def check_client_package_scripts() -> bool:
    path = REPO_ROOT / "client" / "package.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    scripts = data.get("scripts") or {}
    required = ("dev", "build", "preview")
    missing = [s for s in required if s not in scripts]
    if missing:
        _fail(f"client/package.json missing scripts: {missing}")
        return False
    print(f"OK: client/package.json has scripts: {', '.join(required)}")
    return True


def check_assist_route_committed() -> bool:
    assist = REPO_ROOT / "routes" / "assist.py"
    main = REPO_ROOT / "main.py"
    if not assist.is_file():
        _fail("routes/assist.py missing")
        return False
    main_text = main.read_text(encoding="utf-8")
    if "assist_router" not in main_text:
        _fail("main.py does not include assist_router")
        return False
    print("OK: routes/assist.py and assist_router in main.py")
    return True


def check_ci_regression_floor_matches_script() -> bool:
    floor_path = REPO_ROOT / "server" / "scripts" / "ci_pytest_floor.py"
    text = floor_path.read_text(encoding="utf-8")
    m = re.search(r"REGRESSION_FLOOR\s*=\s*(\d+)", text)
    if not m:
        _fail("Could not parse REGRESSION_FLOOR from ci_pytest_floor.py")
        return False
    floor = int(m.group(1))
    print(f"OK: CI regression floor = {floor} (see server/scripts/ci_pytest_floor.py)")
    return True


def check_pytest_collect_meets_floor() -> bool:
    floor_path = REPO_ROOT / "server" / "scripts" / "ci_pytest_floor.py"
    text = floor_path.read_text(encoding="utf-8")
    m = re.search(r"REGRESSION_FLOOR\s*=\s*(\d+)", text)
    if not m:
        return False
    floor = int(m.group(1))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    cm = re.search(r"(\d+)\s+tests?\s+collected", out, re.I)
    if not cm:
        _fail("Could not parse pytest collect count")
        print(out[-2000:], file=sys.stderr)
        return False
    n = int(cm.group(1))
    if n < floor:
        _fail(f"pytest collects {n} tests; CI floor is {floor}")
        return False
    print(f"OK: pytest collects {n} tests (>= floor {floor})")
    return True


def main() -> int:
    print("Open Case — documentation verification")
    print(f"Repo: {REPO_ROOT}")
    ok = True
    ok = check_pattern_rule_count() and ok
    ok = check_client_package_scripts() and ok
    ok = check_assist_route_committed() and ok
    ok = check_ci_regression_floor_matches_script() and ok
    ok = check_pytest_collect_meets_floor() and ok
    if ok:
        print("\nAll verification checks passed.")
        return 0
    print("\nOne or more checks failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
