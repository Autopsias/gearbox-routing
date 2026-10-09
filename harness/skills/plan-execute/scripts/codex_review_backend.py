#!/usr/bin/env python3
"""The CODEX reviewer behind `llm_review_gate.py --reviewer codex`.

WHY A SECOND FAMILY. Every review gate in this harness asks Claude to review
code Claude wrote. A reviewer sharing the author's blind spots is a weaker check
than it looks, and the SSOT's cross-family clause says so. This module swaps ONE
thing — who reads the diff — and leaves the surface, the prompt, the findings
ledger and the three-valued exit code exactly where they were.

WHAT IT ADAPTS, and why it is the whole `data["result"]` contract rather than
`classify()` alone: FOUR consumers parse that one string — `classify()`,
`reviewed_count()`, `surface_attested()` and `ledger.findings_array()`. Satisfy
only the first and the gate scores every Codex run INDETERMINATE, including the
correct ones. So `run()` hands back the same `{"result": <text>}` shape the
`claude -p --output-format json` path produces, with the attested count and a
fenced JSON findings array inside it, and nothing downstream changes.

THREE WAYS THIS REFUSES, none of them a pass (see `egress_reason`,
`availability` and `parse` for the reasoning behind each):
  * RESTRICTED TREE -> `degrade("cross_family")`, no codex process at all.
  * NO CODEX on this box, or one that REFUSED on usage limits ->
    `degrade("cross_family_unavailable")`. Unavailable is not unreadable.
  * A RUN THAT PRODUCED NOTHING USABLE -> INDETERMINATE, never PASS.

WHAT THE REVIEWER IS ASKED lives next door in `codex_review_prompt.py`, and
WHAT IT SAID WHILE IT RAN in `codex_review_events.py` — the seams this file
already drew between its halves, made real when the module hit its size bound.

STDOUT CONTRACT (shared with the gate, see `identity`): line 1 is always the
reviewer identity; `VERIFIER:`, `DEGRADED_FROM:` and `REVIEWED_FILES:` are
PREFIXED marker lines a consumer finds BY PREFIX. Never string-equal line 1.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import llm_review_ledger as ledger
from codex_review_events import quota_hit
from codex_review_egress import _read, egress_reason, text_egress_reason  # noqa: F401
from codex_review_prompt import _norm, contract, prepared_paths  # noqa: F401

#: Mirrors ``llm_review_gate.INDETERMINATE``; duplicated rather than imported
#: back, which would be a cycle (the same rule `llm_review_surface` follows).
INDETERMINATE = 2

FAMILY = "codex"
#: SSOT: agentic/deep-reasoning review rides gpt-5.6-sol. `ultra` is forbidden by
#: policy and is not reachable from here at any level.
MODEL = "gpt-5.6-sol"
#: Gate level -> Codex reasoning effort. A review is a harder read than the work
#: it reviews, so every rung sits one above the naive mapping.
EFFORT = {"low": "medium", "medium": "high", "high": "xhigh"}
SCHEMA = Path(__file__).with_name("codex_review_findings.schema.json")
#: scripts/codex_supervised.py, from skills/plan-execute/scripts/ — same relative
#: layout in the source repo and in the deployed ~/.claude tree.
SUPERVISOR = Path(__file__).resolve().parents[3] / "scripts" / "codex_supervised.py"


def _first_line(argv, timeout=20):
    """First non-empty line `argv` printed on EITHER stream, or None on failure.

    STDOUT IS NOT THE ONLY ANSWER (codex-cli 0.147.0, measured): `--version` answers on
    stdout, `login status` on STDERR, both exiting 0, so a stdout-only read degraded every
    box -- over a green suite, since a shim prints where its author said. And callers MUST
    NEGATE what comes back: "not logged in" is an ANSWER, not an absence. `availability()`.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for stream in (proc.stdout, proc.stderr):
        out = [ln for ln in (stream or "").strip().splitlines() if ln.strip()]
        if out:
            return out[0].strip()[:60]
    return None


@lru_cache(maxsize=8)
def _version(exe):
    """`<exe> --version`, cached on the RESOLVED path.

    Keyed on the path, never on the bare name: a test that puts a stand-in on
    PATH — or removes one — must not read a version cached from a different
    binary, and `shutil.which` is re-run by every caller so an absent tool
    resolves to None and never reaches this cache at all.
    """
    return _first_line([exe, "--version"]) or "unknown"


def identity(reviewer, level):
    """LINE 1 OF EVERY RUN: who reviewed, at what version, model and effort.

    Prose for a human, deliberately. The machine-read facts are the marker lines
    a consumer finds BY PREFIX; pinning a parser to the text of line 1 is how a
    display string becomes an accidental API.
    """
    if reviewer == FAMILY:
        exe = shutil.which("codex")
        ver = _version(exe) if exe else "absent"
        return (f"[llm-review-gate] reviewer: family=codex version={ver} "
                f"model={MODEL} effort={EFFORT.get(level, 'xhigh')}")
    # THE ON-BOX REVIEWER IS NOT PROBED, and that is deliberate. `claude
    # --version` is one more invocation of the very binary this gate is about to
    # run -- it costs a process on every gate run, and the gate's own fixtures
    # stand a stub on PATH and answer from a QUEUE, so a version probe silently
    # eats the first review answer (measured here, three tests). Neither the CLI
    # version nor the model `claude -p` will pick is knowable without spending
    # that invocation, so both are reported as unprobed rather than guessed.
    return (f"[llm-review-gate] reviewer: family=claude version=unprobed "
            f"model=cli-default effort={level}")


# Availability and egress (`codex_review_egress.py`) — the two degrade causes,
# both decided BEFORE any codex process is started.
def availability():
    """(ok, reason). Everything the cross-family path needs, probed on this box.

    Quota is only PARTLY knowable here — a live limit shows up mid-run, and
    `codex_review_events.quota_hit` catches that case against the event log's
    ERROR records. This is the cheap half: no binary, no session, no supervisor.
    """
    if shutil.which("codex") is None:
        return False, "`codex` is not on PATH"
    if _version(shutil.which("codex")) == "unknown":
        return False, "`codex --version` did not answer"
    status = (_first_line(["codex", "login", "status"], timeout=30) or "").lower()
    # NEGATION FIRST: "not logged in" CONTAINS "logged in", so a positive-only substring
    # test reads a LOGGED-OUT box as usable and spends a review that cannot authenticate.
    if "not logged in" in status or "logged in" not in status:
        return False, f"codex is not logged in (`codex login status` said {status!r})"
    if not SUPERVISOR.exists():
        return False, f"the run supervisor {SUPERVISOR} is missing"
    if not SCHEMA.exists():
        return False, f"the findings schema {SCHEMA} is missing"
    return True, ""


def _stamp(led, record):
    """Append one non-finding record to this session's ledger, if there is one.

    No `attempt` key on purpose: a degrade that never reviewed anything must not
    advance the counter `fix_delta` and the prior section are computed from.
    """
    if led is None:
        return None
    path = ledger.ledger_path(led.plan_dir, led.session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return path


def degrade(args, led, cause, reason):
    """The on-box disposition: no verdict, no Codex, INDETERMINATE — and SAID SO.

    Recorded in three places so no reader has to infer it: the marker lines on
    stdout, a record in the findings ledger, and a stamp file the session's
    closeout can carry as evidence.
    """
    rec = {"kind": "reviewer", "family": FAMILY, "identity": identity(FAMILY, args.level),
           "verifier": "on_box_human", "degraded_from": cause, "reason": reason,
           "level": args.level, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    print("VERIFIER: on_box_human")
    print(f"DEGRADED_FROM: {cause}")
    print(f"[llm-review-gate] cross-family review NOT performed: {reason}")
    stamped = _stamp(led, rec)
    if led is not None:
        out = Path(led.plan_dir) / "_verify_state" / f"{led.session}.degraded.json"
        out.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        print(f"[llm-review-gate] degrade stamp: {out} (ledger: {stamped})")
    print(f"llm-review-{args.level}: INDETERMINATE — this tree cannot be reviewed "
          f"by the Codex family ({cause}). NOT a pass, and NOT a silent fall-back "
          f"to the on-box Claude reviewer. The disposition is on_box_human: a "
          f"human/on-box review recorded as an explicit VERIFIED-ON-BOX or BLOCKED.")
    return INDETERMINATE


# The run half.
@lru_cache(maxsize=1)
def _supervisor():
    """scripts/codex_supervised.py, loaded by path (once per process).

    IMPORTED, NEVER RE-DERIVED. The idle-kill, the process-GROUP kill and the
    resume-by-captured-thread-id are three separately-earned lessons (a run that
    stalls for 70 minutes looking alive; helpers orphaned by killing the leader;
    `resume --last` attaching to another runner's thread). A second copy of that
    logic is a second copy that can drift out of them.
    """
    spec = importlib.util.spec_from_file_location("codex_supervised", SUPERVISOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env():
    # TERM=dumb is a documented trigger for codex's stdin-read hang (#27019);
    # codex_supervised.main sets the same override before spawning.
    env = dict(os.environ)
    if env.get("TERM", "dumb") == "dumb":
        env["TERM"] = "xterm-256color"
    return env


def flags(level, out, schema=None):
    """The one flag set, legal on BOTH `codex exec` and `codex exec resume`.

    `--sandbox` is NOT (verified against codex-cli 0.144.1 and unchanged in
    0.147.0: "error: unexpected argument '--sandbox' found" on resume), so the
    sandbox rides as a config key. Two divergent argvs is what silently broke the
    recovery path the first time this was written.

    `--ignore-user-config --ignore-rules`: the reviewed tree must not configure
    its own reviewer. Without them codex loads `<tree>/.codex/config.toml` for any
    trusted folder (`~` is trusted on this box), and measured on codex-cli
    0.159.3, a committed one rewrote the reviewer's instructions and started an
    `mcp_servers` program OUTSIDE the read-only sandbox. Both flags stopped both.
    """
    return ["--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
            "-c", 'sandbox_mode="read-only"',
            "-c", f"model_reasoning_effort={EFFORT.get(level, 'xhigh')}",
            "-m", MODEL,
            "--output-schema", str(schema or SCHEMA),
            "-o", str(out)]


_RESUME_NUDGE = (
    b"Continue and FINISH the review from this session. The previous attempt was "
    b"killed by a supervisor after stalling with no output; that is a transport "
    b"fault, not a signal about the code. Do not restart the review and do not "
    b"re-read what you already read. Answer NOW as the single JSON object the "
    b"output contract specified.\n")


def spawn(prompt, cwd, timeout, level, out, log):
    """Run codex under the supervisor until `out` is non-empty. -> note ("" = ok).

    Two attempts at most, both inside ONE `timeout` budget: the gate already
    retries the whole review, and nesting an unbounded retry inside a retry is
    how a gate burns its wall clock without ever returning a verdict.
    """
    cs = _supervisor()
    deadline = time.time() + timeout
    idle = max(30, min(300, timeout // 3))
    notes = []
    for n in (1, 2):
        if time.time() >= deadline:
            break
        # EACH ATTEMPT IS JUDGED ONLY ON WHAT IT ITSELF WROTE. Both attempts
        # share one `-o` path, so a file left behind by a FAILED attempt 1
        # satisfies the `out.exists()` test below for an attempt 2 that exited 0
        # writing nothing -- an answer produced beside a non-zero exit, accepted
        # as a review, which is exactly what the comment further down refuses.
        out.unlink(missing_ok=True)
        tid = cs.thread_id_from_log(log) if n > 1 else None
        argv, mode = cs.attempt_argv(n, flags(level, out), tid)
        payload = prompt.encode() if mode == "fresh" else _RESUME_NUDGE
        proc, fh = cs._spawn(argv, payload, log, cwd, _env())
        why = cs._supervise(proc, log, idle, deadline)
        fh.close()
        rc = proc.poll()
        notes.append(f"attempt {n} ({mode}, thread={tid}): {why}, exit={rc}")
        # A NON-ZERO EXIT IS NEVER USABLE, even with a file behind it: codex
        # writes `-o` last, so output beside a failure is a partial answer, and a
        # partial review scored as a review is the silent green this gate refuses.
        if rc == 0 and out.exists() and out.stat().st_size > 0:
            return ""
        if why == "deadline":
            break
    if quota_hit(log):
        # An unavailable reviewer, not an unreadable one -- the two want
        # different dispositions. The markers are NOT printed here: only the
        # caller knows whether this is the first attempt (a degrade) or a retry
        # after codex already answered (a note), and only it has the ledger.
        return "codex refused on usage/quota limits; " + "; ".join(notes)
    return "codex produced no usable last message; " + "; ".join(notes)


def parse(out):
    """(payload, note) — the schema'd object from the `-o` file, or a reason.

    A schema miss, an empty file and a truncated answer all land here rather
    than downstream: the gate's contract is that anything it cannot READ is
    INDETERMINATE, never an empty findings array.
    """
    try:
        text = out.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return None, f"codex last-message file unreadable ({exc})"
    if not text:
        return None, "codex last-message file is EMPTY (exit code alone is not a review)"
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)      # a stray fence or preamble
        try:
            data = json.loads(m.group(0)) if m else None
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return None, f"codex last message is not a JSON object: {text[:200]!r}"
    if not isinstance(data.get("findings"), list) or not isinstance(data.get("reviewed"), list) \
            or not isinstance(data.get("priors", []), list):
        return None, f"codex answer does not match the findings schema: {text[:200]!r}"
    if str(data.get("verdict", "")).upper() == "FINDINGS" and not data["findings"]:
        # Incoherent, and the direction matters: a reviewer that says it found
        # something while handing over nothing has not produced a verdict.
        return None, "codex answered verdict=FINDINGS with an EMPTY findings array"
    return data, ""


def attest(prepared, reviewed):
    """Paths the gate PREPARED that Codex did not claim to have read.

    THE ATTESTATION MUST NOT BE A TAUTOLOGY. The count printed as
    `REVIEWED_FILES:` comes from the prepared list, so it can never disagree
    with itself — this comparison, against the reviewer's OWN list, is the part
    that can fail. Both sides are normalised the same way before comparing, or
    `./x.py` and `x.py` read as a shortfall that never happened.
    """
    claimed = {_norm(p) for p in reviewed if str(p).strip()}
    return [p for p in dict.fromkeys(prepared) if p not in claimed]


def _clean(text):
    """Findings text made safe to embed in a fenced JSON array.

    `[`/`]` inside a string would end the gate's non-greedy fence match early
    and the whole array would read as unparseable — an INDETERMINATE caused by
    the reviewer's prose, not by its review. Backticks would close the fence.
    """
    return re.sub(r"\s+", " ", str(text or "").replace("[", "(").replace("]", ")")
                  .replace("`", "'")).strip()


def _summary(f):
    """`title: <first sentence of why>` — the ledger's fingerprint input.

    NEVER EMPTY. `fingerprint()` is file + normalised summary with the LINE
    excluded, so an empty summary collapses every finding in one file onto one
    id: fix one and the ledger believes you fixed them all.
    """
    title = _clean(f.get("title"))
    why = re.split(r"(?<=[.!?])\s", _clean(f.get("why")) or "")[0][:240]
    summary = ": ".join(x for x in (title, why) if x)
    return summary or f"unspecified finding at {_clean(f.get('file'))}:{f.get('line')}"


def adapt(payload, prepared, led, level):
    """The schema'd answer -> the `data["result"]` string the gate already parses.

    Everything downstream (`classify`, `reviewed_count`, `surface_attested`,
    `findings_digest`, `ledger.findings_array`) reads that one string, so this is
    the whole adaptation and there is no second seam to keep in sync.
    """
    priors = {p["fid"]: p for p in (led.priors if led else [])}
    # WHAT WAS SHOWN IS `prepared`, AND NEVER `led.hashes`. The ledger's surface
    # counts captured run output that `prepared_paths` deliberately drops (listed
    # for the reviewer, never deep-read), so a prior on `_evidence/run.log` sits
    # in `hashes` over a file nobody was asked to open. `prepared` is exactly the
    # list `attest` held the reviewer to, so a `fixed` for any other file is
    # a claim about a file nobody was asked to read, and is not honoured.
    shown = set(prepared)
    out, matched = [], set()
    for f in payload["findings"]:
        rec = {"file": _norm(f.get("file")), "line": f.get("line"),
               "severity": str(f.get("severity", "")).lower(), "summary": _summary(f)}
        fid = ledger.fingerprint(rec)
        if fid in priors:
            # Codex emits no prior_id per finding, so the MATCH is made
            # here, on the ledger's own fingerprint (file + summary, line
            # excluded). Re-reported means still open.
            rec["prior_id"], rec["prior"] = fid, "open"
            matched.add(fid)
        out.append(rec)
    # SILENCE IS NOT A FIX. Inferring `fixed` for a shown prior Codex did not
    # re-report let one pass that MISSED the defect clear an open HIGH (found in a
    # security review). Only an explicit `priors[].status == "fixed"` clears.
    said_fixed = {str(p.get("id")): p for p in payload.get("priors") or []
                  if isinstance(p, dict) and p.get("status") == "fixed"}
    for fid, p in priors.items():
        if fid in matched or fid not in said_fixed or _norm(p.get("file") or "") not in shown:
            # Re-reported, not declared fixed, or in a file nobody was shown:
            # judge() decides (OPEN or UNVERIFIABLE), never this adapter.
            continue
        out.append({"prior_id": fid, "prior": "fixed",
                    "summary": _clean(said_fixed[fid].get("evidence")) or "declared fixed"})
    body = "\n".join(
        f"- {f['file']}:{f.get('line')} [{f.get('severity')}] {f['summary']}"
        for f in out if f.get("prior") != "fixed") or "(no finding)"
    return (f"Codex review (model={MODEL}, effort={EFFORT.get(level, 'xhigh')}).\n"
            f"{body}\n\nREVIEWED_FILES: {len(prepared)}\n\n```json\n"
            + json.dumps(out, indent=1) + "\n```")


def run(prompt, cwd, timeout, args, led, prepared, workdir=None):
    """-> (data|None, note, quota). The `run_once` pair, plus the one fact the
    caller cannot recover for itself: whether codex REFUSED on usage limits (an
    unavailable reviewer) rather than answering unusably (an unreadable one)."""
    # A private dir: a pid-named file in a shared /tmp is a guessable symlink target.
    work = Path(workdir or tempfile.mkdtemp(prefix="codex-review-"))
    out = work / f"codex-review-{os.getpid()}.txt"
    log = Path(str(out) + ".jsonl")
    for p in (out, log):
        p.unlink(missing_ok=True)
    log.write_bytes(b"")
    note = spawn(prompt + contract(prepared), cwd, timeout, args.level, out, log)
    if note:
        return None, note, quota_hit(log)
    payload, note = parse(out)
    if note:
        return None, note, False
    missing = attest(prepared, payload["reviewed"])
    if missing:
        return None, (f"reviewed-list SHORTFALL: codex did not attest "
                      f"{len(missing)} of the {len(prepared)} prepared path(s): "
                      + ", ".join(missing[:10])), False
    _stamp(led, {"kind": "reviewer", "family": FAMILY, "verifier": "cross_family",
                 "identity": identity(FAMILY, args.level), "level": args.level,
                 "model": MODEL, "effort": EFFORT.get(args.level, "xhigh"),
                 "reviewed": len(prepared), "findings": len(payload["findings"]),
                 "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    return {"result": adapt(payload, prepared, led, args.level)}, "", False


def _reviewer(args, led, paths, diff_file=None):
    """The gate's review callable — and the ONE marker block for the whole run.

    THE DISPOSITION IS NOT KNOWN UNTIL CODEX HAS ANSWERED. A usage/quota refusal
    is a DEGRADE, so announcing `VERIFIER: cross_family` before the attempt puts
    a contradicting first match in front of the `on_box_human` that follows —
    and the gate retries, so it prints twice. The markers are emitted here,
    exactly once, by whichever outcome the FIRST attempt produces:

      * refused on usage limits -> `degrade()`, and the gate's second attempt is
        skipped: an unavailable reviewer stays unavailable, and a retry buys
        only a second refusal.
      * anything else -> `VERIFIER: cross_family`. A codex process ran and
        answered (well or badly), so that IS the verifier; a quota refusal on
        the RETRY is then a note about the retry, not a new disposition.
    """
    said = []

    def review(prompt, cwd, timeout):
        if said[:1] == ["degraded"]:
            return None, "codex is unavailable for this review (degraded above)"
        if not said:
            # The prompt only exists now, so the text half of the guard runs
            # here, once, before the first byte is sent.
            reason = text_egress_reason(cwd, {"review prompt": prompt + contract(paths),
                                              "review diff": _read(diff_file)})
            if reason:
                said.append("degraded")
                degrade(args, led, "cross_family", reason)
                return None, reason
        data, note, quota = run(prompt, cwd, timeout, args, led, paths)
        if not said:
            if quota:
                said.append("degraded")
                degrade(args, led, "cross_family_unavailable", note)
            else:
                said.append("cross_family")
                print("VERIFIER: cross_family")
                # FROM THE PREPARED COUNT, NEVER FROM THE REVIEWER'S SELF-REPORT:
                # a count taken from the answer under test makes
                # `surface_attested` unfalsifiable. What the reviewer says about
                # its own reading is checked by `attest`.
                print(f"REVIEWED_FILES: {len(paths)}")
        return data, note
    return review


def backend(args, prepared_surface):
    """The reviewer callable for `--reviewer codex`, or an exit code to return.

    An int means the cross-family path is not available for this tree and the
    gate must stop HERE — before a prompt is built, before a process is started,
    and without a verdict of any kind.
    """
    led, _base, changed, new_files, diff_file, _expected = prepared_surface
    paths = prepared_paths(args.cwd, changed, new_files)
    reason = egress_reason(args.cwd)
    if reason:
        return degrade(args, led, "cross_family", reason)
    ok, why = availability()
    if not ok:
        return degrade(args, led, "cross_family_unavailable", why)
    return _reviewer(args, led, paths, diff_file)
