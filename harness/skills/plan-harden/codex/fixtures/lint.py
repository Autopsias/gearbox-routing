#!/usr/bin/env python3
"""Model-selection sanity lint (plan-harden.md §4.0 / model-lint.md's
`task-class-model-mismatch` rule), reused verbatim for the Codex-only port's
fixture smoke test. Not part of the rendered skill (see manifest.toml) — this
is the fixture's own stand-in for what an interactive Codex orchestrator would
compute inline, using the SAME resolver the Claude version uses
(`scripts/resolve_route.py::resolve`), never a hand-reparse of the SSOT.

Usage: lint.py <plan-dir> <ssot-path> <resolve-route-py-dir>
Prints one JSON object: {"source": "ssot", "flags": [...]}
"""
import json
import re
import sys
from pathlib import Path


TEST_PATH = re.compile(r"(^|/)(test_[^/\s]*\.py|[^/\s]*_test\.py|tests?/)")
# parents[3] of this FILE is <repo>/skills (fixtures -> codex -> plan-harden -> skills)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plan-execute" / "scripts"))


def _flag(sid, issue, rec, severity):
    return {"session": sid, "issue": issue, "recommendation": rec, "severity": severity}


def _provider(model):
    """The ladder of the override's OWN provider (model-lint.md): a Codex-family model
    (run._declared_codex) is openai; a glm* model, or any model under the GLM tree
    (effective_provider == "zai"), is zai; every other (Claude-family) model is
    anthropic - never openai, whatever the machine's active provider is."""
    import provider_lane as pl  # noqa: E402
    import run  # noqa: E402
    if run._declared_codex(model):
        return pl.lane_profile("codex", None)
    zai = re.search(r"glm", model, re.I) or pl.effective_provider(None) == "zai"
    substrate = "zai" if zai else "anthropic"
    return pl.lane_profile("claude", substrate)


def _tree_provider(ssot_path):
    """The provider this plan dispatches on by default, resolved the way run.py does
    (provider_lane.load_routing): PLAN_EXECUTE_ROUTING_PROVIDER, else the GLM tree,
    else the SSOT's active_provider dial; no dial means anthropic, run.py's default."""
    import provider_lane as pl  # noqa: E402
    m = re.search(r"^active_provider:\s*([\w-]+)", Path(ssot_path).read_text(), re.M)
    return pl.effective_provider(m.group(1) if m else None) or "anthropic"


def _below_floor(rr, tc, model, effort, ssot_path, tree):
    """Contract section 4: a cross-provider override (its own provider differs from the
    tree's) skips; otherwise resolve_route.below_floor decides - the override ranks lower
    than the class default on that provider's ladder (a model not on it skips)."""
    provider = _provider(model)
    if provider != tree:
        return False
    return rr.below_floor(tc, provider, {"model_id": model, "native_effort": effort}, ssot_path)


def v8_flags(manifest, rr, ssot_path, plan_dir, classes):
    """Schema v8 rules (model-lint.md, "Schema v8 - route-at-dispatch gate")."""
    spec_path = plan_dir / "spec.json"
    items = {}
    if spec_path.exists():
        spec = json.loads(spec_path.read_text())
        items = {i["id"]: i for i in spec.get("items", []) if isinstance(i, dict) and "id" in i}
    by_id = {s["id"]: s for s in manifest["sessions"]}
    tree = _tree_provider(ssot_path)

    def writes_test(s):
        texts = [str(s.get("deliverable") or "")]
        for i in s.get("items", []):
            t = items.get(i, {}).get("touches") or []
            texts += [str(p) for p in ([t] if isinstance(t, str) else t)]
        return any(TEST_PATH.search(x) for x in texts)

    out = []
    for s in manifest["sessions"]:
        sid, tc = s["id"], s.get("task_class")
        model, effort = s.get("model") or "", s.get("reasoning") or ""
        if not tc or tc not in classes:
            out.append(_flag(sid, "task-class-missing",
                             f"session {sid} has task_class={tc!r}; v8 needs one of {sorted(classes)}", "red"))
        override = bool(model or effort)
        if bool(model) != bool(effort):
            out.append(_flag(sid, "override-incomplete",
                             f"session {sid} model={model!r} reasoning={effort!r}: v8 needs both or neither", "red"))
        if override != bool(str(s.get("why_model") or "").strip()):  # whitespace is no reason
            out.append(_flag(sid, "override-without-reason",
                             f"session {sid} model={model!r} reasoning={effort!r} why_model={s.get('why_model')!r}: "
                             "an override needs a reason and a reason needs an override", "red"))
        risky = s.get("peer_triggers") or tc == "linchpin"
        if model and effort and risky and tc in classes and _below_floor(rr, tc, model, effort, ssot_path, tree):
            out.append(_flag(sid, "override-below-floor",
                             f"session {sid} ({tc}) overrides to {model}/{effort}, below the class default", "red"))
        deps = (s.get("dispatch") or {}).get("depends_on") or []
        if not (s.get("verify") or {}).get("locked") and any(
                d in by_id and writes_test(by_id[d]) for d in deps):
            out.append(_flag(sid, "locked-check-suggested",
                             f"session {sid} depends on a session that writes a test file; consider verify.locked",
                             "yellow"))
    return out


def main():
    plan_dir, ssot_path, resolver_dir = sys.argv[1:4]
    sys.path.insert(0, resolver_dir)
    import resolve_route  # noqa: E402

    manifest = json.loads((Path(plan_dir) / "manifest.json").read_text())
    v8 = int(manifest.get("plan_schema_version") or 0) >= 8
    classes = set(resolve_route._parse_task_classes(Path(ssot_path).read_text()))
    flags = []
    if v8:
        flags += v8_flags(manifest, resolve_route, ssot_path, Path(plan_dir), classes)
    for s in manifest["sessions"]:
        tc = s.get("task_class")
        if not tc:
            continue  # missing-field semantics (model-lint.md): no task_class, no flag
        if tc not in classes:  # resolve() would raise; v8 already flagged it above
            if not v8:
                flags.append(_flag(s["id"], "task-class-unknown",
                                   f"session {s['id']} has task_class={tc!r}; expected one of {sorted(classes)}", "yellow"))
            continue
        if v8 and not (s.get("model") or s.get("reasoning")):
            continue  # v8: no override, no declared model to grade
        # Effective provider: this fixture never runs under executor_policy's
        # opt-in dial, so it always resolves openai directly here — the
        # point of the fixture is Codex-only review, so there is no
        # anthropic fallback branch to exercise.
        resolved = resolve_route.resolve(tc, "openai", ssot_path=ssot_path)
        declared_model = s.get("model")
        if resolved.get("model_id") != declared_model:
            flags.append({
                "session": s["id"],
                "issue": "task-class-model-mismatch",
                "declared": declared_model,
                "resolved": resolved,
                "task_class": tc,
                "recommendation": (
                    f"session {s['id']} declares task_class={tc} + model={declared_model}, "
                    f"which resolves to {resolved['model_id']}/{resolved['native_effort']} "
                    "under active_provider=openai"
                ),
                "severity": "yellow",
            })
    print(json.dumps({"source": "ssot", "flags": flags}, indent=2))


if __name__ == "__main__":
    main()
