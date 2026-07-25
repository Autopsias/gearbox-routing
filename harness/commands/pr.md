---
description: "Manages Git PR workflows: stage, commit, push, create PRs, check status, sync branches, and merge. Use when you say 'create PR', 'push changes', 'PR status', 'sync branch', or need '--fast' quick push. For committing with quality checks first, use /commit-orchestrate; for the full ship-to-CI-green chain, use /ship-tail."
argument-hint: "[action] | update (default), status, sync, merge, create, --fast"
allowed-tools: ["Task", "Bash", "SlashCommand", "AskUserQuestion"]
---

# PR Workflow Helper

**Invocation-mode note:** this command remains model-invocable by deliberate choice (parity
with ship-tail/commit-orchestrate routing), but it auto-commits and auto-pushes. `--fast`
additionally skips the pre-push confirmation/conflict check — that flag is for explicit,
deliberate invocation only, not for the model to reach for on its own.

Request: "$ARGUMENTS" (default: update)

## Decision Tree

```
IF "--fast" in $ARGUMENTS:
  → FAST MODE: Skip everything, just push
ELIF action in ["update", "", "status", "sync"]:
  → DO DIRECTLY: Execute git commands now
ELIF action in ["create", "merge", "fix", "review"]:
  → DELEGATE: Use pr-workflow-manager agent
```

---

## DIRECT ACTIONS (Execute Now)

### `/pr` or `/pr update` (DEFAULT)

**Do this immediately - no tasks, no delegation:**

```bash
# 1. Quick conflict check (silent if no PR exists)
BRANCH=$(git branch --show-current)
PR_INFO=$(gh pr list --head "$BRANCH" --json number,mergeStateStatus -q '.[0]' 2>/dev/null || echo "")

if [[ -n "$PR_INFO" ]]; then
  MERGE_STATE=$(echo "$PR_INFO" | jq -r '.mergeStateStatus // "CLEAN"' 2>/dev/null)
  if [[ "$MERGE_STATE" == "DIRTY" ]]; then
    PR_NUM=$(echo "$PR_INFO" | jq -r '.number')
    echo "⚠️  PR #$PR_NUM has merge conflicts - E2E/UAT CI won't run!"
    echo "   Run '/pr sync' first, or continue anyway."
  fi
fi

# 2. Check what's changed
git status --short
git diff --stat HEAD

# 3. Stage, commit, push
git add -A
git commit -m "$(cat <<'EOF'
<generate based on diff>

🤖 Generated with [Claude Code](https://claude.ai/claude-code)

Co-Authored-By: <active model name/version> <noreply@anthropic.com>
EOF
)"
git push
```

### `/pr --fast`

**Maximum speed - minimal checks. MUST NOT be used when a merge conflict is suspected** — `--fast` skips the DIRTY `mergeStateStatus` check that `/pr update` performs above.

```bash
git add -A
git commit -m "wip: quick save

🤖 Generated with [Claude Code](https://claude.ai/claude-code)

Co-Authored-By: <active model name/version> <noreply@anthropic.com>
"
git push
```

### `/pr status`

```bash
gh pr view --json number,title,state,statusCheckRollup,mergeStateStatus 2>/dev/null | jq '.' || echo "No PR for current branch"
```

### `/pr sync`

```bash
BASE=$(gh pr view --json baseRefName -q '.baseRefName' 2>/dev/null || echo "main")
git fetch origin "$BASE"
git merge "origin/$BASE" --no-edit && git push
```

---

## COMPLEX ACTIONS (Delegate to Agent)

For these operations, delegate to pr-workflow-manager:

| Action | Why delegate |
|--------|--------------|
| `create` | Needs branch setup, PR body generation |
| `merge` | Needs verification, cleanup |
| `fix CI` | Needs CI analysis, coordination |
| `review` | Needs code review logic |

```
Task(
  subagent_type="pr-workflow-manager",
  description="Handle PR: $ARGUMENTS",
  prompt="Handle PR operation: $ARGUMENTS

  Current branch: $(git branch --show-current)

  Available operations:
  - create: Create new PR with proper title/body
  - merge: Verify checks, merge, cleanup branches
  - fix CI: Analyze and fix CI failures
  - review: Review PR code quality"
)
```

---

## Summary Table

| Command | Execution | Time |
|---------|-----------|------|
| `/pr` | Direct | ~20s |
| `/pr --fast` | Direct | ~5s |
| `/pr status` | Direct | ~2s |
| `/pr sync` | Direct | ~10s |
| `/pr create` | Agent | ~30s |
| `/pr merge` | Agent | ~30s |
| `/pr fix CI` | Agent | varies |

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `fatal: not a git repository` | Running outside a git repo | `cd` to your project root first |
| `gh: command not found` | GitHub CLI not installed | `brew install gh && gh auth login` |
| Push rejected (non-fast-forward) | Remote has commits you don't | Run `/pr sync` first |
| PR creation fails | No upstream branch | Push first: `git push -u origin HEAD` |
| Merge conflicts after sync | Divergent changes | Resolve conflicts manually, then `git add . && git merge --continue` |
