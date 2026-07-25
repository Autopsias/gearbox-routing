---
name: MCP Research Tool Selection
description: Decision guide for choosing between Perplexity, Exa, Ref, Semgrep, Chrome DevTools for research tasks
type: reference
---

## Quick Decision Guide

**Perplexity** (`perplexity_ask`)
- Simple technical questions or explanations
- Quick synthesis with citations
- Cost-sensitive queries
- General "how does X work?" questions

**GitHub Grep** (`mcp__grep__searchGitHub`)
- Finding real-world code examples
- API usage patterns in production code
- Use literal code patterns (e.g., `useState(`, `async function`)
- NOT keywords (e.g., "react tutorial")

**Exa Deep Research** (`deep_researcher_start` + `deep_researcher_check`)
- User explicitly requests "deep research" or "comprehensive analysis"
- Complex technical decisions requiring multiple sources
- Market research or detailed comparative analysis
- Use `model="exa-research-pro"` for complex topics

**Exa Web Search** (`web_search_exa`)
- Technical implementation details
- When Perplexity lacks sufficient depth
- Code-focused technical search

**Ref Documentation** (`ref_search_documentation` + `ref_read_url`)
- User explicitly requests official docs
- Conflicting info from other sources needs verification
- After 2+ failed integration attempts
- ONLY for authoritative verification (use sparingly)

**Semgrep Security** (`mcp__semgrep-hosted__*`)
- Security vulnerability scanning before commits
- Code quality and security analysis
- Use `security_check` for quick scans of code snippets
- Use `semgrep_scan` for comprehensive file analysis
- Always offer to fix identified vulnerabilities

**Chrome DevTools** (`mcp__chrome-devtools__*`)
- Browser automation and testing
- Visual debugging with screenshots (`take_screenshot`)
- Network request inspection (`list_network_requests`)
- Console message monitoring (`list_console_messages`)
- Page navigation and interaction (`navigate_page`, `click`, `fill`)

## Standard Flow

1. Simple question → **Perplexity**
2. Code examples → **GitHub Grep**
3. "Deep research" request → **Exa Deep Researcher**
4. Technical details → **Exa Web Search**
5. Docs verification → **Ref** (last resort)
6. Security scanning → **Semgrep** (before commits)
7. Browser testing → **Chrome DevTools** (UI validation)

## CLI/AXI-first for high-volume domains

For tool domains hit many times per session, a dedicated CLI beats the equivalent MCP server on success rate, cost, and speed — benchmarked by the AXI project (kunchenguid/axi) at 915 runs total (490 browser + 425 GitHub): 100% task success at $0.050-0.074/task, vs. 82-87% success at $0.101-0.148/task for MCP, with up to 12x cost advantage on complex GitHub investigations.

**Installed on this machine** (verified 2026-07-06, smoke-checked against a real repo and a real page — see provenance below):

- **GitHub operations** → prefer `gh` (already installed/authenticated), or `gh-axi` where its agent-ergonomic output pays for itself (TOON-style structured output, pre-computed CI status, contextual next-step hints).
- **Browser automation** → prefer `chrome-devtools-axi` over the `chrome-devtools` MCP server.

**MCP stays the right tool for:**
- Research tools without a good CLI equivalent (Exa, Ref, Perplexity)
- One-shot integrations not worth installing a CLI for
- Anything where no agent-ergonomic CLI exists yet

**Install provenance (pinned, not curl|sh):**

| Tool | Version | Binary path | Install method | Registry shasum |
|---|---|---|---|---|
| `gh-axi` | 0.1.25 | `~/.npm-global/bin/gh-axi` | `npm install -g gh-axi@0.1.25` | `15b37bb2eafda05ccef5f3a8b3f05413ce7b59a5` (tarball) |
| `chrome-devtools-axi` | 0.1.26 | `~/.npm-global/bin/chrome-devtools-axi` | `npm install -g chrome-devtools-axi@0.1.26` | `81401931f6cdd9b02f96a3190113d34d09535ff6` (tarball) |

Smoke-checked: `gh-axi issue list -R Autopsias/gearbox-routing` (real public repo, confirmed via `gh repo view`, returned 0 issues correctly); `chrome-devtools-axi open https://example.com` (returned accessibility snapshot with actionable refs).

## Best Practices

- Keep queries specific and focused
- Prefer multiple small queries over one broad query
- Use Perplexity first for cost optimization
- Reserve Ref for tiebreakers when sources conflict
- Include version numbers when relevant
