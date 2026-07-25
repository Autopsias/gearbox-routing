# mcp-health.py — MCP auth health probe

**Usage:** `python3 ~/.claude/scripts/mcp-health.py [--days N] [--json]`

Scans `~/.claude.json` for configured user-level MCP servers and greps recent
session transcripts (`~/.claude/projects/**/*.jsonl`, default 3-day lookback,
`--days N` to widen) for auth/quota failure signatures (401/403,
Unauthorized, insufficient_quota, invalid_api_key, expired token,
ECONNREFUSED) attributed to the `mcp__<server>__*` tool call that produced
them. Prints a per-server failure count + the most recent snippet; `--json`
for machine-readable output. Exit code is always 0 (informational).

Run it ad hoc, or wire it into an existing weekly/session-start health check
so a dead server (like Perplexity, dead 401/quota-exceeded for a full month
before anyone noticed — see `~/.claude/reflection-notes.md` #4) surfaces
within a day instead of thirty.
