# Session note — 2026-07-05 — KeySmith inject fix + account-wide cleanup sweep

> Purpose: start the next session from the decisions already made, not from reconstructing them.
> Most of this note is about **KeySmith** and an **account-wide sweep**, not Open-Case specifically —
> it lives here because this is where the `.memory` scaffold and `repo-memory` session packets are read.
> If `repo-memory` is per-project, this will only surface while working in Open-Case; move it to a
> project-neutral home if that mismatch matters.

## What shipped this session

**KeySmith `inject` bug — fixed and committed as `f762ece`** on branch `fix/inject-exec-and-receipt`
(off tag `v0.6.0`), **PUSHED** to `origin/fix/inject-exec-and-receipt` (verified 2026-07-05: remote tip
= local HEAD = `f762ece`, ahead=0/behind=0). Not yet merged to the default branch / no PR opened.

The old `inject` set `os.environ` inside a subprocess that then exits, so the variable never reached
the caller — it was non-functional. Worse, it signed a false `credential_injected` receipt for a
delivery that never happened. The fix splits the one method into two broker methods with honest,
distinct claims:

- **`inject`** — exec-and-handoff via `os.execvpe`. Signs `credential_handed_off`, the honest weaker
  claim: once the process is replaced by the child, the child can't be observed from here, so we can
  only attest the hand-off, not the use.
- **`set_in_process`** — the `os.environ` write for the long-lived MCP server, which legitimately
  signs `credential_injected` because the assignment happens in a process that keeps running.

A shared **`_rotation_gate`** helper means both paths enforce rotation identically. A `shutil.which`
pre-check runs **before** signing, so a bad/missing command fails without ever writing a receipt.
Tests fork per method and assert against the ledger — no receipt on failure, no parent-env leak —
and were verified to actually bite against the exact regressions they cover (not green theater).

**The one false pre-fix receipt was repudiated, not deleted.** The stale `credential_injected` entry
at `2026-07-05T02:27:19Z` in `~/.keysmith/receipts/Open-Case.jsonl` (produced by a pre-fix CLI run)
got a signed **`receipt_superseded`** entry appended, referencing the target by its full signature,
with same-key proof (round-trip: the signer's private key verified under the target's recorded public
key). Append-only discipline preserved. **Limitation to know:** the repudiation is discoverable by a
full-ledger reader, but it does **not** make the target entry self-aware of its supersession — there
is no back-reference resolution in the schema.

## Deferred cleanup (none urgent — all next-session; reasoning included)

1. **History-exposure audit is only PARTIALLY answered.** gitleaks scanned history across 33 repos
   this session — one real hit (Lantern, item 3), the rest false positives (recursive `out/NN/repo/...`
   build-output doc snapshots; parameter-name matches like `private_key: ... = None`). But "no tracked
   `.env`" and "gitleaks clean" are *current-state* claims. A fuller audit across remotes is still open.

2. **TWO GitHub accounts exist:** `Swixixle` (most repos) and `AlexMaksim39586` (RACK's remote). This
   session's inventory reasoned mostly about one account. It is **not account-complete** — flag for a
   real cross-account pass.

3. **Lantern — NON-FINDING, recorded so it isn't lost.** `LANTERN_API_KEY` is Lantern's own auth token
   (`requireAuth` checks the incoming Bearer against it — `server/routes.ts:172`), committed in `.replit`
   at `d056b783`, which is an ancestor of `origin/main` (pushed). Also: `/api/auth/demo-key`
   (`routes.ts:342`) returns the token with **no auth gate**. Both are moot — Lantern was never really
   run, there's been no Replit account for ~6 months, nothing is deployed. **If it is EVER redeployed:**
   gate/delete the `demo-key` route **and** generate a fresh token before it goes public. Until then, inert.

4. **HALO-RECEIPTS diverged `[ahead 1, behind 12]`** — needs a merge-vs-rebase decision made with a clear
   head; a wrong reconcile loses work. Untouched this session.

5. **RACK — 48 uncommitted changes** (real build-out: ~20 modified core files + new modules) **PLUS**
   accidental nested `RACK/` and `RACK-1/` directories. Understand the nesting **before** committing —
   do not `git add` with stray repo-copies inside the repo. Deserves its own scoped session.

6. **Credential hygiene account-wide:** plaintext `.env` everywhere, keychain adoption ~zero (only
   Open-Case's FEC key so far). All    `.env` are gitignored (verified). Largest stores: **debrief**
   (cloud-only — no local `.env`, secrets live in Render, **skip**), **Personal-Podcast** (enumeration
   done 2026-07-05: exactly **one** `.env`, 9 assignment lines; the `keysmith summary` "16" was
   `.env.example`, which summary counts because it globs `.env*` — **list is complete: 8 real secrets**
   + `RECEIPTS_USER_AGENT` config, no signing key), then Open-Case / orchestrator-repo / etc.
   The KeySmith adoption rollout is now unblocked since `inject` works — migrate high-count repos the
   way Open-Case was done: per-repo, confirming each app's env-resolution before editing any `.env`.

## Open decisions carried forward

- `f762ece` is already pushed. Remaining decision: open a PR / merge `fix/inject-exec-and-receipt`
  into the default branch, or hold for a cold re-read first.
- **Personal-Podcast:** enumeration done (one `.env`, 8 real secrets). Next: `connect` loop (you
  paste), `keysmith doctor` to confirm `valid_keychain`, then the never-run integration acceptance
  test — comment (not delete) the secrets in `.env` and boot the app through inject. NOTE: `inject`
  is one-handle-one-exec; multi-secret boot works via an N-deep nested chain. This is now
  **empirically verified 2026-07-05**: a clean-room 8-deep chain with dummy handles delivered all 8
  vars (PRESENT [0..7], MISSING [], exit 0) — so nesting is FUNCTIONAL at depth 8, not just depth 2.
  `child_env = {**os.environ, target_env: raw}` (`vault.py:341`) accumulates across process
  replacements as intended. Therefore a manifest-driven `inject-all`/`run` is **ergonomic polish,
  not a capability blocker** — and the real app boot now cleanly isolates any failure as app-side
  env-resolution (import-time vs lazy reads / caching before inject), NOT the injection mechanism.
  PROCESS MODEL verified 2026-07-05: entry point is a console script (`receipts-ask = engine.cli:main`),
  a bare single Python process — no npm/gunicorn/supervisor, no ProcessPool/multiprocessing/fork in
  `src/`. So inject's direct child IS the process that reads the keys (no process-model hop). Import-time
  env risk is also neutralized for the exec path: `execvpe` sets `os.environ` before the interpreter
  starts, so module-level reads see the vars (that risk applies to `set_in_process`/MCP, not CLI inject).
  Resolution is env-first (`src/utils/secrets.py get_secret`), and hard-require adapters read
  `os.environ["…"]` directly (`congress.py:95`, `perplexity.py:172`) — injected env wins. CAVEAT: the app
  has its OWN off-by-default macOS-keychain fallback (`RECEIPTS_USE_KEYCHAIN=1` → `security
  find-generic-password -s <NAME>`), a different scheme than KeySmith (service `keysmith`, account =
  `sec://…`). Don't conflate; leave `RECEIPTS_USE_KEYCHAIN` off for the inject test.
- **RACK:** keep-or-archive after understanding the nested dirs (history scan already came back clean).
