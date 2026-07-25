# ~/.claude/workflows/

Named, reusable Workflow scripts — the saved counterpart to one-off workflows
launched inline during a session.

## What lives here

Plain `.js` files, each exporting `meta = { name, description, phases }` plus
the pipeline body (`pipeline`/`parallel`/`agent`/`log`/`phase` calls, ending
in a `return`). Invoke a saved one with `Workflow <name>`.

## How they get here

- Saved from the workflow menu with `s` after a run you want to keep.
- Or authored directly, following the shape of an existing file here.
- Skills may bundle their own workflow `.js` files as **templates to adapt**,
  not run verbatim — copy and rename before pointing production usage at them.

## The one binding rule: pin `opts.model`

Per `model-routing.yaml`'s `fanout_policy` (`workflow_agent_model_inheritance`,
RT-03): an `agent()` call with `opts.model` omitted silently **inherits the
main-loop session model** — Fable in main sessions, the exact mechanism
behind the 2026-07-02 incident (~33 agents fanned out on Fable, 406 calls).
Every `agent()` call here **must** set `opts.model` explicitly (`'sonnet'`
default, `'haiku'` for mechanical shards, never `'fable'`) and `opts.phase`.

## Validating a script

`node --check <file>.js` only catches syntax — it does NOT verify the
runtime contract (`meta` as a pure object literal; only the documented
globals `agent`/`pipeline`/`parallel`/`log`/`phase`/`args`/`budget`; no
filesystem APIs; no `Date.now()`/`Math.random()`/argless `new Date()`),
which is enforced at `Workflow` launch, not by Node. If ES-module `export
const meta` trips `--check`, re-check with `node --input-type=module
--check < file.js` or a `.mjs` copy in `/tmp` — never rename the shipped file.

## Example

`adversarial-verify.js` — finder-then-adversarial-verify: one finder agent
per review dimension, each finding refuted by two independent verifiers,
majority vote decides survival.
