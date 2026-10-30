# Git Safety Rules for Agents

**PRIORITY: HIGH** — these prevent git state corruption and lost work in
automated workflows. Invariants only; the worked patterns, recovery procedures
and agent-authoring checklist live in
`~/.claude/docs/reference_git_safety_playbook.md` — read it when you are
actually writing or debugging an agent that touches git.

## Core principle

**Git operations in automated agents must be VISIBLE and RECOVERABLE.** Hidden
state (`git stash`) can't be verified by an orchestrator, survives across
sessions, has no natural "finally" cleanup, and makes work look lost.

- **NEVER `git stash` without a cleanup path for EVERY exit** — including crash
  and timeout. Prefer a temp branch (`temp/agent-<id>`, committed, deleted on
  both success and failure): visible in `git log`, verifiable, backed up.
- **NEVER hand a stash across a process/agent boundary.** Stash is a stack; another
  operation can push or pop between the two agents.
- **NEVER use stash for baseline preservation** — the success path forgets to drop
  it, and the stash then holds OLD state while the tree holds new.
- If stash is truly unavoidable: name it identifiably, drop it by ref before
  returning, and report the outcome.
- **Verify clean state before returning** — no orphaned `agent-*` stashes, no
  orphaned `temp/agent-*` branches — and report `git_state` in agent output.

## Destructive deletion of pre-existing files

**PRIORITY: HIGH** — deleting work you did not create is hard to undo and easy to
over-authorize.

- **NEVER delete or `rm -rf` a pre-existing file/directory you did not create without
  explicit, unambiguous confirmation that names the target.** A one-letter menu pick
  (e.g. "b") or a terse "yes" to a multi-part question is NOT sufficient authorization
  for an irreversible delete — re-confirm the specific path first.
- **NEVER bundle `rm -rf` with `git rm` in one command.** Use `git rm` alone — tracked
  deletions stay recoverable from history; `rm -rf` on top also destroys the untracked
  working copy.
- **Look before you delete.** Inspect the target first; if what you find contradicts how
  it was described, or you didn't create it, surface that instead of proceeding.
- Prefer reversible paths (`git rm`, or move-to-quarantine) over outright `rm -rf`.

*Why:* in one session a terse "b" was read as authorization for
`git rm -r <dir> && rm -rf <dir>` on a pre-existing directory the agent never created;
the harness correctly blocked it as scope escalation. Explicit, target-naming
confirmation is the bar for any destructive delete.

## A local `main` is not the remote

- **ALWAYS `git fetch` and read `origin/main` before you answer a question about the
  code, compare against main, or start a branch.** A local `main` can be days behind
  (or diverged) and answers confidently from old code; nothing errors. Branch from
  `origin/main`, never from the bare local ref.

*Why:* example-project 2026-09-17 reported a deploy gap of 7 commits that was really 2;
Kuzushi 2026-10-16 answered a reader-feedback question from a `main` 33 commits behind
and stated a fault the newest code had already fixed.
