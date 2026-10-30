# Plan-level git isolation — lifecycle and registry commands

Moved from SKILL.md "Plan-level git isolation — the whole-plan lifecycle". The authority stays `plan-isolation-contract.md`; this file is the operator-facing walk-through.

**The lifecycle, one line per stage:**

1. **`begin`** — on a plan at `plan_schema_version >= 7` (or an explicit
   `--isolate`), creates ONE branch (`plan/<slug>`) and ONE **locked** worktree
   (`.plan-worktrees/<slug>/`) off a pinned base. Every session in the plan
   dispatches inside that worktree from here on.
2. **Sessions** — the normal dispatch loop (SKILL.md), unchanged, just running
   inside the plan's own worktree/branch instead of the shared checkout.
3. **`land`** — once every session is terminal, the dispatch loop's `land`
   action (documented in step 1 of SKILL.md "The dispatch loop") runs the whole
   protocol: sync, merge, re-gate, a human ack, then push.
4. **Cleanup — `land`'s own LAST step, not a separate command.** Once the push
   and the plan's final record both succeed, `land` unlocks, removes and prunes
   the plan worktree, then deletes the plan branch — local AND remote. A
   `landed` result carries a `cleanup` object (`worktree`, `branch_local`,
   `branch_remote`) so you can see exactly what happened rather than assume it.
   A branch is deleted only at a tip proven to be on the landed commit; any
   doubt keeps it as `{"kept": "<reason>"}`, and `finish` retries a kept
   remote branch once per finish run that reaches its leftovers step. A retry that finds the branch `not-pushed` is recorded as `already-gone`. If it is still kept after a finished apply-mode `finish`,
   `land` and the `finish` brief give the exact `git push
   --force-with-lease=refs/heads/<branch>:<tip> <remote> :refs/heads/<branch>`
   to run yourself, not another retry. It names the `git fetch` and the
   `git merge-base --is-ancestor` check to run first. If the tip cannot be read
   it gives `git ls-remote` as the first step, and if the branch is already gone
   it says so and gives no command.
   A dirty plan worktree PRESERVES rather than loses content and is reported on
   the SAME `landed` result (the land already succeeded — this is never a
   park); clean it and call `land` again, cleanup retries and is idempotent.
5. **Abandon, instead of landing — `PYBP retire-plan <dir> [--reason "..."]`.**
   The operator ends the plan without landing it. Marks the plan retired
   unconditionally, removes the worktree ONLY when clean (a dirty one is
   preserved and its path reported, never forced), and ALWAYS keeps the
   branch, local and remote — an unlanded branch is the only copy of work
   nobody merged, and deleting it is a decision only an operator makes,
   naming the branch, never a lifecycle transition this command makes on its
   own.

**Registry commands** (repo-wide, no `<plan-dir>` — they scan `_plans/` under
cwd):

- `PYBP plans-status [--json]` — every plan's lifecycle (`active` /
  `stale-lock` / `done` / `unknown`), joined against its `plan/*` branches and
  git worktrees, each classified `active` / `leftover` / `unknown` / `conflict`.
  After a landed-and-cleaned plan, or an abandoned plan whose worktree came out
  clean, that plan shows no `plan/*` branch or worktree row at all — there is
  nothing left to classify.
- `PYBP plans-sweep [--apply] [--json]` — reports (and, with `--apply`, removes)
  dead `.lock` files plus LEFTOVER `plan/*` branches/worktrees a DONE plan left
  behind. `unknown`/`conflict` are always left alone — never guessed at.

**The version gate.** See SKILL.md "Preconditions" for exactly which schema version
turns this on and how `--isolate`/`--no-isolate` override it.
