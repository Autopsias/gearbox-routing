# scripts/

Repo-maintenance scripts. Distinct from `claude/scripts/`, which ships as part
of the routing framework itself — these are tools the maintainer runs against their own
machine, never something an adopter's `install.sh` run touches.

| Script | Purpose |
|---|---|
| `sync-from-claude.py` | Manifest-driven export pipeline (PKG-02/PKG-03). Copies the `publish`/`scrub-then-publish` surfaces named in an EXTERNAL manifest from the private source repo into `harness/`, applies scrub transforms + a blanket path scrub, stamps `harness/SYNCED-FROM` with the export date and pipeline version (see "Provenance" below), then runs two scan layers over the WHOLE Gearbox tree (secrets: gitleaks using this repo's `.gitleaks.toml`, falling back to semgrep, never a silent skip; blocked-pattern grep from the manifest, plus the provenance-stamp shape check) and hard-fails on any hit. Never commits, never pushes. |
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
  --source ~/path/to/your-private-harness \
  --private-commit <pushed-source-repo-sha> \
  --scan-report-out /tmp/scan-report.txt \
  --scan-status-out /tmp/scan-status.json
```

Omit `--private-commit` to fall back to the source worktree's local git HEAD
(recorded as a non-authoritative stamp — see "Provenance" below).
Add `--scan-only` to re-run just the two scan layers over the current tree
without touching `harness/` again — useful after a manual fix to a scrubbed
file.

## Provenance: split by tier

`harness/SYNCED-FROM` used to carry the source repo's commit SHA. It no longer
does, and the scan blocks its return.

- **Public — `harness/SYNCED-FROM`:** `exported_at` and `pipeline_version`, and
  nothing else. Nobody reading this repo can resolve a private commit id, so it
  was never provenance a reader could act on — only a permanent token
  correlating this repo to a private history, republished on every sync. The
  field list is a closed allowlist enforced by `check_provenance_stamp()` inside
  scan layer 2, so it is checked by the pre-push hook too: add any other key and
  the push is refused.
- **Private — `gearbox-export-provenance.jsonl`, written next to your
  manifest:** one JSON line per export run recording the source revision,
  whether it was the authoritative pushed SHA or a local-HEAD fallback, whether
  the source worktree was dirty, and both directories. This is the *only* record
  of which revision produced an export, so a ledger the script cannot write is a
  hard failure (exit 2), never a silent skip.

Join the two on the timestamp: the ledger's `exported_at` is byte-identical to
the one in `harness/SYNCED-FROM`. To answer "which source revision produced the
export in commit X?", read X's `SYNCED-FROM`, then grep the ledger for that
timestamp.

Scrub rules: `../GENERICIZATION.md`. Adding a manifest entry: add one row to
the private manifest's `entries[]` with `path`, `verdict`
(`publish`/`scrub-then-publish`/`private-never`), and `transforms` — no
Gearbox-side change needed, the next sync run picks it up.
