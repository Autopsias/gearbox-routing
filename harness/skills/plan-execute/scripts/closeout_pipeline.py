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
def semantic_verify(data, session_id, session_items):
    """items_completed/blocked subset of session.items, no overlap, full
    coverage for DONE, session-id match."""
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
    if violations:
        return {"status": "semantic_error", "parsed": data, "violations": violations}
    return {"status": "ok", "parsed": data}


# --------------------------------------------------------------------------
# Full pipeline + persist (P3)
# --------------------------------------------------------------------------
def run_pipeline(raw, session_id, session_items):
    """extract -> parse -> schema -> semantic. Returns a dict whose 'status'
    is 'ok' (with 'parsed') or one of the failure states with diagnostics."""
    step = extract(raw)
    if step["status"] != "ok":
        return step
    step = parse_and_validate_schema(step["json_text"])
    if step["status"] != "ok":
        return step
    return semantic_verify(step["parsed"], session_id, session_items)


def closeout_digest(parsed):
    """Stable sha256 over a closeout's *semantic* payload (session, result,
    completed/blocked item sets, notes) — excluding volatile fields like
    ``_persisted_at``/``replayed`` AND the optional audit fields ``evidence``
    (Vista ③) / ``degraded_from`` (reactive degradation), which ``persist`` carries
    through verbatim but which must never affect the digest. Used to digest-bind
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
