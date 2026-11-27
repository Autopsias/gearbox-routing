# Git Safety Rules for Agents

Invariants only; worked patterns, recovery procedures, and incident history live in `~/.claude/docs/reference_git_safety_playbook.md`.

## Core principle

**Git operations in automated agents must be VISIBLE and RECOVERABLE.** Hidden state (`git stash`) cannot be verified, survives across sessions, and makes work look lost.

- **NEVER `git stash` without a cleanup path for EVERY exit** (including crash/timeout). Prefer a temp branch (`temp/agent-<id>`, committed, deleted on success and failure).
- **NEVER hand a stash across a process/agent boundary** (stash is a shared stack).
- **NEVER use stash for baseline preservation.** To test against old code, copy your file aside, run `git show origin/main:<path> > <path>`, test, then restore your copy.
- If stash is unavoidable: name it identifiably, drop it by ref before returning, and report the outcome.
- **Verify clean state before returning** — no orphaned `agent-*` stashes or `temp/agent-*` branches — and report `git_state` in output.

## Destructive deletion of pre-existing files

- **NEVER delete or `rm -rf` a pre-existing file/directory you did not create without explicit confirmation naming the target.** A one-letter pick (e.g. "b") or terse "yes" to a multi-part question is not authorization. A delete made reversible FIRST (saved patch or verified move to a kept folder; state where the copy is) needs only a normal pick.
- **NEVER bundle `rm -rf` with `git rm` in one command.** Use `git rm` alone so tracked deletions remain recoverable and untracked files are not destroyed.
- **Look before you delete.** Inspect the target first; surface contradictions instead of proceeding. Check the exact set the command acts on (`git ls-files --exclude-standard` hides ignored files that `mv`/`rm -r` still take).
- Prefer reversible paths (`git rm` or move-to-quarantine) over `rm -rf`.

## A local `main` is not the remote

- **ALWAYS `git fetch` and read `origin/main` before answering questions about code, comparing against main, or branching.** Branch from `origin/main`, never local `main`. In a feature-branch checkout, search main with `git grep <pattern> origin/main -- <paths>`.
- **ALWAYS fast-forward the primary checkout after merging a PR.** Compare dirty files against incoming commits (`git diff --name-only HEAD origin/main`); restore a dirty file only if identical on `origin/main`, leave others alone (report gap only when blocked; never end "not touched" if a safe pull exists). Run the pull that `merge-ff-main.py` reports after `gh pr merge` (never ignore it).
- **NEVER hide `git commit` output.** Read hook output and check `git log -1 --oneline` for the new hash before pushing.
