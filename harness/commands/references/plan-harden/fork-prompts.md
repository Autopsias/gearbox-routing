# Phase 0 fork prompts (A/B/C/D) + output handling

Read this when Phase 0 SETUP has determined which forks are enabled (S.3 detection)
and you're about to spawn them — either as Workflow-script thunks or as parallel Agent
forks. This file holds the four fork prompt templates verbatim (each with its own
embedded JSON output schema) plus the fork-output-handling rules for both dispatch
paths. `plan-harden.md` itself only decides WHICH forks run (S.3) and how their
outputs merge into `ENRICHMENT_FINDINGS` — the actual prompt bodies live here.

## Contents

- [Pre-dispatch — repo-profile cache](#pre-dispatch--repo-profile-cache)
- [Fork A — project memory grep](#fork-a--project-memory-grep)
- [Fork B — external research](#fork-b--external-research)
- [Fork C — edge-case enumeration](#fork-c--edge-case-enumeration)
- [Fork D — territory blindspot scan](#fork-d--territory-blindspot-scan)
- [Fork output handling](#fork-output-handling)

---

### Pre-dispatch — repo-profile cache

*(Added 2026-07-09, source: everyinc/compound-engineering-plugin gap review.)*

Before spawning any fork, run once:

```
python3 ~/.claude/scripts/repo-profile-cache.py get
```

- **`HIT`** (line 2 = profile JSON: `stack`/`dependencies`/`topology`/`conventions`/`vocabulary`): append this block to the Fork C and Fork D prompts (the codebase-facing forks) so they don't re-derive stack/layout/conventions:

  ```
  REPO PROFILE (pre-derived, cached at current HEAD — trust it, don't re-derive):
  <profile JSON>
  ```

- **`MISS`**: dispatch forks exactly as today (no profile block). Optionally, after Phase 0 completes and only if this repo will be hardened again soon, derive a profile with one cheap Explore pass over root manifests + instruction files, write it to a temp file with exactly the five keys above, and `put <file>` so the next run HITs. Never block Phase 0 on this.
- **`NO-CACHE`**, script missing, or any error: proceed exactly as today. The cache is an optimization, never a correctness dependency — no fork behavior may depend on it.

### Fork A — project memory grep

```
subagent_type: Explore
description: "Memory grep for plan-harden Phase 0"
prompt: |
  You are Fork A in /plan-harden Phase 0. Your single job is to grep this user's project
  memory for content relevant to the plan below.

  MEMORY FILE PATH (already verified to exist by orchestrator):
  <MEMORY_PATH>

  PLAN TO MATCH AGAINST (full content inlined):
  ---
  <full PLAN_FILE content>
  ---

  STEPS:
  1. Read the MEMORY_PATH index file.
  2. Extract 3-7 keywords from the plan's title, "Recommended Approach", and any
     concrete file paths or component names it mentions.
  3. Grep the memory index for those keywords. Follow links to topic files for
     up to 5 promising matches. Read each topic file fully.
  4. Return STRUCTURED JSON ONLY — no preamble, no markdown:

  {
    "status": "ok|skipped|error",
    "source": "memory",
    "skip_reason": null,
    "findings": [
      {
        "text": "<one-sentence finding referencing what prior memo said>",
        "confidence": 0.0-1.0,
        "evidence_ref": "<topic-file-name.md>"
      }
    ],
    "errors": []
  }

  CONSTRAINTS:
  - Maximum 5 findings. Pick the highest-relevance ones.
  - "text" should be SHORT (<200 chars) — quote the relevant phrase, don't summarize.
  - Prioritize feedback_*.md files (user preferences) and project_*_<recent>.md files.
  - If the plan touches a domain with no memory hits, return "findings": [] with status="ok".
  - Total response budget: ~6k tokens. Truncate findings list before exceeding.
```

If `MEMORY_AVAILABLE=false` from S.3: do NOT spawn Fork A. Record the skip reason for the summary: `"no project memory at <MEMORY_PATH> — skipping memory enrichment"`.

### Fork B — external research

```
subagent_type: Explore
description: "External research for plan-harden Phase 0"
prompt: |
  You are Fork B in /plan-harden Phase 0. Your job is to surface industry patterns
  and known anti-patterns relevant to the plan.

  AVAILABLE RESEARCH TIERS (orchestrator-detected, do NOT self-introspect):
  <RESEARCH_TIERS_AVAILABLE>   # e.g. ["perplexity", "exa"]

  PLAN TO RESEARCH AGAINST:
  ---
  <full PLAN_FILE content>
  ---

  STEPS:
  1. Identify 1-2 narrow research questions from the plan (e.g. "what are known
     failure modes of X pattern?", "is Y library still maintained as of 2026?").
     Don't research the plan's whole domain — pick the 1-2 questions where
     external information would most change the plan's design.
  2. Use the FIRST tier in the available list. Fall back to next tier ONLY if
     the first returns no useful result. Do NOT call multiple tiers redundantly.
     - Tier order: perplexity → exa (web) → exa (deep) → ref
  3. Return STRUCTURED JSON ONLY:

  {
    "status": "ok|skipped|error",
    "source": "research",
    "skip_reason": null,
    "tier_used": "perplexity|exa-web|exa-deep|ref",
    "findings": [
      {
        "text": "<one-sentence pattern or anti-pattern + WHY it applies to this plan>",
        "confidence": 0.0-1.0,
        "evidence_ref": "<URL or citation>"
      }
    ],
    "errors": []
  }

  CONSTRAINTS:
  - Maximum 5 findings.
  - Confidence high (>=0.7) ONLY if you have a citation. Speculation gets <=0.5.
  - Total response budget: ~7k tokens.
```

If `RESEARCH_TIERS_AVAILABLE=[]` from S.3: do NOT spawn Fork B. Skip reason: `"no research MCPs connected — skipping external enrichment"`.

### Fork C — edge-case enumeration

If `BMAD_EDGE_CASE_AVAILABLE=true` AND `EDGE_CASE_MODE != skip`:

```
subagent_type: Explore
description: "Edge-case enumeration for plan-harden Phase 0"
prompt: |
  You are Fork C in /plan-harden Phase 0. Invoke the bmad-review-edge-case-hunter
  skill on the plan below.

  PLAN TO ANALYZE:
  ---
  <full PLAN_FILE content>
  ---

  STEPS:
  1. Call: Skill(skill="bmad-review-edge-case-hunter", args="Walk every branching path
     in the plan above. Return ≤5 unhandled edge cases.")
  2. Take the skill's output and convert it to STRUCTURED JSON ONLY:

  {
    "status": "ok|skipped|error",
    "source": "edge-cases",
    "skip_reason": null,
    "via": "bmad",
    "findings": [
      {
        "text": "<edge case + how the plan fails when it's hit>",
        "confidence": 0.0-1.0,
        "evidence_ref": "<plan section that has the gap>"
      }
    ],
    "errors": []
  }

  CONSTRAINTS:
  - Maximum 5 findings.
  - confidence: HIGH-severity edges = 0.9, MEDIUM = 0.6, LOW = 0.3.
  - Total response budget: ~6k tokens.
```

Else if `EDGE_CASE_MODE != skip` AND `BMAD_EDGE_CASE_AVAILABLE=false` (and ALWAYS in the Workflow path, regardless of BMAD detection), use the **inline edge-case prompt variant**:

```
subagent_type: Explore
description: "Inline edge-case enumeration for plan-harden Phase 0"
prompt: |
  You are Fork C in /plan-harden Phase 0 (inline-prompt fallback, BMAD skill not registered).

  PLAN TO ANALYZE:
  ---
  <full PLAN_FILE content>
  ---

  Enumerate unhandled edge cases in the plan. For each:
  1. Boundary: input/state/timing/concurrency edge that the plan doesn't address
  2. Failure path: how the system fails when this edge is hit
  3. Severity: HIGH (breaks plan goals) | MEDIUM (degrades) | LOW (cosmetic)

  Return ≤5 cases as STRUCTURED JSON ONLY:

  {
    "status": "ok|skipped|error",
    "source": "edge-cases",
    "skip_reason": null,
    "via": "inline",
    "findings": [
      {
        "text": "<boundary + failure path, severity prefix>",
        "confidence": 0.0-1.0,
        "evidence_ref": "<plan section where the gap lives>"
      }
    ],
    "errors": []
  }

  Total response budget: ~6k tokens.
```

If `EDGE_CASE_MODE=skip`: do NOT spawn Fork C.

### Fork D — territory blindspot scan

If `BLINDSPOT_AVAILABLE=true` AND `BLINDSPOT_TARGETS` is non-empty:

```
subagent_type: Explore
description: "Territory blindspot scan for plan-harden Phase 0"
prompt: |
  You are Fork D in /plan-harden Phase 0. Invoke the `blindspot` skill on the code
  areas this plan touches, to surface real landmines (reverted attempts, mid-flight
  migrations, flag divergence, unwritten conventions) instead of imagined ones.

  SCAN TARGETS (orchestrator-derived from the plan's touched code areas):
  <BLINDSPOT_TARGETS>

  PLAN FOR CONTEXT:
  ---
  <full PLAN_FILE content>
  ---

  STEPS:
  1. Call: Skill(skill="blindspot", args="Scan <BLINDSPOT_TARGETS> before this plan
     touches it. Return landmine/convention/history/missing-concept cards plus any
     prompt constraints.")
  2. Take the skill's output and convert it to STRUCTURED JSON ONLY:

  {
    "status": "ok|skipped|error",
    "source": "blindspot",
    "skip_reason": null,
    "findings": [
      {
        "text": "<landmine/convention/history/missing-concept card, one sentence>",
        "confidence": 0.0-1.0,
        "evidence_ref": "<commit sha, file path, or convention source>"
      }
    ],
    "errors": []
  }

  CONSTRAINTS:
  - Maximum 5 findings. Prioritize landmine cards (reverted attempts, mid-flight
    migrations, flag divergence) over convention notes — those are the empirical
    failure material Phase 3's premortem needs.
  - confidence: a documented reverted/rolled-back attempt = 0.9, an inferred
    convention = 0.5-0.6.
  - Total response budget: ~6k tokens.
```

If `BLINDSPOT_AVAILABLE=false` from S.3 (including via `--no-blindspot`): do NOT spawn Fork D. Skip reason: `"blindspot skill not available — skipping territory enrichment"`. If `BLINDSPOT_TARGETS=[]` (plan touches no code): do NOT spawn Fork D. Skip reason: `"plan touches no code — skipping blindspot enrichment"`.

### Fork output handling

**Workflow path:** nothing to parse — the workflow returns schema-validated objects. Hold its return directly as `ENRICHMENT_FINDINGS = {memory: ..., research: ..., edge_cases: ..., blindspot: ...}` (skipped forks already synthesized as `{"status": "skipped", "skip_reason": "..."}`; per the Fork D note above, `blindspot` may arrive from a separate Agent fork merged in after the Workflow call returns).

**Dossier-to-disk rule (any fork producing long prose):** the four standard forks return ≤5 structured findings inline — that stays as-is. But a fork asked to return a long prose body (a full research dossier, a multi-page scan) intermittently returns an executive summary instead, and the prose is then unrecoverable (observed upstream: compound-engineering-plugin issue #956). If a future fork variant needs to hand back long prose, dispatch it as a general-purpose agent (Explore lacks Write), have it write the full artifact to a scratch file, and return only the path + a ≤5-line gist; downstream readers open the file themselves.

**Agent-fork fallback path only:** forks emit JSON as their final agent message (they do NOT write to `/tmp/` — Explore lacks Write). For each fork return: take the first `{` to the last `}` of the final message and `JSON.parse` it; on parse failure treat as `{"status": "error", "source": "<known>", "errors": ["fork output unparseable"]}` (first-class failure, not silent corruption); if output appears truncated, use what's parseable and append `"truncated"` to errors. Wait for every spawned fork to return, error, or be written off per the ~2-3 min rule before starting Phase 1. Then hold `ENRICHMENT_FINDINGS` in the same shape as above.

---
