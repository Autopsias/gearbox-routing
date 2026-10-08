# --adopt branch: brownfield onboarding

Read this ONLY when `$ARGUMENTS` contains `--adopt`. This flag onboards an EXISTING
repo onto the quality framework and exits — it does not analyze, fix, or dispatch.

**Principle: the ratchet.** Never demand that an ongoing project fix its history
before it adopts the gates. Grandfather every violation that exists today into the
exception baselines; enforce on everything that changes from now on. The baselines
then only shrink: each refactor removes entries, and no step ever adds one —
except the sanctioned merge-time re-record in step 7, which may only admit debt
that already existed on a merge parent.

The whole adoption is one sitting: **vendor → hook → baseline → CI → rules.**
Each step below is one commit-sized action.

## Step 1: Vendor the checkers (the sync step)

The source of truth is `~/.claude/scripts/quality/` (gearbox `scripts/quality/`).
Never point a hook at a home directory — it fails for every other clone and in CI.
Vendor byte-identical copies instead:

```bash
python3 ~/.claude/scripts/quality/vendor_quality.py "$PWD"
```

This writes `tools/check_file_sizes.py`, `tools/check_function_lengths.py`,
`tools/check_complexity.py`, `tools/ratchetlib.py`, and
`.github/workflows/quality-ratchet.yml`. To pick up a checker fix later, re-run
the same command; `--check` reports drift without writing.

## Step 2: Snapshot current state

Run the checkers WITHOUT baselines to show the true backlog:

```bash
echo "=== ADOPTION SNAPSHOT (pre-baseline) ==="
python3 tools/check_file_sizes.py --project "$PWD" 2>&1 | tail -3
python3 tools/check_function_lengths.py --project "$PWD" 2>&1 | tail -3
python3 tools/check_complexity.py --project "$PWD" 2>&1 | tail -2
```

The size checkers are stdlib-only, so `python3` is enough; the complexity checker
needs `ruff` on PATH. Do NOT wrap them in `uv run` — under zsh a
`PY="uv run python"` variable does not word-split and the command fails, and in a
repo that has a `pyproject.toml` `uv run` also creates a stray `.venv`.

## Step 3: Generate the three baselines (the grandfather step)

**Generate them on the branch you merge from** (master/main, or the integration
branch every feature branch targets). A baseline recorded on a side branch, or on
a master that long-lived branches will later bypass, goes stale on the first
merge — step 7 is the standing repair for that, but the zero point belongs on the
merge target.

```bash
python3 tools/check_file_sizes.py --project "$PWD" --generate-baseline
python3 tools/check_function_lengths.py --project "$PWD" --generate-baseline
python3 tools/check_complexity.py --project "$PWD" --generate-baseline
echo "Baselines: .file-size-exceptions, .function-length-exceptions, .complexity-exceptions"
echo "Commit these files — they ARE the ratchet's zero point."
```

Then confirm git will actually track them. A deny-by-default `.gitignore` (a bare
`/*` with an allowlist) silently swallows the dotfiles, and the adoption dies at
the first fresh clone:

```bash
git check-ignore -v .file-size-exceptions .function-length-exceptions .complexity-exceptions
```

Any output means the files are ignored. Add negations (`!/.file-size-exceptions`,
…) before continuing — an uncommittable baseline is not a baseline.

## Step 4: Wire the pre-commit hooks (staged files only)

The local hooks run in `--staged` mode: they judge ONLY the files staged in the
commit, and they block only what the commit makes WORSE than every commit parent.
A one-file commit is never refused because of 25 files it did not touch, and a
merge is never refused for debt the branch already carried. The whole-project
pass belongs to CI (step 5), not to the commit hook.

Add to `.pre-commit-config.yaml` under a `repo: local` block:

```yaml
      - id: file-size-ratchet
        name: no staged file grows past the size limit (baseline only shrinks)
        entry: python3 tools/check_file_sizes.py --staged
        language: system
        pass_filenames: false
        files: '\.py$'

      - id: function-length-ratchet
        name: no staged function grows past the length limit
        entry: python3 tools/check_function_lengths.py --staged
        language: system
        pass_filenames: false
        files: '\.py$'

      - id: complexity-ratchet
        name: no staged function grows past complexity 12
        entry: python3 tools/check_complexity.py --staged
        language: system
        pass_filenames: false
        files: '\.py$'
```

## Step 5: Commit the CI backstop

Step 1 already wrote `.github/workflows/quality-ratchet.yml`. Commit it. It runs
all three checkers whole-project on every push and PR, and first proves each
checker can still fail against a planted known-bad file. This job is what turns
a local bypass into a visible red check instead of a permanent silent hole.

## Step 6: Write the bypass and merge rules where sessions read them

Add this block to the repo's agent context file (`CLAUDE.md`, or `AGENTS.md`
when that is the canonical one). Adapt the hook ids if you renamed them:

```markdown
## Committing — the quality ratchet

- **Never `git commit --no-verify`.** It skips EVERY hook, including the
  security and packaging gates. There is no situation where the ratchet
  justifies dropping those.
- The ratchet hooks judge only STAGED files and only block what your commit
  makes worse. If one still blocks you wrongly, skip that hook alone and say
  why in the commit body:
  `SKIP=file-size-ratchet git commit ...` (comma-separate for several).
  CI re-runs the checkers whole-project, so a skip is visible, never final.
- **Merging a long-lived branch:** if the merge warns about inherited debt,
  re-record the baselines IN the merge commit:
  `python3 tools/check_file_sizes.py --generate-baseline` (and the two
  siblings), review that the diff only admits files the branch already
  carried, `git add` the baseline files, and complete the merge. Never
  regenerate a baseline to absorb debt authored in the commit itself.
```

## Step 7: The standing merge-time rule (what step 6 documented)

The decided rule for stale baselines: **re-record at merge time, as part of the
merge commit.** The staged hooks make this non-blocking — debt that already
existed on a merge parent warns instead of blocking — and the CI whole-project
job stays red until the re-record lands. So the failure mode is a red check
that names the fix, never a wall that only `--no-verify` gets past.

## Step 8: Optional threshold config

If `pyproject.toml` exists and has no `[tool.claude-quality]` section, defaults
apply (file 500/warn 400, test file 800, function 100/warn 50, complexity 12).
Only add the section to deviate. The one key most brownfield repos DO need is
`exclude`, which drops whole trees from all three gates:

```toml
[tool.claude-quality]
exclude = ["_plans", "_archive", "fixtures"]
```

A pattern matches a path component, a glob, or a directory prefix. Exclude what
is not authored source — generated output, frozen archives, deliberate test
fixtures. Baselining those instead bloats the zero point, and every NEW file in
such a tree then blocks a commit. Excluding is the durable fix.

## Step 9: Explain the ratchet wiring (output, verbatim intent)

```
ADOPTION COMPLETE — how enforcement works from here:

1. Legacy violations are grandfathered in the three baseline files. Commit them.
2. New violations BLOCK at commit time, but only in files you staged, and only
   when your commit makes them worse.
3. CI re-runs all three checkers whole-project — a SKIP or bypass shows up as
   a red check on the next push.
4. After a refactor session, run /code_quality --refresh-exceptions —
   baselines shrink as fixes land.
5. NEVER regenerate baselines to absorb new violations. Regeneration is valid
   immediately after verified fixes, and at merge time for debt a branch
   already carried — nothing else.
6. To burn down the backlog deliberately: /code_quality --fix (dispatches
   safe-refactor agents against the worst offenders first).
7. Checker fix upstream? Re-sync: python3 ~/.claude/scripts/quality/vendor_quality.py .
```

Exit after the report (no other steps execute).
