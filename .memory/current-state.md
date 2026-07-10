# Current State

## What is true right now
_Updated 2026-07-10 (park, violations):_ Two belt tools now committed on `feature/build-delta-belt-tool` (unmerged to `main`): delta (`8f95399`) and violations (`97425ad`). violations (`belt/violations.py`) is a thin, deterministic, read-only, fail-loud, stdlib-only aggregator — runs belt feeders (v1: delta only, via subprocess+JSON), maps facts to findings via the PURE `delta_findings()` function, emits `### Violations` + `--json`, strictly non-gating (findings never change exit code). Feeders in a `FEEDERS` registry (one-line extension); a downed feeder → operational note, not a crash. 11 belt tests pass. KNOWN false alarm: the `[warn] constraints.md changed` fires every run because `.memory/` is untracked (delta's `commits-since ∪ dirty` sees it as always-dirty) — fixed by tracking `.memory/`, which is the load-bearing reason for the next session's Part A.

---

_Updated 2026-07-10 (park):_ Branch `feature/build-delta-belt-tool`, one commit (`8f95399`) ahead of `main`. Belt tool #1, `delta`, is built and committed: `belt/delta.py` + `belt/tests/test_delta.py` (491 insertions). delta is a deterministic, read-only, stdlib-only git+fs script answering "what changed since last session?" — human `### Delta` report + `--json`; exits 2 (fail-loud) outside a git repo. Anchor = newest `.memory/sessions/session-*.md` (parses both full-timestamp and date+slug filename forms), else a 7-day fallback; anchor always printed. All 4 hermetic tests pass (0.44s). Runs under `uv run python` (no bare `python`; Python 3.14.3, pytest 9.0.2). Only `belt/` committed; `.memory/`, `ignition_data/`, `.mcp.json`, `Open-Case/`, `scripts/` remain untracked.

---

_Updated 2026-07-09 (park):_ `/park` now runs end-to-end in Claude Code — the deferred ignition + loadmem MCP tools load via ToolSearch and `ignition_current` / `memory_get_state` / `memory_save_session` all execute successfully. This closes the prior "next" item #1.

---

Branch `main`; no active IGNITION session. Test baseline per canon: 392 collected, CI floor >=201. Credentials: core `.env` keys present and FEC key valid in keychain; the source-referenced keys still missing are `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` (LLM router), `PROPUBLICA_API_KEY` (STOCK Act adapters), and `GOVINFO_API_KEY` (committee witnesses) — everything else flagged "missing" is a config/model-name toggle with a default and is not blocking. `constraints.md` is filled (signing / epistemic / publication invariants). GitHub `swixixle/Open-Case` has 0 open issues.

**The belt now runs in BOTH Cowork/Claude Desktop and Claude Code.** loadmem and ignition are wired into Claude Code via a project-scoped `.mcp.json` at the repo root — ignition uses the absolute `ignition-mcp` binary plus an explicit PATH env; loadmem uses the absolute `uv` + script path. Verified in a fresh Claude Code session launched from Open-Case: `ignition_current` and `memory_get_state` both returned real data.

## What changed in the last session
_2026-07-10 (park, violations):_ Built and committed belt tool #2, violations (`97425ad`), with tests. Verified with a before/after cross-check table proving every finding traces to a real delta fact AND every absent finding is correctly absent. Rule logic factored into the pure `delta_findings()` (unit-testable without git); feeder-down resilience tested via monkeypatch; integration test drives the real subprocess path against a temp repo. Diagnosed the permanent `constraints.md changed` false alarm (untracked `.memory/` → always dirty), which reframes tracking `.memory/` from tidiness to load-bearing.

---

_2026-07-10 (park):_ Built and committed belt tool #1, `delta`. Started an IGNITION session ("build delta belt tool") which created branch `feature/build-delta-belt-tool`. Wrote `belt/delta.py` (repo root resolved from cwd so tests can target a temp repo; "changed since anchor" = commits-since ∪ working-tree-dirty to catch uncommitted memory/dep changes) and `belt/tests/test_delta.py` (4 hermetic tests: commit/dirty/anchor detection, newest-anchor across both filename styles, no-session fallback, fail-loud outside git). Verified: tests pass; real-repo run cross-checked against raw `git status --porcelain` (delta correctly reported its own commit, 12→11 untracked). Committed only `belt/` as `8f95399`.

---

_2026-07-09 (park):_ Ran `/park` end-to-end in Claude Code for the first time (previously only the two underlying reads were verified). Loaded the deferred ignition + loadmem tools via ToolSearch; `ignition_current` returned `active: false` / branch `main`; saved session log `session-2026-07-09-park-verify-claude-code.md`. No code changes — belt/tooling verification run only.

---

First full ignite belt run (Keysmith / repo-memory / IGNITION / GitHub) was executed and dashboarded. Identified that repo-memory recorded nothing at session end, so "where I left off" came from canon alone — fixed by writing this file, `constraints.md`, and the first session log (all done from Cowork). Wrote a `/park` command at `~/.claude/commands/park.md`. Then wired loadmem + ignition into Claude Code via project `.mcp.json` and confirmed both resolve there. Also noted: no durable ignite plugin is installed (ignite exists only in transient session copies).

## What needs to happen next
_2026-07-10 (spec written, one combined session, hard ordering gate):_
PART A (git/pytest-verifiable) — merge `feature/build-delta-belt-tool` into `main` (fast-forward expected); gitignore `ignition_data/`; investigate the nested `Open-Case/` dir before touching it; gitignore `.mcp.json`; decide track-vs-ignore for `scripts/run_cruz_once.sh`; then `git add .memory/` + commit (NO `.memory/canon.md` exists — canon is in loadmem's store; check `constraints.yaml` for secrets first). ACCEPTANCE (non-obvious): criterion is `git status --porcelain` NO LONGER listing `.memory/constraints.md` — NOT `constraints_changed: False`. The tracking commit is itself a change-since-anchor, so `constraints_changed` legitimately stays True via commits-since until the NEXT park re-anchors past it. Render as a before/after table. HARD GATE before Part B.
PART B (live-run-verifiable, NOT pytest) — wire `python belt/violations.py` into the ignite plugin's DURABLE SKILL.md (different codebase) as the FIRST dashboard block above Keysmith; verify via a fresh ignite run showing the `### Violations` block on top.
Then: `test-health` — next belt tool, first feeder adding a new signal beyond delta.
NB: this very park writes a new anchor, so future violations runs will show `constraints_changed: False` once `.memory/` is tracked.

Older, still open (not urgent):
1. If touching LLM/narrative code, set `ANTHROPIC_API_KEY` or confirm the Perplexity path is sufficient.
2. Optional: install a durable ignite plugin so the belt survives across sessions.
