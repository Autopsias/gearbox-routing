# --adopt branch: brownfield onboarding

Read this ONLY when `$ARGUMENTS` contains `--adopt`. This flag onboards an EXISTING
repo onto the quality framework and exits — it does not analyze, fix, or dispatch.

**Principle: the ratchet.** Never demand that an ongoing project fix its history
before it adopts the gates. Grandfather every violation that exists today into the
exception baselines; enforce on everything that changes from now on. The baselines
then only shrink: each refactor removes entries, and no step ever adds one.

The adoption commit contains two baseline files and (optionally) one pyproject
section — zero code changes. Safe to run on any branch of any ongoing repo.

## Step 1: Snapshot current state

Run both checkers WITHOUT baselines to show the true backlog:

```bash
echo "=== ADOPTION SNAPSHOT (pre-baseline) ==="
python3 ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" 2>&1 | tail -3
python3 ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" 2>&1 | tail -3
```

Both checkers are stdlib-only, so `python3` is enough. Do NOT wrap them in
`uv run` — under zsh a `PY="uv run python"` variable does not word-split and the
command fails, and in a repo that has a `pyproject.toml` `uv run` also creates a
stray `.venv`.

## Step 2: Generate both baselines (the grandfather step)

```bash
python3 ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" --generate-baseline
python3 ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" --generate-baseline
echo "Baselines written: .file-size-exceptions, .function-length-exceptions"
echo "Commit these files — they ARE the ratchet's zero point."
```

Then confirm git will actually track them. A deny-by-default `.gitignore` (a bare
`/*` with an allowlist) silently swallows both dotfiles, and the adoption dies at
the first fresh clone:

```bash
git check-ignore -v .file-size-exceptions .function-length-exceptions
```

Any output means the files are ignored. Add negations (`!/.file-size-exceptions`,
`!/.function-length-exceptions`) before continuing — an uncommittable baseline is
not a baseline.

## Step 3: Offer threshold config (only if the project needs different numbers)

If `pyproject.toml` exists and has no `[tool.claude-quality]` section, show the
snippet and ask before writing — this is the user's project file:

```toml
[tool.claude-quality.file-size]
warning = 400
limit = 500
[tool.claude-quality.test-file-size]
limit = 800
[tool.claude-quality.function-length]
warning = 50
limit = 100
```

Defaults apply without the section — only add it to deviate.

The one key most brownfield repos DO need is `exclude`, which drops whole trees
from the size gates:

```toml
[tool.claude-quality]
exclude = ["_plans", "_archive-vista", "fixtures"]
```

A pattern matches a path component, a glob, or a directory prefix. Exclude what is
not authored source — generated output, frozen archives, deliberate test fixtures.
Baselining those instead works, but it bloats the zero point and every NEW file
that lands in such a tree then blocks a commit. Excluding is the durable fix.

## Step 4: Advisory scans (report only, never block adoption)

```bash
if command -v ruff &> /dev/null; then
    echo "=== Complexity backlog (ruff C901) ==="
    ruff check --select C901 --config 'lint.mccabe.max-complexity=12' "$PWD" --statistics 2>&1 || true
    echo "=== Slop backlog (advisory) ==="
    ruff check --select F401,F841,E722,ERA001,BLE001 "$PWD" --statistics 2>&1 || true
else
    echo "⚠️ ruff not found - skipping complexity and slop scans"
fi
```

Complexity has no baseline file. On a brownfield repo it stays ADVISORY until the
backlog reaches zero; report the count, do not block.

## Step 5: Context-file audit

Agents perform measurably worse with bloated instruction files (context files above
~200 lines dilute adherence and add cost). Report, do not edit:

```bash
echo "=== Context-file sizes (keep each under ~200 lines) ==="
for f in CLAUDE.md AGENTS.md GEMINI.md .cursorrules; do
    [ -f "$f" ] && wc -l "$f"
done
find .claude/rules .cursor/rules -name "*.md*" -exec wc -l {} + 2>/dev/null | tail -5
```

Flag any file over 200 lines with: "consider splitting into path-scoped rules
(`.claude/rules/` with `paths:` frontmatter) — detail loads only where it applies."

## Step 6: Explain the ratchet wiring (output, verbatim intent)

```
ADOPTION COMPLETE — how enforcement works from here:

1. Legacy violations are grandfathered in the two baseline files. Commit them.
2. New violations BLOCK: /commit-orchestrate runs both checkers at commit time;
   anything not in the baseline fails the gate.
3. After a refactor session, run /code_quality --refresh-exceptions —
   baselines shrink as fixes land.
4. NEVER regenerate baselines to absorb new violations. Regeneration is only
   valid immediately after verified fixes; anything else is a ratchet leak.
5. To burn down the backlog deliberately: /code_quality --fix (dispatches
   safe-refactor agents against the worst offenders first).
```

Exit after the report (no other steps execute).
