# mcp-health.py — MCP auth & package-pinning health probe

**Usage:** `python3 ~/.claude/scripts/mcp-health.py [--days N] [--pin-only] [--json]`

Checks user-level MCP server configs (`~/.claude.json` top-level and per-project,
`~/.codex/config.toml`, `~/.cursor/mcp.json`) for unpinned `npx`/`uvx`/`bunx`/
`pnpm dlx`/`pipx run` launchers (never reads or prints env/headers), and (unless
`--pin-only` is passed) greps recent Claude Code session transcripts
(`~/.claude/projects/**/*.jsonl`, default 3-day lookback, `--days N` to widen)
for auth/quota failure signatures (401/403, Unauthorized, insufficient_quota,
invalid_api_key, expired token, ECONNREFUSED) attributed to the
`mcp__<server>__*` tool call that produced them. Prints unpinned launchers and a
per-server failure count + the most recent snippet; `--json` for
machine-readable output. Exit code is always 0 (informational).

Run it ad hoc, or wire it into an existing weekly/session-start health check
so a dead server (like Perplexity, dead 401/quota-exceeded for a full month
before anyone noticed — see `~/.claude/reflection-notes.md` #4) surfaces
within a day instead of thirty.
