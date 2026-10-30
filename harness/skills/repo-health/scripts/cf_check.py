"""check-run: gearbox's own completion check for one Cloudflare quick run.

Split out of exposure.py to keep both files under the house 500-line limit.
It reads the run's documents once, validates those bytes with the two upstream
validators, binds the run to this repo and commit, and maps the result. The
status rules live in one place, `_status` below. Anything that stops the check
itself -> pending, never pass.
"""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from exposure import (ACCEPTED, DOCS, RUN_NAME, SEP, VENDOR, bad_text, dirty,
                      dirty_message, head, loads, open_set, read_exposure, read_file, read_why, route,
                      runs_parent)
from revalidation import _cleared, _norm, _tracked, trace_files

AGENT_CAP = 40          # the trial used about 25 agent calls
MODEL = "sonnet"
NAMES = {"lead": "lead", "unit": "open unit", "confirmed": "confirmed finding",
         "candidate": "unvalidated candidate"}
# unit states no one finished reviewing: each is carried until covered or candidate
OPEN_UNIT = ("deferred", "blocked", "out_of_scope", "planned", "in_progress")
REPORTS = ("architecture.md", "REPORT.md", "FINDINGS-DETAIL.md", "NEEDS-VALIDATION.md")
FINGERPRINT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")  # the vendored pattern, capped
MAX_REASON = 2000
POLICY = "sandboxed-source-and-local-only"     # vendored SKILL.md, "Write isolation"


def cf_detail(repo, out_dir, agents, detail):
    exp = read_exposure(repo)           # never name the exposed route for a repo not on it
    route_line = (f"route: cloudflare-quick ({exp['reason']})" if exp["exposed"] else
                  f"route: not cloudflare-quick ({exp['error'] or 'not exposed'})")
    return SEP.join([route_line, detail,
                     f"agents: {agents} calls (cap {AGENT_CAP}), model {MODEL}",
                     f"output: {Path(out_dir).resolve()}"])


def _validate(node, raw):
    """Run both validators on private copies of the bytes check_run evaluates."""
    timeout = float(os.environ.get("EXPOSURE_VALIDATOR_TIMEOUT", "300"))
    with tempfile.TemporaryDirectory(prefix="check-run-") as td:
        for name, data in raw.items():
            (Path(td) / name).write_bytes(data)
        for script, doc in (("validate-findings.cjs", "findings.json"),
                            ("validate-coverage-ledger.cjs", "coverage-ledger.json")):
            try:
                p = subprocess.run([node, str(VENDOR / script), str(Path(td) / doc)],
                                   capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                return f"{script} did not finish in {timeout:g} s"
            except OSError as e:
                return f"{script} could not start: {e.strerror or e}"
            if p.returncode:
                lines = [ln.strip() for ln in (p.stderr + p.stdout).splitlines() if ln.strip()]
                return f"{script} failed: {lines[0] if lines else f'exit {p.returncode}'}"
    return None


def _source_ref(meta):
    ref = meta.get("source_ref")
    if isinstance(ref, dict):           # {"commit": ..., "dirty": ...} is also seen
        return None if ref.get("dirty") else ref.get("commit")
    return ref if isinstance(ref, str) else None


def _mismatch(meta, out, r, sha):
    target = meta.get("target")
    if not isinstance(target, str) or Path(target).resolve() != r:
        return "target"
    if sha is None or _source_ref(meta) != sha:
        return "source_ref"
    if meta.get("profile") != "quick":
        return "profile"
    if meta.get("budget") != AGENT_CAP:
        return "budget"
    if meta.get("scope_paths") != ["."]:   # the whole-repo pass, never a scoped run
        return "scope_paths"
    if meta.get("execution_policy") != POLICY:
        return "execution_policy"
    if out.parent != runs_parent(r).resolve() or not RUN_NAME.match(out.name):
        return "run folder"
    return None


def _where(rec):
    t = (rec.get("trace") or [{}])[-1]  # the sink: where the boundary breaks
    return f"{t.get('file')}:{t.get('line')}"


def _lead_line(rec):
    plan = rec.get("validation_plan") or {}
    check = "; ".join(f"{k}: {plan[k]}" for k in ("local", "deployment") if plan.get(k))
    return (f"needs validation: {'; '.join(rec.get('blockers') or [])}"
            f" - check: {check} ({_where(rec)})")


def _reasons(units):
    return "; ".join(sorted({r for u in units for r in (u.get("unresolved") or [])}))


def _bad_bookkeeping(meta, ledger):
    """What is wrong with gearbox's own bookkeeping fields (the validators ignore
    them), or None: `revalidates` on a unit, `resolved_prior_leads` in metadata."""
    def fps(x):
        return isinstance(x, list) and all(isinstance(f, str) and FINGERPRINT.match(f) for f in x)
    for u in ledger:
        if "revalidates" in u and not fps(u["revalidates"]):
            return f"revalidates on unit {u.get('coverage_id')} is not a list of fingerprints"
    rpl = meta.get("resolved_prior_leads")
    if rpl is not None and not (isinstance(rpl, list) and all(
            isinstance(e, dict) and fps([e.get("fingerprint")]) and isinstance(e.get("reason"), str)
            and len(e["reason"]) <= MAX_REASON for e in rpl)):
        return ("resolved_prior_leads is not a list of {fingerprint, reason} "
                f"(reason at most {MAX_REASON} characters)")
    return None


def _incomplete(meta, ledger, findings, out, r):
    attempts = [a for u in ledger for a in [u, *(u.get("attempts") or [])]]
    return (_run_gaps(meta, ledger, out) + _ledger_gaps(ledger, findings)
            + _path_gaps(attempts, r) + _check_gaps(attempts, out))


def _run_gaps(meta, ledger, out):
    why = []
    if meta.get("run_status") != "complete":
        st = meta.get("run_status", "missing")
        why.append(f"run_status {str(st)[:100]}" + (
            f" ({str(meta['incomplete_reason'])[:200]})" if meta.get("incomplete_reason") else ""))
    for n in REPORTS:                   # read the way a reader would: text, not metadata
        try:
            read_file(out / n, nonblank=True)
        except (OSError, ValueError) as e:
            why.append(f"report file not readable text: {n} ({read_why(e, out / n)})")
    if not any(u.get("status") in ("covered", "candidate") for u in ledger):
        why.append("no coverage unit reviewed")
    for st in OPEN_UNIT:                # unfinished work, deferred included (whole-repo run)
        n = sum(u.get("status") == st for u in ledger)
        if n:
            why.append(f"{n} unit(s) {st}")
    return why


def _ledger_gaps(ledger, findings):
    why = []
    # quick runs exactly one hunter wave; later work is deferred (vendored SKILL.md, "quick")
    later = sorted({str(u.get("coverage_id")) for u in ledger
                    for a in [u, *(u.get("attempts") or [])] if a.get("wave") != 1})
    if later:
        why.append("quick profile allows one hunter wave; past wave 1: "
                   + ", ".join(_capped(later, "")))
    # every candidate, live or archived in attempts, needs a final record
    open_fps = _candidates(ledger, findings)
    if open_fps:
        why.append("candidate without a final record: " + ", ".join(open_fps))
    for u, a, fps in _unsettled(ledger):    # a candidate the run itself says is not settled
        where = "candidate unit" if a is u else f"archived candidate attempt (wave {a.get('wave')}) of"
        why.append(f"{where} {u.get('coverage_id')} unresolved "
                   f"({'; '.join(a['unresolved'])[:200]}): " + ", ".join(fps))
    return why


def _path_gaps(attempts, r):
    why = []
    # a reviewed path must be a regular file at HEAD (not a folder, link, ignored or stray file)
    paths = {p for a in attempts for p in [*(a.get("reviewed_paths") or []), *(
        x for c in a.get("local_checks") or [] for x in c.get("reviewed_paths") or [])]}
    files, dirs, special = _tracked(r)
    linked = sorted(p for p in paths if _norm(p) in special)
    folders = sorted(p for p in paths if _norm(p) in dirs)
    gone = sorted(p for p in paths if _norm(p) not in files | dirs | special)
    if linked:
        why.append("reviewed path is a symlink or submodule at HEAD, not a regular file: "
                   + ", ".join(_capped(linked, "")))
    if folders:
        why.append("reviewed path is a folder, not a regular file: "
                   + ", ".join(_capped(folders, "")))
    if gone:
        why.append("reviewed path not tracked at HEAD: " + ", ".join(_capped(gone, "")))
    return why


def _check_gaps(attempts, out):
    why = []
    for chk in [c for a in attempts for c in a.get("local_checks") or []]:
        art = chk.get("artifact")
        if art:
            p = (out / art).resolve()
            if out not in p.parents or not p.is_file():
                why.append(f"missing artifact: {art}")       # upstream issue 21
                continue
        if chk.get("method") == "local":
            why.append(f"local check in a source-only run: {art}")
    return why


def agent_floor(ledger, findings):
    """Fewest agent calls this ledger can show: 4 recon + 1 critic + each distinct
    evidence owner (a unit or attempt owner, or a local check's agent_id) + one fresh
    verifier per final record (quick profile)."""
    owners = {o for u in ledger for a in [u, *(u.get("attempts") or [])]
              for o in [a.get("agent_id"), *(c.get("agent_id") for c in a.get("local_checks") or [])]
              if o}
    return 5 + len(owners) + len(findings)


def _capped(items, more):
    return items if len(items) <= 5 else items[:5] + [f"(+{len(items) - 5} more{more})"]


def _candidates(ledger, findings):
    """Candidate fingerprints, live or archived in attempts, with no final record here."""
    final = {f.get("fingerprint") for f in findings}
    return sorted({fp for u in ledger for a in [u, *(u.get("attempts") or [])]
                   if a.get("status") == "candidate"
                   for fp in a.get("result_fingerprints") or []} - final)


def _unsettled(ledger):
    """[(unit, candidate attempt, its fingerprints)] for every candidate attempt, live or
    archived in `attempts`, with unresolved reasons (validation_budget_exhausted and the
    like): a final record does not settle it."""
    return [(u, a, sorted(a.get("result_fingerprints") or [])) for u in ledger
            for a in [u, *(u.get("attempts") or [])]
            if a.get("status") == "candidate" and a.get("unresolved")]


def _carry(old, ledger, findings, cleared=None, failed=()):
    """(open items after this run, earlier open items this run does not mention,
    earlier open items resolved here).

    THE INVARIANT, the one place an item can leave the open set: any failed check
    keeps every open item open. A run with any failed completion check (`failed`:
    the reasons _incomplete gives) resolves nothing; it carries every earlier open
    item unchanged, plus its own. (Every other failed check is pending, and a
    pending run is never accepted, so its open set never replaces the last one.)

    Open after this run: every lead, confirmed finding, unvalidated candidate and
    unit in an OPEN_UNIT state in it, plus every earlier open item not resolved. A
    lead leaves as confirmed or rejected, a unit as covered or candidate, a
    candidate through any final record, and a confirmed finding as rejected (a
    demoted one stays confirmed); a lead with no final record here also leaves when
    `cleared` (a critic entry, see _cleared) names it. A revalidation unit clears nothing."""
    cleared = cleared or {}
    verdict = {f["fingerprint"]: f["verdict"] for f in findings}
    status = {u["coverage_id"]: u["status"] for u in ledger}
    now = {("confirmed" if f["verdict"] == "confirmed" else "lead", f["fingerprint"]):
           f["title"] for f in findings if f["verdict"] != "rejected"}
    now |= {("unit", u["coverage_id"]): f"{u['surface']} / {u['attack_class']}"
            for u in ledger if u["status"] in OPEN_UNIT}
    now |= {("candidate", fp): fp for fp in _candidates(ledger, findings)}
    now |= {("candidate", fp): fp for _, _, fps in _unsettled(ledger) for fp in fps}
    missing, resolved = [], []
    for (kind, key), item in old.items():
        v, st, label = verdict.get(key), status.get(key), item["label"]
        if kind == "unit":
            done, absent = st in ("covered", "candidate"), st in (None, "not_applicable")
        elif kind == "candidate":       # only a final record, of any verdict, settles it
            done, absent = v is not None, v is None and (kind, key) not in now
        else:
            done = v == "rejected" or (kind, v) == ("lead", "confirmed") or (
                v is None and (kind, key) in cleared)
            absent = v is None and not done
            st = cleared.get((kind, key))
        done = done and not failed
        if absent:
            missing.append(f"{NAMES[kind]} {label}")
        if done:
            resolved.append(f"{NAMES[kind]} {label} ({v or st})")
        if not done:
            if kind == "confirmed":
                now.pop(("lead", key), None)
            if kind != "lead" or ("confirmed", key) not in now:   # never a lead beside it
                now.setdefault((kind, key), label)
    return now, missing, resolved


def _status(open_now, incomplete):
    """The one place the status rules live."""
    if any(kind == "confirmed" for kind, _ in open_now):
        return "fail"           # a known vulnerability is open: confirmed now or never resolved
    return "warn" if open_now or incomplete else "pass"


def _read_docs(out):
    """(raw bytes, parsed docs), or (None, why) on the first refusal.

    Each document is read ONCE; check_run validates and evaluates those same bytes,
    so a file lost or swapped after validation can never become an empty passing audit."""
    raw, doc = {}, {}
    for name in DOCS:
        try:
            text = read_file(out / name)
        except (OSError, ValueError) as e:
            return None, f"cannot read {name}: {read_why(e, out / name)}"
        raw[name] = text.encode()
        try:
            doc[name] = loads(text)
        except ValueError as e:
            return None, f"cannot parse {name}: {e}"
    for name in DOCS:
        if bad_text(doc[name]):
            return None, f"{name} holds text that is not valid UTF-8"
    return raw, doc


def _prechecks(out, r, agents):
    """The refusals before any evaluation, in order -> a reason string on the first one,
    else (sha, raw, (meta, ledger, findings), floor)."""
    kind, why = route(r, make_dir=False)    # the opt-in itself, read under the lock
    if kind != "cloudflare-quick":
        return why if kind == "error" else f"route is {kind}, not cloudflare-quick"
    if os.path.lexists(out / ACCEPTED):     # only accept() writes it, once, in a fresh folder
        return f"run folder already holds {ACCEPTED}: start a new run"
    sha = head(r)                       # read once; re-checked before returning
    if agents is not None and agents > AGENT_CAP:
        return f"agent cap exceeded: {agents} calls, cap {AGENT_CAP}"
    raw, doc = _read_docs(out)
    if raw is None:
        return doc
    failed = _validate(shutil.which("node"), raw)
    if failed:
        return failed
    meta, ledger, findings = (doc[n] for n in DOCS)
    if not isinstance(meta, dict):
        return "cannot parse run-metadata.json: not a JSON object"
    floor = agent_floor(ledger, findings)
    if agents is not None and agents < floor:
        return (f"agent count below the ledger floor: {agents} reported, "
                f"the ledger shows at least {floor}")
    field = _mismatch(meta, out, r, sha)
    if field:
        return f"run does not match this repo: {field}"
    changed = dirty(r)
    if changed:
        return dirty_message(changed)
    return sha, raw, (meta, ledger, findings), floor


def _detail(out, ledger, findings, floor, old, since, carry, incomplete):
    """The check-run detail lines, in their reading order."""
    open_now, missing, resolved = carry
    confirmed = [f for f in findings if f.get("verdict") == "confirmed"]
    leads = [f for f in findings if f.get("verdict") == "needs_validation"]
    deferred = [u for u in ledger if u.get("status") == "deferred"]
    excluded = [u for u in ledger if u.get("status") == "not_applicable"]
    old_keys = {key for _, key in old}
    new_leads = [f for f in leads if f.get("fingerprint") not in old_keys]
    new_def = [u for u in deferred if u.get("coverage_id") not in old_keys]
    carried = sum(key in old_keys for _, key in open_now)
    detail = [f"new since {since or 'no prior run'}: {len(new_leads)} leads, "
              f"{len(new_def)} deferred units; carried: {carried}"]
    if missing:
        detail.append("still open from earlier runs, missing here: "
                      + ", ".join(_capped(missing, "")))
    if resolved:                        # shown, so a false resolution is visible
        detail.append("resolved here: " + ", ".join(_capped(resolved, "")))
    if incomplete and old:
        detail.append("nothing resolved here: the run failed a completion check")
    detail += [f"confirmed: {f.get('title')} "
               f"({(f.get('severity') or {}).get('overall_severity')}) ({_where(f)})"
               for f in confirmed]
    lead_fps = {f.get("fingerprint") for f in leads}    # a demoted finding stays confirmed
    still = [label for (kind, key), label in open_now.items()
             if kind == "confirmed" and key in lead_fps]
    detail += [f"still confirmed (reported here as a lead): {label}" for label in still]
    if incomplete:
        detail.append("incomplete run: " + "; ".join(incomplete))
    detail += _capped([_lead_line(f) for f in new_leads + [f for f in leads if f not in new_leads]],
                      f": see {out / 'NEEDS-VALIDATION.md'}")
    if deferred:
        names = _capped([f"{u.get('surface')} / {u.get('attack_class')}"
                         for u in new_def + [u for u in deferred if u not in new_def]], "")
        detail.append(f"deferred: {len(deferred)} ({_reasons(deferred)}): " + ", ".join(names))
    if excluded:
        detail.append(f"excluded on purpose: {len(excluded)} ({_reasons(excluded)})")
    covered = sum(u.get("status") in ("covered", "candidate") for u in ledger)
    detail.append(f"{covered} units reviewed, {len(confirmed)} confirmed, "
                  f"{len(leads) - len(still)} need validation, "
                  + (f"{len(still)} still confirmed, " if still else "")
                  + f"ledger floor {floor} agent calls")
    return detail


def _open_list(sha, old, open_now, ledger, findings, failed=()):
    # ref: the commit an item was last reported at, failed run or not; a carried one keeps
    # its old ref. Files: an inherited item reported in a run that failed a completion check
    # keeps its inherited files; a complete run gives a reported item its own files.
    kinds = {"confirmed": "confirmed", "needs_validation": "lead"}
    seen = {(kinds[f.get("verdict")], f.get("fingerprint")) for f in findings
            if f.get("verdict") in kinds}
    seen |= {("candidate", fp) for fp in _candidates(ledger, findings)}
    seen |= {("candidate", fp) for _, _, fps in _unsettled(ledger) for fp in fps}
    seen |= {("unit", u.get("coverage_id")) for u in ledger if u.get("status") in OPEN_UNIT}
    # a failed run's confirmed item that replaced an inherited lead inherits from that lead
    def inherited(k, key):
        return old.get((k, key)) or (old.get(("lead", key)) if k == "confirmed" else None)

    def reported(k, key):           # a confirmed finding reported here as a lead counts too
        return (k, key) in seen or (k == "confirmed" and ("lead", key) in seen)

    # files: a finding's own trace files, stored with its ref (a record only: nothing clears by them)
    own = {f.get("fingerprint"): trace_files(f) for f in findings}
    return [{"kind": k, "id": key, "label": label,
             "ref": sha if reported(k, key) else (inherited(k, key) or {}).get("ref"),
             "files": own.get(key) if reported(k, key) and not (failed and inherited(k, key))
             else (inherited(k, key) or {}).get("files")}
            for (k, key), label in sorted(open_now.items())]


def check_run(out_dir, repo, agents=None):
    """Gearbox's own completion check for one Cloudflare quick run -> {status, detail}."""
    out, r = Path(out_dir).resolve(), Path(repo).resolve()

    def pending(why):
        return {"status": "pending", "detail": why}

    got = _prechecks(out, r, agents)
    if not isinstance(got, tuple):      # a refusal reason
        return pending(got)
    sha, raw, (meta, ledger, findings), floor = got
    incomplete = _incomplete(meta, ledger, findings, out, r)
    # obligations inherited from every earlier accepted run, not just the last one
    bad = _bad_bookkeeping(meta, ledger)
    if bad:
        return pending(f"bad bookkeeping field: {bad}")
    try:
        old, since = open_set(r)
    except ValueError as e:
        return pending(f"open-set state unreadable: {e}")
    cleared = _cleared(old, meta, findings)
    carry = _carry(old, ledger, findings, cleared, incomplete)
    detail = _detail(out, ledger, findings, floor, old, since, carry, incomplete)
    status = _status(carry[0], incomplete)
    if head(r) != sha or dirty(r):
        return pending("source changed during check-run")
    return {"status": status, "detail": SEP.join(detail),
            # runlog._digest of the bytes validated here: accept() refuses any other
            "digest": hashlib.sha256(b"".join(raw[n] for n in DOCS)).hexdigest(),
            "open": _open_list(sha, old, carry[0], ledger, findings, incomplete)}


# ---------------------------------------------------------------- CLI
