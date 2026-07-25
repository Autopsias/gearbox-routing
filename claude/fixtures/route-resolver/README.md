# fixtures/route-resolver/ — unit tests for resolve_route.py

`test_resolve_route.py` (stdlib `unittest`, no extra dependency) exercises
`claude/scripts/resolve_route.py`'s three entry points (`resolve` / `escalate`
/ `degrade`) against two SSOTs:

- **The real repo SSOT** (`claude/model-routing.yaml`) — baseline resolve for
  each of the three example providers (anthropic/openai/gemini), a full
  escalation rung sequence for anthropic (workhorse effort ladder exhaustion
  -> tier advance -> frontier_reasoner's own ladder), and a degrade step
  (frontier_reasoner -> workhorse, floor-respecting, per-target compensation
  effort).
- **`no-ladder-provider.yaml`** (a small fixture SSOT, not a copy of the real
  one) — two synthetic providers:
  - `no_ladder_provider`: `escalation: none` / `degrade: none` — proves the
    resolver returns an explicit `"exhausted"` for an opted-out provider,
    never a value borrowed from another provider's ladder shape.
  - `bare_signal_provider`: a `degrade.signals: [entitlement, unavailable]`
    block identical in shape to the real SSOT's — proves the resolver reads
    `signals` as a plain string-list key via regex, never through a real YAML
    loader. (The SSOT's degrade-signal key is named `signals:`, not `on:`,
    precisely because a YAML-1.1 loader such as PyYAML's default resolver
    parses a BARE `on:` as the boolean `True`; see `resolve_route.py`'s
    `_sub_block` docstring for the full note.)

Run:

```
python3 claude/fixtures/route-resolver/test_resolve_route.py
```

Or the module's own runnable demo (resolves + escalates + degrades for two
real providers against the real SSOT):

```
python3 claude/scripts/resolve_route.py
```
