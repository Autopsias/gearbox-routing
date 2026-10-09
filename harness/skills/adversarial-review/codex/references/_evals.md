# Adversarial Review E-checks

Run these checks before returning any review. If a check fails, repair the
review and rerun the full set. After two repair rounds, return ACTION REQUIRED
with the failing check id, evidence, and attempted repairs.

- id: E1
  check: "The review contains the required sections: Findings, Open Questions, Review Basis and Assumptions, Research Performed and Limitations, Residual Risks or Unreviewed Areas, the run-integrity E-check block, and a final Claude Handoff section with a fenced text block that is self-contained enough for Claude to act: artifact identity, review as-of date, supported decision/scope, verdict, actionable finding evidence and required action, owner-gated open questions, preserve constraints, residual risks, and a bounded task for Claude."
  how: read
  on_fail: repair

- id: E2
  check: "Every reported finding uses only blocking, material, or minor for impact and verified or probable for evidence strength."
  how: read
  on_fail: repair

- id: E3
  check: "Every reported finding includes Location, Criterion, Assumption-dependent, Evidence, Consequence, Remediation, and Provenance fields."
  how: read
  on_fail: repair

- id: E4
  check: "No finding exists only to satisfy a quota, and zero material findings remains allowed when no candidate survives falsification."
  how: judge
  on_fail: action_required

- id: E5
  check: "Current, legal, regulatory, standards, policy, market, vendor, and citation-dependent material findings have verified source status and do not treat provisional agreement, proposal, guidance, commentary, or forecast as operative fact."
  how: judge
  on_fail: action_required

- id: E6
  check: "Temporal-staleness findings state the artifact date, review as-of date, source-of-truth date, and whether the later source is decided, proposed, superseded, or working material."
  how: read
  on_fail: repair

- id: E7
  check: "Acknowledged owner inputs, placeholders, and missing approvals are in Open Questions unless the review explains why they block or materially distort the supported decision."
  how: judge
  on_fail: repair

- id: E8
  check: "Every finding states the concrete decision impact or failure mode and survives an explicit counter-interpretation or falsification attempt."
  how: judge
  on_fail: action_required

- id: E9
  check: "The review-only boundary is preserved: the artifact is not edited unless the user explicitly requested revision."
  how: read
  on_fail: action_required

- id: E10
  check: "If a durable lesson, correction, or process surprise occurred, an event-driven memory entry was appended; routine successful runs append nothing."
  how: read
  on_fail: repair
