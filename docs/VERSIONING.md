# Versioning the routing policy

The routing policy (`claude/model-routing.yaml`) carries a semver `version:`
field (with a companion `last_reviewed:` date, see the file's own GUARD PARSE
CONTRACT header). Adopters track changes through this number and
`CHANGELOG.md` — never through private decision-log comments inside the file.

## What bumps what

| Bump | When | Examples |
|---|---|---|
| **MAJOR** | The provider-neutral vocabulary or schema shape changes — anything that can break a consumer reading the file | rename/add/remove a tier, intent level, or task class; restructure the `providers:` block; change resolver contract |
| **MINOR** | Provider calibration changes within the existing schema | model-id swap on a tier; effort-map recalibration; escalation/degrade ladder re-bounds; price-driven re-pin; a new provider profile |
| **PATCH** | No behavioural change | typo, comment, doc clarification, formatting |

## CHANGELOG policy

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com) with one
entry per released version, newest first. Every calibration applied via
`/routing-update` MUST:

1. bump `version:` per the table above (and stamp `last_reviewed:` to the
   date of the change),
2. append a CHANGELOG entry with: date, bump level, what moved (old → new), and a
   one-line evidence pointer (the eval, price check, or provider announcement that
   justified it),
3. land the version bump and the CHANGELOG entry in the same commit as the policy
   change.

Unreleased work accumulates under an `## [Unreleased]` heading. There are no
silent recalibrations: if the policy file changed behaviourally, the version
moved and the changelog says why.

## Where this fits with the rest of the docs

- **How** to decide a calibration change is warranted (task classification,
  the retro → update loop, adding a provider) lives in
  [`docs/METHODOLOGY.md`](METHODOLOGY.md) — this file only owns the version
  *number* and changelog mechanics, not the judgment calls behind a bump.
- The very first public release is `1.0.0` (see `CHANGELOG.md`) — nothing
  ships as `0.x`. A policy file distributed to adopters is already a stable
  public contract from the first tag; there is no pre-1.0 "unstable" phase to
  work through.
