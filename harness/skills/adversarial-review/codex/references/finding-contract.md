# Finding Contract

## Classification

Classify impact independently from evidence strength.

### Impact

- `blocking`: Likely to invalidate the decision, break a critical contract, or
  cause serious harm.
- `material`: A meaningful defect, omission, or risk that can change the
  decision or outcome.
- `minor`: A limited but concrete issue worth correcting.

Do not inflate impact because a claim is easy to prove.

### Evidence Strength

- `verified`: Directly demonstrated by the artifact, execution, authoritative
  source, or reproducible counterexample.
- `probable`: Strong contextual evidence supports the finding, but a material
  part is not directly demonstrated.
- `question`: Important uncertainty that requires clarification or validation.

Only `verified` and `probable` items are findings. Put `question` items in a
separate Open Questions section.

Use these exact labels. Do not translate them to `critical`, `high`, `medium`,
`low`, or numeric scores.

## Admission Test

Report a finding only when all answers are yes:

1. Does it violate an explicit or disclosed inferred criterion?
2. Is there artifact, tool, test, or verified external evidence?
3. Is the consequence concrete?
4. Did the objection survive a serious counter-interpretation?
5. Is it material to the artifact's objective or supported decision?
6. Is it distinct from other findings?

If the criterion is inferred rather than stated, the finding must say
`Assumption-dependent: yes`. If the criterion is merely a generic best practice
and no concrete consequence can be demonstrated for this artifact, reject the
candidate or record it as residual risk.

## Markdown Template

```markdown
### [Impact] Concise problem statement

- **Evidence strength:** verified | probable
- **Location:** file:line, section, paragraph, or quoted claim
- **Criterion:** violated review-basis criterion
- **Assumption-dependent:** yes | no
- **Evidence:** artifact evidence, tool result, counterexample, or verified source
- **Consequence:** concrete failure or decision impact
- **Remediation:** fix, decision change, or discriminating experiment
- **Provenance:** source title and URL when external evidence is used
```

Keep evidence and reasoning concise but sufficient for another reviewer to
reproduce the conclusion. Quote only the minimum artifact text needed to anchor
the finding. Do not replace the template with an unclassified bullet list.

## Conceptual Record

```json
{
  "title": "Concise problem statement",
  "impact": "blocking | material | minor",
  "evidence_strength": "verified | probable",
  "location": "file:line, section, paragraph, or quoted claim",
  "criterion": "The review-basis criterion that is violated",
  "evidence": "Artifact evidence, tool result, or verified external source",
  "consequence": "What can concretely go wrong",
  "remediation": "Fix, decision change, or discriminating experiment",
  "assumption_dependent": false,
  "provenance": []
}
```

Return Markdown unless the user explicitly requests machine-readable output.

## Report Structure

1. Findings, ordered by impact and then evidence strength
2. Open Questions
3. Review Basis and Assumptions
4. Research Performed and Limitations
5. Residual Risks or Unreviewed Areas
6. Run-integrity E-check block
7. Claude Handoff section with one fenced `text` block

When there are no surviving findings, begin with:

```markdown
No material findings.
```

Do not replace findings with compliments or a generic summary. Do not include a
finding quota, score, confidence theater, or unsupported "best practice" claims.

## Final Compliance Check

Before returning:

- every finding uses an allowed impact and evidence label
- every finding includes all template fields
- inferred criteria are marked `assumption-dependent`
- questions are outside Findings
- all five report sections are present
- the run-integrity E-check block is present
- the Claude Handoff section is present, self-contained enough for Claude to
  act, and only summarizes information already supported in the review
- the Claude Handoff includes artifact identity, review as-of date, supported
  decision/scope, verdict, actionable finding evidence and required action,
  owner-gated open questions, preserve constraints, residual risks, and a
  bounded task for Claude
- zero findings is stated plainly when no candidate passes
