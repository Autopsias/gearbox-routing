#!/usr/bin/env python3
"""Per-session execution-bundle emitters: the markdown a subagent actually reads.

Split out of build_plan.py, which had grown 64 lines past its size baseline.
Self-contained by construction — these three functions take a session dict and
return a string, calling nothing else in the builder, which is exactly why this
was the clean seam.
"""
import json


# --------------------------------------------------------------------------
# Per-session execution-bundle emitters
# --------------------------------------------------------------------------
def gen_post_session_section(post_session):
    """Render a 'Post-session actions' section so the subagent KNOWS what will
    fire after it returns, and leaves the working tree appropriately staged.
    The subagent does NOT run these itself — /plan-execute does, after the
    closeout is applied."""
    if not post_session:
        return ""
    lines = ["", "## Post-session actions (run by /plan-execute, NOT by you)", ""]
    git = post_session.get("git", "none")
    if git != "none":
        lines.append(f"- **git:** `{git}` — when you finish, leave the working tree in a "
                     "coherent, committable state (no half-edits, no debug scratch).")
    gates = post_session.get("pre_deploy_gates") or []
    if gates:
        lines.append(f"- **pre-deploy gates:** {', '.join(gates)} (must pass before any deploy).")
    if post_session.get("deploy_argv"):
        lines.append("- **deploy:** a project-local command vector will run after gates pass.")
    elif post_session.get("deploy", "none") not in (None, "none"):
        lines.append(f"- **deploy:** target `{post_session['deploy']}` will run after gates pass.")
    if post_session.get("rollback_hint"):
        lines.append(f"- **rollback hint (if this deploy is later invalidated):** "
                     f"{post_session['rollback_hint']}")
    if post_session.get("skip_if_partial"):
        lines.append("- These actions are SKIPPED unless your result is `DONE`.")
    lines.append("")
    lines.append("Because these are plan-declared, they are pre-authorized and fire without "
                 "re-prompting. Make sure your final state is shippable.")
    return "\n".join(lines)


def gen_verify_section(verify):
    """Render a 'Verification gates' section so the subagent knows its claimed-
    DONE work will be checked BEFORE it counts as done — and that a gate failure
    re-dispatches it with the failure attached. The subagent does NOT run these;
    /plan-execute does, at the apply boundary."""
    has_gates = bool(verify and verify.get("gates"))
    require_evidence = bool(verify and verify.get("require_evidence"))
    checks = (verify or {}).get("checks") or []
    if not has_gates and not require_evidence and not checks:
        return ""
    on_fail = verify.get("on_fail", "rework")
    mr = verify.get("max_rework", 1)
    lines = ["", "## Verification gates (run by /plan-execute, NOT by you)", ""]
    if has_gates:
        gates = ", ".join(f"`{g}`" for g in verify["gates"])
        lines.append(f"After you return `DONE`, these gates MUST pass before the session counts as "
                     f"done: {gates}.")
    if require_evidence:
        lines.append("This session **requires evidence**: your closeout MUST include an `evidence` "
                     "array of paths to artifacts that PROVE the work engaged — e.g. an eval/metrics "
                     "JSON, the saved output of a `grep -c <log-event>` (count > 0), a screenshot, or "
                     "a command transcript. Write those files to disk (commit-safe locations), then "
                     "list their paths. `DONE` is REFUSED until every listed path exists and is "
                     "non-empty — a metric swing without log/grep proof of engagement does not count.")
    if checks:
        lines.append("**Evidence contracts** — produce EVERY one of these named artifacts; the runner "
                     "asserts each path exists and is non-empty before `DONE` is granted (list them in "
                     "your closeout `evidence` array):")
        for c in checks:
            a = f" — {c['assert']}" if c.get("assert") else ""
            lines.append(f"  - `{c['evidence_path']}` ({c['name']}){a}")
    if on_fail == "rework":
        lines.append(f"- If a gate (or the evidence check) fails, you are re-dispatched with the "
                     f"failure attached (up to {mr} rework attempt{'s' if mr != 1 else ''}), then the "
                     f"session halts.")
    else:
        lines.append("- If a gate (or the evidence check) fails, the session halts immediately for "
                     "human investigation.")
    lines.append("- Do NOT claim `DONE` unless you believe these checks will pass — a self-reported "
                 "`DONE` that fails is the exact failure this catches. If you cannot make them pass, "
                 "return `PARTIAL` or `BLOCKED` with the reason in `notes`.")
    lines.append("")
    return "\n".join(lines)


def _prompt_fields(session, items_by_id, verify, post_session):
    """Everything the prompt template interpolates, worked out up front.

    Separate from the template so neither half is 110 lines: this one is all
    computation, the caller is all text.
    """
    sid = session["id"]
    title = session.get("title", sid)
    items = session.get("items", [])
    body = session.get("prompt", "").strip()
    if not body:
        body = (
            f"Execute the work for session {sid.upper()} — {title}. "
            "Use the agent-spec content in the context bundle for details."
        )
    item_lines = []
    for iid in items:
        it = items_by_id.get(iid, {})
        t = it.get("title", iid)
        s = it.get("human_summary") or it.get("description") or ""
        item_lines.append(f"- **{iid.upper()}** — {t}" + (f": {s}" if s else ""))
    items_block = "\n".join(item_lines) if item_lines else "_(no items linked)_"

    closeout_example = {
        "session": sid,
        "result": "DONE",
        "items_completed": items,
        "items_blocked": [],
        "notes": {iid: "one-line outcome" for iid in items} | {sid: "one-line session outcome"},
        "dispatch_next": True,
        "human_checkpoint_reason": None,
    }
    check_paths = [c["evidence_path"] for c in (verify or {}).get("checks", []) if c.get("evidence_path")]
    if verify and (verify.get("require_evidence") or check_paths):
        closeout_example["evidence"] = check_paths or [
            "docs/operations/<this-session>-evidence.md",
            "_evidence/<this-session>/grep-count.txt",
        ]
    closeout_json = json.dumps(closeout_example, indent=2)
    verify_block = gen_verify_section(verify)
    post_session_block = gen_post_session_section(post_session)

    return {
        "sid": sid, "title": title, "body": body, "items_block": items_block,
        "closeout_json": closeout_json,
        "verify_block": verify_block,
        "post_session_block": post_session_block,
    }


def gen_session_prompt_md(session, plan_dir_name, items_by_id, post_session=None, verify=None):
    """Render the invocation prompt the orchestrator hands to the subagent.

    The prompt body comes from session['prompt']. We wrap it with metadata
    (which items are in scope, where the per-session context bundle lives), a
    Post-session actions notice (so the subagent leaves the tree staged), and a
    strict closeout contract. The subagent never edits PLAN.html — it returns a
    structured closeout block.
    """
    f = _prompt_fields(session, items_by_id, verify, post_session)
    sid, title, body = f["sid"], f["title"], f["body"]
    items_block, closeout_json = f["items_block"], f["closeout_json"]
    verify_block, post_session_block = f["verify_block"], f["post_session_block"]
    return f"""# Session {sid.upper()} — {title}

You are executing **SESSION {sid.upper()}** as a subagent dispatched by `/plan-execute`.

## Plan location
The plan dashboard is at the sibling `PLAN.html` in this directory: `{plan_dir_name}/`.
You do **NOT** edit PLAN.html — the orchestrator handles all state mutation.

## Items in scope
{items_block}

## Context bundle
For schemas, mockups, code excerpts, and agent instructions per item, read:
`sessions/{sid}.context.md` (sibling of this file).

## Work
{body}
{verify_block}
{post_session_block}

## Implementation notes

Maintain a running implementation-notes log as you work. As much as a spec covers, there are always ambiguities and unknown unknowns — this log is your out to make a reasonable call and keep the human in the loop rather than stall or guess silently. Capture:
- Design decisions where the spec was ambiguous
- Intentional deviations from the item/spec, and why
- Tradeoffs considered and the reasoning for the choice made
- Open questions to confirm with the human later

Fold anything material from this log into the closeout's `human_summary`/`notes` fields below — the closeout is what the plan's history actually retains.

## If you hit an edge case not covered by the plan

Pick the conservative option, keep going, and record it — don't stall waiting for a human unless you're genuinely blocked. Record each deviation as one short line under an OPTIONAL `deviations` array in your closeout JSON (alongside `notes`) — this is how plan-vs-reality drift reaches the next session instead of evaporating. Omit the field entirely if you didn't deviate.

**An abort condition is not an edge case, and this paragraph does not apply to one.** If this session declares abort conditions or hard constraints, a hit on one **HALTS the run** — you close `BLOCKED` and report, **even when you are confident the constraint itself is wrong**. Say why you think it is wrong, in the `decision_brief`; that is exactly the report the operator needs. But rewriting, relaxing or replacing a hard constraint mid-run is the operator's call, never yours, and never justified by your fix being sound. A hard stop exists so that continuing does not depend on your judgement being correct — including the times it is. When you halt this way, `dispatch_next` is `false` and `human_checkpoint_reason` names the constraint, what it was guarding, and what you would do instead.

## If you fail twice at the SAME root cause — the stuck protocol

**Trigger:** two consecutive failed attempts whose error has the SAME root-cause signature (same error class and same message locus, ignoring paths, line numbers and whitespace). A *different* error on the second attempt is progress — keep going, the protocol does not fire. Repeating the same one means your model of the problem is wrong, and a third blind retry will not fix that.

This is not advisory. The orchestrator records a normalised failure signature and a consecutive counter for this session in `run_state.json` on every rework cycle; when the same signature repeats, the research pass below is injected into the feedback file your re-dispatch reads (`_verify_state/<session>.feedback.md`) and a `stuck_protocol_armed` event is written to `run.ndjson`.

**Action, in order:**
1. **One time-boxed research pass — 10 minutes, hard stop.** Look OUTSIDE this repository for this exact error class + locus. Tiered, first available wins, degrade gracefully: **Perplexity** (`mcp__perplexity-ask__perplexity_ask`) → **Exa** (`mcp__exa__web_search_exa`) → **Ref** (`mcp__ref__ref_search_documentation`). If a tier's MCP is not configured, drop to the next and say so; if none is available, the built-in web search counts. A pass that finds nothing is reported as "nothing found" — never silently skipped.
2. **Then exactly one of two things:** apply the fix, citing what the research changed about your diagnosis — or close `BLOCKED` carrying a `decision_brief` (below). Never silence the check, weaken the assertion, or retry blind.

This research pass is the rung between "retry" and "raise the model" on the standing escalation ladder. Do not jump past it to a model escalation.

## Closeout — return EXACTLY this block at the END of your final message

<plan-execute-closeout>
{closeout_json}
</plan-execute-closeout>

Closeout contract:
- `result`: one of `DONE` (all items in scope completed), `PARTIAL` (some items completed, session needs continuation), or `BLOCKED` (cannot proceed; explain in notes)
- `items_completed`: list of item IDs in scope that are now finished
- `items_blocked`: list of item IDs in scope that cannot be completed; explain each in `notes`
- `notes`: object mapping each item ID (and optionally the session ID) to a one-line outcome
- `dispatch_next`: hint for orchestrator; true normally, false if you set a human checkpoint
- `human_checkpoint_reason`: null normally. Set it ONLY when a human must decide something specific — and then state the decision itself in plain language (what happened, what the options are, what you recommend), never just "please review". If you cannot name a decision, leave it null.
- `evidence` (only if this session requires evidence — see "Verification gates" above): a list of paths to artifacts that prove the work engaged. Each path must exist and be non-empty or `DONE` is refused.
- `deviations` (optional): array of strings, one per edge case where you deviated from the plan (conservative option chosen + why, one line each). Omit entirely if you didn't deviate — routine sessions should not generate noise.
- `decision_brief`: **REQUIRED when `result` is `BLOCKED`** (recommended whenever you set `human_checkpoint_reason`). "Stuck, please advise" is not a report — a blocked closeout without this is REFUSED and reworks like any malformed closeout. Shape: `{{"attempts": ["what you tried", …], "findings": [{{"source": "…", "takeaway": "…"}}, …], "options": ["…"] (1-3), "recommendation": "…"}}`. `findings` carries what the stuck-protocol research pass turned up, with its sources — if it genuinely found nothing, say so as a finding (`{{"source": "none", "takeaway": "searched X and Y, nothing found"}}`). At most three options, and name the one you'd pick. The brief is rendered into `HALT_NOTICE.txt`, so the operator sees the options and your recommendation without opening any file.

The closeout block must be the LAST non-whitespace content of your message. Do not embed it inside markdown code fences (no triple-backtick wrap). HTML-escaping of note text is handled by the orchestrator.
"""
