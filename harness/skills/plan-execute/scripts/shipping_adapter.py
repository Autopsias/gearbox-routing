"""Shipping adapter — the SINGLE home for per-skill / per-target knowledge.

`run.py`/`shipping.py` never hard-code a skill's flags. They ask this module:
"how do I invoke the `commit` sub-step?" and get back an invocation descriptor
plus a success predicate, a failure classifier, and a timeout. When a skill
changes its interface (e.g. `/commit-orchestrate` renames `--push-after`, or
`/pr create` changes its success contract) this is the ONE place to edit — plus
the contract test. (Resolves the consensus "undefined skill-invocation
contract" finding.)

Two execution kinds:

  * ``skill`` — invoked by the ORCHESTRATOR (Claude) via the Skill tool, because
    a plain Python process cannot call the Skill tool. ``shipping.py`` emits a
    directive; the orchestrator runs it and records the outcome.
  * ``argv`` — a real executable run by ``shipping.py`` itself via
    ``run_state_io._run_deploy_argv`` (``shell=False``, explicit cwd, allow-listed
    env). The ``deploy_argv`` session field and argv-kind registry targets/gates
    use this.

Security: ``redact()`` strips credentials from any text before it is written to
``run.ndjson`` or ``_shipping_state`` (Codex HIGH — stderr credential
disclosure).

Capability probe: ``probe_skill_flags`` asserts every flag the adapter references
still exists in the target skill's current interface. It runs at BUILD time
(`/plan-builder`) AND at EXECUTION time (under the lock, Step 3) so a plan
authored week-1 / executed week-20 against an evolved skill fails loud rather
than silently invoking a renamed flag mid-deploy (premortem class
``vendor_change``).
"""

import re
from pathlib import Path

# --------------------------------------------------------------------------
# git enum -> ordered logical sub-steps
# --------------------------------------------------------------------------
# Each git enum value expands to an ordered list of sub-step names. Sub-steps
# are fine-grained for resumable idempotency: a push failure leaves
# ``commit: done, push: failed`` and resume retries push only. ``push`` re-runs
# `/commit-orchestrate --push-after`; on a clean tree commit-orchestrate is a
# no-op for the commit and only pushes, so no duplicate commit fires.
GIT_STEPS = {
    "none": [],
    "commit": ["commit"],
    "commit-push": ["commit", "push"],
    "commit-push-pr": ["commit", "push", "pr"],
}

VALID_GIT = set(GIT_STEPS)
VALID_FAILURE_MODES = {"fail-halt", "best-effort"}

# --------------------------------------------------------------------------
# Sub-step adapter entries (skill-kind). argv-kind steps (deploy / gates) are
# resolved from the project-local registries, not here.
# --------------------------------------------------------------------------
_GIT_ADAPTER = {
    "commit": {
        "kind": "skill",
        "skill": "commit-orchestrate",
        "args": "",
        "probe_flags": [],
        "timeout": 600,
    },
    "push": {
        "kind": "skill",
        "skill": "commit-orchestrate",
        "args": "--push-after",
        "probe_flags": ["--push-after"],
        "timeout": 600,
    },
    "pr": {
        "kind": "skill",
        "skill": "pr",
        "args": "create",
        "probe_flags": ["create"],
        "timeout": 900,
    },
}


def git_substeps(git_value):
    """Ordered logical sub-step names for a ``git`` enum value."""
    if git_value not in GIT_STEPS:
        raise AdapterError(f"unknown git value {git_value!r}; expected one of {sorted(VALID_GIT)}")
    return list(GIT_STEPS[git_value])


def git_adapter(step):
    """Adapter descriptor for a git sub-step name (``commit``/``push``/``pr``)."""
    if step not in _GIT_ADAPTER:
        raise AdapterError(f"no adapter entry for git sub-step {step!r}")
    return dict(_GIT_ADAPTER[step])


class AdapterError(Exception):
    pass


# --------------------------------------------------------------------------
# Secret redaction (Codex HIGH)
# --------------------------------------------------------------------------
_REDACT_PATTERNS = [
    # GitHub tokens (classic + fine-grained)
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "ghX_***REDACTED***"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "github_pat_***REDACTED***"),
    # AWS access key ids + session tokens
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA)[A-Z0-9]{12,}"), "AWS_KEY_***REDACTED***"),
    (re.compile(r"(?i)\baws_secret_access_key\s*[=:]\s*\S+"), "aws_secret_access_key=***REDACTED***"),
    # Bearer / Authorization headers
    (re.compile(r"(?i)\b(bearer|authorization:)\s+[A-Za-z0-9._\-+/=]+"), r"\1 ***REDACTED***"),
    # Generic token-ish key=value pairs
    (
        re.compile(r"(?i)\b(token|api[_-]?key|access[_-]?token|secret|password|passwd|pwd)"
                   r"(\s*[=:]\s*|\s+)\S+"),
        r"\1=***REDACTED***",
    ),
    # Signed-URL query params
    (
        re.compile(r"(?i)([?&](?:x-amz-signature|signature|sig|x-amz-security-token)=)[^&\s]+"),
        r"\1***REDACTED***",
    ),
    # Credential-bearing URLs: scheme://user:pass@host
    (re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1***REDACTED***@"),
]

REDACTED_MARKER = "***REDACTED***"


def redact(text, *, max_len=800):
    """Strip credentials from ``text`` before it is logged or persisted.

    Order matters: credential-URL and signed-param rules run last so they are
    not pre-mangled by the generic key=value rule. Truncates to ``max_len`` from
    the END (the tail of stderr is usually the useful part).
    """
    if not text:
        return ""
    s = str(text)
    for pat, repl in _REDACT_PATTERNS:
        s = pat.sub(repl, s)
    if len(s) > max_len:
        s = "…" + s[-(max_len - 1):]
    return s


# --------------------------------------------------------------------------
# Capability probe (build-time + execution-time)
# --------------------------------------------------------------------------
def _skill_search_roots():
    home = Path.home()
    return [
        home / ".claude" / "commands",
        home / ".claude" / "skills",
    ]


def locate_skill_file(skill_name, extra_roots=None):
    """Best-effort path to a skill/command definition. ``None`` if not found.

    Searches ``extra_roots`` FIRST (a project's own ``.claude/commands`` +
    ``.claude/skills`` — so project-local commands like ``/eval`` resolve), then
    ``~/.claude/commands/<name>.md`` / ``~/.claude/skills/<name>/SKILL.md``, then
    any plugin command/skill file matching the name.
    """
    name = skill_name.lstrip("/")
    roots = [Path(r) for r in (extra_roots or [])] + _skill_search_roots()
    for root in roots:
        cmd = root / f"{name}.md"
        if cmd.is_file():
            return cmd
        skill = root / name / "SKILL.md"
        if skill.is_file():
            return skill
    # Plugin fallback (commands or skills bundled in marketplaces/cache).
    plugins = Path.home() / ".claude" / "plugins"
    if plugins.is_dir():
        for cand in plugins.rglob(f"{name}.md"):
            return cand
        for cand in plugins.rglob(f"{name}/SKILL.md"):
            return cand
    return None


def probe_skill_flags(skill_name, flags, extra_roots=None):
    """Assert every flag in ``flags`` appears in the skill's definition text.

    Returns ``{"ok": bool, "skill": ..., "missing": [...], "located": path|None}``.
    A missing skill file or any absent flag is a contract drift. (At build time
    -> hard error; at runtime -> ``adapter-contract-drift`` halt.)
    """
    path = locate_skill_file(skill_name, extra_roots)
    if path is None:
        return {"ok": False, "skill": skill_name, "missing": list(flags),
                "located": None, "error": "skill-not-found"}
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        return {"ok": False, "skill": skill_name, "missing": list(flags),
                "located": str(path), "error": f"read-error: {e}"}
    missing = [f for f in flags if f not in text]
    return {"ok": not missing, "skill": skill_name, "missing": missing, "located": str(path)}


def probe_git_value(git_value, extra_roots=None):
    """Probe every skill flag referenced by a ``git`` enum value's sub-steps."""
    results = []
    for step in git_substeps(git_value):
        entry = git_adapter(step)
        if entry["kind"] == "skill" and entry["probe_flags"]:
            results.append((step, probe_skill_flags(entry["skill"], entry["probe_flags"], extra_roots)))
    return results


def probe_registry_entry(entry_id, entry, extra_roots=None):
    """Probe the ``probe_flags`` of a skill-kind registry entry (deploy target or gate).

    Returns ``{"ok": bool, "entry": entry_id, "skill": ..., "missing": [...], ...}``.
    argv-kind entries have no skill file to probe — returns ``{"ok": True, ...}`` immediately.
    An entry whose ``probe_flags`` is absent or empty is always ``ok`` (nothing to assert).

    EXPLICIT METADATA REQUIRED: every skill-kind entry MUST carry explicit ``skill``
    and ``probe_flags`` fields — this function never infers them from the entry name.
    A missing ``skill`` field on a skill-kind entry is itself a contract error (ok=False).
    """
    if entry.get("kind") != "skill":
        return {"ok": True, "entry": entry_id, "reason": "argv-kind-no-probe"}
    skill_name = entry.get("skill")
    if not skill_name:
        return {"ok": False, "entry": entry_id, "skill": None, "missing": [],
                "error": "skill-kind entry is missing required 'skill' field"}
    flags = entry.get("probe_flags") or []
    if not flags:
        return {"ok": True, "entry": entry_id, "skill": skill_name, "missing": [],
                "reason": "no probe_flags declared — nothing to assert"}
    result = probe_skill_flags(skill_name, flags, extra_roots)
    result["entry"] = entry_id
    return result
