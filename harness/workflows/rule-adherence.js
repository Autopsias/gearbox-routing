export const meta = {
  name: 'rule-adherence',
  description:
    'Rule-adherence verifier: one verifier agent per CLAUDE.md behavior rule checks a target ' +
    '(diff or file list) for violations; each violated finding is then refuted by a skeptic agent ' +
    'biased to REFUTE (defaults to refuted when uncertain). Returns only findings that survive the skeptic.',
  phases: [
    { title: 'Verify', detail: 'one verifier agent per rule, run in parallel' },
    { title: 'Skeptic', detail: 'one skeptic agent per violated finding, biased to refute' },
  ],
}

// args: { rules: [{ id, text }], target: string }
// This workflow is always invoked with args built by the calling skill (e.g. no-mistakes
// --rules) - a bare/malformed invocation must fail loud, not silently fan out over nothing.
if (!args || !Array.isArray(args.rules) || args.rules.length === 0) {
  throw new Error(
    "rule-adherence requires args = { rules: [{ id, text }], target }; args.rules was " +
    "missing, not an array, or empty. Build args.rules from the rules you want checked " +
    "before invoking this workflow.",
  )
}

const rules = args.rules
const target = args.target || 'the current diff'

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['rule_id', 'violated', 'evidence', 'confidence'],
  properties: {
    rule_id: { type: 'string' },
    violated: { type: 'boolean', description: 'Does the target violate this rule?' },
    evidence: { type: 'string', description: 'concrete citation: file:line or a quoted excerpt' },
    confidence: { type: 'number', description: '0-1 confidence in the violated verdict' },
  },
}

const SKEPTIC_SCHEMA = {
  type: 'object',
  required: ['refuted', 'reason'],
  properties: {
    refuted: { type: 'boolean', description: 'true if this reported violation is a false positive' },
    reason: { type: 'string' },
  },
}

// ---- Phase: Verify — one verifier per rule, run in parallel ----
phase('Verify')
const verdicts = await parallel(
  rules.map(r => () =>
    agent(
      `Check ${target} against exactly ONE rule. Rule id: ${r.id}. Rule text:\n"${r.text}"\n\n` +
      `Decide whether the target violates this rule. Cite concrete evidence (file:line or a quoted ` +
      `excerpt) for your verdict either way. If the rule does not apply to this target at all, violated = false.`,
      { model: 'sonnet', phase: 'Verify', label: `verify:${r.id}`, schema: VERIFY_SCHEMA },
    ),
  ),
)

const violations = verdicts
  .filter(Boolean)
  .filter(v => v.violated)
  .map(v => ({ ...v, rule: rules.find(r => r.id === v.rule_id) }))
log(`${violations.length} violated finding(s) out of ${rules.length} rule(s) checked`)

// ---- Phase: Skeptic — one skeptic per violated finding, biased to refute ----
phase('Skeptic')
const reviewed = await parallel(
  violations.map(v => async () => {
    const skeptic = await agent(
      `You are a skeptic reviewing one reported rule violation about ${target}. Your job is to ` +
      `REFUTE it if at all reasonably possible - look for reasons it is a false positive: the rule ` +
      `doesn't actually apply here, the evidence is misread, the behavior is already compliant, or ` +
      `there is a legitimate exception. If you are uncertain either way, default to refuted = true - ` +
      `only report refuted = false when the violation is clear and you cannot construct a good-faith refutation.\n\n` +
      `Rule id: ${v.rule_id}. Rule text: "${v.rule ? v.rule.text : ''}"\n` +
      `Reported evidence: ${v.evidence}\nReported confidence: ${v.confidence}\n` +
      `Open the cited location yourself and base your verdict on what you actually find there.`,
      { model: 'sonnet', phase: 'Skeptic', label: `skeptic:${v.rule_id}`, schema: SKEPTIC_SCHEMA },
    )
    const refuted = !skeptic || skeptic.refuted !== false // no result or uncertain -> refuted
    return { ...v, refuted, skepticReason: skeptic ? skeptic.reason : 'skeptic agent returned no result' }
  }),
)

const confirmed = reviewed.filter(v => !v.refuted)
log(`${confirmed.length} confirmed after skeptic review, ${reviewed.length - confirmed.length} refuted`)

return { confirmed, checked: rules.length }
