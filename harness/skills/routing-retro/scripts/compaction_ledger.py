#!/usr/bin/env python3
"""compaction_ledger.py — defensive readers for the ~/.dyno/compaction/ ledgers.

The leaf half of the compaction retro (s07/PF-01): parsing, validation, and the
per-intervention exposure model. `compaction_retro.py` builds the report on top
of it and `arming_check.py` reads the same functions, so there is exactly one
implementation of "is this row admissible" in the tree.

Three rules are load-bearing and each has a fixture pinning it:

1. NO COHORT IS EVER COMPUTED FROM A SINGLE LEDGER TIMESTAMP. Exposure is per
   intervention, from that intervention's OWN activation row. There is no global
   "since the deploy" boundary anywhere in this module.
2. A session that starts before an activation and ends strictly after it is
   `spanning` and belongs to NO cohort. `end == ts` is `before`; `start == ts`
   is `after`.
3. VALIDITY IS DECIDED BEFORE EXPOSURE. A malformed/conflicting activation makes
   the intervention `activation_unknown` and its cohorts are never computed; an
   intervention with no row at all is `no_exposure`. The two are different facts
   and are never swapped.

stdlib only. Read-only: writes nothing anywhere.
"""

import json
import os
import re
from datetime import datetime, timezone

# MUST match hooks/compact_activation.py's own INTERVENTIONS — that file is the
# WRITER, this one is the READER, and a name the writer emits but this list omits
# is counted as a malformed row and silently dropped. That is exactly what
# happened to `compact_window`: two valid activation rows written 2026-08-23,
# both discarded, the intervention reporting no cohort and no verdict on every
# retro until 2026-08-27. test_compaction_retro.py asserts the two lists agree.
INTERVENTIONS = ("hooks", "base_context", "routing", "repo_diet", "compact_window")

# s02 guarantees these on every decisions line. TYPE checks, not key presence —
# a `policy_version: "1"` is a different mechanism's row, not this one's.
DECISION_SPINE = {
    "ts": str, "session": str, "event": str,
    "source": str, "policy_version": int, "reason": str,
}

ACTIVATION_FIELDS = ("schema", "event_id", "ts_utc", "intervention", "scope", "evidence", "source")
EVENT_ID_RX = re.compile(r"([a-z_]+)#([0-9]+)")

# A compaction RECORD is an attempted compaction, not a prompt classification.
COMPACTION_EVENTS = ("pre-compact", "compacted", "post-compact")

# Where ctx_after ACTUALLY comes from. PostCompact writes the `compacted` row
# with ctx_after=None and ctx_after_deferred=True, because the number does not
# exist yet at that moment (context_tokens.context_after_compaction explains
# why); a later prompt writes `compaction-measured` carrying the real value.
# These events are DELIBERATELY OUTSIDE COMPACTION_EVENTS: a measured row is
# the second record of ONE compaction, so counting it as a compaction record
# would double every compaction count on the page.
CTX_PAIR_EVENTS = ("compacted", "compaction-measured")


def ctx_pair_rows(records):
    """THE accessor for rows that may carry a ctx_before/ctx_after pair."""
    return [r for r in records if r.get("event") in CTX_PAIR_EVENTS]

# What the ONE writer (hooks/compact-policy.py verdict()) actually emits on a
# compaction decision. "veto" appears nowhere in the writer; a reader counting
# it made the Vetoes column structurally always zero. A "compacted" row
# legitimately carries no decision at all (compact_reorient.py writes none).
DECISION_BLOCK = "block"
DECISION_ALLOW = "allow"

# run_stdin_hook's fail-open wrapper stamps decision='allow' on a CRASH
# (hooks/compact-policy.py, reason 'hook-error'). That allow is the wrapper's
# stamp, not a decision the policy made: a reader taking it at face value
# renders a hook that crashes on every compaction as a 100% clean allow rate —
# the measured-sounding non-measurement MOVE 2b exists to expose.
REASON_HOOK_ERROR = "hook-error"


def compaction_rows(records):
    """THE accessor for compaction decision rows — the only way any consumer may
    select them. A prompt-classification line is not a compaction; three modules
    each re-filtering by hand is how 150 prompt rows entered the Allows column."""
    return [r for r in records if r.get("event") in COMPACTION_EVENTS]


def decision_counts(records):
    """{vetoes, allows, unrecognized, hook_errors} over compaction rows ONLY.

    Tolerant-reader rule (arc42): an unknown value in an enum field the reader
    DOES read is surfaced and counted, never a silent default — a zero produced
    by vocabulary drift must be distinguishable from a measured zero."""
    out = {"vetoes": 0, "allows": 0, "unrecognized": 0, "hook_errors": 0}
    for r in compaction_rows(records):
        if r.get("reason") == REASON_HOOK_ERROR:
            # The reason is checked BEFORE the decision value: a crash row
            # carries decision='allow', and counting it there is exactly the
            # defect this bucket exists to prevent.
            out["hook_errors"] += 1
            continue
        d = r.get("decision")
        if d == DECISION_BLOCK:
            out["vetoes"] += 1
        elif d == DECISION_ALLOW:
            out["allows"] += 1
        elif d is not None:
            out["unrecognized"] += 1
    return out


def measurement_unavailable(row):
    """This decision row was an allow-BY-DEFAULT because the hook could not
    measure headroom (verdict() rule (e)). The writer stamps
    'unreadable_context:<ctx_source>'; ctx_source contains
    'measurement_unavailable:<why>' only when the TRANSCRIPT was unreadable —
    a measured ctx with an unknown model window stamps
    'unreadable_context:read:no_model_window', which a bare substring test for
    'measurement_unavailable' silently counted as a clean allow. Match the
    writer's rule, not one spelling of one branch of it."""
    reason = row.get("reason") or ""
    return reason.startswith("unreadable_context") or "measurement_unavailable" in reason

DYNO = os.path.expanduser("~/.dyno/compaction")


def paths(root=None):
    root = root or DYNO
    return {
        "decisions": os.path.join(root, "decisions.ndjson"),
        "sessions": os.path.join(root, "sessions.ndjson"),
        "activations": os.path.join(root, "activations.ndjson"),
        "version": os.path.join(root, "policy", "VERSION.json"),
        "policy_dir": os.path.join(root, "policy"),
    }


def parse_ts(value):
    """A real UTC datetime, or None. Never a string comparison: an ISO8601 value
    with a fractional second sorts differently as text than as a moment in time,
    and an invalid calendar date must be rejected rather than ordered."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_ndjson(path):
    """(rows, rejected, exists). A missing file is a RESULT, never an exception;
    a line that is not JSON, or is JSON but not an object, is rejected and
    counted — never dropped silently and never fatal."""
    if not path or not os.path.exists(path):
        return [], 0, False
    rows, rejected = [], 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                rejected += 1
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                rejected += 1
    return rows, rejected, True


def spine_ok(row):
    for key, typ in DECISION_SPINE.items():
        val = row.get(key)
        if typ is int and isinstance(val, bool):
            return False       # a bool is an int in Python; it is not a version
        if not isinstance(val, typ):
            return False
        if typ is str and not val.strip():
            return False
    return parse_ts(row["ts"]) is not None


def load_decisions(path):
    """Spine-validated decisions. Every read in this session rejects a row missing
    any spine field and COUNTS it — a malformed line silently entering a cohort
    produces a false verdict, which is the one outcome this session prevents."""
    rows, rejected, exists = load_ndjson(path)
    good = []
    for row in rows:
        if spine_ok(row):
            row = dict(row)
            row["_ts"] = parse_ts(row["ts"])
            good.append(row)
        else:
            rejected += 1
    return good, rejected, exists


def load_heartbeats(path):
    """session id -> the FIRST heartbeat for that session (cp-02 writes one per
    session at UserPromptSubmit). Its `type` is the first classification only —
    read the live type from the policy file, which an upgrade may have moved."""
    rows, rejected, exists = load_ndjson(path)
    beats = {}
    for row in rows:
        sid, ts = row.get("session"), parse_ts(row.get("ts"))
        if not isinstance(sid, str) or not sid or ts is None:
            rejected += 1
            continue
        if not isinstance(row.get("kill_switch"), str) or not isinstance(row.get("hooks_registered"), bool):
            rejected += 1
            continue
        if sid not in beats or ts < beats[sid]["_ts"]:
            row = dict(row)
            row["_ts"] = ts
            beats[sid] = row
    return beats, rejected, exists


def load_policy_types(policy_dir):
    """session id -> live session type from ~/.dyno/compaction/policy/<sid>.json."""
    types = {}
    if not policy_dir or not os.path.isdir(policy_dir):
        return types
    for name in os.listdir(policy_dir):
        if not name.endswith(".json") or name == "VERSION.json":
            continue
        try:
            with open(os.path.join(policy_dir, name), encoding="utf-8") as f:
                obj = json.load(f)
        except (ValueError, OSError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("type"), str):
            types[name[: -len(".json")]] = obj["type"]
    return types


def autocompact_window(settings_path):
    """The one auto-compact window surface (cp-05). None unless a settings record
    is actually found — NEVER a default standing in for a measurement.

    ONE definition, here in the leaf module: retro_scan.py and compaction_report.py
    both need it, and a second copy is how a rule drifts."""
    try:
        with open(settings_path, encoding="utf-8") as f:
            obj = json.load(f)
    except (ValueError, OSError):
        return None
    val = obj.get("autoCompactWindow") if isinstance(obj, dict) else None
    return val if isinstance(val, int) and not isinstance(val, bool) else None


def current_policy_version(version_path):
    try:
        with open(version_path, encoding="utf-8") as f:
            obj = json.load(f)
    except (ValueError, OSError):
        return None
    ver = obj.get("policy_version") if isinstance(obj, dict) else None
    return ver if isinstance(ver, int) and not isinstance(ver, bool) else None


# --------------------------------------------------------------------------
# Activations — the SOLE authority on when an intervention went live.
# --------------------------------------------------------------------------
def _activation_row_ok(row):
    """(intervention, seq) for a well-formed row, else (None, None)."""
    if any(k not in row for k in ACTIVATION_FIELDS):
        return None, None
    if row.get("schema") != 1 or isinstance(row.get("schema"), bool):
        return None, None
    eid, iv = row.get("event_id"), row.get("intervention")
    if not isinstance(eid, str) or not isinstance(iv, str) or iv not in INTERVENTIONS:
        return None, None
    # fullmatch, not `$`: `$` also matches before a trailing newline, and a
    # trailing-newline event_id is exactly one of the shapes that must be refused.
    mo = EVENT_ID_RX.fullmatch(eid)
    if not mo or mo.group(1) != iv:
        return None, None
    if parse_ts(row.get("ts_utc")) is None:
        return None, None
    if not isinstance(row.get("source"), str) or row["source"] not in ("operator", "session"):
        return None, None
    return iv, int(mo.group(2))


def load_activations(path):
    """{intervention: {status, ts, row, prior_stages}} for all four interventions.

    status is `exposed` (a valid activation, cohorts may be computed),
    `activation_unknown` (rows exist but are malformed/conflicting) or
    `no_exposure` (no row at all — the clock never started).

    Several rows for ONE intervention are valid only when their seqs are exactly
    1..n with no gap and no repeat (a deliberate `--restage`); the HIGHEST SEQ,
    not the highest timestamp, is the activation used for cohorts.
    """
    rows, rejected, exists = load_ndjson(path)
    by_iv, malformed = {}, 0
    # A malformed row whose `intervention` field is still READABLE names the
    # intervention it damages. Dropping it left that intervention reporting
    # `no_exposure` — "the clock never started" — when the truth is "the ledger
    # is broken", and the two are opposite instructions to the reader: one says
    # wait for data, the other says fix the writer. A row whose intervention
    # cannot be read at all still degrades the whole ledger's trust and nothing
    # more, because it cannot be attributed.
    damaged = set()
    for row in rows:
        iv, seq = _activation_row_ok(row)
        if iv is None:
            malformed += 1
            named = row.get("intervention") if isinstance(row, dict) else None
            if isinstance(named, str) and named in INTERVENTIONS:
                damaged.add(named)
            continue
        by_iv.setdefault(iv, []).append((seq, row))

    out = {}
    for iv in INTERVENTIONS:
        entries = by_iv.get(iv, [])
        # Checked BEFORE the entries test on purpose: one malformed row makes the
        # ledger untrustworthy for this intervention whether or not a valid row
        # also exists. Reporting `exposed` off the surviving rows would quietly
        # answer from a sequence with a hole in it.
        if iv in damaged:
            out[iv] = {"status": "activation_unknown", "ts": None, "row": None,
                       "prior_stages": [],
                       "reason": "a row naming this intervention is malformed"}
            continue
        if not entries:
            out[iv] = {"status": "no_exposure", "ts": None, "row": None, "prior_stages": []}
            continue
        seqs = sorted(s for s, _ in entries)
        if seqs != list(range(1, len(seqs) + 1)):
            out[iv] = {"status": "activation_unknown", "ts": None, "row": None,
                       "prior_stages": [], "reason": f"seqs {seqs} are not 1..n"}
            continue
        entries.sort(key=lambda p: p[0])
        top = entries[-1][1]
        out[iv] = {"status": "exposed", "ts": parse_ts(top["ts_utc"]), "row": top,
                   "prior_stages": [r for _, r in entries[:-1]]}
    # A malformed row naming an unrecognised intervention cannot be attributed to
    # one, so it degrades the WHOLE ledger's trust rather than one intervention.
    return {
        "interventions": out,
        "rejected_lines": rejected,
        "malformed_rows": malformed,
        "any_object": bool(rows),          # "an activation object exists"
        "any_valid": any(v["status"] == "exposed" for v in out.values()),
        "exists": exists,
    }


def cohort_label(started, ended, activation_ts):
    """'before' | 'after' | 'spanning' for ONE session against ONE activation.

    end == ts is `before` (a session that ENDED at the activation instant saw
    none of it); start == ts is `after`. Anything that started before and ended
    strictly after is `spanning` — excluded from that intervention's comparison,
    counted, and reported. A session whose end is unknown can still be `after`
    (start >= ts is sufficient) but is never assumed to have ended in time.
    """
    if activation_ts is None or started is None:
        return None
    if ended is not None and ended <= activation_ts:
        return "before"
    if started >= activation_ts:
        return "after"
    return "spanning"
