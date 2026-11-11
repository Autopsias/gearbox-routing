# S06 worked run — internal-corpus verification (SV-04 · SV-06)

run_id: 2026-05-31T1730_s06-internal-verification
artifact: harbor_board_note.docx (document) · travel_class: board-regulatory (verification auto)

Backends resolved THROUGH the F2-04 registry (live tool list, never hard-coded):
  internal_search          -> mcp__obsidian-connector__obsidian_search@unknown
  internal_semantic_search -> mcp__obsidian-connector__obsidian_search@unknown

Three internal claims folded into the SAME §2 provenance record:
  1. "Harbor completes in 2027"  -> corroborates (Obsidian note)            -> VERIFIED
  2. "Harbor completes in 2025"  -> contradicts (Obsidian note says 2027)   -> CONFLICTING (§2 conflict opened, never blended)
  3. "Group headcount is 1195"      -> corroborates (knowledge-base record, after Obsidian returned nothing
                                        -> the adapter FELL THROUGH the chain) -> VERIFIED

Internal-as-sensitive (§2.1): each admitted source carries the note path/id + an excerpt-hash +
a MINIMUM snippet (never the whole note) and is classified `internal` (same redaction/model-routing).
The external pass (no external provider resolved in this run) degraded to not-found — never fabricated.
SV-06: the manifest.verification block was written at the source-packet stage; the trust-review
re-check was idempotent (recheck_noop=True, stages=['source-packet', 'trust-review'], same fingerprint,
no duplicate, no re-run).

verdicts (external+internal passes): ['not-found', 'corroborates', 'not-found', 'corroborates', 'not-found', 'contradicts']
promoted_to_verified: ['The Harbor programme completes in 2027', 'Group headcount is 1195']
