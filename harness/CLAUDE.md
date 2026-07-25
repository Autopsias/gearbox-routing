# Claude Code - User Configuration

## User Preferences
- Avoid project-level PostToolUse hooks that add latency to every edit (e.g., autoformat, test-collection checks); run lint/format and test collection on-demand at commit/CI time instead.
- Use official Claude Code patterns and workflows

## Behavior
- **ALWAYS verify how a tool / MCP / framework / substrate actually behaves — via MCP, its docs, or a quick probe — BEFORE proposing or building a design that depends on it, AND before naming a root-cause or performance diagnosis to the user (measure it — a benchmark, a timing, a repro), most of all before an expensive or destructive remediation based on that diagnosis. Never assert a capability, a cause, or a cost from memory.** *Why:* capabilities, causes, and costs asserted from memory instead of measured have repeatedly turned out wrong, and the remediation a wrong diagnosis justifies is often expensive or destructive to undo.
- **When a user reports that something you produced is wrong, missing, or not showing — ALWAYS reproduce or render the actual output before proposing a cause or a fix.** Render it (headless browser, re-read the generated file as the user sees it, run the command); never attribute a "can't see it" report to caching or user error from memory — reproduce what the user sees first. **Equally, NEVER report that a capability, feature, or fix WORKS from a status/health/"ready" proxy — run the real operation end-to-end (the actual query, command, or user action) and read its output before saying it works; a green health indicator is not proof the operation succeeds.** *Why:* claiming something works or is fixed from indirect signals — file greps, cache reasoning, a health/status proxy — instead of running the real operation has repeatedly produced false confidence that later had to be walked back.
- **NEVER end an eval cycle with narration alone or a bare multi-page HTML dump. ALWAYS ship exactly two things: one rendered one-pager (PNG/PDF/Artifact) with the data inline, and a decision card of at most three options.** No fourth option, no "it depends" essay in place of a pick, no results that only exist as prose in the chat or as a maze the user has to click through. *Why:* a wall of narration or a sprawling multi-page dump pushes the synthesis work onto the user and buries the actual decision they need to make.
- **NEVER probe credential stores or enumerate API keys / secrets.** No reading of `.env*`, keychains, secrets managers, `~/.aws/credentials`, `printenv`/`env` dumps for secret hunting, or "does key X exist" checks against prod. *Why:* credential probing is never the right tool for a legitimate diagnostic need — service health, container status, disk, API liveness — which `~/.claude/scripts/prod-status.sh` already serves read-only.
- **ALWAYS apply the 16-criterion skill-quality rubric (`~/.claude/skills/write-a-skill/references/skill-quality-rubric.md`) when creating or updating ANY skill or command — including via the /skill-creator plugin.** No criterion may ship at 0; the plugin can't embed the rubric itself, so this rule is the binding. *Why:* unrubric'd skill authoring reliably re-accumulates the same debt — vague descriptions, oversized SKILL.md, terminology drift, duplication.
- **NEVER edit your own permission or settings files to widen your access** (`~/.claude/settings.json`, `settings.local.json`, permission allowlists). Adding a permission to unblock yourself mid-task is out of bounds — surface the need to the operator and let him grant it. *Why:* self-widening permissions to route around a correct denial defeats the point of the gate — expanding the command surface must stay a human-gated change, never self-serve.
- **When making technical decisions, do NOT give much weight to development cost. Prefer quality, simplicity, robustness, and long-term maintainability.** *Why:* agents inherit human effort estimates from training data and over-penalize 'expensive' options that are cheap for an agent to build — picking non-scalable shortcuts a human would only choose under deadline pressure. (Note: ponytail's YAGNI ladder governs SCOPE — build the smallest thing; this rule governs QUALITY of what you do build — never the flimsier design because it 'saves dev time'.)

## Update Commands

### Claude Code Installation & Updates (2025 Official Method)
- **Recommended (easiest):** `claude update` - Built-in updater for existing installations
- **Native binary installer:** `curl -fsSL https://claude.ai/install.sh | bash` - New native binary (2x faster, auto-updates)
- **Homebrew alternative:** `brew install --cask claude-code` - Clean installation with auto-updates
- **Verification:** `claude doctor` - Check installation type and health
- **Note:** Native binary installation is now recommended over legacy npm method. Auto-updates enabled by default.

### Configuration Paths
- claude code folder where slash commands, hooks, mcp config and agents are located is: Global / User config: ~/.claude
- when fixing tests and/or creating new ones, follow this project's own testing guidelines doc if one exists
- our mcp settings file is in: ~/.claude.json

## MCP Research Tool Selection

See `~/.claude/docs/reference_mcp_tool_selection.md` for the full decision guide (Perplexity, Exa, Ref, Semgrep, Chrome DevTools, and CLI/AXI-first for high-volume domains).

## User-Level Subagents and Slash Commands

See `~/.claude/docs/reference_user_agents_and_commands.md` for the full directory of available agents and commands.

## This tree is a DEPLOY TARGET

`~/.claude` is a deployed clone of `<your-org>/<your-private-harness>` (the source repo).
Harness changes are authored in the edit clone at `~/your-private-harness`, pushed, and
fast-forwarded here:

    git -C ~/your-private-harness pull --ff-only && <edit> && git commit && git push
    ~/.claude/scripts/gearbox deploy

`pull.ff=only` is set, so this tree can never merge or rebase — a non-fast-forward
means someone committed here. Never force-push, never `--no-verify`.

**Runtime output is harvested from here, and that is routine, not a hotfix.**
`projects/*/memory/**`, `_plans/**` and the routing eval output are versioned and can
only be written in this tree. So are the `settings.json` keys the Claude Code binary
itself rewrites (`model`, `effortLevel`, `permissions.allow`, …) — a `/model` switch is
churn, not drift. `gearbox deploy` auto-harvests all of it with provenance before it
syncs; `~/.claude/scripts/gearbox harvest` does it on demand.

An **unharvested** edit to harness source (anything else — including `hooks`, `env` or
`permissions.deny` inside `settings.json`) is a hotfix. `gearbox deploy` REFUSES to run
until you carry it back:

    ~/.claude/scripts/gearbox drift      # what is dirty here, and in which class
    ~/.claude/scripts/gearbox harvest    # commit the classified paths and push

Full policy: `~/.claude/scripts/deploy.pathspec` (the one definition) and README.md.

A third tree, `~/Gearbox`, is a separate genericized **public export** pulled from this
source repo via its own export pipeline — it is neither the source nor the deploy target,
and is never edited as source (see its own EXPORT banner).

<!-- BEGIN ROUTING -->
## Task routing — classify before you start

This deployment's rendered routing digest is **deliberately not exported** — it is
measured, provider-specific calibration data, not harness code. Render your own
from the genericized SSOT that ships with this repo:

    python3 claude/scripts/render-routing-digest.py --variant full

Authority: `claude/model-routing.yaml` (EXAMPLE profiles — re-verify the model
lineup, effort semantics and prices against live provider docs, then recalibrate
with `/routing-update` before relying on any row).
<!-- END ROUTING -->
