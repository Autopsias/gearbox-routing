# Genericization rules

Binding scrub rules for **every** change that ports content from a private
deployment (`~/.claude`) into a public repo. Apply at port time, before the
first content-bearing commit — a public repo ships its full git history, so a
string redacted later still leaks. Use this checklist when forking Gearbox from
your own deployment, or when contributing back a change extracted from one.

## Replacements

| Source content | Ships as |
|---|---|
| Your client / customer names and any corpus/graph references to them | `<your-confidential-corpus>` |
| Your personal knowledge-base / vault paths | a generic placeholder path (e.g. `~/your-vault`) |
| Real plan / project directory paths | a synthetic example path |
| `MISROUTES.md` real ledger entries | header + format only, **zero real rows** |
| Concrete model ids / tiers / prices | dated **verify-before-use examples** (see `ARCHITECTURE.md` §3) |
| Your username, machine paths (`/Users/<you>/...`) | `~` or generic placeholders |
| The source repo's commit SHA in `harness/SYNCED-FROM` | export date + pipeline version only (below) |
| `scripts/deploy.pathspec`'s `[mode-0600]` credential-file list | fictional `REPLACE_ME_*` placeholders (below) |

### Provenance: what the export stamps, and what it does not

`harness/SYNCED-FROM` carries **`exported_at` and `pipeline_version`, nothing
else.** A source-repo commit id is not resolvable by anyone reading this repo,
so it was never provenance a public reader could act on — it was a permanent,
unique token correlating this repo to a private history, republished on every
sync. The field list is a closed **allowlist** (`check_provenance_stamp()` in
`scripts/sync-from-claude.py`, run inside scan layer 2, therefore enforced by
`pre-push`): any other key in that file blocks the push. An allowlist, not a
"no SHA-shaped token" regex, because the class to keep out is *every*
private-tier fact about the source repo, not just the one removed last time.

The source revision is still recorded — in `gearbox-export-provenance.jsonl`,
appended next to the (private-tier, out-of-repo) export manifest, joined to the
public stamp by the identical `exported_at` timestamp. A ledger the pipeline
cannot write is a hard failure, not a silent skip: it is the only record there
is. See `scripts/README.md` §Provenance.

### `deploy.pathspec`: policy shape ships, deployment map does not

The deploy classifier's three sections are not the same disclosure, so the
export treats them differently (`_scrub_deploy_pathspec` in
`scripts/sync-from-claude.py`):

- `[live-state]` and `[settings-churn-keys]` ship **real**. They are generic
  categories — glob classes over runtime surfaces this harness documents openly,
  and the Claude Code binary's own settings keys. Templating them would buy no
  privacy and would leave an exported classifier that classifies nothing.
- `[mode-0600]` ships **fictional**. That list is a map of which files on a
  deploy target hold credentials: per-deployment, and of no use to an adopter,
  who has their own. The exported names are `REPLACE_ME_*` placeholders.

The obvious objection is that fictional names ship a classifier that silently
protects nothing — a missing file is skipped by design in that tighten-only
`chmod` pass. So `scripts/gearbox` **refuses to deploy** while any `REPLACE_ME_`
entry remains. Loud, at the point of harm, instead of quiet.

Everything outside the section bodies is **regenerated from a template** rather
than filtered, so private prose in the source's comments cannot ride along after
a future edit, and an **unrecognized section is a hard failure** — a section the
transform has not classified could be anything, and defaulting to "publish it"
is how maps leak. The transform names none of the strings it removes: whole-
section replacement needs no match string, which is the same reason scrub
strings live in the manifest rather than in the published script.

## Drops

- **All `epic-*` agent rows** in the policy `agents:` block, and the
  epic-dev-assignments cross-check machinery that references them. The epic-dev
  suite is not part of Gearbox.

## Exclusions (never ported at all)

- `evals/routing/{results,harness,probe,graders,tasks}` — operator eval data,
  sweep outputs, and graders. Only the `evals/routing` README/runbook shape and
  `MISROUTES.md` template ship.
- `evals/dyno/**` — dyno eval data, in full: distilled `results/**` reports,
  harness/probe/grader code, and any transcript-derived content. None of it is
  a generic category the way `[live-state]` globs are; all of it is
  operator/session-specific. Nothing under `evals/dyno/` ships, at any tier.
- `.routing-guard.log` and any other runtime logs.
- Anything matched by the leak-defence scanner (below).

## Leak defence (fail-closed)

Three hooks, all fail-closed, guarding three different moments. **Enable them
once per clone with `git config core.hooksPath .githooks`** — git does not do
this for you on clone, and an unwired clone has no gate at all (see "Why CI
cannot scan for identifiers" below).

- **`.githooks/pre-commit` — pinned gitleaks** (version recorded in the hook)
  with the committed `.gitleaks.toml`. gitleaks missing or version-drifted =
  commit blocked, never skipped.
- **`.githooks/commit-msg` — no session / co-author trailers.** See "Commit
  trailers" below.
- **`.githooks/pre-push` — the last gate before publication.** Three layers,
  all fail-closed: pinned gitleaks; the **blocked-pattern sweep** (every
  confidential-identifier regex from the export manifest); the same sweep over
  the exact objects a push would create (new commits, their messages and
  identity fields, and every new blob); and an **allowlist of what may
  publish** (a new top-level path blocks the push).
  Layers 1–2 are executed by reusing `scripts/sync-from-claude.py --scan-only`,
  so the gate cannot drift away from the export pipeline's own scan.
  - **A secrets scanner alone is not enough.** gitleaks looks for *credentials*.
    It returns GREEN on a document naming your client or your home directory —
    verified. The blocked-pattern layer is what catches those.
  - The manifest holds the confidential values **as data**, so it lives
    **outside** this repo; point `GEARBOX_EXPORT_MANIFEST` at your own copy.
    There is no default path — a default would have to name a real private
    location. **No manifest = no push**; the hook never degrades to a
    secrets-only scan.
  - **The scan has no path exemptions.** It used to skip the sync script by
    name, on the reasoning that a scrub rule legitimately contains the strings
    it matches. That reasoning made the one file holding every confidential
    literal the one file no gate could see. The literals now live in the
    manifest instead.
- The CI gitleaks job uses the same pinned version and also fails closed — but
  it is a **credentials** scan only, not an identifier scan (next section).
- Scrubbing happens **before** content lands in a commit; a post-hoc worktree grep
  is not a defence because history retains the original blob.

### Why CI cannot scan for identifiers

**`.github/workflows/verify.yml` runs structural checks only. A green CI run is
not evidence that a change is identifier-clean.**

The blocked-pattern sweep is driven by the private-tier export manifest, and
that manifest's *contents are the denylist* — the client names, personal paths,
host identifiers and plan slugs the pipeline exists to keep unpublished.
Supplying it to GitHub Actions as a repository secret would upload the entire
list to the same platform this repo is published on, in order to check that the
repo does not name them. That is not a trade worth making, so **the manifest is
never uploaded and CI does not run that sweep.** CI's job name and its own log
output say so, so a reader is never left inferring coverage that does not exist.

What CI does check, all manifest-free: `verify-routing.sh --full --strict`,
`install.sh` into a fresh directory, pinned gitleaks (credentials), and the
integrity of the local gate files.

**Residual risk, and the one thing that closes it.** The local `pre-push` hook
is now the *only* gate that can catch an identifier leak. A push from a clone
that never ran `git config core.hooksPath .githooks` is completely unguarded.
That single command is the whole defence — run it immediately after cloning, and
confirm with `git config core.hooksPath`.

As partial cover, CI verifies that `.githooks/pre-commit`, `.githooks/commit-msg`
and `.githooks/pre-push` are executable in the git index and byte-identical to
`.githooks/CHECKSUMS.sha256`. That makes weakening or deleting a hook visible in
review; it cannot make an unwired clone run one. Regenerate the checksums
deliberately when a hook legitimately changes:

```bash
shasum -a 256 .githooks/pre-commit .githooks/commit-msg .githooks/pre-push \
    > .githooks/CHECKSUMS.sha256
```

### Commit trailers: this repo carries none

**No commit on this repo may carry a `Claude-Session:` trailer, a session URL,
or a `Co-Authored-By:` trailer.**

The surrounding environment's global commit convention appends them to every
commit. That is right for a private repo and wrong here: a session URL is a
private-tier identifier and a co-author line discloses which model wrote the
change, both published irreversibly with the commit. It is why an earlier
outgoing range on this repo had to be rebuilt before it was ever pushed, and
left alone it would recur on every future export commit.

The exception is structural, not a note someone has to remember, and it is
enforced in both directions:

- **`.githooks/commit-msg`** rejects the message at commit time.
- **`.githooks/pre-push`** (scan layer 2b, `FORBIDDEN_COMMIT_TRAILERS` in
  `scripts/sync-from-claude.py`) rejects any *outgoing commit message* carrying
  them — so a commit made with `--no-verify` still cannot be pushed.

Those patterns are hardcoded rather than manifest-driven: they are generic
trailer key names, not confidential values, so the gate works on any clone.
Write the message as subject + body — what changed and why — and nothing else.
Provenance for this repo is the `harness/SYNCED-FROM` stamp, not a trailer — and
that stamp carries an export date and a pipeline version, never a source-repo
revision (see below).

### Why the gate is on `push`, not just `commit`

A local commit is recoverable — amend it, reset it, nobody saw it. **A push is
not.** Once a blob reaches GitHub it stays reachable: the fork network keeps
otherwise-unreachable objects alive on sibling repos, and a commit SHA remains
fetchable by a short prefix long after a force-push "removed" it. `git push
--force` does not unpublish anything; it only stops a branch from pointing at
the object. The only defence that works is refusing the **first** push.

### Rotate-on-leak runbook

If something published anyway, in this order:

1. **Rotate every credential in the pushed blob first** — before any history
   rewrite. A rewrite does not un-disclose a secret; assume it was scraped
   within seconds.
2. Only then rewrite history (`git-filter-repo` / BFG) and force-push.
3. **Open a GitHub Support request** to garbage-collect the unreachable objects
   and purge the fork network. A rewrite alone leaves the old SHAs fetchable —
   until Support confirms, treat the content as public.
4. If the leak was a **non-rotatable identifier** (a client name, a home path, a
   person's name), rewriting is cosmetic. Record it, and fix the scrub rule in
   `scripts/sync-from-claude.py` so the whole class cannot recur.

## Verification per port session

Before committing ported content, grep the staged diff for your own
confidential identifiers — client names, username, vault/knowledge-base name,
real project paths — plus `epic-` (dropped rows). Maintain this list in your
fork's `.gitleaks.toml` and CI sweep. Any hit is a stop-and-fix, not a warning.
