# Review Contract

## Purpose

Stress-test an artifact against an explicit basis while minimizing unsupported
findings. Generate objections broadly, but admit them to the final report only
after verification and attempted falsification.

## Scope Control

1. Name the exact artifact, version, files, sections, or decision under review.
2. Read the complete relevant artifact.
3. Inspect adjacent context only when it can confirm or refute a candidate.
4. State any part of the artifact that was unavailable.
5. Do not silently expand from reviewing an artifact to reviewing its entire
   organization, product, or domain.

For a diff, inspect both changed behavior and the contracts it can affect. For a
document excerpt, request or locate surrounding context when omission could
reverse the conclusion.

## Review Basis

Capture these fields before judging the artifact:

| Field | Question |
|---|---|
| Objective | What outcome is this artifact trying to produce? |
| Audience | Who will use, approve, operate, or be affected by it? |
| Decision | What choice or action should it support? |
| Constraints | What limits are binding: time, cost, compatibility, law, risk, or policy? |
| Sources of truth | Which contracts, requirements, data, tests, or authorities govern correctness? |
| Non-goals | What is intentionally outside scope? |
| Assumptions | What must be true for the artifact to work? |
| Review lenses | What did the user explicitly ask to challenge? |

For dated artifacts, also capture:

| Field | Question |
|---|---|
| Artifact date | What date, version, or effective state does the artifact represent? |
| Review as-of date | What current date is the review using? |
| Freshness mode | Is the review point-in-time, latest/current-state, or both? |
| Later sources | Which later decisions, records, releases, or facts can supersede the artifact? |

Use explicit criteria first. Infer a criterion only when review cannot proceed
without it. Label any resulting finding `assumption-dependent`.

Ask one concise question when multiple plausible bases would produce materially
different verdicts. Otherwise proceed and disclose the chosen assumptions.

## Steelman Check

Before generating objections:

1. Summarize the artifact's central claim or approach internally.
2. Identify the strongest evidence and constraints supporting it.
3. Distinguish what it claims from what it merely leaves open.
4. Prefer the interpretation most consistent with the complete artifact.

Do not turn ambiguity into a defect when a reasonable interpretation satisfies
the basis. Record material unresolved ambiguity as a candidate or question.

## Common Lenses

Apply only lenses relevant to the review basis:

- goal alignment and decision usefulness
- internal consistency
- hidden assumptions and missing dependencies
- failure modes, edge cases, and cascading effects
- stakeholder impact and misuse
- feasibility, sequencing, and ownership
- evidence quality and missing validation
- reversibility, observability, and operational risk
- safety, security, integrity, privacy, or compliance

## Temporal Staleness

When the review asks whether an artifact is current, treat later facts as
evidence only after establishing their status:

- `decided` or `operative`: can support a verified staleness finding.
- `formally adopted but not effective`: can support a finding when the artifact
  claims no future change or the supported decision depends on the future date.
- `provisional`, `proposed`, `working`, or `draft`: usually belongs in Open
  Questions or a probable finding, unless the artifact itself claims the matter
  is final.
- `conflicting`: lower evidence strength or report the conflict rather than
  choosing the convenient source.

State the artifact date, review as-of date, later-source date, and source status
in any staleness finding.

## Candidate Ledger

For each private candidate, capture:

| Field | Content |
|---|---|
| Claim or behavior | The exact artifact statement or observed behavior |
| Location | File and line, section, paragraph, table, or quoted claim |
| Criterion | The review-basis criterion possibly violated |
| Consequence | The concrete failure that could follow |
| Evidence needed | What would confirm or reject the objection |
| Verification type | Internal, tool-verifiable, or externally factual |
| Countercase | What would make the objection wrong |

Candidates are working hypotheses. Do not expose the ledger as findings.

## Falsification Gate

For every candidate:

1. State the strongest counter-interpretation.
2. Search the artifact and surrounding context for refuting evidence.
3. Run focused tests or inspect tool output when available and proportionate.
4. Check whether the consequence is concrete and material to the objective.
5. Check whether the criterion is explicit or clearly marked as inferred.
6. Merge duplicates that share the same cause and consequence.

Discard the candidate when it is:

- speculative or unsupported
- contradicted by stronger evidence
- based on an invented requirement
- a subjective preference outside the basis
- a style note with no concrete consequence
- immaterial to the supported decision
- better expressed as an unresolved question
- dependent on an unverified external claim

## Stopping Conditions

Stop when:

- every material candidate has been verified or discarded
- credible sources converge enough to classify the claim
- additional testing or research is unlikely to change a verdict
- the remaining uncertainty requires unavailable information or qualified review

Do not keep searching to satisfy a finding quota. Zero findings is valid.

## Review-Only Boundary

Propose remediations or discriminating experiments, but do not edit the artifact
unless the user explicitly asks for revision. A request to "review," "challenge,"
or "stress-test" alone does not authorize changes.
