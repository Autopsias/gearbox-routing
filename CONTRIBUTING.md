# Contributing

Gearbox is a policy file plus a small set of guard scripts and skills. Most
contributions fall into one of three shapes:

1. **A provider profile update** — new model, changed price, changed effort
   semantics. Follow `docs/METHODOLOGY.md` §2/§4 and land it through
   `/routing-update` so the version bump + CHANGELOG entry travel with it.
2. **A schema/vocabulary change** — a new tier, a new task class, a resolver
   contract change. This is a MAJOR version bump (`docs/VERSIONING.md`) —
   open an issue describing the change before sending a PR, since it can
   break every consumer reading the file.
3. **A guard/skill/doc fix** — typos, clarifications, guard bugs. Business
   as usual: branch, PR, CI green.

## Before you open a PR

- Run `./install.sh --claude-home <tempdir> --accept-example-profile` and
  confirm `verify-routing.sh --full` passes. Never touch your real
  `~/.claude` while testing.
- Never assert a model id, price, or effort-dial behavior from memory —
  verify against the provider's current docs (`docs/METHODOLOGY.md` §2).
- **First thing after cloning, run `git config core.hooksPath .githooks`.**
  Git never does this for you, and the local hooks are the *only* gate that
  scans for confidential identifiers — CI cannot (see below). Confirm with
  `git config core.hooksPath`. That wires all three: pinned `gitleaks` at
  pre-commit, the no-trailers rule at commit-msg, and the full fail-closed
  identifier sweep at pre-push.
- If you're adding or editing a provider profile, follow the scrub rules in
  `GENERICIZATION.md` — no personal identifiers, no real corpus/vault paths,
  no concrete production policy data.

## CI

Every PR runs **structural checks only**: `verify-routing.sh --full --strict`,
`install.sh` into a fresh dir, pinned gitleaks (credentials), and an integrity
check of the local hook files (see `.github/workflows/verify.yml`).

**CI does not scan for confidential identifiers and cannot** — that sweep needs
a private denylist that must not be uploaded to GitHub. It runs locally at
pre-push instead, which is why the `core.hooksPath` step above is not optional.
`GENERICIZATION.md` §"Why CI cannot scan for identifiers" has the full reasoning
and the residual risk.

A separate scheduled weekly job re-checks the shipped
example profiles' `as_of`/`calibration.date` staleness even without an open
PR — a failure there means the shipped examples need a `/routing-update`
pass, not necessarily a code change.

## Code of conduct

See `CODE_OF_CONDUCT.md`.
