# HARNESS.md — the harness/ tree, sync-from-claude.py, and the two-tier workflow

`harness/` is the public, shareable home of the author's Claude Code harness
(skills, commands, hooks, scripts, rules, agents) — continuously synced from
his private `~/.claude` deployment. It ships as an ADDITIVE tree beside
`claude/` (the routing SSOT); nothing here changes routing's own versioning
or install behavior (see `docs/VERSIONING.md`). Full layout rationale lives in the private-tier plan directory that designed
this tree (`_plans/<your-plan>-<date>/_evidence/s01/integration-map.md`) and is
not part of this public repo.

## The three tiers

Two git remotes, three roles. Which file you edit depends on which role you
are in — get this wrong and the edit is silently overwritten by the next sync.

| # | Tier | Where | Role |
|---|---|---|---|
| 1 | **Source** | `<your-org>/<your-private-harness>` (a private GitHub repo) | The source of truth. Every harness change originates here. |
| 2 | **Deployed clone** | `~/.claude` on each machine, remote `private` → tier 1 | The live, running harness — and the working copy you actually edit. Push it to tier 1. |
| 3 | **Genericized export** | this repo, `Autopsias/gearbox-routing` (public) | A scrubbed, provider-abstract **derivative**. Never a source. |

1. **Source + deployed clone** — `~/.claude` is a clone of the private repo,
   so "source" and "working copy" are the same tree in practice; the remote is
   the durable copy. Real client names, real paths, real credentials-adjacent
   config. **Never** browsable by anyone but the harness owner.
   *(Renamed 2026-07-25 from `claude-harness-private` → `your-private-harness`, so
   the source repo's name says "gearbox" rather than "claude": Gearbox is a
   provider-neutral harness meant to be usable with other LLM providers.
   GitHub redirects the old name, so existing clones keep working — but update
   your remote URL. See ADR-0001 in the private repo.)*
2. **Public tier** — this repo (`gearbox-routing`), including `harness/`.
   Every file here has been through the scrub + scan pipeline below. It ships
   full git history publicly — anything that lands in a commit here is
   public **forever**, even if a later commit "removes" it (see the runbook
   at the bottom).

**Direction is one-way: tier 2 → tier 1 → tier 3.** Editing tier 3 as if it
were a source loses the edit at the next sync.

Guard hooks are tracked in the source repo under `githooks/` and wired into a
clone by `scripts/install-hooks.sh` (`core.hooksPath=githooks`) — git cannot
version `.git/hooks`, so a fresh clone has no guard until that runs.
`verify-routing.sh` fails closed when the hooks are not wired.

## Ongoing workflow

```
 1. improve ~/.claude (edit a skill, fix a script, add a rule, etc.)
 2. commit + push to the PRIVATE remote AS-IS — no scrubbing needed here,
    this remote is confidential by design
        git -C ~/.claude add -A && git -C ~/.claude commit -m "..."
        git -C ~/.claude push private main
 3. run the sync pipeline (from the Gearbox repo):
        scripts/sync-from-claude.py \
          --manifest /path/to/private/manifest.json \
          --source ~/path/to/your-private-harness \
          --private-commit <the SHA you just pushed in step 2> \
          --scan-report-out /tmp/scan-report.txt \
          --scan-status-out /tmp/scan-status.json
    This copies every `publish`/`scrub-then-publish` manifest entry into
    harness/, applies scrub transforms, stamps harness/SYNCED-FROM with the
    export date + pipeline version, records the step-2 SHA in the private
    provenance ledger beside your manifest (NOT in this repo — see
    scripts/README.md §Provenance), then hard-fails if either scan layer
    finds a hit. NEVER weaken the scan to make it pass — fix the manifest
    transform or the source file instead.
 4. review the scrubbed diff (git diff / git status inside Gearbox) —
    this is a human checkpoint, not automated
 5. gated PUBLIC push: only after review, commit + push to `origin`
    (the public gearbox-routing remote) on a real branch / PR, same
    review discipline as any other change to this repo
```

Step 3 never commits or pushes anything itself — it only writes into the
working tree. Steps 2 and 5 are the only pushes, to two different remotes,
and step 5 always follows a human diff review (step 4).

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
repos), `security/` (re-installable venv/tooling cache), and this plan's own
`_evidence/`/manifest files (see "Why the manifest lives outside this repo"
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

`_evidence/s06/scan-status.json`-shaped output (`{status, blocked_pattern_hits,
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
6. Add the leaked pattern to `.gitleaks.toml`'s `gearbox-confidential-identifiers`
   rule (or a new rule) so the pre-commit hook and CI catch a recurrence —
   the pipeline that missed it needs the same fix a human catch would
   trigger.

## Optional module, install.sh extension

`harness/` install wiring (`install.sh --with-harness`) is a **separate,
later** piece of work — this doc describes the export/sync side only. See
the S01 integration map's install.sh extension design for that when it
lands.

ponytail: one script + one external manifest, no daemon, no git hook wiring
in this pipeline yet. A future `/harness-sync` skill wrapper would call
`scripts/sync-from-claude.py` with the same flags shown above — nothing
about the script's contract needs to change for that to work.
