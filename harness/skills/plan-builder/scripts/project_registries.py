#!/usr/bin/env python3
"""Project registry resolution for build-time validation: which project's
.claude/{deploy-targets,eval-gates}.json a build reads, merged over the
skill-bundled defaults.

Split out of build_plan.py when registry_root() pushed it past its size
baseline. These functions read files and return dicts; they call nothing
else in the builder.
"""
import json
import sys
from pathlib import Path

_PE_SCRIPTS = Path(__file__).resolve().parents[2] / "plan-execute" / "scripts"

# Single-sourced project root: /plan-execute resolves a plan's
# registries from the plan directory (shipping.find_project_root ->
# plan_scope.project_root), so a build without --register-in resolves them the
# same way instead of reading no project registry at all.
try:
    if str(_PE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_PE_SCRIPTS))
    import plan_scope as _pscope
except ImportError:
    _pscope = None


def registry_root(project_root, out_path):
    """The project whose .claude/ registries a build validates against:
    --register-in when given, else derived from the plan directory exactly as
    /plan-execute derives it at run time. Without the fallback, a --rebuild
    without --register-in never read .claude/eval-gates.json and refused every
    project-only gate as "not in the project's .claude/eval-gates.json"."""
    if project_root or _pscope is None:
        return project_root
    plan_dir = Path(out_path)
    if plan_dir.suffix == ".html":  # legacy `<dir>/PLAN.html` output form
        plan_dir = plan_dir.parent
    return str(_pscope.project_root(plan_dir))


def load_project_registries(project_root):
    """Load .claude/{deploy-targets,eval-gates}.json from the project (if any)."""
    reg = {"deploy": {}, "gates": {}}
    if not project_root:
        return reg
    base = Path(project_root) / ".claude"
    for key, fname in (("deploy", "deploy-targets.json"), ("gates", "eval-gates.json")):
        p = base / fname
        if p.is_file():
            try:
                reg[key] = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError) as e:
                raise ValueError(f"{p} is not valid JSON: {e}") from e
    return reg


def _bundled_gate_ids():
    p = _PE_SCRIPTS.parent / "references" / "eval-gates.default.json"
    if p.is_file():
        try:
            return set(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def _bundled_registries():
    """Load the skill-bundled default registries (deploy-targets + eval-gates).

    Mirrors the merge order in plan-execute/scripts/shipping.py: bundled defaults
    are the floor; project-local files win when present (but here we only load the
    bundled floor for probe-flag access during build-time validation).
    """
    result = {"deploy": {}, "gates": {}}
    base = _PE_SCRIPTS.parent / "references"
    for key, fname in (("deploy", "deploy-targets.default.json"),
                       ("gates", "eval-gates.default.json")):
        p = base / fname
        if p.is_file():
            try:
                result[key] = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                pass
    return result


def _merged_registries(project_root):
    """Project-local registries merged OVER the bundled defaults (project wins)."""
    merged = _bundled_registries()
    local = load_project_registries(project_root)
    merged["deploy"].update(local["deploy"])
    merged["gates"].update(local["gates"])
    return merged
