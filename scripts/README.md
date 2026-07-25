# scripts/

Repo-maintenance scripts. Distinct from `claude/scripts/`, which ships as part
of the routing framework itself — these are tools the maintainer runs against their own
machine, never something an adopter's `install.sh` run touches.

| Script | Purpose |
|---|---|
| `sync-from-claude.py` | Manifest-driven export pipeline (PKG-02/PKG-03). Copies the `publish`/`scrub-then-publish` surfaces named in an EXTERNAL manifest from `~/.claude` into `harness/`, applies scrub transforms + a blanket path scrub, stamps `harness/SYNCED-FROM` with the private-repo provenance commit, then runs two scan layers over the WHOLE Gearbox tree (secrets: gitleaks using this repo's `.gitleaks.toml`, falling back to semgrep, never a silent skip; blocked-pattern grep from the manifest) and hard-fails on any hit. Never commits, never pushes. |
| `sync-from-claude.manifest.example.json` | Fake-placeholder schema example only — every value is invented. The REAL manifest (real blocked patterns: client names, personal paths, emails) is confidential and lives OUTSIDE this repo; `sync-from-claude.py` refuses to run if `--manifest` resolves inside the Gearbox worktree. |

## Why the manifest lives outside this repo

The manifest itself is the thing the scrub exists to protect — it names, as
data, every client/personal identifier that must never ship. Committing the
real manifest to this public repo would defeat its own purpose. Only the
example schema above may live here.

## Usage

```
scripts/sync-from-claude.py \
  --manifest /path/to/private/manifest.json \
  --claude-home ~/.claude \
  --private-commit <pushed-private-repo-sha> \
  --scan-report-out /tmp/scan-report.txt \
  --scan-status-out /tmp/scan-status.json
```

Omit `--private-commit` to fall back to a local `~/.claude` git HEAD stamp
(see the script's own docstring for the ordering/provenance fallback rule).
Add `--scan-only` to re-run just the two scan layers over the current tree
without touching `harness/` again — useful after a manual fix to a scrubbed
file.

Scrub rules: `../GENERICIZATION.md`. Adding a manifest entry: add one row to
the private manifest's `entries[]` with `path`, `verdict`
(`publish`/`scrub-then-publish`/`private-never`), and `transforms` — no
Gearbox-side change needed, the next sync run picks it up.
