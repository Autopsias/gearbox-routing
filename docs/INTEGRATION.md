# Integration — binding Gearbox into consumers

Gearbox is a policy file plus a resolver, not a fork of your build tooling. A
consumer (a plan orchestrator, a lint step, a CI check) binds to
`claude/model-routing.yaml` by **reading** it — never by inlining a copy of
its own routing rules that can silently drift. This doc covers the two real
bindings this repo ships against (a rubric-lint binding and a
resolver/ladder binding), what to do if your consumer is neither shape, and
what Gearbox does and does not require alongside it.

---

## 1. The packaged resolver — the reference escalation/degrade path

`claude/scripts/resolve_route.py` is Gearbox's own escalation/degrade
resolver: a small, provider-neutral function of `(task_class,
active_provider, current, signal)` — see `ARCHITECTURE.md` §3 "Resolver
contract" for the full normative shape. It is the reference implementation
every provider profile's schema is tested against, and it is how
"self-calibrating / provider-agnostic" holds for the **degrade path on any
provider** — not just the one this framework happened to be extracted from.

Any harness can call it directly:

```python
from claude.scripts.resolve_route import resolve

decision = resolve(
    task_class="agentic_build",
    active_provider="anthropic",   # or openai, gemini, or your own profile
    current=None,                  # None on the first call for this task
    signal="none",                 # none | failure | refusal | entitlement | unavailable
)
# -> {"model_id": "...", "native_effort": "..."} or "exhausted"
```

A provider profile that opts out of automatic escalation/degrade
(`escalation: none` / `degrade: none`) simply returns `exhausted` sooner —
there is **no silent fallback to the Anthropic profile's shape**. A consumer
targeting a provider with no ladder gets an explicit "no automatic
escalation/degrade" answer from the resolver, never a default borrowed from
another provider.

`resolve_route.py` also exports `escalate(task_class, active_provider,
current, reason=None)` and `degrade(task_class, active_provider, current,
signal="unavailable")` — thin convenience wrappers over `resolve(...,
signal="failure")` / `resolve(..., signal=signal)` respectively, with no
ladder logic of their own. Use them when the call site reads more naturally
as "escalate after this failure" / "degrade for this signal" than as a bare
`signal=` string; `resolve()` remains the one normative entry point the
schema in `ARCHITECTURE.md` §3 is defined against, so the three functions
can never drift apart from each other. The two extra parameters are
DELIBERATELY named differently: `escalate()`'s `reason` is inert,
caller-side audit prose (e.g. "2 failures at the same root cause") that is
never evaluated in logic; `degrade()`'s `signal` is a real dispatch value
forwarded straight into `resolve()`'s `signal=` and validated against the
provider's own `degrade.signals` list. Neither function decides *whether*
escalating/degrading is warranted; that decision stays with the caller per
the stateless resolver contract.

See `claude/fixtures/route-resolver/` for `resolve_route.py`'s own unit
tests: baseline resolve for each example provider, a full escalation rung
sequence, a degrade step, and the "provider declares no ladder" path
(including a fixture proving `degrade.signals`'s `signals:` key is read as a
plain string list, never coerced to a boolean the way a bare `on:` key would
be by a YAML-1.1-style loader — this resolver never uses one, and the key is
named `signals:` rather than `on:` precisely to avoid that trap outright).

## 2. One consumer that mirrors the ladder in code: Claude Code's `run.py`

Some harnesses can't call a Python module directly at dispatch time (their
dispatcher already hardcodes constants for a completely different reason —
e.g. speed, or an existing non-Python runner). Claude Code's
`plan-execute` skill is exactly this case: its `scripts/run.py` carries its
own `_FALLBACK_LADDER` / `_DEGRADE_EFFORT` dicts, used at dispatch time
without importing `resolve_route.py`. That mirror is a second, harness-local
copy of the **same ladder the SSOT and `resolve_route.py` express** — and it
is only safe because it's guard-verified against the SSOT (byte- or
contract-checked), never a freestanding copy that can drift unnoticed.

```python
# claude/skills/plan-execute/scripts/run.py (excerpt — Claude Code's dispatcher)
#
# SSOT LOCKSTEP: this dict is CODE-AUTHORITATIVE per model-routing.yaml's header
# carve-out — the SSOT MIRRORS this constant, not the other way round.
# verify-routing.sh ast-parses this file (never imports/executes it) and fails
# the drift guard if `_FALLBACK_LADDER` byte-diverges from the SSOT
# `degradation.ladder` block. Edit both sides together; the guard is what keeps
# them honest, not this comment.
_FALLBACK_LADDER = {"fable": "opus", "opus": "sonnet"}
_DEGRADE_EFFORT = {"opus": "xhigh", "sonnet": "high"}  # keyed by TARGET model
```

**What the byte-match binding actually asserts:** `verify-routing.sh` reads
`run.py` as text (AST-parsed, never imported or executed — no code from a
consumer runs inside the guard), extracts `_FALLBACK_LADDER`'s literal dict,
and fails the drift guard the moment it stops matching
`claude/model-routing.yaml`'s `degradation:`/provider `degrade:` block. This
keeps a harness-local dispatcher honest **for that one consumer** — it is
not how every other harness must integrate. Any other harness calls
`resolve_route.py` directly instead of hand-writing its own mirror dict; the
byte-match pattern above only exists because `run.py` predates the packaged
resolver and a rewrite of a live dispatcher was out of scope for this port.

Do not fork `plan-execute` (or `plan-builder`, or `plan-harden`) to add this
binding — the guard's byte-match check is the entire integration surface.
Nothing in those skills needs Gearbox-specific logic beyond the mirrored
constant and a comment pointing at the SSOT.

## 3. The rubric binding — `plan-harden`'s model-selection lint

`plan-harden` (a pre-flight plan-quality skill) runs a lightweight,
always-on "model-selection sanity lint" (§4.0 in its own doc) that reads
task-class defaults **from the SSOT when present**, falling back to a
schema-file rubric only if the SSOT is missing or fails to parse:

```markdown
<!-- ~/.claude/commands/references/plan-harden/model-lint.md (excerpt) -->
Data source: read task-class defaults from `~/.claude/model-routing.yaml`
(`task_classes:` block) when it exists and parses; fall back to
`~/.claude/skills/plan-builder/references/schemas.md` → "Model + reasoning
rubric" only if the SSOT is missing/unparseable.
```

The lint then flags sessions whose declared `(model, effort)` disagrees with
what the SSOT would recommend for that session's apparent task shape (dead
rungs, `opusplan`-style build/design mismatches, a hard session left at a
soft effort, an unpinned specialist agent, a codex-peer trigger with no
adversarial-review gate). Every flag is capped at Polish/Known-debt
severity — this lint never blocks a plan outright; it's advisory-with-a-
paper-trail, the same posture as the main-session advisory default in
`ARCHITECTURE.md` §1.

**The binding is the data-source line, not a fork.** `plan-harden` (and by
extension `plan-builder`, which shares the same rubric reference) needs
*zero* Gearbox-specific code — only that one sentence pointing at
`model-routing.yaml` before falling back to its own built-in schema table.
Porting Gearbox into a project that already has `plan-harden` means editing
that one data-source pointer to your installed SSOT path; it does not mean
copying `plan-harden`'s logic into this repo or vice versa.

## 4. If your consumer is neither shape

Most consumers are simpler than either of the above. A CI lint step, a
pre-commit hook, or a one-off script just needs to:

1. Parse `claude/model-routing.yaml` (any YAML library; no special tooling
   required — the file is a plain, documented schema).
2. Read `task_classes:` for the neutral tier/effort defaults, or call
   `resolve_route.py` if it also needs escalation/degrade behavior.
3. Register itself in the SSOT's own `consumers:` block with a `stamp:` value
   describing how it binds (`rendered-block`, `lint-reads`,
   `prose-rationale`, or a new stamp shape if none fit) — this is what the
   drift guard iterates over to know what to check.

Never hand-copy tier→model or task_class→tier mappings into a new consumer's
source. If the mapping isn't reachable by parsing the SSOT at the point you
need it, that's a sign the consumer should call `resolve_route.py` instead
of re-deriving the logic.

---

## Dependencies & companions

Gearbox's routing loop depends on a small, explicit set of companions. Two
buckets below are required or optional *for routing to work*; two are
explicitly **not** required, so you don't over-install chasing an assumed
dependency.

### (a) Required — a research MCP for `/routing-update` self-calibration

`/routing-update` (`claude/skills/routing-update/`) hard-stops rather than
asserting a model fact from memory when it has no way to verify current
provider docs. It needs **any one** of:

- **Exa** (`mcp__exa__*` / `web_search_exa` / `deep_researcher_*`), or
- **Ref** (`mcp__ref__ref_search_documentation` / `ref_read_url`), or
- **Perplexity** (`perplexity_ask` or equivalent).

Any single one connected is sufficient — this is not "install all three."
Without at least one, `/routing-update` cannot do its job and will say so
rather than guess.

### (b) Optional — the codex peer lane

`model-routing.yaml`'s `codex_peer:` block (architecture/irreversible/
security triggers → `/adversarial-review`; a stuck-after-escalation trigger
→ a rescue subagent, e.g. `codex:rescue`) needs the **openai-codex plugin**
(or whatever provides your `adversarial-review`/rescue tooling) to actually
fire. A consumer without that plugin installed simply **never fires that
lane** — the SSOT still names the trigger and the lint-keyword matching in
§3 above still runs (it's a prose match against session text, not a runtime
call), but there's no second-model peer to hand off to. This is a graceful
no-op, not a broken integration: don't install the codex plugin speculatively
if you don't already use a peer-review lane.

### (c) Consumers — the reference integrations that apply routing

`plan-builder` / `plan-execute` / `plan-harden` (or your own equivalents) are
the reference integrations that actually **apply** the routing policy:

- `plan-harden` applies the rubric binding (§3).
- `plan-execute`'s `run.py` applies the resolver/ladder byte-match binding
  (§2) for its one dispatcher.
- `plan-builder` shares `plan-harden`'s rubric reference at authoring time.

If you don't use plan-builder-style tooling, Gearbox is still useful as a
standalone SSOT + resolver — these three are the worked example of
*consuming* it, not a prerequisite for the SSOT or resolver to function.

### (d) Explicitly NOT required — Gearbox does NOT require these

The following are orthogonal to routing. Do not install them on the
assumption that Gearbox needs them — none of the schema, the resolver, or
the two skills above call into any of these:

- **Coding-style skills** (e.g. a "ponytail"-style lazy-code-discipline
  skill) — governs *how* code is written, not *which model* writes it.
- **Review/design skills** (e.g. `code-review`, `frontend-design`) — quality
  review is a separate concern from model/effort selection.
- **Security-guidance skills** — security review process is unrelated to
  routing, even though `codex_peer.lint_keywords` happens to name
  "security" as one of its trigger keywords (that's a text match on session
  content, not a dependency on a security skill existing).
- **Domain MCPs** — a transcription service, a health-advisor MCP, NotebookLM
  tooling, a hosted BI/dashboard connector, a browser-automation MCP
  (chrome-devtools-style), or any other domain-specific integration. None of
  these touch model/effort selection; they're unrelated capabilities that
  happen to live in the same Claude Code ecosystem.

Installing any of the above is a decision independent of adopting Gearbox —
don't over-install chasing a dependency that doesn't exist.

### (e) Optional — a pre-origin correctness gate

A third-party pre-commit/pre-origin gate (e.g.
[no-mistakes](https://github.com/kunchenguid/no-mistakes)) is a useful
companion for catching issues before a push, same graceful-no-op shape as (b)
above: a consumer without it installed simply runs their normal git flow, no
routing behavior changes either way. Gearbox does not depend on it, does not
install it, and never will — see its own repo for install instructions. This
is a documented pointer, not a dependency; nothing in `install.sh` fetches or
executes third-party tooling.
