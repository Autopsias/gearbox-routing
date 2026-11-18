#!/usr/bin/env python3
"""ENG-04: one `status_changed` row in run.ndjson for every status change that
reaches PLAN.html, and a reader that counts the from-to pairs.

The rows are a record for the 2026-12-10 measurement. No pair is enforced yet:
nothing here refuses a change. The rows are a lower bound: a crash between the
HTML write and the row write loses the row.

Writers (article_block.apply_mutation, plan_mutate._run) call `log_status_change`
after the HTML write succeeded. A failed row write only prints one line on stderr.
`carry_over_state` copies statuses across a rebuild and logs nothing.

Reader: `status_pairs.py RUN.ndjson|PLAN_DIR ...` prints the files and rows read,
the `status_changed` rows, unparsed lines, each from-to pair with its count, the
pairs outside EXPECTED (a first draft) and, for a plan directory, each article
whose last logged `to` differs from the status in PLAN.html. Zero rows prints
NO DATA. It always exits 0: it reports, it does not judge.
"""

import sys
from collections import Counter
from pathlib import Path

import safe_json as sj

STATUSES = {"TODO", "DOING", "DONE", "BLOCKED", "DEFERRED", "WONTFIX",
            "AWAITS_REVIEW", "PARTIAL"}

# FIRST DRAFT, from the apply_mutation call sites (run.py, rework.py, replan.py,
# verify.py, verifier_park.py, empty_surface.py), plan_mutate status ops
# (retire -> WONTFIX) and eng-01/eng-02 (begin never leaves DONE/WONTFIX/DEFERRED).
EXPECTED = {
    ("TODO", "DOING"), ("TODO", "DONE"), ("TODO", "BLOCKED"), ("TODO", "WONTFIX"),
    ("TODO", "DEFERRED"),
    ("DOING", "DONE"), ("DOING", "PARTIAL"), ("DOING", "BLOCKED"),
    ("DOING", "AWAITS_REVIEW"), ("DOING", "TODO"), ("DOING", "WONTFIX"),
    ("PARTIAL", "DOING"), ("PARTIAL", "BLOCKED"), ("PARTIAL", "TODO"),
    ("PARTIAL", "DONE"), ("PARTIAL", "WONTFIX"),
    ("BLOCKED", "DOING"), ("BLOCKED", "TODO"), ("BLOCKED", "PARTIAL"),
    ("BLOCKED", "WONTFIX"),
    ("AWAITS_REVIEW", "DOING"), ("AWAITS_REVIEW", "DONE"),
    ("AWAITS_REVIEW", "BLOCKED"), ("AWAITS_REVIEW", "TODO"),
    ("AWAITS_REVIEW", "PARTIAL"), ("AWAITS_REVIEW", "WONTFIX"),
    ("DONE", "TODO"), ("DONE", "WONTFIX"),
    ("DEFERRED", "TODO"), ("DEFERRED", "WONTFIX"),
}


def log_status_change(html_path, aid, old, new):
    """Append one row to the plan directory's run.ndjson. Never raises."""
    if old == new:
        return
    try:
        plan_dir = Path(html_path).parent
        if not (plan_dir / "manifest.json").is_file():
            return
        from run_state_io import log_event  # late: keeps article_block stdlib-only
        log_event(plan_dir, "status_changed", article=aid, **{"from": old, "to": new})
    except Exception as exc:  # noqa: BLE001 - a lost row must not block the status write
        print(f"status_pairs: row not written for {aid}: {exc}", file=sys.stderr)


def _read_rows(path):
    rows, bad = [], 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        rec = sj.load_dict(line)
        if rec is None:
            bad += 1
        else:
            rows.append(rec)
    return rows, bad


def _html_statuses(plan_dir):
    html = Path(plan_dir) / "PLAN.html"
    if not html.is_file():
        return {}
    import article_block as ab
    return ab.read_all_statuses(html.read_bytes().decode("utf-8"))


def report(paths):
    out, pairs, last = [], Counter(), {}
    files = rows_n = changed = bad_n = 0
    html_states = {}
    for p in map(Path, paths):
        log = p / "run.ndjson" if p.is_dir() else p
        if not log.is_file():
            out.append(f"missing: {log}")
            continue
        files += 1
        rows, bad = _read_rows(log)
        rows_n += len(rows)
        bad_n += bad
        if p.is_dir():
            html_states[p] = _html_statuses(p)
        for r in rows:
            if r.get("event") != "status_changed":
                continue
            changed += 1
            pairs[(r.get("from"), r.get("to"))] += 1
            last[(p, r.get("article"))] = r.get("to")
    out.insert(0, f"files read: {files}\nrows read: {rows_n}\n"
                  f"status_changed rows: {changed}\nlines not parsed: {bad_n}")
    if not changed:
        out.append("NO DATA")
        return "\n".join(out)
    out.append("pairs (from -> to: count):")
    for (a, b), n in sorted(pairs.items(), key=lambda kv: (str(kv[0]), kv[1])):
        flags = []
        if (a, b) not in EXPECTED:
            flags.append("NOT EXPECTED")
        if a not in STATUSES or b not in STATUSES:
            flags.append("UNKNOWN STATUS WORD")
        out.append(f"  {a} -> {b}: {n}" + (f"  [{', '.join(flags)}]" if flags else ""))
    for (p, aid), to in sorted(last.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        cur = html_states.get(p, {}).get(aid)
        if p in html_states and cur is not None and cur != to:
            out.append(f"DIFFERENCE {aid}: last logged {to}, PLAN.html {cur}")
    return "\n".join(out)


def main(argv):
    print(report(argv) if argv else "usage: status_pairs.py RUN.ndjson|PLAN_DIR ...")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
