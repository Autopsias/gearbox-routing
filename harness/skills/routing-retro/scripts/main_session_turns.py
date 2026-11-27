#!/usr/bin/env python3
"""main_session_turns.py — read the main-session turn ledger of the model nudge (ADR 0007).

Prints JSON: per task type x model x effort the turns, sessions, pushback rate, moved-up-after rate,
nudges shown, followed share and silent would-nudge count; malformed and skipped line counts;
`table_proposals` (evidence only: this script never edits hooks/model_nudge_table.json); `health`
and the `display_verdict` (broken, wait, keep or silent) for the nudge display.

Field names, enums and the table come from hooks/model_nudge_lib.py, never from a copy here.

Usage:
  main_session_turns.py [--ledger PATH] [--since YYYY-MM-DD] [--table PATH]
                        [--include-headless] [--now ISO_UTC]
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hooks"))
import model_nudge_lib as lib  # noqa: E402

# Thresholds: the owner can change them here (named constants, one place).
MIN_TURNS = 20             # a cell, or a lower/raise test, needs this many turns
LOWER_MIN_SESSIONS = 5     # lower a row: turns below the advice in at least this many sessions
RAISE_MOVED_UP_RATE = 0.30  # raise a row: moved-up-after rate at the advised step at or above this
RULE_MIN_SHOWS = 10        # mark a rule wrong: at least this many shows of one nudge kind for one type
RULE_IGNORED_RATE = 0.80   # ... ignored in at least this share
VERDICT_MIN_NUDGES = 20    # fewer nudges shown than this: wait
VERDICT_MIN_KNOWN = 20     # fewer KNOWN followed/ignored outcomes than this: wait (an unknown outcome is not 0%)
VERDICT_KEEP_FOLLOWED = 0.25  # followed share at or above this: keep, else silent
BROKEN_MODEL_NULL_SHARE = 0.20  # a share of lines with no model, or an id the table cannot place, above this: broken
BROKEN_QUIET_DAYS = 7      # no line in the last N days of a window this long or longer: broken


# Effort sources that are measured (not the settings.json guess); taken from the hook library.
MEASURED_EFFORT_SOURCES = tuple(x for x in lib.EFFORT_SOURCES if x != "settings")


def _parse_ts(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def read_lines(path, since, include_headless, table):
    """Classify every ledger line. Returns (kept records sorted by session and turn, counters)."""
    c = {"lines": 0, "malformed": 0, "malformed_by_field": {}, "before_since": 0, "skipped": 0,
         "skipped_entrypoint": {}, "skipped_non_owner_switch": {}}
    kept = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        c["ledger_missing"] = True
        return kept, c
    valid = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        c["lines"] += 1
        rec, bad = _parse_line(raw)
        if not bad:
            valid.append(rec)
            continue
        c["malformed"] += 1
        for name in {b.split(" ", 1)[0] for b in bad}:
            c["malformed_by_field"][name] = c["malformed_by_field"].get(name, 0) + 1
    # Per-turn facts are judged over ALL valid lines in turn order; a cut must not hide the turn that holds the answer.
    valid.sort(key=lambda r: (r["session"], r["turn"]))
    for rec in valid:
        rec["_skip"] = _skip_reason(rec, since, include_headless)
    _annotate(valid, table)
    for rec in valid:
        reason = rec["_skip"]
        if reason == "since":
            c["before_since"] += 1
        elif reason == "entrypoint":
            c["skipped"] += 1
            c["skipped_entrypoint"][rec["entrypoint"]] = c["skipped_entrypoint"].get(rec["entrypoint"], 0) + 1
        elif reason == "switch":
            c["skipped"] += 1
            key = rec.get("switch_source") or "(none)"
            c["skipped_non_owner_switch"][key] = c["skipped_non_owner_switch"].get(key, 0) + 1
        else:
            kept.append(rec)
    return kept, c


def _parse_line(raw):
    """(record, problems) for one ledger line; problems is empty when the line is valid."""
    try:
        rec = json.loads(raw)
        bad = lib.turn_problems(rec)
    except ValueError:
        return None, ["json is not valid"]
    if not bad:
        try:
            rec["_t"] = _parse_ts(rec["ts"])
        except ValueError:  # shape-valid but impossible date
            bad = ["ts is not a date"]
    return rec, bad


def _skip_reason(rec, since, include_headless):
    if since and rec["ts"][:10] < since:
        return "since"
    if rec["entrypoint"] != lib.INTERACTIVE_ENTRYPOINT and not include_headless:
        return "entrypoint"
    if lib.model_changed(rec) and rec.get("switch_source") not in lib.OWNER_SWITCH_SOURCES:
        return "switch"
    return None


def _alias(table, rec):
    return lib.alias_of(table, rec.get("model")) or ("null" if rec.get("model") is None else "other")


def _rank(ladder, value):
    return ladder.index(value) if value in ladder else None


def _moved_up(table, cur, nxt):
    """True/False when the next turn is comparable, None when it cannot be judged."""
    if (nxt["session"] != cur["session"] or nxt["turn"] != cur["turn"] + 1 or nxt["_skip"]
            or nxt["task_type"] != cur["task_type"]):
        return None  # a gap, a dropped line or a task change is not a move up during this work
    model = (_rank(table["model_ladder"], _alias(table, cur)), _rank(table["model_ladder"], _alias(table, nxt)))
    effort = (_rank(table["effort_ladder"], cur.get("effort")), _rank(table["effort_ladder"], nxt.get("effort")))
    measured = all(r.get("effort_source") in MEASURED_EFFORT_SOURCES for r in (cur, nxt))  # an inferred effort is not evidence
    if None not in model and model[1] > model[0]:
        return True  # a rise in model alone is enough
    if not measured or None in effort or None in model:
        return None  # no rise seen, and a side could not be compared: unknown, never "did not move up"
    return effort[1] > effort[0]


def _annotate(recs, table):
    """Add the per-turn facts that need the neighbouring turn of the same session (recs sorted by session, turn)."""
    pending, sess = None, None
    for i, r in enumerate(recs):
        prev = recs[i - 1] if i and recs[i - 1]["session"] == r["session"] else None
        if r["session"] != sess:
            pending, sess = None, r["session"]
        r["_followed"] = None
        r["_pushback"] = None  # unknown until the next turn is seen: a missing next turn is not "no pushback"
        # a rise in pushback_count at turn t complains about turn t-1's answer; no t-1 line, no judgement
        if prev and prev["turn"] == r["turn"] - 1:
            prev["_pushback"] = r["pushback_count"] > prev["pushback_count"]
        if prev and prev["turn"] != r["turn"] - 1:
            pending = None  # a turn is missing: this verdict (and any pending nudge) cannot be matched; unknown, not ignored
        if pending is not None and r.get("followed_prev_nudge") is not None:
            pending["_followed"], pending = r["followed_prev_nudge"], None  # a machine turn in between is skipped by the hook
        if r["nudge_shown"]:
            pending = r  # a newer shown nudge replaces the pending one
        nxt = recs[i + 1] if i + 1 < len(recs) else None
        r["_moved_up"] = _moved_up(table, r, nxt) if nxt else None


def _rate(num, den):
    return round(num / den, 4) if den else None


def _count(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _stats(recs):
    ups = [r["_moved_up"] for r in recs if r["_moved_up"] is not None]
    shown = [r for r in recs if r["nudge_shown"]]
    known = [r["_followed"] for r in shown if r["_followed"] is not None]
    pb = [r["_pushback"] for r in recs if r["_pushback"] is not None]
    return {"turns": len(recs), "sessions": len({r["session"] for r in recs}),
            "pushbacks": sum(pb), "pushback_n": len(pb),  # raw counts over turns whose next turn is known: proposals compare raw fractions
            "pushback_sessions": len({r["session"] for r in recs if r["_pushback"] is not None}),
            "pushback_rate": _rate(sum(pb), len(pb)),
            "moved_up_after_rate": _rate(sum(ups), len(ups)), "moved_up_after_n": len(ups), "moved_ups": sum(ups),
            "nudges_shown": len(shown), "followed_share": _rate(sum(known), len(known)), "followed_n": len(known),
            "effort_sources": _count(r.get("effort_source") or "none" for r in recs),
            "silent_would_nudge": sum(1 for r in recs if r["would_nudge"] and not r["nudge_shown"])}


def build_cells(recs, table):
    groups = {}
    for r in recs:
        groups.setdefault((r["task_type"], _alias(table, r), r.get("effort") or "null"), []).append(r)
    cells = []
    for (t, m, e), g in sorted(groups.items()):
        s = _stats(g)
        cells.append({"task_type": t, "model": m, "effort": e, **s,
                      "status": "ok" if s["turns"] >= MIN_TURNS else "below_min_n"})
    return cells


def _steps(table, row):
    """(model, effort) pairs: the advised range, and the one-step-below pairs (model one rung down, effort one down)."""
    ml, el = table["model_ladder"], table["effort_ladder"]
    lo, hi = el.index(row["effort_start"]), el.index(row["effort_top"])
    advised = {(row["model"], e) for e in el[lo:hi + 1]}
    below = set()
    mi = ml.index(row["model"])
    if mi:
        below.update((ml[mi - 1], e) for e in el)
    if lo:
        below.add((row["model"], el[lo - 1]))
    return advised, below


def _pick(recs, table, task, pairs):
    """Turns of one task type at any pair; a None in a pair matches any value (an advised step may name only a model or only an effort)."""
    return [r for r in recs if r["task_type"] == task
            and any(m in (None, _alias(table, r))
                    and (e is None or (r.get("effort") == e and r.get("effort_source") in MEASURED_EFFORT_SOURCES))  # a settings guess is not evidence
                    for m, e in pairs)]


def lower_candidates(recs, table):
    out = []
    for task, row in table["types"].items():
        if not row:
            continue
        advised, below = _steps(table, row)
        low, adv = _stats(_pick(recs, table, task, below)), _stats(_pick(recs, table, task, advised))
        if not low["turns"]:
            continue
        # size is counted over turns whose pushback is known: an unknown outcome is not evidence
        ok = (low["pushback_n"] >= MIN_TURNS and low["pushback_sessions"] >= LOWER_MIN_SESSIONS and adv["pushback_n"] > 0
              and low["pushbacks"] * adv["pushback_n"] <= adv["pushbacks"] * low["pushback_n"])
        out.append({"kind": "lower_row", "task_type": task, "status": "proposed" if ok else "below_min_n",
                    "n": low["turns"], "sessions": low["sessions"], "pushback_rate": low["pushback_rate"],
                    "pushback_n": low["pushback_n"], "advised_step_pushback_rate": adv["pushback_rate"],
                    "advised_step_turns": adv["turns"], "advised_step_pushback_n": adv["pushback_n"],
                    "needs": "%d turns over %d sessions, and an advised-step baseline" % (MIN_TURNS, LOWER_MIN_SESSIONS)})
    return out


def raise_candidates(recs, table):
    steps = {}
    for r in recs:
        adv = r.get("advised")
        # lines passed lib.turn_problems: a non-empty advised holds a model, an effort or both (ADR 0007)
        if r["would_nudge"] and r.get("nudge_kind") != "stuck" and isinstance(adv, dict) and adv:
            steps.setdefault(r["task_type"], set()).add((adv.get("model"), adv.get("effort")))
    out = []
    for task, pairs in sorted(steps.items()):
        for model, effort in sorted(pairs, key=lambda p: (p[0] or "", p[1] or "")):
            s = _stats(_pick(recs, table, task, {(model, effort)}))
            ok = s["moved_up_after_n"] >= MIN_TURNS and s["moved_ups"] >= RAISE_MOVED_UP_RATE * s["moved_up_after_n"]
            out.append({"kind": "raise_row", "task_type": task, "advised_model": model, "advised_effort": effort,
                        "status": "proposed" if ok else "below_min_n", "n": s["moved_up_after_n"],
                        "sessions": s["sessions"], "moved_up_after_rate": s["moved_up_after_rate"]})
    return out


def wrong_rule_candidates(recs):
    groups = {}
    for r in recs:
        if r["nudge_shown"] and r["_followed"] is not None and r.get("nudge_kind"):
            groups.setdefault((r["task_type"], r["nudge_kind"]), []).append(r["_followed"])
    out = []
    for (task, kind), g in sorted(groups.items()):
        ignored_n = sum(1 for f in g if not f)
        ignored = _rate(ignored_n, len(g))
        ok = len(g) >= RULE_MIN_SHOWS and ignored_n >= RULE_IGNORED_RATE * len(g)
        out.append({"kind": "rule_wrong", "task_type": task, "nudge_kind": kind,
                    "status": "proposed" if ok else "below_min_n", "n": len(g), "ignored_rate": ignored})
    return out


def health_and_verdict(recs, now, since, table, counts=None):
    counts = counts or {}
    n = len(recs)
    shown = [r for r in recs if r["nudge_shown"]]
    known = [r["_followed"] for r in shown if r["_followed"] is not None]
    last7 = sum(1 for r in recs if _parse_ts(r["ts"]) >= now - timedelta(days=BROKEN_QUIET_DAYS))
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc) if since else (
        _parse_ts(recs[0]["ts"]) if recs else now)
    start = min([start] + [_parse_ts(r["ts"]) for r in recs])
    days = (now - start).total_seconds() / 86400
    null_share = _rate(sum(1 for r in recs if r.get("model") is None), n)
    unknown_share = _rate(sum(1 for r in recs if r.get("model") is not None and lib.alias_of(table, r["model"]) is None), n)
    health = {"lines": n, "model_null_share": null_share, "model_unrecognized_share": unknown_share, "effort_null_share": _rate(sum(1 for r in recs if r.get("effort") is None), n),
              "lines_last_7_days": last7, "window_days": round(days, 2)}
    followed = _rate(sum(known), len(known))
    unusable = counts.get("lines", 0) - counts.get("before_since", 0) > 0 and not n  # lines in the window, all malformed or skipped
    if (counts.get("ledger_missing") or unusable or (n and (null_share + unknown_share) > BROKEN_MODEL_NULL_SHARE)
            or (days >= BROKEN_QUIET_DAYS and last7 == 0)):
        verdict = "broken"  # re-probe the payload facts of s02: the hook fails open, so silence is not 'no data'
    elif len(shown) < VERDICT_MIN_NUDGES or len(known) < VERDICT_MIN_KNOWN:
        verdict = "wait"
    elif sum(known) >= VERDICT_KEEP_FOLLOWED * len(known):
        verdict = "keep"
    else:
        verdict = "silent"
    health.update(ledger_missing=bool(counts.get("ledger_missing")), nudges_shown=len(shown), followed_share=followed, followed_n=len(known))
    return health, verdict


def analyse(path, since=None, include_headless=False, table_path=None, now=None):
    table = lib.load_table(table_path)
    if table is None:
        return {"error": "advice table is missing or invalid", "display_verdict": "broken"}
    recs, counts = read_lines(path, since, include_headless, table)
    health, verdict = health_and_verdict(recs, now or datetime.now(timezone.utc), since, table, counts)
    return {"ledger": str(path), "since": since, "include_headless": include_headless, **counts,
            "cells": build_cells(recs, table),
            "table_proposals": lower_candidates(recs, table) + raise_candidates(recs, table) + wrong_rule_candidates(recs),
            "health": health, "display_verdict": verdict}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", default=str(lib.LEDGER))
    ap.add_argument("--since", default=None, help="lines with ts on or after YYYY-MM-DD (inclusive)")
    ap.add_argument("--table", default=None, help="advice table path (default: the hook's table)")
    ap.add_argument("--include-headless", action="store_true", help="count non-interactive lines (proofs only)")
    ap.add_argument("--now", default=None, help="UTC ISO time to judge the last 7 days from (tests)")
    a = ap.parse_args(argv)
    if a.since:
        try:
            datetime.strptime(a.since, "%Y-%m-%d")
        except ValueError:
            ap.error("--since must be YYYY-MM-DD")
    now = _parse_ts(a.now) if a.now else None
    json.dump(analyse(a.ledger, a.since, a.include_headless, a.table, now), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
