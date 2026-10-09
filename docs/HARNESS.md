# HARNESS.md — how `harness/` is exported

This page is for maintainers. To **use** the harness, read
[`MODULES.md`](MODULES.md) and [`INSTALL.md`](INSTALL.md) instead.

`harness/` is a snapshot of the original author's Claude Code setup (skills,
commands, hooks, scripts, rules, agents). An export script copies it from a
private source repo, removes personal data, and scans the result. It sits
beside `claude/` (the routing framework) and does not change the routing
framework's versioning or install behaviour (see `docs/VERSIONING.md`).

## The three tiers

Three places, three roles. Which file you edit depends on the role — get this
wrong and the next export overwrites the edit.

| # | Tier | Where | Role |
|---|---|---|---|
| 1 | **Source** | a private repo, cloned to an edit clone (`~/your-private-harness`) | The source of truth. Every harness change starts here. |
| 2 | **Deploy target** | `~/.claude` on each machine | The running setup. It only fast-forwards from tier 1; nobody edits it by hand. |
| 3 | **Public export** | this repo, `Autopsias/gearbox-routing` | A scrubbed copy of the publishable parts. Never a source for `harness/`. |

**Direction is one-way: tier 1 → tier 2, and tier 1 → tier 3.** An edit made
in `harness/` here is lost at the next export. The other parts of this repo
(`claude/`, the docs, `install.sh`, `scripts/`) are maintained here directly.

A public repo ships its full git history: anything that lands in a commit here
is public **forever**, even if a later commit removes it (see the runbook at
the bottom).

## Ongoing workflow

```
 1. edit the private source clone (a skill, a script, a rule, …)
 2. commit and push it to the PRIVATE remote; deploy it to ~/.claude
 3. run the export (from this repo):
        scripts/sync-from-claude.py \
          --manifest /path/to/private/manifest.json \
          --source ~/path/to/your-private-harness \
          --private-commit <the SHA you pushed in step 2> \
          --scan-report-out /tmp/scan-report.txt \
          --scan-status-out /tmp/scan-status.json
    It copies every `publish` / `scrub-then-publish` manifest entry into
    harness/, applies the scrub transforms, stamps harness/SYNCED-FROM with
    the export date and pipeline version, records the step-2 SHA in the
    private provenance ledger beside the manifest (NOT in this repo — see
    scripts/README.md §Provenance), then fails if either scan layer finds a
    hit. NEVER weaken the scan to make it pass — fix the manifest transform
    or the source file instead.
 4. run the docs check: python3 -m pytest scripts/test_docs.py -q
    It fails when a module card in docs/modules/ names a harness path that
    the export renamed or dropped, or when a new skill, command or agent has
    no card. Update docs/modules/ and docs/MODULES.md to match.
 5. review the diff (git diff / git status) — a human checkpoint
 6. commit and push to `origin` (the public remote), only after the review
```

Step 3 never commits or pushes anything. Steps 2 and 6 are the only pushes,
to two different remotes, and step 6 always follows the human review in step 5.

## Adding a manifest entry

The manifest lives OUTSIDE this repo (private-tier data — see below for why).
To add something new to the export:

1. Add one row to the private manifest's `entries[]`:
   `{ "path": "<path relative to ~/.claude>", "verdict": "publish" |
   "scrub-then-publish" | "private-never", "transforms": [...], "blockers":
   [...] }`.
2. If `verdict` is `scrub-then-publish` and the needed rewrite is anything
   beyond the blanket personal-path scrub, add a targeted transform function
   to `TARGETED_TRANSFORMS` in `scripts/sync-from-claude.py`, keyed by the
   entry's relative path. The script fails closed (refuses to run) if a
   `scrub-then-publish` entry has no matching transform — this is
   deliberate; an unhandled scrub instruction must never silently ship as a
   raw copy.
3. Re-run the sync pipeline. No Gearbox-side manifest change is needed for a
   `publish`-verdict entry with no special transform.

## What never leaves this machine at all

The manifest's own `private_repo_never_track[]` list (paths this script never
even considers, in either tier) covers: `mcp.json`/`config.json`/
`settings.local.json` (live credentials), `projects/` beyond
`*/memory/**` (1GB+ of raw session transcripts), `history.jsonl`,
`file-history/`, `paste-cache/`, `image-cache/`, `session-env/`,
`shell-snapshots/`, `telemetry/`, `debug/`, `cache/`, `tasks/`, `sessions/`,
`backups/`, `plugins/cache/` (272MB+ re-installable cache with nested `.git`
repos), `security/` (re-installable venv/tooling cache), and the export's own
evidence and manifest files (see "Why the manifest lives outside this repo"
below). None of these are runtime state anyone would want public, and
several are simply too large or too volatile to be worth versioning anywhere.

## Why the manifest lives outside this repo

The manifest holds the confidential values **as data** — both the patterns the
gate matches on and the scrub rules' replacement targets. That is the exact
material the pipeline exists to keep out of a public repo, so committing the
real manifest here would defeat its own purpose. Only a fake-placeholder
schema example (`scripts/sync-from-claude.manifest.example.json`) ships in
this repo; `sync-from-claude.py` hard-refuses to run if `--manifest` resolves
inside the Gearbox worktree.

## Scan layers (what "green" means)

Both layers run over the **whole** Gearbox working tree, not just `harness/`
— other paths (this doc, `CHANGELOG.md`, `install.sh`) can carry sensitive
strings too, and the gate doesn't trust subtree scoping.

1. **Secrets** — `gitleaks` using this repo's own committed `.gitleaks.toml`
   (not gitleaks defaults), run in `--no-git` mode against the raw
   filesystem tree so dotfiles/untracked files are covered (default git-log
   mode can miss them — see gitleaks#1927). Falls back to `semgrep --config
   p/secrets` if gitleaks isn't installed; if neither is installed, the gap
   is a loud, documented failure, never a silent skip.
2. **Blocked-pattern grep** — every regex in the manifest's
   `blocked_patterns` (client names, personal paths, the CGNAT/Tailscale IP
   range, SSH login-string patterns, etc.), grepped line-by-line across every
   file `git ls-files` would consider part of the tree. This layer also runs
   the **published-provenance shape check**: `harness/SYNCED-FROM` may carry
   only `exported_at` and `pipeline_version`, so a re-added source-repo SHA (or
   any other new key) is a hit here — see `GENERICIZATION.md` §Provenance.

The JSON that `--scan-status-out` writes (`{status, blocked_pattern_hits,
secret_findings, scanner, scanner_version, scan_scope, tree_or_commit_hash}`)
is what a CI job or reviewer should parse — `status=="green"` requires BOTH
hit counts to be exactly zero; a scanner-absent run without
`--allow-missing-scanner` is a hard failure, never a soft pass.

## POST-PUBLISH leak-response runbook

**A public repo ships its full git history.** If a leak is found in
`harness/` (or anywhere else in this repo) AFTER it has been pushed to the
public `origin`, a later commit that "removes" the string does **not** fix
anything — every prior commit still has it, and anyone who cloned the repo
(or GitHub's own caches/forks) already has it too. Treat any post-publish
leak as already-compromised secret material:

1. **Rotate the exposed secret first, immediately** — API key, token,
   password, whatever it was. This is the only step that actually
   neutralizes the exposure; everything below is cleanup, not containment.
2. **Confirm scope** — `git log -p --all -S '<the leaked string>'` to find
   every commit that ever introduced or touched it, on every branch/tag.
3. **Rewrite history** with `git filter-repo` (not `git filter-branch` —
   filter-repo is the currently-recommended tool) to strip the string from
   every commit that has it, then **force-push** the rewritten history.
   Coordinate with anyone else who has a clone — their clones will diverge
   and need a fresh clone, not a pull.
4. **If severe** (e.g. the leak has plausibly already been scraped/cloned
   widely, or a full history rewrite isn't practical) — **delete and
   recreate the repo** instead of rewriting history. A deleted-and-recreated
   repo breaks every existing clone/fork link, which is the point: it's a
   harder guarantee than a force-push that some stale mirror might still
   carry the old history.
5. **Never** a silent later redaction commit with no rotation and no
   history rewrite — that leaves the secret live and byte-for-byte
   retrievable from history, while looking fixed at HEAD.
6. Add the leaked pattern to the **private** identifier list that the export
   scans with (the confidential manifest's blocked patterns), and fix the scrub
   rule that missed it, so the next export refuses a recurrence. Never add the
   identifier itself to the published `.gitleaks.toml`: that file is public, so
   doing so would publish the name again. CI here scans for credentials only;
   identifier scanning happens in the local hooks and the export.

## Installing harness modules

`install.sh` installs only the routing framework. Harness modules are copied
by hand, one module at a time: see [`MODULES.md`](MODULES.md) and
[`INSTALL.md`](INSTALL.md) Part 2.
