"""TEL-01 — the outcome ledger writer.

One append-only NDJSON line per session-attempt RESOLUTION (verify passed, a
rework recorded, rework exhausted/halted, a terminal DONE/BLOCKED closeout with
no verify block, or a WONTFIX retirement). This is the memory the adaptive
routing loop is meant to learn from — see
``_plans/adaptive-routing-upward-escalation-outcome-learning-2026-08-13``.

POSTURE, copied deliberately from ``hooks/dispatch-audit.py``: this is an
OBSERVER, never a gate. :func:`write` never raises — any failure is swallowed
(and printed to stderr) so a broken ledger can never block or crash the dispatch
loop it is only watching. ``PLAN_EXECUTE_NO_OUTCOME_LEDGER=1`` disables it
outright (tests / emergencies); ``PLAN_EXECUTE_OUTCOME_LEDGER=<path>`` overrides
the ledger location (tests only — the allow-listed override).

ATTESTATION. A background Agent-tool dispatch carries no usage telemetry of its
own, so "which model actually served this session" is knowable only through an
explicit correlation step: the orchestrator calls the new ``record-receipt``
subcommand after the Agent call returns and before ``apply``/verify resolution,
and the receipt lands in ``run_state.json["dispatch_receipts"]`` (see
:mod:`run_state_io`). Everything here reads STRICTLY off that receipt for
``model_ran``/``model_ran_source`` — a closeout's own ``escalated_from`` /
``degraded_from`` claims are carried through verbatim as their own nullable
fields (they are honest reports of what the ORCHESTRATOR did), but they are
NEVER treated as proof of what actually served the request. No receipt at all
degrades honestly to ``model_ran_source="unknown"`` / ``model_ran="unknown"`` —
never assumed from the request.
"""

import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import closeout_pipeline as cp
import escalation as esca
import manifest_io as mio
import run_state_io as rsi

KILL_SWITCH_ENV = "PLAN_EXECUTE_NO_OUTCOME_LEDGER"
LEDGER_PATH_ENV = "PLAN_EXECUTE_OUTCOME_LEDGER"  # allow-listed test/override var

RESULTS = {"passed", "rework", "exhausted", "blocked", "done_unverified", "wontfix"}
SOURCE_ATTESTED = "attested"
SOURCE_REQUESTED = "requested"
SOURCE_UNKNOWN = "unknown"

_MAX_RECORD_BYTES = 4000  # hygiene only (PIPE_BUF governs pipes, not regular files)
_GATES_FAILED_MAX = 3


def _now():
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------
# Ledger location
# --------------------------------------------------------------------------
def _default_ledger_path():
    # run.py lives at <root>/skills/plan-execute/scripts/run.py -> parents[3] is
    # the harness root (~/.claude when deployed, the source repo in dev/tests) —
    # same anchor `_routing_ssot_path()` uses for model-routing.yaml.
    return Path(__file__).resolve().parents[3] / "evals" / "routing" / "outcomes.ndjson"


def ledger_path():
    override = os.environ.get(LEDGER_PATH_ENV)
    return Path(override) if override else _default_ledger_path()


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------
def _project_name(plan_dir):
    """Repo basename this plan lives in — the ledger's ``project`` field."""
    try:
        import worktree as wtree  # noqa: PLC0415 — only the ledger needs git

        root = wtree.repo_root(plan_dir)
        if root:
            return Path(root).name
    except Exception:  # noqa: BLE001 — never let identity resolution crash the writer
        pass
    for p in Path(plan_dir).resolve().parents:
        if (p / ".git").exists():
            return p.name
    return Path.cwd().name


def _plan_slug(plan_dir):
    return Path(plan_dir).resolve().name


def record_id(project, plan, session, generation, attempt, resolution):
    """Deterministic id from the resolution's identity, so a replayed resolution
    (e.g. ``verify-finalize`` re-invoked after ``outcome=passed``) collides with
    the record already on disk instead of duplicating it."""
    blob = "|".join(str(x) for x in (project, plan, session, generation, attempt, resolution))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------------------
# run.ndjson helpers
# --------------------------------------------------------------------------
def _events(plan_dir, event_type, session_id):
    try:
        lines = (Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if rec.get("event") == event_type and session_id in (rec.get("session_ids") or []):
            out.append(rec)
    return out


# --------------------------------------------------------------------------
# Backend / lane + authored cell (lazy `run` import — run.py imports verify.py,
# which will import this module, so a top-level `import run` here would cycle)
# --------------------------------------------------------------------------
def _backend_and_authored(plan_dir, manifest, session, task_class):
    """(backend, model_authored, reasoning_authored) — best-effort, never raises.

    Mirrors the exact lane + cell resolution ``verify.py``'s
    ``_escalation_descriptor`` already applies (the codex lane's authored cell is
    the RESOLVED ``(codex_model, codex_effort)`` pair, never the manifest's raw
    Claude token — walking the openai ladder from a Claude token raises)."""
    import run as _run  # noqa: PLC0415

    try:
        provider, ssot_text = _run._load_routing()
        lane = _run._dispatch_lane(session, provider or "anthropic", ssot_text, plan_dir=plan_dir)
    except Exception:  # noqa: BLE001
        return "claude", _run._normalize_model(session.get("model")), \
            _run._reasoning_tier(session.get("reasoning"))
    if lane == "codex":
        try:
            model, reasoning = _run._resolve_codex_dispatch(
                session.get("model"), session.get("reasoning"), ssot_text, task_class=task_class,
            )
            return "codex", model, reasoning
        except Exception:  # noqa: BLE001 — unroutable: fall back to the raw tokens
            return "codex", session.get("model"), (session.get("reasoning") or None)
    return "claude", _run._normalize_model(session.get("model")), _run._reasoning_tier(session.get("reasoning"))


def _ssot_version_ran():
    """The `version:` stamp of the model-routing.yaml THIS PROCESS read — the
    SSOT the deployed tree was actually running, not a calendar date (source
    edits only take effect after `gearbox deploy`)."""
    import run as _run  # noqa: PLC0415

    try:
        _, ssot_text = _run._load_routing()
        if not ssot_text:
            return None
        m = re.search(r"^version:\s*(\d+)", ssot_text, re.M)
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def _routing_provenance(plan_dir, session, task_class, backend, model_authored, reasoning_authored):
    """default_resolved | pinned_override | experimental — derived at write time.

    `experimental` wins outright (a canary-tagged session is never "just the
    default"). Otherwise the authored cell is compared against
    `resolve_route.resolve(task_class, EFFECTIVE_PROVIDER)`'s baseline, where the
    effective provider is backend-aware — the SAME mapping `run.py` dispatch
    uses (`anthropic` for the claude lane, `openai` for the codex one). Any
    resolution gap (no task_class, resolver unavailable, no baseline) falls back
    to `pinned_override` rather than guessing "default" — a false "pinned" is a
    missed data point; a false "default" corrupts the class-default calibration
    the aggregator computes from this field."""
    if session.get("routing_experiment"):
        return "experimental"
    if not task_class or not model_authored:
        return "pinned_override"
    try:
        import run as _run  # noqa: PLC0415

        effective_provider = _run._ESCALATION_PROVIDER.get(backend, "anthropic")
        rr = _run._import_resolver()
        baseline = rr.resolve(task_class, effective_provider,
                              ssot_path=str(_run._routing_ssot_path()))
        if not isinstance(baseline, dict):
            return "pinned_override"
        matches = (baseline.get("model_id") == model_authored
                   and (baseline.get("native_effort") or None) == (reasoning_authored or None))
        return "default_resolved" if matches else "pinned_override"
    except Exception:  # noqa: BLE001
        return "pinned_override"


def _effort_mechanism(session):
    """tier_agent | agent_definition | prompt_directive_advisory — the same
    three-way classification `cmd_begin` computes at dispatch (mirrored here,
    not re-imported, since the dispatch-time value is not persisted anywhere a
    later resolution moment can read)."""
    agent = ((session.get("dispatch") or {}).get("subagent_type") or "").strip()
    if not agent:
        return "prompt_directive_advisory"
    if agent.startswith("tier-"):
        return "tier_agent"
    return "agent_definition"


# --------------------------------------------------------------------------
# Attestation receipt lookup
# --------------------------------------------------------------------------
def _dispatch_ordinal(plan_dir, session_id):
    """How many `begin`s this session has ever had (count of `dispatch_started`
    events naming it) — the correlation key finding 1 needs. Independent of
    both `rework_count` (verify.py's attempt counter) and `_apply_attempt`
    (apply_terminal's), because a receipt is written right after `begin`
    returns, before either of those resolution-specific counters is even
    relevant — this is purely "which BEGIN produced the Agent ID on this
    receipt" vs "which BEGIN is THIS resolution about", compared by the same
    yardstick on both sides."""
    return len(_events(plan_dir, "dispatch_started", session_id))


def _model_ran(plan_dir, session_id, model_authored, reasoning_authored, escalated_from,
               degraded_from, generation):
    """(model_ran, reasoning_ran, model_ran_source) — STRICTLY receipt-gated.

    No `record-receipt` for this session at all -> ("unknown", "unknown",
    "unknown"), full stop, even if the closeout claims an `escalated_from` or
    `degraded_from` substitution: those are honest reports of what the
    ORCHESTRATOR asked for, never proof of what actually served the request
    (see module docstring).

    TEL-01 finding 1 — a receipt does NOT get cleared on re-dispatch
    (`run_state_io.get_receipt`'s own docstring says so): if `record-receipt`
    is skipped on a later attempt, the receipt still on file describes an
    EARLIER attempt. `record_receipt` stamps the (generation, dispatch
    ordinal) it was recorded for; if those don't match what THIS resolution
    is actually about, the receipt is stale evidence for a different
    dispatch and must degrade to `unknown` exactly like having no receipt at
    all — never silently carry an old attempt's model forward."""
    receipt = rsi.get_receipt(plan_dir, session_id)
    if not receipt:
        return "unknown", "unknown", SOURCE_UNKNOWN
    if (receipt.get("generation") != generation
            or receipt.get("attempt") != _dispatch_ordinal(plan_dir, session_id)):
        return "unknown", "unknown", SOURCE_UNKNOWN
    attested = receipt.get("attested")
    if attested and attested.get("model"):
        return attested["model"], attested.get("reasoning") or reasoning_authored, SOURCE_ATTESTED
    # Receipt exists (an Agent ID was correlated) but no served-model evidence —
    # the best honest claim is "what we asked for", which is the escalated/
    # degraded cell when one applies, else the authored one.
    if escalated_from and isinstance(escalated_from, dict):
        ran = escalated_from.get("ran") or {}
        if ran.get("model"):
            return ran["model"], ran.get("reasoning") or reasoning_authored, SOURCE_REQUESTED
    if degraded_from and isinstance(degraded_from, dict) and degraded_from.get("ran"):
        return degraded_from["ran"], degraded_from.get("reasoning") or reasoning_authored, SOURCE_REQUESTED
    return model_authored or "unknown", reasoning_authored, SOURCE_REQUESTED


# --------------------------------------------------------------------------
# Served-model attestation from a dispatch transcript — PER BACKEND, per the
# plan's explicit finding: a codex member is a Claude Agent wrapper around an
# inner `codex exec`, so attesting the outer Agent says nothing about the model
# that actually did the work.
# --------------------------------------------------------------------------
def attest_from_transcript(backend, transcript_path):
    """Served-model evidence from `transcript_path`, or None. Never raises.

    CLAUDE backend: the one PROVEN telemetry source in this repo is headless
    `claude -p --output-format json`'s `modelUsage` block (s09's capability
    probe; an in-session Agent-dispatch transcript handle is UNPROVEN — never
    assumed). `modelUsage`'s keys can include a background `claude-haiku-4-5`
    title/fast-mode helper alongside the task model
    (evals/routing/harness/lib_common.py `attest_served_model`) — excluded here
    the same proven way: drop any key containing "haiku", keep the rest.

    CODEX backend: the `codex exec --json` event stream carries usage but NO
    served-model field in any envelope on the version probed
    (evals/routing/graders/judge.py:8) — this always returns None for codex.
    That is the honest, MEASURED cap this plan names (codex cohorts stay at
    `model_ran_source="requested"`), not a gap to paper over by guessing.

    When `modelUsage` holds more than one non-haiku entry, the one that did
    the most WORK — highest `inputTokens + outputTokens` — is the served
    model; a tied max is genuinely ambiguous and this refuses to guess,
    returning None so the caller stays at `model_ran_source="requested"`
    rather than naming an arbitrary key with false confidence."""
    if backend == "codex" or not transcript_path:
        return None
    try:
        data = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mu = data.get("modelUsage") if isinstance(data, dict) else None
    if not isinstance(mu, dict) or not mu:
        return None
    non_helper = [k for k in mu if "haiku" not in k.lower()]
    if not non_helper:
        return {"model": next(iter(mu)), "reasoning": None}
    if len(non_helper) == 1:
        return {"model": non_helper[0], "reasoning": None}

    def _tokens(k):
        usage = mu.get(k) or {}
        try:
            return int(usage.get("inputTokens") or 0) + int(usage.get("outputTokens") or 0)
        except (TypeError, ValueError):
            return 0

    ranked = sorted(non_helper, key=_tokens, reverse=True)
    top_tokens = _tokens(ranked[0])
    if len(ranked) > 1 and _tokens(ranked[1]) == top_tokens:
        return None  # tied max — genuinely ambiguous, refuse rather than guess
    return {"model": ranked[0], "reasoning": None}


# --------------------------------------------------------------------------
# Compose
# --------------------------------------------------------------------------
def compose(plan_dir, session_id, *, resolution, result, verified, attempt=1,
           rework_count=0, gates_failed=None, stuck_armed=False, duration_s=None):
    """Build one ledger record dict. Never raises — best-effort field resolution,
    everything defaults to a plainly-absent value rather than crashing."""
    manifest = mio.load_manifest(plan_dir)
    session = mio.session_by_id(manifest).get(session_id) or {"id": session_id}
    task_class = (session.get("task_class") or "").strip().lower() or None

    backend, model_authored, reasoning_authored = _backend_and_authored(
        plan_dir, manifest, session, task_class,
    )
    tier_authored = esca.rung_key(model_authored, reasoning_authored)

    closeout = cp.load_closeout(plan_dir, session_id) or {}
    degraded_from = closeout.get("degraded_from")
    escalated_from = closeout.get("escalated_from")
    if not escalated_from:
        # Older/rework-path closeouts may not carry it; the dispatch-time event
        # is the fallback source of truth (`_record_escalation`, run.py).
        ev = _events(plan_dir, "escalation_applied", session_id)
        if ev:
            last = ev[-1]
            if last.get("rung", 0) > 0:
                escalated_from = {
                    "authored": last.get("authored"), "ran": last.get("ran"),
                    "rung": last.get("rung"), "generation": last.get("generation"),
                }

    generation = esca.session_state(plan_dir, session_id)["generation"]

    model_ran, reasoning_ran, model_ran_source = _model_ran(
        plan_dir, session_id, model_authored, reasoning_authored, escalated_from, degraded_from,
        generation,
    )
    tier_ran = esca.rung_key(model_ran, reasoning_ran) if model_ran != "unknown" else "unknown"

    rec = {
        "ts": _now(),
        "project": _project_name(plan_dir),
        "plan": _plan_slug(plan_dir),
        "session": session_id,
        "generation": generation,
        "task_class": task_class,
        "backend": backend,
        "model_authored": model_authored,
        "reasoning_authored": reasoning_authored,
        "tier_authored": tier_authored,
        "model_ran": model_ran,
        "reasoning_ran": reasoning_ran,
        "tier_ran": tier_ran,
        "model_ran_source": model_ran_source,
        "effort_mechanism": _effort_mechanism(session),
        "degraded_from": degraded_from,
        "escalated_from": escalated_from,
        "routing_experiment": session.get("routing_experiment"),
        "routing_provenance": _routing_provenance(
            plan_dir, session, task_class, backend, model_authored, reasoning_authored,
        ),
        "ssot_version_ran": _ssot_version_ran(),
        "attempt": int(attempt or 1),
        "rework_count": int(rework_count or 0),
        "stuck_armed": bool(stuck_armed),
        "gates_failed": list(gates_failed or [])[:_GATES_FAILED_MAX],
        "result": result,
        "verified": bool(verified),
    }
    if duration_s is not None:
        rec["duration_s"] = duration_s
    rec["record_id"] = record_id(rec["project"], rec["plan"], rec["session"],
                                 generation, rec["attempt"], resolution)
    return rec


# --------------------------------------------------------------------------
# Atomic append (flock + short-write loop) with record_id dedup
# --------------------------------------------------------------------------
def _write_full(fh, payload):
    """Write `payload` (bytes) to `fh` in full, looping on short writes. A single
    `write()` syscall is not guaranteed to consume the whole buffer even under an
    exclusive flock — the lock guarantees exclusivity, not atomicity of one call."""
    written = 0
    while written < len(payload):
        n = fh.write(payload[written:])
        if not n:  # a 0-return would spin forever on a truly broken fd
            raise OSError("short write returned 0 bytes — file descriptor stalled")
        written += n
    return written


def _append_dedup(path, rec):
    """Append `rec` unless a line with the same record_id already exists.

    Holds ONE exclusive flock across the whole read-check-write so two
    concurrent appenders can never interleave a partial line or double-write a
    replayed resolution. Binary mode + an explicit short-write loop: a str-mode
    file's `write()` return value is characters, not bytes, and cannot be looped
    on correctly for a partial write — `fcntl.flock` guarantees exclusivity
    (POSIX advisory lock), not that a single `write()` syscall lands whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    payload = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    with open(path, "a+b") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.seek(0)
            for existing in fh:
                existing = existing.strip()
                if not existing:
                    continue
                try:
                    if json.loads(existing).get("record_id") == rec["record_id"]:
                        return {"written": False, "duplicate": True}
                except ValueError:
                    continue  # a torn/short line from a crash — not this record
            fh.seek(0, os.SEEK_END)
            _write_full(fh, payload)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return {"written": True, "duplicate": False}


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def write(plan_dir, session_id, *, resolution, result, verified, attempt=1,
          rework_count=0, gates_failed=None, stuck_armed=False, duration_s=None):
    """Append one outcome record. NEVER RAISES — same posture as
    `hooks/dispatch-audit.py`: a broken ledger must never block or crash the
    dispatch loop it only observes. Returns a dict describing what happened
    (for tests); callers in the loop itself should ignore the return value."""
    if os.environ.get(KILL_SWITCH_ENV):
        return {"written": False, "reason": "kill_switch"}
    if result not in RESULTS:
        # A caller bug (unknown result string) is still swallowed — an
        # observer must not be able to take the loop down — but said loudly.
        print(f"outcomes: refusing unknown result {result!r} for {session_id}",
              file=sys.stderr)
        return {"written": False, "reason": "bad_result"}
    try:
        rec = compose(
            plan_dir, session_id, resolution=resolution, result=result, verified=verified,
            attempt=attempt, rework_count=rework_count, gates_failed=gates_failed,
            stuck_armed=stuck_armed, duration_s=duration_s,
        )
        line_len = len(json.dumps(rec, ensure_ascii=False).encode("utf-8"))
        if line_len > _MAX_RECORD_BYTES:
            # Hygiene, not atomicity (PIPE_BUF governs pipes, not regular
            # files) — a record this large means a field ballooned unexpectedly;
            # trim the one unbounded-ish field rather than drop the record.
            rec["gates_failed"] = rec["gates_failed"][:1]
        out = _append_dedup(ledger_path(), rec)
        out["record_id"] = rec["record_id"]
        return out
    except Exception as e:  # noqa: BLE001 — an observer never fails the caller
        print(f"outcomes: write failed for {session_id} ({resolution}): {e}", file=sys.stderr)
        return {"written": False, "reason": "error", "error": str(e)}
