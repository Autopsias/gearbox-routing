"""Plan schema v8 — the builder half of the route-at-dispatch contract.

Authority: ``../../plan-execute/references/route-at-dispatch-contract.md`` (v1).
From ``plan_schema_version`` 8 the author names the KIND of work (``task_class``)
and ``/plan-execute begin`` picks the model and effort. A model stays possible
only as an override pair (``model`` + ``reasoning``) with a stated ``why_model``.

Kept out of build_plan.py, which is pinned by the file-size ratchet; build_plan
imports this module and calls it at each rule's one enforcement point.

Every rule here is gated on the spec's OWN ``plan_schema_version``, the same
opt-in shape as ``PRIOR_ART_MIN_SCHEMA``: a spec below 8 validates, renders and
stamps exactly as it did before this module existed.
"""
import html
import posixpath
import re
import stat
import sys
from pathlib import Path

ROUTE_AT_DISPATCH_MIN_SCHEMA = 8
"""The spec version at which every rule in this module switches on (contract §1)."""

SUPPORTED_MAX_SCHEMA = 8
"""The highest spec version this builder understands. A spec above it is refused,
never downgraded: stamping a lower version than the author declared would run the
plan under rules the author did not write for (contract §1)."""

PRE_V8_STAMP = 7
"""The fresh manifest stamp for every spec below v8 — the stamp such a spec got
before v8 existed, so its manifest stays byte-identical (contract §1)."""

DISPATCH_LABEL = "model: chosen at dispatch"
"""What the dashboard shows where a v8 session authored no model."""

_PAIR = ("model", "reasoning")
_GLOB_CHARS = set("*?[]")
_REPO_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_PE_SCRIPTS = Path(__file__).resolve().parents[2] / "plan-execute" / "scripts"

try:  # the routing vocabulary, one copy: plan-execute's session_fields.py
    if str(_PE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_PE_SCRIPTS))
    from session_fields import TASK_CLASSES
except ImportError:  # pragma: no cover - plan-execute missing: every v8 session is refused
    TASK_CLASSES = ()


def spec_version(spec):
    """The spec's declared version as an int; 0 when absent (pre-bump)."""
    v = spec.get("plan_schema_version")
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def is_v8(spec):
    return spec_version(spec) >= ROUTE_AT_DISPATCH_MIN_SCHEMA


def check_version(spec):
    """Refuse a malformed version, and any version above SUPPORTED_MAX_SCHEMA."""
    v = spec.get("plan_schema_version")
    if v is None:
        return
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValueError(f"plan_schema_version must be an integer, got {v!r}")
    if v > SUPPORTED_MAX_SCHEMA:
        raise ValueError(
            f"spec declares plan_schema_version {v}, but this plan-builder supports at most "
            f"{SUPPORTED_MAX_SCHEMA}. Refusing rather than stamping a lower version: update "
            "plan-builder, or lower the spec's plan_schema_version.")


def fresh_stamp(spec):
    """The stamp a build writes when it keeps no prior stamp: 7 below v8, else the
    declared version (never lower than declared; above the max is refused first)."""
    v = spec_version(spec)
    return v if v >= ROUTE_AT_DISPATCH_MIN_SCHEMA else PRE_V8_STAMP


def manifest_stamp(spec, prior_v, preserve_state):
    """The stamp build() writes. --preserve-state keeps the prior manifest's stamp
    exactly as before v8 (when it is an int no higher than this builder writes),
    but refuses a rebuild across the v8 boundary in either direction: validation
    would follow the spec's version while execution follows the manifest's."""
    fresh = fresh_stamp(spec)
    if not (preserve_state and isinstance(prior_v, int)):
        return fresh
    if (prior_v >= ROUTE_AT_DISPATCH_MIN_SCHEMA) != is_v8(spec):
        raise ValueError(
            f"--preserve-state rebuild crosses the v8 boundary: the spec declares "
            f"plan_schema_version {spec_version(spec) or 'none'} but the published manifest is "
            f"stamped {prior_v}. Validation would follow one version and execution the other. "
            "Rebuild fresh (without --preserve-state), or align the spec's version.")
    if prior_v > SUPPORTED_MAX_SCHEMA:
        raise ValueError(
            f"the published manifest is stamped plan_schema_version {prior_v}, but this "
            f"plan-builder supports at most {SUPPORTED_MAX_SCHEMA}. Refusing rather than "
            "stamping a lower version: update plan-builder, or rebuild fresh "
            "(without --preserve-state).")
    return prior_v


def required_session_fields(spec):
    """At v8 `model` is an optional override, no longer a required field."""
    return ("id", "title", "items") if is_v8(spec) else ("id", "title", "model", "items")


def _set(s, key):
    return bool(str(s.get(key) or "").strip())


def _normalise_override(s, sid):
    """An explicit JSON null in an override field means "not set": the key is DROPPED,
    so every later reader (the pair rule, the card, the manifest) sees it absent —
    `.get(k, default)` defaults only a missing key, never a null one. Any other
    non-string value is refused here instead of crashing the renderer."""
    for k in (*_PAIR, "why_model"):
        if k in s and s[k] is None:
            del s[k]
        elif k in s and not isinstance(s[k], str):
            raise ValueError(f"Session {sid}.{k} must be a string (or omitted), got {s[k]!r}")


def validate_session(s, spec):
    """The v8 session rules (contract §2 and §4). No-op below v8. Normalises the
    override fields in place (see `_normalise_override`)."""
    if not is_v8(spec):
        return
    sid = s["id"]
    _normalise_override(s, sid)
    if isinstance(s.get("dispatch"), dict) and s["dispatch"].get("subagent_type") == "fork":
        raise ValueError(
            f"Session {sid}.dispatch.subagent_type is 'fork': a fork always runs the "
            "orchestrator's model and context, so it cannot honour a resolved or authored model "
            "and route at dispatch cannot apply to it. At plan_schema_version 8 use a fresh "
            "agent (omit subagent_type, or name a typed agent)")
    tc = str(s.get("task_class") or "").strip().lower()
    if tc not in TASK_CLASSES:
        raise ValueError(
            f"Session {sid}.task_class is required at plan_schema_version 8 and must be one of "
            f"{list(TASK_CLASSES) or '(none: plan-execute/scripts/session_fields.py is missing)'}"
            f", got {s.get('task_class')!r}")
    have = [k for k in _PAIR if _set(s, k)]
    if len(have) == 1:
        missing = next(k for k in _PAIR if k not in have)
        raise ValueError(
            f"Session {sid} sets {have[0]} without {missing}: at plan_schema_version 8 model and "
            "reasoning are an override pair — set both (with why_model) or neither")
    if not have:
        if _set(s, "why_model"):
            raise ValueError(
                f"Session {sid} has why_model but no model/reasoning override: at "
                "plan_schema_version 8 why_model is the reason for an override. Drop it, or "
                "add the override pair it explains")
        return
    if not _set(s, "why_model"):
        raise ValueError(
            f"Session {sid} overrides the class default with {s['model']}@{s['reasoning']} but "
            "gives no why_model. At plan_schema_version 8 an override needs a stated reason")
    if s.get("peer_triggers") or tc == "linchpin":
        _refuse_below_floor(s, sid, tc)


def _resolver():
    if str(_REPO_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_REPO_SCRIPTS))
    import resolve_route  # noqa: PLC0415 — lazy: only a risky override needs the SSOT

    return resolve_route


def _override_provider(rr, text, model):
    """(provider, model_id) of the override's OWN provider, read from the routing
    file's `models:` maps — or (None, None) when no provider names the model."""
    low = model.strip().lower()
    for provider in ("anthropic", "openai"):
        for mid in rr._Profile(text, provider).models.values():
            if mid and mid in low:
                return provider, mid
    return None, None


def _tree_provider(text):
    """The provider the plan dispatches on, read as plan-harden's lint reads it
    (provider_lane.effective_provider, then the SSOT dial, then anthropic)."""
    if str(_PE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_PE_SCRIPTS))
    import provider_lane as pl  # noqa: PLC0415 — lazy, like _resolver

    m = re.search(r"^active_provider:\s*([\w-]+)", text, re.M)
    return pl.effective_provider(m.group(1) if m else None) or "anthropic"


def _refuse_below_floor(s, sid, tc):
    """Contract §4 floor: an override is below the floor when it ranks lower than the
    class default on its own provider's ladder (resolve_route.below_floor, shared with
    plan-harden's lint). No provider, or a cross-provider override, skips the check."""
    rr = _resolver()
    path = rr._default_ssot_path()
    text = Path(path).read_text(encoding="utf-8")
    provider, mid = _override_provider(rr, text, str(s["model"]))
    if provider is None or provider != _tree_provider(text):
        return  # no ladder, or cross-provider: contract section 4 skips the floor check
    if rr.below_floor(tc, provider, {"model_id": mid, "native_effort": s["reasoning"]}, path):
        default = rr.resolve(tc, provider, ssot_path=path)
        raise ValueError(
            f"Session {sid} overrides {tc} with {s['model']}@{s['reasoning']}, which is BELOW "
            f"the class default {default['model_id']}@{default['native_effort']} on "
            f"{provider}'s escalation ladder. A below-floor override is refused on a session "
            "with peer_triggers or task_class linchpin. Drop the override, or raise it")


def _check_locked_shape(locked, where):
    if not isinstance(locked, list) or not locked:
        raise ValueError(f"{where}.locked must be a non-empty list of repo-relative file paths")
    for p in locked:
        bad = (not isinstance(p, str) or not p.strip() or p.startswith("/") or "\\" in p
               or ".." in p.split("/") or _GLOB_CHARS & set(p) or posixpath.normpath(p) == ".")
        if bad:
            raise ValueError(
                f"{where}.locked entry {p!r} must be a repo-relative file path: no leading '/', "
                "no '..', no glob characters")


def _guards(vb):
    """A verify block that carries something to verify (locked alone does not)."""
    return bool(vb.get("gates") or vb.get("require_evidence") or vb.get("checks"))


def validate_plan(spec):
    """Plan-wide v8 rules: `touches` on every item, `verify.locked` shape. Runs
    after the per-item and per-session checks, so every id exists by now."""
    v8 = is_v8(spec)
    phase_verify = {p.get("id"): p.get("verify") for p in spec.get("phases") or []}
    for p in spec.get("phases") or []:
        if "locked" in (p.get("verify") or {}):
            raise ValueError(f"Phase {p['id']}.verify.locked: declare locked checks per session")
    for s in spec["sessions"]:
        vb = s.get("verify") or {}
        if "locked" in vb:
            if not v8:
                raise ValueError(
                    f"Session {s['id']}.verify.locked needs plan_schema_version 8 — below it "
                    "nothing enforces the lock, so the check would look locked and not be")
            _check_locked_shape(vb["locked"], f"Session {s['id']}.verify")
            if not _guards(vb) and not _guards(phase_verify.get(s.get("phase")) or {}):
                raise ValueError(
                    f"Session {s['id']}.verify.locked has no gates, require_evidence or checks "
                    "to guard, on the session or its phase. With nothing to verify there is no "
                    "verify step, so the lock would silently vanish. Add a gate")
    if not v8:
        return
    for it in spec["items"]:
        t = it.get("touches")
        if not ((isinstance(t, str) and t.strip()) or t == []):
            raise ValueError(
                f"Item {it['id']} must declare touches at plan_schema_version 8 — the files it "
                "writes, comma-separated, or [] for an item that writes nothing")


def _repo_root(project_root, out_path):
    if project_root:
        return Path(project_root)
    for p in Path(out_path).resolve().parents:
        if (p / ".git").exists():
            return p
    return None


def check_locked_files(spec, project_root, out_path):
    """Build-time half of contract §5: a locked path that exists but is not a
    regular file (a directory, FIFO, device...) or is a symlink (or sits under a
    symlinked folder) is refused. A path that does not exist yet is allowed here;
    `begin` refuses it at dispatch."""
    root = _repo_root(project_root, out_path)
    if root is None:
        return
    for s in spec["sessions"]:
        for rel in (s.get("verify") or {}).get("locked") or []:
            parts = Path(rel).parts
            links = [q for q in (root.joinpath(*parts[:i]) for i in range(1, len(parts) + 1))
                     if q.is_symlink()]
            try:
                irregular = not stat.S_ISREG(root.joinpath(rel).lstat().st_mode)
            except (FileNotFoundError, NotADirectoryError):  # not there yet: begin refuses it
                irregular = False
            if links or irregular:
                raise ValueError(
                    f"Session {s['id']}.verify.locked {rel!r} under {root} is a symlink or not a "
                    "regular file. Only regular files can be locked (they are hashed at dispatch)")


def integration_session(integ, spec):
    """The auto-emitted integration session: at v8 it names its task class and
    leaves the model to the executor, like any other v8 session."""
    if is_v8(spec):
        for k in (*_PAIR, "why_model"):
            integ.pop(k, None)
    return integ


def manifest_override_fields(s, spec):
    """Extra per-session manifest keys at v8: no default model (the executor
    resolves it), and the override's reason when there is one. {} below v8."""
    if not is_v8(spec):
        return {}
    extra = {"model": s.get("model") or ""}
    if _set(s, "why_model"):
        extra["why_model"] = s["why_model"]
    return extra


def manifest_touches(it, spec):
    """An item's `touches` manifest key. Below v8, only a non-empty declaration, as
    before, so a pre-v8 manifest stays byte-identical. At v8 `[]` ("writes nothing")
    is a declaration too, so a present key is always carried."""
    t = it.get("touches")
    keep = ("touches" in it) if is_v8(spec) else bool(str(t or "").strip())
    return {"touches": t} if keep else {}


def model_chips(session, v8, model_chip):
    """Header chips for a session card. Below v8: the model chip, unchanged. At v8:
    a task-class chip, then the override's model chip or the dispatch label."""
    if not v8:
        return model_chip
    tc = html.escape(str(session.get("task_class") or ""), quote=True)
    cls = f'<span class="chip chip-task-class" title="Task class">{tc}</span> '
    if _set(session, "model"):
        return cls + model_chip
    return cls + f'<span class="chip model-dispatch">{DISPATCH_LABEL}</span>'
