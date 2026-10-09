#!/usr/bin/env python3
"""Known-positive probes: prove every repo-health check can refuse its defect.

A check that reports clean because it never really looked reads exactly like a
check that passed. So every deterministic check `health.py collect` emits is run here
against a matrix of fixture repos from probe_cases.py: a clean one (expect
pass/na/pending) and one where ONLY the defect that check exists to catch was
planted (expect a refusal whose message matches a per-check pattern).

Three rules are load-bearing:

* Refusal is proven by the check's OWN violation message. A different status, a
  traceback or an unrelated message is ERROR, never "refused".
* A plant that changes nothing is CANNOT FAIL — a finding, not a fixture to
  adjust. Read the check's code before touching the fixture.
* A tool probe whose CLEAN fixture already trips the plant pattern is ERROR:
  a clean side that was never clean makes the whole probe unfalsifiable.

Run:  python3 probe_checks.py [--tools] [--out FILE]
`--tools` adds the external-tool probes (gitleaks / semgrep / ruff / make / uvx),
which need those binaries installed. Everything else is deterministic and always
runs — test_probe_checks.py runs exactly that set, with no subset of its own.

Exit code, deliberately three-valued, because "we did not look" is not "clean":
  0  every probe ran, and every check refused its known positive
  1  a check could NOT refuse (CANNOT FAIL), or a probe errored — outranks 2
  2  nothing broke, but a probe was SKIPPED, so some check id's real plant never
     ran — its tool is not installed, or the tool could not reach the feed it
     reads (an offline runner) (GAP), or `--tools` was not passed at all and all
     eight external probes sat out. Green must not mean unlooked-at.
"""
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from functools import lru_cache
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import health  # noqa: E402
from probe_cases import BASE, CASES, TOOL_PROBES, git, make_repo  # noqa: E402


# ---------- running one fixture ----------

_CACHE = {}


def collect_statuses(root, slug, overrides, setup=None):
    """Build the fixture, run the real collector, return {id: check}.

    Memoised on the fixture ITSELF, not on the case: forty-odd cases share the
    unmodified BASE as their clean side, and building each one costs a git init,
    a commit and a full collect. The key is the override map plus the setup
    function's name, so two cases only share a result when they would have built
    byte-identical repos.
    """
    key = (tuple(sorted((k, v if v is None else hash(v))
                        for k, v in overrides.items())),
           getattr(setup, "__name__", None))
    if key in _CACHE:
        return _CACHE[key]
    files = {k: v for k, v in BASE.items()}
    for k, v in overrides.items():
        if v is None:
            files.pop(k, None)
        else:
            files[k] = v
    repo = make_repo(root, slug, files)
    if setup:
        setup(repo)
    with redirect_stdout(StringIO()):
        health.collect(repo, fresh=True)
    _CACHE[key] = {c["id"]: c for c in health.load_card(repo)["checks"]}
    return _CACHE[key]


def judge(case, clean, plant):
    cid = case["id"]
    cs, cpat = case["clean_expect"]
    ps, ppat = case["plant_expect"]
    c, p = clean.get(cid), plant.get(cid)
    if c is None or p is None:
        return "ERROR", f"{cid} was not emitted by collect at all"
    if c["status"] != cs:
        return "ERROR", (f"clean fixture is not clean: expected {cs}, got "
                         f"{c['status']} — {c['detail']}")
    if cpat and not re.search(cpat, c["detail"]):
        return "ERROR", f"clean detail {c['detail']!r} does not match {cpat!r}"
    # A false-positive CONTROL is the one case where "nothing changed" is the
    # right answer, so it is the one case the identity rule must not judge.
    # Every check id still carries at least one real plant (sec.secrets-history's
    # lives in TOOL_PROBES, not here), so this cannot be used to excuse a gate.
    if case["kind"] == "plant" and (p["status"], p["detail"]) == (c["status"], c["detail"]):
        return "CANNOT FAIL", (f"the planted defect changed nothing: still "
                               f"{p['status']} — {p['detail']}")
    if p["status"] != ps:
        return "CANNOT FAIL", f"expected {ps} on the plant, got {p['status']} — {p['detail']}"
    # An EMPTY plant pattern is a probe that cannot fail: re.search("", anything)
    # returns a match, so the refusal test below would wave through any message at
    # all — in the file written to catch exactly that. The clean side has been
    # guarded this way since it was written (`if cpat and ...`, where an absent
    # pattern legitimately means "do not assert on the wording"); the plant side
    # never was, and there the absence means the opposite. probe_cases.py refuses
    # such a case at import too; this is the guard for every OTHER caller, since
    # nothing makes judge() reachable only through that table.
    if not ppat:
        return "ERROR", (f"{cid}'s plant pattern is empty — re.search('') matches "
                         "anything, so this case could never have failed")
    if not re.search(ppat, p["detail"]):
        return "ERROR", (f"refused with the wrong message: {p['detail']!r} "
                         f"does not match {ppat!r}")
    return "OK", ""


def run_case(case, idx, collector=None, root=None):
    collector = collector or collect_statuses
    root = Path(root or tempfile.mkdtemp(prefix="repo-health-probe-"))
    slug = f"{idx:02d}-" + re.sub(r"[^a-z0-9]+", "-", case["id"])
    rec = {"id": case["id"], "case": case["case"], "clean": "", "planted": "",
           "kind": case["kind"]}
    try:
        clean = collector(root, slug + "-clean", case.get("clean") or {},
                          case.get("clean_setup"))
        plant = collector(root, slug + "-plant", case["plant"], case.get("plant_setup"))
    except Exception as exc:                       # noqa: BLE001 — any crash is ERROR
        rec.update(verdict="ERROR", note=f"{type(exc).__name__}: {exc}")
        return rec
    verdict, note = judge(case, clean, plant)
    for key, got in (("clean", clean), ("planted", plant)):
        c = got.get(case["id"])
        rec[key] = f"{c['status']} — {c['detail']}" if c else "NOT EMITTED"
    rec.update(verdict=verdict, note=note, pattern=case["plant_expect"][1])
    return rec


def case_rec(c):
    """The identity of a deterministic case, for when run_case dies past its try.

    judge() and the record formatting sit OUTSIDE run_case's own try, so a record
    missing `detail` raised KeyError and discarded the whole run — the same shape
    guarded() was written for. Identity has to come from the case table here
    because the crash may be what stopped the record from being built.

    Every read here is defensive ON PURPOSE. `case_rec(c)` is an ARGUMENT to
    guarded(), so it is evaluated BEFORE guarded() can catch anything — a case
    entry missing a key would raise here and discard the run, which is precisely
    the failure this wiring removes. The identity net cannot need a net.
    """
    return {"id": c.get("id", "<case with no id>"), "case": c.get("case", ""),
            "kind": c.get("kind", ""),
            "pattern": (c.get("plant_expect") or ("", ""))[1]}


def tool_rec(p):
    return {"id": p["id"], "case": f"[{p['tool']}] " + p["case"],
            "clean": "", "planted": "", "pattern": p["plant_pat"]}


@lru_cache(maxsize=None)
def reachable(host, port=443, timeout=3):
    """Can this machine open a socket to the source the tool reads from?

    The other half of `shutil.which`. A missing binary is already GAP — "we did
    not look" — but a tool that IS installed and cannot reach its rule feed or
    its package index has not looked either, and it used to be recorded as ERROR:
    "a gate is broken", exit 1. That collapses the exact distinction this file's
    three-valued exit code exists to preserve, and on an offline runner it
    accuses semgrep and pip-audit of a defect neither was asked about.

    Asked only when a net-dependent probe has ALREADY produced a non-OK reading,
    never as a preflight: a tool with a warm local cache can still probe fine
    with the host unreachable, and skipping it then would lose real coverage.
    """
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def could_not_look(p):
    """The reason this net-dependent probe never got an answer, or ""."""
    host = p.get("net")
    if host and not reachable(host):
        return f"{p['tool']} could not reach {host} — NOT probed (offline?)"
    return ""


def run_tool_probe(p):
    rec = tool_rec(p)
    if not shutil.which(p["tool"]):
        rec.update(verdict="GAP", note=f"{p['tool']} is not installed — NOT probed")
        return rec
    outs = {}
    for phase in ("clean", "plant"):
        d = Path(tempfile.mkdtemp(prefix=f"probe-{p['tool']}-"))
        for rel, body in p[phase].items():
            (d / rel).write_text(body)
        if p.get("git"):
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            git(d, "add", "-A")
            git(d, "commit", "-qm", "init")
        r = subprocess.run(p["cmd"], cwd=d, capture_output=True, text=True)
        outs[phase] = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout + r.stderr)
        tail = (outs[phase].strip().splitlines() or [""])[-1]
        rec["clean" if phase == "clean" else "planted"] = \
            f"rc={r.returncode} — {tail}"[:200]
    # The clean side must ALSO not carry the defect. A clean_pat loose enough to
    # match a dirty run turns the whole probe into a gate that cannot fail — which
    # is what happened to the semgrep probe before this line existed.
    if re.search(p["plant_pat"], outs["clean"], re.S):
        # Returns HERE, before the reachability question: a clean side that
        # already carries the defect is a fixture defect, and no network outage
        # explains it. Everything below is a reading the network could.
        rec.update(verdict="ERROR",
                   note=(f"the CLEAN fixture already trips {p['plant_pat']!r} — "
                         "fixture problem, not a gate problem"))
        return rec
    if not re.search(p["clean_pat"], outs["clean"], re.S):
        rec.update(verdict="ERROR",
                   note=f"clean fixture did not read clean: {outs['clean'][-400:]!r}")
    elif not re.search(p["plant_pat"], outs["plant"], re.S):
        rec.update(verdict="CANNOT FAIL",
                   note=f"planted defect not reported: {outs['plant'][-400:]!r}")
    else:
        rec.update(verdict="OK", note="")
        return rec
    blind = could_not_look(p)
    if blind:
        rec.update(verdict="GAP", note=blind + f" — was: {rec['note'][:160]}")
    return rec


def guarded(rec, fn, *args):
    """Run a probe; turn any crash into ONE ERROR record, never a lost run.

    run_case() has caught its own crashes since the first draft. The vendor-pin
    and external-tool probes shell out with `check=True` and read cards with a
    bare next(), and had no such net — so a missing binary, a changed card shape
    or a git failure discarded every record collected so far instead of recording
    the one probe that broke. The contract is the file's own: a crash is ERROR.
    """
    try:
        return fn(*args)
    except Exception as exc:                       # noqa: BLE001 — any crash is ERROR
        return {"clean": "", "planted": "", **rec, "verdict": "ERROR",
                "note": f"{type(exc).__name__}: {exc}"}


VENDOR_REC = {"id": "sec.vendor-pins", "kind": "plant",
              "case": "gate: an install that vendors no skills",
              "pattern": r"vendors no skills"}


def probe_vendor_pins():
    """sec.vendor-pins gates on the INSTALL's vendor.json, not on the target repo.

    So the only way to run both sides is to build two throwaway installs of the
    collector and invoke health.py as a subprocess — which is also the most
    honest provenance the transcript can carry for this one.
    """
    root = Path(tempfile.mkdtemp(prefix="probe-vendor-"))
    repo = make_repo(root, "fixture", dict(BASE))
    rec = dict(VENDOR_REC)
    seen = {}
    for name, has in (("clean", True), ("plant", False)):
        inst = root / ("install-" + name)
        shutil.copytree(HERE, inst / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__", ".ruff_cache"))
        if has:
            (inst / "vendor.json").write_text('{"skills": {}}\n')
        subprocess.run([sys.executable, str(inst / "scripts" / "health.py"),
                        "collect", "--repo", str(repo), "--fresh"],
                       check=True, capture_output=True)
        # health.load_card, not a second reader: it is the one place the card's
        # type is guarded, and a private json.loads here would skip that guard.
        seen[name] = next(c for c in health.load_card(repo)["checks"]
                          if c["id"] == "sec.vendor-pins")
    rec["clean"] = f"{seen['clean']['status']} — {seen['clean']['detail']}"
    rec["planted"] = f"{seen['plant']['status']} — {seen['plant']['detail']}"
    if seen["clean"]["status"] != "pending":
        rec.update(verdict="ERROR", note="an install WITH vendor.json should be pending")
    elif seen["plant"]["status"] != "na":
        rec.update(verdict="CANNOT FAIL", note="an install with no vendor.json stayed "
                                               + seen["plant"]["status"])
    elif not re.search(rec["pattern"], seen["plant"]["detail"]):
        rec.update(verdict="ERROR", note="na, but not for the reason it claims to check")
    else:
        rec.update(verdict="OK", note="")
    return rec


# ---------- transcript ----------

def run_all(tools=False):
    """Every deterministic probe — the set CI runs, with no subset of any kind.

    There was a `slow=False` subset, and it was the defect this file exists to
    catch. It dropped BOTH probes for hyg.large-files and sec.vendor-pins — the
    only coverage those two check ids had — so neutering health.py's 5 MB
    threshold to 5 GB left CI green, and the docstring here claimed the
    opposite. Measured afterwards, the two "slow" probes were a small fraction of
    the run's wall clock, for 100 % of two checks. Cost that is worth opting
    out of lives behind `--tools`; a
    deterministic case either runs on every push or it is deleted.
    """
    _CACHE.clear()
    root = Path(tempfile.mkdtemp(prefix="repo-health-probe-"))
    recs = [guarded(case_rec(c), run_case, c, i, None, root) for i, c in enumerate(CASES)]
    recs.append(guarded(VENDOR_REC, probe_vendor_pins))
    if tools:
        recs += [guarded(tool_rec(p), run_tool_probe, p) for p in TOOL_PROBES]
    return recs


# What the plant side ACTUALLY did, keyed on the status the collector RECORDED.
# `na`/`pending` are gate cases: the check declined to apply, or had not run.
# Neither is a refusal, and calling them one is the lie this file exists to catch.
PLANT_WORD = {"fail": "refused", "warn": "refused", "pass": "held",
              "na": "did not apply", "pending": "did not run"}


def plant_word(rec):
    """The word for the plant side, DERIVED from the recorded result.

    It used to be chosen ALONGSIDE the result — "refused" for anything OK that
    was not a control — so ~14 gate cases whose plant resolves to `na`/`pending`
    printed "refused" directly beside their own recorded `na`. Two sources that
    can disagree is how that happened, so there is now one: the status inside
    rec["planted"], the same string printed next to the word.
    """
    if rec["verdict"] != "OK":
        return rec["verdict"]
    status = rec["planted"].split(" — ", 1)[0]
    if status.startswith("rc="):
        return "refused"               # a tool probe: OK means the tool reported it
    return PLANT_WORD.get(status, status)


def transcript(recs, tools):
    counts = {}
    for r in recs:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    ids = sorted({r["id"] for r in recs})
    out = ["", "## Generated probe run", "",
           f"cases: {len(recs)} · check ids covered: {len(ids)} · "
           + " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())),
           "", "External tools: " + ("probed" if tools else "SKIPPED (--tools not given)"),
           "", "| verdict | check | case |", "|---|---|---|"]
    for r in recs:
        out.append(f"| {r['verdict']} | `{r['id']}` | {r['case']} |")
    out += ["", "### Per-case evidence", ""]
    for r in recs:
        label = "control" if r.get("kind") == "control" else "planted"
        out += [f"**`{r['id']}` — {r['case']}**",
                f"- clean: {r['clean']}",
                f"- {label}: {plant_word(r)} ({r['planted']})",
                f"- matched: `{r.get('pattern')}`"]
        if r.get("note"):
            out.append(f"- NOTE: {r['note']}")
        out.append("")
    bad = [r for r in recs if r["verdict"] != "OK"]
    if bad:
        out += ["### Findings", ""] + [
            f"- **{r['verdict']}** `{r['id']}` — {r['case']}: {r.get('note')}" for r in bad]
    return "\n".join(out) + "\n"


def main(argv):
    tools = "--tools" in argv
    out = None
    if "--out" in argv:
        out = Path(argv[argv.index("--out") + 1])
    recs = run_all(tools=tools)
    text = transcript(recs, tools)
    if out:
        out.write_text(out.read_text() + text if out.exists() else text)
        print(f"appended {len(recs)} probe records to {out}")
    else:
        print(text)
    verdicts = {r["verdict"] for r in recs}
    if verdicts & {"CANNOT FAIL", "ERROR"}:
        return 1                                   # a gate is broken
    if "GAP" in verdicts:
        return 2                                   # a tool is missing — we did not look
    # Without --tools, EIGHT declared probes never ran. Exiting 0 there is the exact
    # lie this file exists to catch: absence of a probe reading as a clean probe.
    return 0 if tools else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
