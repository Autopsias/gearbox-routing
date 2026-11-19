"""The ACCEPT LIST of a review gate: findings the owner declared a session does not
have to fix, by file and phrase, each with a reason. Split out of
`llm_review_ledger` (which re-exports every name here) to keep that file under the
size limit; the rules and their history are in the docstrings below.
"""
import re
from pathlib import Path

import safe_json as sj


def _norm(path):
    """Strip leading `./` only. NOT `lstrip("./")` -- that strips a character SET,
    so a root dotfile like `.complexity-exceptions` came back as
    `complexity-exceptions`: it never matched the delta, so a real finding on it
    was classed `outside` and demoted to non-blocking `noted` (2026-08-20, the
    fifth instance of this same call in this repo)."""
    return re.sub(r"^(\./)+", "", str(path).strip())


def _norm_summary(text):
    """A summary reduced to comparable words: lowercase, letters and spaces only,
    single-spaced. The fingerprint and the accept list both read a summary through
    this, so they agree about what two wordings have in common."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", str(text).lower())).strip()


def accepted_path(plan_dir, session):
    return Path(plan_dir) / "_verify_state" / f"{session}.accepted.json"


def accepted_paths(plan_dir, session):
    """The gate's own file, then the session-wide `<sid>.accepted.json`. Both are
    read: the short name is the one an owner writes by hand, and for 13 review
    runs it was ignored with a warning nobody acted on (2026-11-16, s04)."""
    wide = accepted_path(plan_dir, session.split(".")[0])
    return list(dict.fromkeys([accepted_path(plan_dir, session), wide]))


def load_accepted(plan_dir, session):
    """Findings the operator has declared this session does not have to fix.

    OPT-IN and FAIL-CLOSED. A missing file, unreadable JSON, or a malformed entry
    yields NO accepts -- never a blanket one. An entry needs a `file`, a non-empty
    `contains` phrase and a `reason`; an EMPTY phrase would match every finding in
    the file, so it is dropped rather than honoured.

    KEYED ON A PHRASE, NEVER ON THE FINDING ID. The id hashes the whole summary
    and the reviewer rewords its summary between attempts, so one deferred gap
    arrived under two different ids on attempts 4 and 5. A phrase
    that survives the rewording is the only key that keeps matching.

    This CANNOT clear a real regression by itself: it names one file and one
    phrase, the reason is recorded beside every finding it clears, and the
    convergence line counts what it cleared.
    """
    entries = []
    for path in accepted_paths(plan_dir, session):
        try:
            entries += sj.get_list(sj.load_dict(path.read_bytes()), "accepted") or []
        except OSError:
            continue
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        phrase = _norm_summary(e.get("contains", ""))
        if not (e.get("file") and phrase and str(e.get("reason", "")).strip()):
            continue
        out.append({"file": _norm(e["file"]), "contains": phrase,
                    "severity": str(e.get("severity", "")).strip().lower(),
                    "reason": str(e["reason"]).strip()})
    return out


def accepted_match(accepted, file, summary, severity):
    """The accept entry covering this finding, or None. The file must match and the
    phrase must appear in the summary; a `severity` on the entry pins that too."""
    path, summ = _norm(file or ""), _norm_summary(summary)
    sev = str(severity or "").strip().lower()
    for e in accepted or []:
        if e["file"] == path and e["contains"] in summ and \
                (not e["severity"] or e["severity"] == sev):
            return e
    return None


def accept_note(plan_dir, session, accepted):
    """Always say what the accept file did: a misnamed file was ignored in silence."""
    d = Path(plan_dir) / "_verify_state"
    read = accepted_paths(plan_dir, session)
    strays = sorted(f.name for f in d.glob(f"{session.split('.')[0]}*.accepted.json")
                    if f not in read) if d.is_dir() else []
    note = f"accept_file={'+'.join(f.name for f in read if f.is_file() or f == read[0])} entries={len(accepted)}"
    if strays:
        note += f" WARNING other accept file(s) NOT read: {', '.join(strays)}"
    sid, c = session.split(".")[0], Path(plan_dir) / "_closeouts"
    closed = max((f.stat().st_mtime for f in [*c.glob(f"{sid}.json"), *c.glob(f"{sid}.r*.json")]), default=0)
    early = [f.name for f in read if f.is_file() and f.stat().st_mtime < closed]
    if early:  # the owner accepts AFTER the session closes out; an earlier write may be the session's own
        note += f" WARNING accept file written BEFORE the session's last closeout: {', '.join(early)} (confirm you wrote it)"
    return note
