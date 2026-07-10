## What is true right now
Branch `main`; no active IGNITION session. Test baseline per canon: 392 collected, CI floor >=201. Credentials: core `.env` keys present and FEC key valid in keychain; the source-referenced keys still missing are `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` (LLM router), `PROPUBLICA_API_KEY` (STOCK Act adapters), and `GOVINFO_API_KEY` (committee witnesses) — everything else flagged "missing" is a config/model-name toggle with a default and is not blocking. `constraints.md` is filled (signing / epistemic / publication invariants). GitHub `swixixle/Open-Case` has 0 open issues. The ignite belt runs in BOTH Cowork/Claude Desktop and Claude Code via the repo-root project `.mcp.json`.

## What changed in the last session
Ran `/park` end-to-end in Claude Code for the first time — this closes the previously-open "next" item #1 (only the two underlying reads were verified before, not the full command). In this Claude Code session, the deferred ignition and loadmem MCP tools were loaded via ToolSearch and executed successfully: `ignition_current` (returned `active: false`, branch `main`), `memory_get_state`, and `memory_save_session` all resolved and returned real data. No code changes were made this session; it was a belt/tooling verification run.

## What needs to happen next
Not urgent:
1. If touching LLM/narrative code, set `ANTHROPIC_API_KEY` or confirm the Perplexity path is sufficient.
2. Optional: install a durable ignite plugin so the belt survives across sessions (ignite still exists only in transient session copies).
