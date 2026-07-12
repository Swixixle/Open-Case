# Current State

## What is true right now
_Updated 2026-07-11 (park, rotate-api-key):_ The leaked Open-Case admin API key formerly hardcoded in `scripts/run_cruz_once.sh` is ROTATED and de-hardcoded. A fresh key was minted for handle `localhost_d495f9e7` via `POST /api/v1/auth/keys` (re-issue revokes the prior key by overwriting its stored SHA-256 hash), stored in `.env` as `OPEN_CASE_API_KEY` (gitignored); the script now reads `AUTH="Authorization: Bearer ${OPEN_CASE_API_KEY:?set OPEN_CASE_API_KEY}"` (zero hardcoded tokens). Verified live: new key → `POST /cases` 200; old key → 401. Both `.env` and the script are gitignored, so the key can never be committed. `main` unchanged at `945f14a` (rotation touched only gitignored files — nothing to commit). Belt unchanged: 13 tests pass, delta anchors correctly. Residual: rotation only revoked the key in the LOCAL sqlite DB (moot unless a deployed instance ever accepted it).

---

_Updated 2026-07-10 (park, fix-delta-anchor):_ Branch `main` at `945f14a`. Full belt arc on main: `8f95399` (delta), `97425ad` (violations), `df9d407` (gitignore), `a684606` (track `.memory/`), `945f14a` (delta anchor fix). delta's `resolve_anchor` now ranks sessions and derives `--since` by `effective_dt = max(filename timestamp, file mtime)`, so date-only `/park` logs no longer undersort beneath a same-day full-timestamp log. 13 belt tests pass. On this repo delta now anchors to the merge-track park (since `05:54:08Z`), `constraints_changed: False`, violations warn gone. Limitation (documented in code): mtime-based anchoring is fragile to a fresh `git clone`/`checkout`. `/park` still writes readable date-slug names (reader handles them; writer not normalized).

---

_Updated 2026-07-10 (park, merge+track+wire):_ Branch `main` at `a684606`. Both belt tools (delta `8f95399`, violations `97425ad`) are MERGED to `main` (fast-forward), plus `df9d407` (gitignore session/runtime paths) and `a684606` (track `.memory/`). Working tree clean. `.memory/` is now git-tracked (canon.md was already tracked); gitignored: `ignition_data/`, `belt/state/`, `/.mcp.json`, `/scripts/run_cruz_once.sh`, `/Open-Case/`. The permanent `constraints.md changed` false alarm is FIXED — constraints.md tracked & clean, no longer dirty; residual `constraints_changed: True` is commits-since (a684606) and clears at THIS park's new anchor. `/park` fixed to `--parking leave` (v0.8.0 `stop` has no `--note`). Phase B (dashboard wiring) BLOCKED here — no durable ignite SKILL.md on disk in Claude Code — deferred to Cowork with a handed-off snippet. Flags: `scripts/run_cruz_once.sh` embeds a live bearer token (gitignored; rotate it); nested `/Open-Case/` is a full separate git repo (gitignored; investigate/remove).

---

_Updated 2026-07-10 (park, violations):_ Two belt tools now committed on `feature/build-delta-belt-tool` (unmerged to `main`): delta (`8f95399`) and violations (`97425ad`). violations (`belt/violations.py`) is a thin, deterministic, read-only, fail-loud, stdlib-only aggregator — runs belt feeders (v1: delta only, via subprocess+JSON), maps facts to findings via the PURE `delta_findings()` function, emits `### Violations` + `--json`, strictly non-gating (findings never change exit code). Feeders in a `FEEDERS` registry (one-line extension); a downed feeder → operational note, not a crash. 11 belt tests pass. KNOWN false alarm: the `[warn] constraints.md changed` fires every run because `.memory/` is untracked (delta's `commits-since ∪ dirty` sees it as always-dirty) — fixed by tracking `.memory/`, which is the load-bearing reason for the next session's Part A.

---

_Updated 2026-07-10 (park):_ Branch `feature/build-delta-belt-tool`, one commit (`8f95399`) ahead of `main`. Belt tool #1, `delta`, is built and committed: `belt/delta.py` + `belt/tests/test_delta.py` (491 insertions). delta is a deterministic, read-only, stdlib-only git+fs script answering "what changed since last session?" — human `### Delta` report + `--json`; exits 2 (fail-loud) outside a git repo. Anchor = newest `.memory/sessions/session-*.md` (parses both full-timestamp and date+slug filename forms), else a 7-day fallback; anchor always printed. All 4 hermetic tests pass (0.44s). Runs under `uv run python` (no bare `python`; Python 3.14.3, pytest 9.0.2). Only `belt/` committed; `.memory/`, `ignition_data/`, `.mcp.json`, `Open-Case/`, `scripts/` remain untracked.

---

_Updated 2026-07-09 (park):_ `/park` now runs end-to-end in Claude Code — the deferred ignition + loadmem MCP tools load via ToolSearch and `ignition_current` / `memory_get_state` / `memory_save_session` all execute successfully. This closes the prior "next" item #1.

---

Branch `main`; no active IGNITION session. Test baseline per canon: 392 collected, CI floor >=201. Credentials: core `.env` keys present and FEC key valid in keychain; the source-referenced keys still missing are `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` (LLM router), `PROPUBLICA_API_KEY` (STOCK Act adapters), and `GOVINFO_API_KEY` (committee witnesses) — everything else flagged "missing" is a config/model-name toggle with a default and is not blocking. `constraints.md` is filled (signing / epistemic / publication invariants). GitHub `swixixle/Open-Case` has 0 open issues.

**The belt now runs in BOTH Cowork/Claude Desktop and Claude Code.** loadmem and ignition are wired into Claude Code via a project-scoped `.mcp.json` at the repo root — ignition uses the absolute `ignition-mcp` binary plus an explicit PATH env; loadmem uses the absolute `uv` + script path. Verified in a fresh Claude Code session launched from Open-Case: `ignition_current` and `memory_get_state` both returned real data.

## What changed in the last session
_2026-07-11 (park, rotate-api-key):_ Rotated the leaked localhost admin token and fixed the root cause (no belt work). Confirmed the mechanism in `routes/auth.py` first; started the local sqlite server, minted a new key with `X-Admin-Secret`, wrote it to `.env` as `OPEN_CASE_API_KEY`, and de-hardcoded `scripts/run_cruz_once.sh` via `sed` (env var + `:?` fail-fast). Proof: new key `POST /cases` = 200, old key = 401; env-wiring tested both ways. Recalibrated risk mid-task: localhost admin token, hygiene not emergency. One throwaway `RotationVerify` case row left in the local dev DB; server stopped after. No git commits (both edited files gitignored).

---

_2026-07-10 (park, fix-delta-anchor):_ Fixed a delta anchor correctness bug (`945f14a`, merged to main). Root cause: delta ranked session logs purely by filename timestamp; `/park`'s date-only names parse to midnight, so a same-day full-timestamp log outranked later date-only parks — delta anchored to a stale 01:35Z session, swept in 3 later sessions, and fed a false `constraints_changed: True` to violations. Fix: `resolve_anchor` uses `effective_dt = max(filename_dt, mtime)` (repairs legacy on-disk logs too). Updated the 2 anchor tests that conflated filename-date with `since` (now pin mtimes); added 2 regressions (same-day date-only-vs-timestamp = the actual failing case; mtime tiebreak). Verified real repo: anchor stale→merge-track (`05:54:08Z`), `constraints_changed` True→False, warn gone. Chose to fix the reader (delta); left `/park` writing readable date-slug names.

---

_2026-07-10 (park, merge+track+wire):_ Phase 0: fixed `park.md` → `--parking leave`. Phase A: fast-forward merged the belt branch to `main`; added `.gitignore` entries (commit `df9d407` — caught+fixed a bug where inline trailing `#` comments no-op'd 4/5 patterns; `.gitignore` has no inline comments); tracked `.memory/` (commit `a684606`, no secrets). Verified with a before/after table: constraints.md no longer in `git status --porcelain` (load-bearing PASS); residual `constraints_changed: True` proven commits-since (a684606), not dirty. Investigation: nested `/Open-Case/` = full separate git repo (gitignored); `run_cruz_once.sh` embeds a live bearer token (gitignored, rotate); spec's "no canon.md" premise was wrong (exists AND already tracked). Phase B BLOCKED: no durable ignite SKILL.md on disk in Claude Code → deferred to Cowork with a wiring snippet.

---

_2026-07-10 (park, violations):_ Built and committed belt tool #2, violations (`97425ad`), with tests. Verified with a before/after cross-check table proving every finding traces to a real delta fact AND every absent finding is correctly absent. Rule logic factored into the pure `delta_findings()` (unit-testable without git); feeder-down resilience tested via monkeypatch; integration test drives the real subprocess path against a temp repo. Diagnosed the permanent `constraints.md changed` false alarm (untracked `.memory/` → always dirty), which reframes tracking `.memory/` from tidiness to load-bearing.

---

_2026-07-10 (park):_ Built and committed belt tool #1, `delta`. Started an IGNITION session ("build delta belt tool") which created branch `feature/build-delta-belt-tool`. Wrote `belt/delta.py` (repo root resolved from cwd so tests can target a temp repo; "changed since anchor" = commits-since ∪ working-tree-dirty to catch uncommitted memory/dep changes) and `belt/tests/test_delta.py` (4 hermetic tests: commit/dirty/anchor detection, newest-anchor across both filename styles, no-session fallback, fail-loud outside git). Verified: tests pass; real-repo run cross-checked against raw `git status --porcelain` (delta correctly reported its own commit, 12→11 untracked). Committed only `belt/` as `8f95399`.

---

_2026-07-09 (park):_ Ran `/park` end-to-end in Claude Code for the first time (previously only the two underlying reads were verified). Loaded the deferred ignition + loadmem tools via ToolSearch; `ignition_current` returned `active: false` / branch `main`; saved session log `session-2026-07-09-park-verify-claude-code.md`. No code changes — belt/tooling verification run only.

---

First full ignite belt run (Keysmith / repo-memory / IGNITION / GitHub) was executed and dashboarded. Identified that repo-memory recorded nothing at session end, so "where I left off" came from canon alone — fixed by writing this file, `constraints.md`, and the first session log (all done from Cowork). Wrote a `/park` command at `~/.claude/commands/park.md`. Then wired loadmem + ignition into Claude Code via project `.mcp.json` and confirmed both resolve there. Also noted: no durable ignite plugin is installed (ignite exists only in transient session copies).

## What needs to happen next
_2026-07-11 (post rotate-api-key):_
SESSION 2 — build `test-health` (belt tool #3): first feeder adding a new signal beyond delta; gitignored append-only history at `belt/state/test-health-history.jsonl` (`belt/state/` already gitignored), pure analysis functions + hermetic tests, `--run`/no-run/`--json`. Then wire test-health into violations' `FEEDERS` registry (small; ask for spec).
PART B — DEFERRED TO COWORK (live-run-verifiable): add the handed-off violations startup step to the ignite `SKILL.md` (first dashboard block above Keysmith, fallback line if not a belt repo); verify via a fresh ignite run. (Blocked from Claude Code — no durable ignite SKILL.md on disk here.)
Residual security note: the key rotation only revoked in the LOCAL sqlite DB — if a deployed Open-Case instance ever accepted that key, mint a fresh one there too (almost certainly moot — localhost-only token).
Optional follow-ups: normalize `/park` to full-timestamp filenames (belt-and-suspenders vs the mtime-checkout fragility); investigate/remove the nested `/Open-Case/` repo.
NB: delta now anchors correctly (mtime-aware), so `constraints_changed` reads False on this repo.

Older, still open (not urgent):
1. If touching LLM/narrative code, set `ANTHROPIC_API_KEY` or confirm the Perplexity path is sufficient.
2. Optional: install a durable ignite plugin so the belt survives across sessions.
