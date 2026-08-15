#!/usr/bin/env python3
"""S07 / PL-03 — the four contract-first shaping fixtures, and their real output.

Two consumers, one definition, so the evidence can never describe behaviour the
tests do not assert:

  * `test_schema_hardening.py` imports `CASES` and asserts each `expect`.
  * `main()` re-runs the same cases and prints the VERBATIM validator/builder
    output as markdown — that is how `_evidence/s07/builder-shaping.md` is
    produced, rather than by hand-copying a transcript.

Two plants and two allows, because a plant-only suite cannot tell a firing gate
from one that fires on everything:

  1. PLANT  chained-disjoint  — a file-disjoint chain WARNS, naming the pairs.
  2. ALLOW  chained-justified — the same spec with `serial_reason` is quiet.
  3. ALLOW  worktree-group    — an isolated group EMITS its integration session.
  4. PLANT  member-ships      — a member declaring commit/push is REFUSED (M1).

Run: python3 parallel_shaping_fixtures.py [out.md]
"""

import contextlib
import copy
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_plan  # noqa: E402

# ---------------------------------------------------------------- the specs


def _base(**over):
    """A minimal v4 spec: 3 sessions, each writing its own file."""
    spec = {
        "title": "Shaping Fixture Plan",
        "plan_schema_version": 4,
        "categories": [{"key": "work", "label": "Work"}],
        "items": [
            {"id": "w-01", "title": "W1", "category": "work", "touches": "src/alpha.py",
             "research_status": "skipped", "research_reason": "synthetic fixture"},
            {"id": "w-02", "title": "W2", "category": "work", "touches": "src/beta.py",
             "research_status": "skipped", "research_reason": "synthetic fixture"},
            {"id": "w-03", "title": "W3", "category": "work", "touches": "docs/readme.md",
             "research_status": "skipped", "research_reason": "synthetic fixture"},
        ],
        "sessions": [
            {"id": "s01", "title": "First", "model": "Sonnet", "items": ["w-01"],
             "prompt": "do", "dispatch": {"depends_on": []}},
            {"id": "s02", "title": "Second", "model": "Sonnet", "items": ["w-02"],
             "prompt": "do", "dispatch": {"depends_on": ["s01"]}},
            {"id": "s03", "title": "Third", "model": "Sonnet", "items": ["w-03"],
             "prompt": "do", "dispatch": {"depends_on": ["s02"]}},
        ],
        "infographic": {
            "type": "before-after", "title": "t",
            "before": {"name": "Before", "bullets": ["a"]},
            "after": {"name": "After", "bullets": ["b"]},
            "workstreams": [{"name": "Track A", "items": ["w-01", "w-02", "w-03"]}],
        },
    }
    spec.update(over)
    return spec


def chained_disjoint():
    """PLANT — three file-disjoint sessions in a pure chain, no `serial_reason`."""
    return _base()


def chained_justified():
    """ALLOW — the identical chain, with the author's reason declared."""
    return _base(serial_reason=(
        "Each session rewrites the same public interface the next one consumes; "
        "splitting them would freeze a contract nobody has agreed yet."
    ))


_GATES = {"s02": "code-review-gate", "s03": "test-orchestrate"}


def worktree_group():
    """ALLOW — a worktree-isolated group with NO author-written integrator."""
    spec = _base()
    for sid in ("s02", "s03"):
        s = next(x for x in spec["sessions"] if x["id"] == sid)
        s["dispatch"] = {"depends_on": ["s01"], "parallel_group": "g1",
                         "isolation": "worktree"}
        # Two DIFFERENT registry-resolvable gates, so "the integrator re-runs the
        # UNION of its members' gates" (§3 rule 3) is a real assertion.
        s["verify"] = {"gates": [_GATES[sid]], "on_fail": "rework"}
    return spec


def member_ships():
    """PLANT — a member of that same group declares a shipping action (M1)."""
    spec = worktree_group()
    next(x for x in spec["sessions"] if x["id"] == "s02")["post_session"] = {
        "git": "commit-push"
    }
    return spec


# ---------------------------------------------------------------- the runner

class Isolated:
    """Run with `ISOLATION_IMPLEMENTED` forced True.

    Contract M3 makes the executor REFUSE `isolation: "worktree"` while its own
    build cannot honour it, and at S07's merge base that flag is still False
    (S06B flips it when the dispatch mechanism lands). The builder's emission is
    a separate question from the executor's readiness, so the two isolated
    fixtures below pin the flag rather than encoding today's value of it — the
    assertions then read the same before and after S06B lands.
    """

    def __enter__(self):
        self.pcon = build_plan._pcon
        self.prev = self.pcon.ISOLATION_IMPLEMENTED if self.pcon else None
        if self.pcon:
            self.pcon.ISOLATION_IMPLEMENTED = True
        return self

    def __exit__(self, *exc):
        if self.pcon:
            self.pcon.ISOLATION_IMPLEMENTED = self.prev
        return False


def run_case(spec, *, isolated=False, build=False):
    """``{"error": str|None, "stderr": str, "manifest": dict|None, "prompt": str|None}``."""
    spec = copy.deepcopy(spec)
    err, buf, manifest, prompt = None, io.StringIO(), None, None
    ctx = Isolated() if isolated else contextlib.nullcontext()
    with ctx, contextlib.redirect_stderr(buf):
        try:
            if build:
                with tempfile.TemporaryDirectory() as td:
                    out = Path(td) / "plan"
                    build_plan.build(spec, out)
                    manifest = json.loads((out / "manifest.json").read_text())
                    integ = integration_session(manifest)
                    if integ:
                        prompt = (out / integ["prompt_file"]).read_text()
            else:
                build_plan.validate_spec(spec)
        except ValueError as e:
            err = str(e)
    return {"error": err, "stderr": buf.getvalue(), "manifest": manifest, "prompt": prompt}


# name, description, spec factory, isolated?, build?, expectation
CASES = [
    ("chained-disjoint", "PLANT — pure chain of file-disjoint sessions",
     chained_disjoint, False, False,
     {"error": None, "stderr_has": ["PURE CHAIN", "s01+s02", "s02+s03", "serial_reason"]}),
    ("chained-justified", "ALLOW — same chain, `serial_reason` declared",
     chained_justified, False, False,
     {"error": None, "stderr_lacks": ["PURE CHAIN"]}),
    ("worktree-group", "ALLOW — isolated group auto-emits its integration session",
     worktree_group, True, True,
     {"error": None, "integration": {"depends_on": ["s02", "s03"],
                                     "gates": ["code-review-gate", "test-orchestrate"],
                                     "git": "commit"}}),
    ("member-ships", "PLANT — group member declares commit-push",
     member_ships, True, False,
     {"error_has": ["[M1]", "s02", "commit-push"]}),
    ("worktree-group-as-shipped",
     "CONTROL — the same isolated spec against the executor's REAL readiness flag",
     worktree_group, False, False,
     # Contract M3: while `ISOLATION_IMPLEMENTED` is False the executor REFUSES a
     # worktree declaration rather than silently running the group in a shared
     # tree. The builder does not second-guess that — it hands the spec to the
     # same shared checker and reports whatever it says. Expressed against the
     # flag's live value so this case keeps asserting the truth when S06B flips
     # it, instead of freezing today's answer into a test that then breaks.
     {"error_has": ["[M3]", "does not implement"]}
     if not (build_plan._pcon and build_plan._pcon.ISOLATION_IMPLEMENTED)
     else {"error": None}),
]


def integration_session(manifest, group="g1"):
    return next(
        (s for s in manifest["sessions"]
         if (s.get("dispatch") or {}).get("integrates_group") == group),
        None,
    )


def check(name, res, expect):
    """Return a list of failure strings ([] = the case behaved as declared)."""
    bad = []
    if "error" in expect and res["error"] != expect["error"]:
        bad.append(f"expected no refusal, got: {res['error']}")
    for needle in expect.get("error_has", []):
        if needle not in (res["error"] or ""):
            bad.append(f"refusal missing {needle!r}: {res['error']!r}")
    for needle in expect.get("stderr_has", []):
        if needle not in res["stderr"]:
            bad.append(f"stderr missing {needle!r}")
    for needle in expect.get("stderr_lacks", []):
        if needle in res["stderr"]:
            bad.append(f"stderr unexpectedly contains {needle!r}")
    exp_int = expect.get("integration")
    if exp_int is not None:
        integ = integration_session(res["manifest"] or {"sessions": []})
        if integ is None:
            bad.append("no session declares integrates_group='g1' in the manifest")
        else:
            deps = integ["dispatch"]["depends_on"]
            if sorted(deps) != exp_int["depends_on"]:
                bad.append(f"integration depends_on {deps} != {exp_int['depends_on']}")
            gates = sorted((integ.get("verify") or {}).get("gates") or [])
            if gates != exp_int["gates"]:
                bad.append(f"integration gates {gates} != {exp_int['gates']}")
            git = (integ.get("post_session") or {}).get("git")
            if git != exp_int["git"]:
                bad.append(f"integration post_session.git {git!r} != {exp_int['git']!r}")
            for m in ("s02", "s03"):
                ms = next(s for s in res["manifest"]["sessions"] if s["id"] == m)
                if ms["dispatch"].get("isolation") != "worktree":
                    bad.append(f"member {m} lost dispatch.isolation in the manifest")
            for it in res["manifest"]["items"]:
                if not it.get("touches"):
                    bad.append(f"manifest item {it['id']} carries no `touches` (M2a input)")
    return bad


def run_all():
    """``[(name, desc, result, failures), ...]``."""
    out = []
    for name, desc, factory, isolated, build, expect in CASES:
        res = run_case(factory(), isolated=isolated, build=build)
        out.append((name, desc, res, check(name, res, expect)))
    return out


# ---------------------------------------------------------------- evidence

def _fence(text):
    return ["```", *(text.rstrip().splitlines() or ["(empty)"]), "```", ""]


def main():
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = run_all()
    L = [
        "# S07 / PL-03 — contract-first shaping in plan-builder: fixtures and output",
        "",
        "Generated by `skills/plan-builder/scripts/parallel_shaping_fixtures.py`. Every "
        "block below is verbatim output of the code named above it; the same `CASES` are "
        "asserted by `test_schema_hardening.py`, which the session quality gate runs.",
        "",
        f"`build_plan.PLAN_SCHEMA_VERSION` = {build_plan.PLAN_SCHEMA_VERSION}; the frozen "
        "parallel-group contract is enforced at build time on a spec that opts in with "
        f"`plan_schema_version >= {build_plan.PARALLEL_CONTRACT_MIN_SCHEMA}`, and printed as "
        "warnings below that (so `plan_mutate` keeps working on the live plans whose manifests "
        "are grandfathered out of it — measured: 5 of this repo's 10 plans).",
        "",
        "**Read case 5 before case 3.** `parallel_contract.ISOLATION_IMPLEMENTED` is "
        f"`{bool(build_plan._pcon and build_plan._pcon.ISOLATION_IMPLEMENTED)}` in this build: "
        "S06 froze ROUTE A (isolation is real and declarable) but M3 makes the shared checker "
        "REFUSE a worktree declaration until the executor can actually honour it — S06B flips "
        "that flag. Case 3 pins the flag True to prove the BUILDER's emission, which is a "
        "separate question from the executor's readiness; case 5 runs the identical spec "
        "against the flag's live value and shows the refusal the builder correctly passes "
        "through today.",
        "",
    ]
    for i, (name, desc, res, failures) in enumerate(results, 1):
        L += [f"## {i} — {name}: {desc}", ""]
        verdict = "REFUSED" if res["error"] else ("warned" if res["stderr"].strip() else "clean")
        L += [f"**Outcome: {verdict}** — assertions: "
              f"{'PASS' if not failures else 'FAIL — ' + '; '.join(failures)}", ""]
        if res["error"]:
            L += ["`validate_spec()` raised:", ""] + _fence(res["error"])
        if res["stderr"].strip():
            L += ["stderr:", ""] + _fence(res["stderr"])
        if not res["error"] and not res["stderr"].strip():
            L += ["`validate_spec()` returned clean, with nothing on stderr.", ""]
        integ = integration_session(res["manifest"]) if res["manifest"] else None
        if integ:
            L += ["The integration session as emitted into `manifest.json` "
                  "(prompt body elided — it is written to `sessions/<id>.prompt.md`):", ""]
            shown = {k: v for k, v in integ.items() if k != "prompt_file"}
            L += _fence(json.dumps(shown, indent=2))
            members = [s for s in res["manifest"]["sessions"]
                       if (s.get("dispatch") or {}).get("parallel_group") == "g1"]
            L += ["Its members, as emitted (note `isolation` on each, and `touches` "
                  "carried onto every item — the M2a input that was missing before this "
                  "session):", ""]
            L += _fence(json.dumps(
                {"members": [{"id": s["id"], "dispatch": s["dispatch"]} for s in members],
                 "items": res["manifest"]["items"]}, indent=2))
        if res.get("prompt"):
            body = res["prompt"]
            body = body[body.index("## Work"):body.index("## Implementation notes")]
            L += ["And the task body written to that session's prompt file — the §3 merge "
                  "protocol the author never had to write (baseline, containment, base ref, "
                  "producer-first merge, gate re-run), plus the two limits an integrator "
                  "otherwise rediscovers by debugging:", ""]
            L += _fence(body)
    failed = [n for n, _d, _r, f in results if f]
    L += ["## Verdict", "",
          f"{len(results) - len(failed)}/{len(results)} fixtures behaved as declared."
          + (f" FAILED: {', '.join(failed)}" if failed else ""), ""]
    text = "\n".join(L)
    if dest:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        print(f"wrote {dest}")
    else:
        print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
