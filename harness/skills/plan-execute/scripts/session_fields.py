"""Small optional-field helpers for a new `add-session` session dict (RT-01).

Kept out of plan_mutate.py, which is pinned by the file-size ratchet — new
helper code lands here instead of growing that file.
"""
import sys
from pathlib import Path

# The SSOT's `task_classes:` vocabulary (~/.claude/model-routing.yaml).
# Mirrored here (not imported live) because argparse needs a static choices
# tuple at parse time; run.py's own routing-guard already checks these two
# stay in lockstep.
TASK_CLASSES = ("mechanical", "standard_build", "agentic_build", "deep_reasoning", "linchpin")


def apply_optional_fields(session, *, reasoning=None, task_class=None, human_summary=None):
    """Set reasoning / task_class / human_summary on `session` when given.
    Every key stays OMITTED rather than null when absent — same contract as
    the rest of `_new_session`."""
    if reasoning:
        session["reasoning"] = reasoning
    if task_class:
        session["task_class"] = task_class
    if human_summary:
        session["human_summary"] = human_summary


def _class_default(task_class):
    """(model token, effort) the routing resolver gives `task_class` on the active provider."""
    import provider_lane as pl  # noqa: PLC0415 — lazy: only add-session needs the SSOT
    scripts = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import resolve_route  # noqa: PLC0415
    provider = pl.load_routing()[0] or "anthropic"
    try:
        got = resolve_route.resolve(task_class, provider)
        return str(got["model_id"]), str(got.get("native_effort") or "")
    except Exception as e:  # noqa: BLE001 — any gap is a refusal, never a half-set cell
        raise ValueError(f"task_class {task_class!r} does not resolve on provider "
                         f"{provider!r} ({e})") from e


def _token(model):
    low = str(model or "").lower()
    return next((t for t in ("opus", "sonnet", "haiku", "fable") if t in low), low)


def resolve_model_pair(spec, model, reasoning, task_class):
    """The (model, reasoning) add-session writes — always a whole pair or neither,
    never the half-set override that leaves `begin` with no tier agent.
    Raises ValueError with the reason."""
    v = spec.get("plan_schema_version")
    v8 = isinstance(v, int) and not isinstance(v, bool) and v >= 8
    if not model:
        if reasoning:
            raise ValueError("--reasoning without --model: model and reasoning are an "
                             "override pair — pass both, or neither")
        if not task_class:
            raise ValueError("--task-class is required when no --model is given: the class "
                             "default is what the session will run on")
        if v8:
            return None, None  # the class default resolves at dispatch
        m, e = _class_default(task_class)  # below v8 validation requires a model
        return m, e or None
    if reasoning:
        return model, reasoning
    if not task_class:
        raise ValueError(f"--model {model!r} without --reasoning: model and reasoning are an "
                         "override pair. Pass --reasoning, or --task-class so the class "
                         "default effort can fill it")
    m, e = _class_default(task_class)
    if _token(m) != _token(model):
        raise ValueError(f"--model {model!r} without --reasoning: model and reasoning are an "
                         f"override pair, and the {task_class} default is {m}@{e or 'unset'}, "
                         "not that model, so no effort can be assumed. Pass --reasoning")
    return model, e or None


def warn_missing_task_class(task_class):
    """Name the consequence of an unset task_class at the point the session
    is created, rather than let it surface later as an unexplained gap in a
    routing proposal. task_class is OPTIONAL — this is a warning, not a refusal."""
    if not task_class:
        print(
            "add-session: no --task-class; this session's outcomes will be "
            "excluded from routing proposals (task_class unknown)",
            file=sys.stderr,
        )
