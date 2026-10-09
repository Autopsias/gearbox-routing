# Gearbox

**The right gear for every task.**

[![verify](https://github.com/Autopsias/gearbox-routing/actions/workflows/verify.yml/badge.svg)](https://github.com/Autopsias/gearbox-routing/actions/workflows/verify.yml)

Gearbox tells a coding agent which model to use for each kind of task, and how
hard to make it think. It also ships an optional set of Claude Code skills,
commands, subagents and hooks that you can install one module at a time.

## What you get

| Part | What it is | Install |
|---|---|---|
| **Routing framework** — `claude/` | One versioned policy file (`model-routing.yaml`) that maps five task classes to a model tier and an effort level, for Anthropic, OpenAI, Gemini or Z.ai. Scripts check the file, resolve a route, and write a routing table into your `CLAUDE.md`. | `install.sh` — one command, with backups and an uninstall |
| **Harness modules** — `harness/` | About 30 optional modules from one working Claude Code setup: a plan runner, two-model code review, git safety hooks, a status line, cost reports and more. | By hand, one module at a time. Each module has a card with its install, check and remove steps. |

The two parts are independent. Take one, the other, or both.

## Quickstart — the routing framework

Install into a scratch folder first. The installer never writes to your real
`~/.claude` unless you ask twice.

```bash
git clone https://github.com/Autopsias/gearbox-routing.git
cd gearbox-routing
./install.sh --claude-home /tmp/gearbox-try --accept-example-profile --provider anthropic
```

The last lines show `guard: PASS`. Look at `/tmp/gearbox-try/CLAUDE.md` to see
the routing table it wrote:

| Class | Cues | Resolved tier (Anthropic example) |
|---|---|---|
| mechanical | rename, format, codemod, doc edit | claude-haiku-4-5 |
| standard_build | CRUD, wiring, templated feature | claude-sonnet-5 · medium |
| agentic_build | multi-file, integration, non-obvious debug | claude-sonnet-5 · high |
| deep_reasoning | architecture, security, hard root cause | claude-opus-4-8 · high |
| linchpin | one-shot irreversible call | claude-opus-4-8 · high |

The model ids and prices are **dated examples**, which is why
`--accept-example-profile` is required. Check them against your provider
before you rely on them ([`docs/PROVIDERS.md`](docs/PROVIDERS.md)).

Next: [`docs/INSTALL.md`](docs/INSTALL.md) — install into your real Claude
home, change the provider, update and remove.

## Pick harness modules

Browse the catalogue in [`docs/MODULES.md`](docs/MODULES.md). Each card says
what the module does, what it needs, the exact copy commands, how to check it,
how to remove it, and what it does without asking.

Install only what you will use. Claude Code puts the name and description of
every installed skill, command and subagent into its context on every turn.

Not sure where to start? These three are small and stand alone:

- [Git safety guard](docs/modules/git-safety.md) — blocks `git reset --hard` and similar commands when you have uncommitted work.
- [Status line](docs/modules/statusline.md) — shows rate limits, context use and cache state.
- [Grilling](docs/modules/grilling.md) — interviews you about a plan before you build it.

## How routing works

For every task, Gearbox answers two questions separately:

1. **Which model?** A capability tier (`cheap_fast`, `workhorse`,
   `frontier_reasoner`), not a fixed model id. Each provider profile maps the
   tiers to its own models.
2. **How hard should it think?** An effort intent (`light`, `standard`,
   `thorough`). Each provider profile translates it into that provider's own
   control: Anthropic `effort`, OpenAI `reasoning_effort`, Gemini
   `thinking_level`. These controls differ in kind, so Gearbox never assumes
   one provider's "high" equals another's.

To change provider, you edit one line (`active_provider:`). The task classes
stay the same. When a task fails twice at the same root cause, the resolver
walks an escalation ladder: more effort first, then a stronger tier.
[`ARCHITECTURE.md`](ARCHITECTURE.md) has the full schema.

## Documentation

| I want to… | Read |
|---|---|
| Install, update or remove | [`docs/INSTALL.md`](docs/INSTALL.md) |
| Choose harness modules | [`docs/MODULES.md`](docs/MODULES.md) |
| Understand the routing model | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Calibrate the policy for my own models | [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) |
| Choose or switch a provider | [`docs/PROVIDERS.md`](docs/PROVIDERS.md) |
| Call the resolver from my own tool or CI | [`docs/INTEGRATION.md`](docs/INTEGRATION.md) |
| Know what changed | [`CHANGELOG.md`](CHANGELOG.md) · versioning rules: [`docs/VERSIONING.md`](docs/VERSIONING.md) |
| Contribute | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Report a security issue | [`SECURITY.md`](SECURITY.md) |
| Understand how `harness/` is exported | [`docs/HARNESS.md`](docs/HARNESS.md) · [`GENERICIZATION.md`](GENERICIZATION.md) |

## Requirements

- **Claude Code**, or another agent that reads a `CLAUDE.md` file.
- **bash**, **git** and **Python 3**. The routing scripts use only the Python
  standard library. CI runs them on Python 3.12.
- **For `/routing-update`:** one research MCP server (Exa, Ref or Perplexity).
  It checks model ids and prices against live docs instead of memory.
- Harness modules list their own needs on their cards.

## Status

- **Routing framework:** policy version 2.4.0. CI runs the drift check, the
  unit tests, a docs check and a scratch install on every pull request and on
  every push to `master`.
- **Harness:** a snapshot of one person's working setup, refreshed from time
  to time. CI does not run the harness tests, because many of them expect a
  live `~/.claude`, macOS, or tools a CI runner does not have. The module cards
  say which parts are stable, experimental or author-specific.

## About this repo

`harness/` is generated. An export script copies it from a private source
repo, removes personal data, and scans the result
([`docs/HARNESS.md`](docs/HARNESS.md)). A change made directly in `harness/`
is lost at the next export. Everything else — the docs, `claude/`,
`install.sh` and `scripts/` — is maintained here.

If you clone this repo to push changes, run `git config core.hooksPath .githooks`
first. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Credits

- Several harness skills (`grill-me`, `grill-with-docs`, `tdd`,
  `improve-codebase-architecture`, `setup-matt-pocock-skills`) are adapted from
  [mattpocock/skills](https://github.com/mattpocock/skills) (MIT).
- `harness/scripts/repo-profile-cache.py` is adapted from
  [everyinc/compound-engineering-plugin](https://github.com/everyinc/compound-engineering-plugin)
  (MIT).
- `harness/skills/repo-health/vendor/` holds a pinned copy of Sentry's
  `gha-security-review` skill (Apache-2.0; its licence ships beside it).

## License

[MIT](LICENSE)
