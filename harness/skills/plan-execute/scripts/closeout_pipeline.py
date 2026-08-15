"""Closeout pipeline: extract -> parse -> schema -> semantic verify -> persist.

The subagent ends its final message with a <plan-execute-closeout>...JSON...
</plan-execute-closeout> block. This module turns that raw text into a durable,
verified `_closeouts/<sid>.json` write-ahead record BEFORE any PLAN.html edit
(P3). Three failure states are distinguished (P4/H6): missing, json_error,
schema_error/semantic_error — all halt, with distinct diagnostics.

The parser is hardened (P4):
  * takes the LAST closeout block (not the first)
  * rejects fenced fakes — a block inside a ``` code fence is ignored
  * the real block must be the last non-whitespace content
  * the `session` field must match the dispatched session id
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

OPEN_TAG = "<plan-execute-closeout>"
CLOSE_TAG = "</plan-execute-closeout>"
VALID_RESULTS = {"DONE", "BLOCKED", "PARTIAL"}

# The manifest `plan_schema_version` at which `plan_impact` becomes a REAL field
# (validated here, acted on by run.cmd_apply's REPLAN park). Below it the key is
# ignored entirely — a plan built before this schema never gains a new refusal
# mode retroactively, which is the whole point of gating on a version stamp
# instead of on "the code is newer now".
PLAN_IMPACT_MIN_SCHEMA = 3

# The manifest `plan_schema_version` at which a BLOCKED closeout MUST carry a
# `decision_brief` (RS-04). Same gating discipline, same reason: a plan already
# on disk keeps the contract it was built under. Set to the version that
# introduced the field, so `build_plan.PLAN_SCHEMA_VERSION` and this constant
# move together and every plan built before the bump is untouched.
DECISION_BRIEF_MIN_SCHEMA = 5

MAX_BRIEF_OPTIONS = 3


def _now():
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------
# 1. Extraction (P4)
# --------------------------------------------------------------------------
def _strip_code_fences(text):
    """Remove fenced code blocks so a fake closeout demoed inside ``` is ignored."""
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def extract(raw):
    """Return {'status': 'ok', 'json_text': ...} or a failure dict.

    Failure dicts: {'status': 'missing'} | {'status': 'multiple'} ...
    """
    defenced = _strip_code_fences(raw)
    opens = [m.start() for m in re.finditer(re.escape(OPEN_TAG), defenced)]
    closes = [m.start() for m in re.finditer(re.escape(CLOSE_TAG), defenced)]
    if not opens or not closes:
        return {"status": "missing"}

    # Pair each OPEN with the next CLOSE after it; take the LAST complete block.
    blocks = []
    for o in opens:
        after = [c for c in closes if c > o]
        if after:
            c = after[0]
            inner = defenced[o + len(OPEN_TAG) : c]
            blocks.append((o, c, inner.strip()))
    if not blocks:
        return {"status": "missing"}

    # The real block must be the LAST non-whitespace content of the message.
    last_o, last_c, inner = blocks[-1]
    tail = defenced[last_c + len(CLOSE_TAG) :].strip()
    if tail:
        return {
            "status": "json_error",
            "raw": inner,
            "error": "closeout block must be the LAST content of the message; "
            f"found trailing text: {tail[:120]!r}",
        }
    return {"status": "ok", "json_text": inner}


# --------------------------------------------------------------------------
# 2. Parse + 3. Schema
# --------------------------------------------------------------------------
def parse_and_validate_schema(json_text):
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        return {"status": "json_error", "raw": json_text, "error": str(e)}
    if not isinstance(data, dict):
        return {
            "status": "schema_error",
            "parsed": data,
            "violations": ["closeout is not an object"],
        }

    violations = []
    required = {
        "session": str,
        "result": str,
        "items_completed": list,
        "items_blocked": list,
        "notes": dict,
    }
    for key, typ in required.items():
        if key not in data:
            violations.append(f"missing required field {key!r}")
        elif not isinstance(data[key], typ):
            violations.append(f"{key!r} must be {typ.__name__}, got {type(data[key]).__name__}")
    if data.get("result") not in VALID_RESULTS:
        violations.append(
            f"result must be one of {sorted(VALID_RESULTS)}, got {data.get('result')!r}"
        )
    for k in ("items_completed", "items_blocked"):
        v = data.get(k, [])
        if isinstance(v, list) and not all(isinstance(x, str) for x in v):
            violations.append(f"{k!r} must be a list of strings")
    if violations:
        return {"status": "schema_error", "parsed": data, "violations": violations}
    return {"status": "ok", "parsed": data}


# --------------------------------------------------------------------------
# 4. Semantic verification (H2)
# --------------------------------------------------------------------------
def plan_impact_violations(data, session_id, all_session_ids):
    """Schema + reference check for the optional ``plan_impact`` block (RP-05).

    Shape: ``{"invalidates": ["s07", ...], "reason": "<one line>"}``. Every id
    must name a session that EXISTS in the manifest, and must not be the
    reporting session itself (a session cannot invalidate its own finished
    work — that is what `redispatch` is for).

    A bad id is a LOUD closeout error, never a silent drop: this field parks the
    whole plan, and silently ignoring the half of it that did not resolve would
    let the plan carry on past work the session just declared invalid.
    """
    pi = data.get("plan_impact")
    if pi is None:
        return []
    if not isinstance(pi, dict):
        return [f"'plan_impact' must be an object, got {type(pi).__name__}"]
    v = []
    inv = pi.get("invalidates")
    if not isinstance(inv, list) or not inv:
        v.append("'plan_impact.invalidates' must be a non-empty list of session ids")
    elif not all(isinstance(x, str) for x in inv):
        v.append("'plan_impact.invalidates' must be a list of strings")
    else:
        known = set(all_session_ids or ())
        unknown = [x for x in inv if x not in known]
        if unknown:
            v.append(
                f"plan_impact.invalidates names session(s) that are in no manifest: "
                f"{sorted(unknown)} (known: {sorted(known)})"
            )
        if session_id in inv:
            v.append(
                f"plan_impact.invalidates names the reporting session {session_id!r} — a "
                "session cannot invalidate its own finished work; use `redispatch` for that"
            )
    reason = pi.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        v.append("'plan_impact.reason' must be a non-empty string saying WHAT was learned")
    extra = sorted(set(pi) - {"invalidates", "reason"})
    if extra:
        v.append(f"unknown key(s) in plan_impact: {extra} (allowed: invalidates, reason)")
    return v


def _option_text(opt):
    """One option's text, whether it was written as a plain string or as an
    object (`{"option": …, "tradeoff": …}`). Both shapes are accepted — refusing
    the object form would be pedantry, not a safety property."""
    if isinstance(opt, str):
        return opt.strip()
    if isinstance(opt, dict):
        head = str(opt.get("option") or opt.get("title") or "").strip()
        tail = str(opt.get("tradeoff") or opt.get("trade_off") or "").strip()
        return f"{head} — {tail}" if head and tail else head
    return ""


def decision_brief_violations(data):
    """Schema check for `decision_brief` (RS-04).

    Shape::

        {"attempts": ["…", …],
         "findings": [{"source": "…", "takeaway": "…"}, …],
         "options":  ["…", …]  (1-3),
         "recommendation": "…"}

    REQUIRED when ``result == "BLOCKED"``; optional (but recommended) otherwise —
    a session parking on ``human_checkpoint_reason`` is handing a human a
    decision, and the same four things are what makes that decision answerable.

    This is what turns "stuck, please advise" into a report the operator can act
    on without re-deriving the session's whole afternoon. The refusal is a LOUD
    closeout error that reworks exactly like any malformed closeout — a blocked
    session with no brief has not finished reporting.
    """
    brief = data.get("decision_brief")
    blocked = data.get("result") == "BLOCKED"
    if brief is None:
        if not blocked:
            return []
        return [
            "result=BLOCKED requires a 'decision_brief' — a bare block is not a report. "
            "Add decision_brief {attempts: [what you tried], findings: [{source, takeaway}] "
            "from the stuck-protocol research pass, options: 1-3, recommendation: <one>}"
        ]
    if not isinstance(brief, dict):
        return [f"'decision_brief' must be an object, got {type(brief).__name__}"]

    v = []
    attempts = brief.get("attempts")
    if not isinstance(attempts, list) or not attempts or not all(
        isinstance(a, str) and a.strip() for a in attempts
    ):
        v.append("'decision_brief.attempts' must be a non-empty list of non-empty strings "
                 "saying WHAT YOU TRIED")

    findings = brief.get("findings")
    if not isinstance(findings, list) or not findings:
        v.append("'decision_brief.findings' must be a non-empty list of "
                 "{source, takeaway} objects — what OUTSIDE sources say (the "
                 "stuck-protocol research pass); use [{'source': 'none', 'takeaway': "
                 "'searched X, nothing found'}] if the pass genuinely found nothing")
    else:
        for i, f in enumerate(findings):
            if not isinstance(f, dict):
                v.append(f"'decision_brief.findings[{i}]' must be an object with "
                         "'source' and 'takeaway'")
                continue
            for key in ("source", "takeaway"):
                if not isinstance(f.get(key), str) or not f[key].strip():
                    v.append(f"'decision_brief.findings[{i}].{key}' must be a non-empty string")

    options = brief.get("options")
    if not isinstance(options, list) or not options:
        v.append("'decision_brief.options' must be a list of 1-3 options")
    elif len(options) > MAX_BRIEF_OPTIONS:
        v.append(f"'decision_brief.options' has {len(options)} entries; at most "
                 f"{MAX_BRIEF_OPTIONS} are allowed — more than three is not a decision, "
                 "it is a menu")
    elif not all(_option_text(o) for o in options):
        v.append("every entry in 'decision_brief.options' must be a non-empty string "
                 "(or an object with a non-empty 'option')")

    rec = brief.get("recommendation")
    if not isinstance(rec, str) or not rec.strip():
        v.append("'decision_brief.recommendation' must be a non-empty string naming "
                 "the option you would pick — an unrecommended brief pushes the work back")

    extra = sorted(set(brief) - {"attempts", "findings", "options", "recommendation"})
    if extra:
        v.append(f"unknown key(s) in decision_brief: {extra} "
                 "(allowed: attempts, findings, options, recommendation)")
    return v


def format_decision_brief(brief):
    """Render a `decision_brief` as plain text for the HALT_NOTICE / checkpoint
    surfaces — options and recommendation visible to the operator WITHOUT opening
    `_closeouts/<sid>.json`. Returns "" when there is no usable brief."""
    if not isinstance(brief, dict):
        return ""
    lines = ["DECISION BRIEF"]
    attempts = [a for a in (brief.get("attempts") or []) if isinstance(a, str) and a.strip()]
    if attempts:
        lines.append("  Tried:")
        lines += [f"    - {a.strip()}" for a in attempts]
    findings = [f for f in (brief.get("findings") or []) if isinstance(f, dict)]
    if findings:
        lines.append("  Sources:")
        for f in findings:
            src = str(f.get("source") or "").strip() or "(unnamed source)"
            lines.append(f"    - {src}: {str(f.get('takeaway') or '').strip()}")
    options = [_option_text(o) for o in (brief.get("options") or [])]
    options = [o for o in options if o]
    if options:
        lines.append("  Options:")
        lines += [f"    {i}. {o}" for i, o in enumerate(options, 1)]
    rec = str(brief.get("recommendation") or "").strip()
    if rec:
        lines.append(f"  RECOMMENDATION: {rec}")
    return "\n".join(lines) if len(lines) > 1 else ""


def semantic_verify(data, session_id, session_items, all_session_ids=None,
                    plan_schema_version=None):
    """items_completed/blocked subset of session.items, no overlap, full
    coverage for DONE, session-id match — plus ``plan_impact`` on v3+ plans and
    the BLOCKED ``decision_brief`` on v5+ plans."""
    violations = []
    if data["session"] != session_id:
        violations.append(
            f"closeout session {data['session']!r} != dispatched session {session_id!r}"
        )
    scope = set(session_items)
    completed = set(data["items_completed"])
    blocked = set(data["items_blocked"])

    extra_c = completed - scope
    if extra_c:
        violations.append(f"items_completed not in session scope: {sorted(extra_c)}")
    extra_b = blocked - scope
    if extra_b:
        violations.append(f"items_blocked not in session scope: {sorted(extra_b)}")
    overlap = completed & blocked
    if overlap:
        violations.append(f"items both completed and blocked: {sorted(overlap)}")

    if data["result"] == "DONE":
        covered = completed | blocked
        if covered != scope:
            missing = scope - covered
            violations.append(
                f"result=DONE but items not all accounted for; uncovered: {sorted(missing)}"
            )

    if (plan_schema_version or 0) >= PLAN_IMPACT_MIN_SCHEMA:
        violations += plan_impact_violations(data, session_id, all_session_ids)

    if (plan_schema_version or 0) >= DECISION_BRIEF_MIN_SCHEMA:
        violations += decision_brief_violations(data)

    if violations:
        return {"status": "semantic_error", "parsed": data, "violations": violations}
    return {"status": "ok", "parsed": data}


# --------------------------------------------------------------------------
# Full pipeline + persist (P3)
# --------------------------------------------------------------------------
def run_pipeline(raw, session_id, session_items, all_session_ids=None,
                 plan_schema_version=None):
    """extract -> parse -> schema -> semantic. Returns a dict whose 'status'
    is 'ok' (with 'parsed') or one of the failure states with diagnostics."""
    step = extract(raw)
    if step["status"] != "ok":
        return step
    step = parse_and_validate_schema(step["json_text"])
    if step["status"] != "ok":
        return step
    return semantic_verify(
        step["parsed"], session_id, session_items,
        all_session_ids=all_session_ids, plan_schema_version=plan_schema_version,
    )


def closeout_digest(parsed):
    """Stable sha256 over a closeout's *semantic* payload (session, result,
    completed/blocked item sets, notes) — excluding volatile fields like
    ``_persisted_at``/``replayed`` AND the optional audit fields ``evidence``
    (Vista ③) / ``degraded_from`` (reactive degradation) / ``deviations`` /
    ``plan_impact`` (RP-05) / ``decision_brief`` (RS-04), which ``persist`` carries
    through verbatim but which
    must never affect the digest — every plan already on disk was digested
    without them, so folding one in would read as state-drift. Used to digest-bind
    ``_shipping_state`` to ``_closeouts`` so a re-applied identical closeout does NOT
    look like drift, but a genuinely different one does (Codex CRITICAL — state
    divergence)."""
    payload = {
        "session": parsed.get("session"),
        "result": parsed.get("result"),
        "items_completed": sorted(parsed.get("items_completed", [])),
        "items_blocked": sorted(parsed.get("items_blocked", [])),
        "notes": parsed.get("notes", {}),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def persist(plan_dir, session_id, parsed):
    """Atomic write-ahead record. Written BEFORE PLAN.html mutation."""
    co_dir = Path(plan_dir) / "_closeouts"
    co_dir.mkdir(exist_ok=True)
    record = dict(parsed)
    record["_persisted_at"] = _now()
    record["_closeout_digest"] = closeout_digest(parsed)
    record["replayed"] = False
    record["replayed_at"] = None
    target = co_dir / f"{session_id}.json"
    fd, tmp = tempfile.mkstemp(dir=str(co_dir), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(record, indent=2, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target


def mark_replayed(plan_dir, session_id):
    target = Path(plan_dir) / "_closeouts" / f"{session_id}.json"
    if not target.exists():
        return
    rec = json.loads(target.read_text())
    rec["replayed"] = True
    rec["replayed_at"] = _now()
    target.write_text(json.dumps(rec, indent=2, ensure_ascii=False))


def load_closeout(plan_dir, session_id):
    target = Path(plan_dir) / "_closeouts" / f"{session_id}.json"
    if not target.exists():
        return None
    return json.loads(target.read_text())
