#!/usr/bin/env python3
"""violations — "what should I worry about?"

Belt trust tool #2. A thin aggregator: it runs the belt's feeder inspectors
(v1: just delta), applies severity rules to their facts, and emits a compact
"### Violations" block. Deterministic, read-only, fail-loud, stdlib only —
same principles as delta.

Belt principle: one feeder down, keep going. A feeder that fails to run or
emits garbage becomes an operational note, not a crash.

Non-gating: findings never change the exit code. This is an informational
startup inspect, not a gate (gating is a later tool). The process exits
non-zero only if the aggregator itself fails to run.

Usage:
  python belt/violations.py           human "### Violations" block
  python belt/violations.py --json    {findings: [...], operational_notes: [...]}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# Worst-first ordering for grouping/display.
SEVERITY_ORDER = ("critical", "warn", "notice")

_DELTA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "delta.py")


# ---------------------------------------------------------------------------
# Feeders — each returns (data | None, note | None). A note means "unavailable".
# ---------------------------------------------------------------------------

def run_delta_feeder() -> tuple[dict | None, str | None]:
    """Run `delta --json` as a subprocess and parse it.

    Inherits cwd, so delta inspects the same repo violations was invoked in.
    Resolves the interpreter via sys.executable (same as delta's tests).
    Never raises for a feeder-level problem — returns an operational note.
    """
    try:
        proc = subprocess.run(
            [sys.executable, _DELTA_PATH, "--json"],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return None, f"delta feeder unavailable: could not launch ({exc})"
    if proc.returncode != 0:
        reason = proc.stderr.strip() or f"exit code {proc.returncode}"
        return None, f"delta feeder unavailable: {reason}"
    try:
        return json.loads(proc.stdout), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"delta feeder unavailable: unparseable output ({exc})"


# ---------------------------------------------------------------------------
# Rules — PURE fact->findings mappers. No git, no subprocess: unit-testable.
# ---------------------------------------------------------------------------

def _finding(severity: str, message: str, source: str = "delta") -> dict:
    return {"source": source, "severity": severity, "message": message}


def delta_findings(delta_json: dict) -> list[dict]:
    """Map a delta --json object to findings. Pure and total.

    Every finding traces to a concrete delta fact. current-state.md changing
    is expected on every park and is deliberately not surfaced.
    """
    findings: list[dict] = []

    memory = delta_json.get("memory", {})
    if memory.get("constraints_changed"):
        findings.append(
            _finding(
                "warn",
                "constraints.md changed since last session — verify you "
                "didn't weaken an invariant",
            )
        )
    if memory.get("canon_changed"):
        findings.append(
            _finding(
                "warn",
                "canon.md changed since last session — verify you didn't "
                "weaken an invariant",
            )
        )

    if delta_json.get("dependencies", {}).get("changed"):
        files = ", ".join(delta_json["dependencies"].get("files", [])) or "dependencies"
        findings.append(
            _finding("warn", f"dependencies drifted ({files}) — re-lock / re-verify")
        )

    wt = delta_json.get("working_tree", {})
    total = wt.get("total", 0)
    if total:
        findings.append(_finding("notice", f"{total} uncommitted files"))

    branch = delta_json.get("branch", {})
    ahead = branch.get("ahead") or 0
    if not branch.get("on_main") and ahead:
        name = branch.get("current") or "this branch"
        findings.append(
            _finding("notice", f"{ahead} commits on {name} not yet on main")
        )

    return findings


# ---------------------------------------------------------------------------
# Feeder registry — tools #2..N plug in with one entry: run() + rules().
# ---------------------------------------------------------------------------

FEEDERS = {
    "delta": {"run": run_delta_feeder, "rules": delta_findings},
}


def aggregate() -> dict:
    """Run every feeder, collect findings and operational notes."""
    findings: list[dict] = []
    operational_notes: list[str] = []
    for name, feeder in FEEDERS.items():
        data, note = feeder["run"]()
        if note is not None:
            operational_notes.append(note)
        if data is not None:
            findings.extend(feeder["rules"](data))
    return {"findings": findings, "operational_notes": operational_notes}


def _severity_rank(finding: dict) -> int:
    try:
        return SEVERITY_ORDER.index(finding["severity"])
    except ValueError:
        return len(SEVERITY_ORDER)  # unknown severities sort last


def render_human(report: dict) -> str:
    findings = sorted(report["findings"], key=_severity_rank)
    notes = report["operational_notes"]

    if not findings and not notes:
        return "Violations: clean"

    lines = ["### Violations"]
    if findings:
        for f in findings:
            lines.append(f"[{f['severity']}] {f['message']}")
    else:
        lines.append("(no findings)")
    if notes:
        lines.append("")
        lines.append("Operational notes:")
        for n in notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    as_json = "--json" in argv[1:]
    try:
        report = aggregate()
    except Exception as exc:  # aggregator itself failed — the only non-zero path
        print(f"violations: aggregator failed to run: {exc}", file=sys.stderr)
        return 2

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(render_human(report))
    return 0  # findings are informational; they never change the exit code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
