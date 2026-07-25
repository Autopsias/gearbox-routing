export const meta = {
  name: 'adversarial-verify',
  description:
    'Finder-then-adversarial-verify template: one finder agent per review dimension, each finding refuted by two independent verifiers, majority vote decides survival. Adapt DIMENSIONS and the prompt builders to your review target.',
  phases: [
    { title: 'Find', detail: 'one finder agent per review dimension, run in parallel' },
    { title: 'Verify', detail: 'two independent refuters per finding; majority vote' },
  ],
}

// TEMPLATE — adapt DIMENSIONS and the two prompt builders below; the Find ->
// Verify -> majority-vote shape is what's worth keeping.
// args: { target: string, dimensions?: [{ key, brief }] }
const target = (args && args.target) || 'the current diff'

const DIMENSIONS = (args && args.dimensions) || [
  { key: 'correctness', brief: 'logic bugs: wrong conditions, off-by-one, null/undefined handling, wrong-variable mistakes.' },
  { key: 'reuse', brief: 'code that reimplements something the codebase already has elsewhere.' },
]

const FINDING = {
  type: 'object',
  required: ['location', 'summary', 'failureScenario'],
  properties: {
    location: { type: 'string', description: 'file:line or equivalent citation' },
    summary: { type: 'string' },
    failureScenario: { type: 'string', description: 'concrete, observable consequence' },
  },
}
const FINDINGS_SCHEMA = { type: 'object', required: ['findings'], properties: { findings: { type: 'array', items: FINDING } } }

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['real', 'reason'],
  properties: {
    real: { type: 'boolean', description: 'Is this finding genuinely present and consequential?' },
    reason: { type: 'string' },
  },
}

// ---- Phase: Find — one finder per dimension, run in parallel ----
phase('Find')
const found = await parallel(
  DIMENSIONS.map(d => () =>
    agent(
      `Review ${target} for ONE dimension only: ${d.brief}\n` +
      `Every finding needs a precise location citation and a concrete failureScenario ` +
      `(the observable consequence, not an intermediate state). If nothing qualifies, return an empty list.`,
      { model: 'sonnet', phase: 'Find', label: `find:${d.key}`, schema: FINDINGS_SCHEMA },
    ),
  ),
)
const all = found.filter(Boolean).flatMap(r => r.findings || [])
log(`${all.length} raw findings from ${DIMENSIONS.length} finders`)

// ---- Phase: Verify — two independent refuters per finding, majority vote ----
phase('Verify')
function refute(finding, label) {
  return agent(
    `You are an adversarial reviewer trying to REFUTE one reported finding about ${target}. ` +
    `Look for reasons it is a false positive: already handled elsewhere, unreachable, misdescribed.\n` +
    `Location: ${finding.location}\nSummary: ${finding.summary}\nFailure scenario: ${finding.failureScenario}\n` +
    `Open the cited location yourself and base your verdict on what you actually find there.`,
    { model: 'sonnet', phase: 'Verify', label, schema: VERDICT_SCHEMA },
  )
}

const verified = await parallel(
  all.map((f, i) => async () => {
    const votes = (await parallel([
      () => refute(f, `verify:${i}:a`),
      () => refute(f, `verify:${i}:b`),
    ])).filter(Boolean)
    const realVotes = votes.filter(v => v.real).length
    const verdict = votes.length > 0 && realVotes * 2 >= votes.length ? 'CONFIRMED' : 'REFUTED'
    return { ...f, verdict, reasons: votes.map(v => v.reason) }
  }),
)

const confirmed = verified.filter(f => f.verdict === 'CONFIRMED')
const refuted = verified.filter(f => f.verdict === 'REFUTED')
log(`${confirmed.length} confirmed, ${refuted.length} refuted by majority vote`)

return {
  target,
  findings: confirmed,
  refuted,
  stats: { dimensions: DIMENSIONS.length, raw: all.length, confirmed: confirmed.length, refuted: refuted.length },
}
