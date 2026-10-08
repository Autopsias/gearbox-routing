"""LND-12 — flaky gates, under one fail-closed rule (finish-contract.md, the s03
amendment): land never treats a check as passed on a tree where that check
already failed, unless the plan changed something since that failure.

``record`` runs on EVERY failing land-tree run of an argv gate (the first run
and the re-run, never the base run), from ``land_gate._run_argv_gate``, and saves
at once, before any early return. ``remember``, ``flake_hold`` and ``unrepaired``
only read what it wrote.

A failure names each test id and each file a collection error names
(``ERROR <path> - ...``), else the whole gate. A name is HELD (it can block)
when the plan's diff touches its file, when git cannot read that diff, or when
it is the whole gate; any other name is kept only to be named in the
red-then-green note. Each record is anchored to the plan head and the base.
Records are kept per gate (FIN-16): the same pytest id printed by two gates
(two packages, two working directories) is two records, ``"<gate>: <name>"``,
each with its own file, anchor and flaky mark. A land.json written before that
keyed them by name alone; ``_per_gate`` re-keys those under their recorded gate.

A green verdict judges each record against the tree it failed on. Same tree
(nothing changed outside ``_plans/<slug>/``): it failed, then passed, so it is a
flake, and a held one parks ``gate-flaky``. A known flake (one seen red, then
green, on one tree) clears only when the plan's OWN patch changes (not the base,
not the plan record): its file, or for a whole gate any file. A failure never
seen to pass on its own tree ends once its tree changes: a repair, or the base
fixing it.
"""

import os
import re
from pathlib import Path

import land_start as lsg
import land_state as lst
from land_state import park, save

_TESTS = re.compile(r"(?m)^(?:FAILED|ERROR) (\S+::\S+)|^(\S+::\S+) (?:FAILED|ERROR)\b")
_FILES = re.compile(r"(?m)^ERROR ([^\s:]+)(?: - |\s*$)")   # pytest collection errors


def tests(text):
    """Failing test ids (pytest's ``FAILED x::y`` / ``x::y FAILED`` lines), in order."""
    found = [a or b for a, b in _TESTS.findall(text or "")]
    return list(dict.fromkeys(found))


def _text(rec):
    try:
        return Path(rec["log"]).read_text(errors="replace") if rec.get("log") else rec.get("excerpt")
    except OSError:
        return rec.get("excerpt")


def names(gid, ids, text):
    """``[(name, file)]`` for what failed: test ids and collection-error files,
    else the whole gate (``file`` None). Nothing parseable never means nothing failed."""
    named = [(t, t.split("::")[0]) for t in ids]
    named += [(f, f) for f in dict.fromkeys(_FILES.findall(text or ""))]
    return named or [(f"{gid} (no test ids found)", None)]


def _of(rec):
    text = _text(rec)
    return names(rec["gate_id"], tests(text), text)


def key(gid, name):
    """One record per (gate, name); also how a record is shown to the operator."""
    return f"{gid}: {name}"


def _per_gate(st):
    """Re-key records an older land.json keyed by name alone (their ``gate`` kept)."""
    for field in ("plan_touched_flakes", "named_failures"):
        recs = st.get(field) or {}
        for old in [k for k, e in recs.items() if "name" not in e]:
            e = recs.pop(old)
            recs[key(e["gate"], old)] = {**e, "name": old}


def labels(pairs):
    """``[(gate, name)]`` as the operator reads them: the bare test id, or
    ``"<gate>: <name>"`` when two gates in ``pairs`` share that id."""
    gates = {}
    for g, n in pairs:
        gates.setdefault(n, set()).add(g)
    return [n if len(gates[n]) == 1 else key(g, n) for g, n in pairs]


def hold(st, gid, named, cwd=None):
    """Record one failure's names at the current plan head and base. A name already
    held stays held (a known flake stays known), and so does the gate's whole-gate
    hold: re-anchored, because a change made before this failure fixed nothing."""
    _per_gate(st)
    held = st.setdefault("plan_touched_flakes", {})
    other = st.setdefault("named_failures", {})
    at = {"gate": gid, "plan_head": st.get("plan_head"), "expected": st.get("expected"),
          "at": lst.now()}
    diff = lst.range_files(st["land_path"], st["expected"])
    land = Path(st["land_path"]).resolve()
    for name, f in named:
        k, hit = key(gid, name), None
        if k not in held and f is not None:
            rel = os.path.relpath(os.path.join(cwd or str(land), f), land)
            # FIN-17: the gate-relative path first; the raw id only when that is
            # not in the diff (never a lexicographic pick between the two)
            hit = rel if diff is None or rel in diff else (f if f in diff else None)
            if not hit:
                other[k] = {**at, "name": name}
                continue
        other.pop(k, None)
        held[k] = {**held.get(k, {"file": hit}), **at, "name": name}
    for e in held.values():
        if e["file"] is None and e["gate"] == gid:
            e.update(at)


def record(plan_dir, ctx, st, rec):
    """Every failing land-tree run, recorded and saved the moment it happens."""
    if rec["outcome"] == "fail":
        hold(st, rec["gate_id"], _of(rec), rec.get("cwd"))
        save(plan_dir, ctx, st)


def _flake(st, gid, name):
    """``name`` failed in gate ``gid``, then passed, on one tree: note it, and
    make that gate's hold known."""
    seen = st.setdefault("red_then_green", [])
    if name not in seen:
        seen.append(name)
    k = key(gid, name)
    (st.get("named_failures") or {}).pop(k, None)
    if k in (st.get("plan_touched_flakes") or {}):
        st["plan_touched_flakes"][k]["flaky"] = True


def remember(st, flaky):
    """Each gate that failed, then passed on its re-run in this land: a flake."""
    _per_gate(st)
    for r in flaky:
        for name, _ in _of(r):
            _flake(st, r["gate_id"], name)


def brief(plan_dir, st, why, failed, extra=""):
    """The ``gate-flaky`` brief over ``failed``, ``[(gate, name)]``. A held check
    may be a race the plan added, so then the only ways out are fix or
    quarantine, never a re-run."""
    held = st.get("plan_touched_flakes") or {}
    sid = lsg.open_repair(plan_dir, st)
    left = (f"repair session {sid} is still open; after the fix, retire it with "
            f"`{lsg.retire_cmd(plan_dir, sid)}`" if sid else "no repair session was added")

    def note(g, n):
        if key(g, n) not in held:
            return ""
        f = held[key(g, n)]["file"]
        return (f"  (in {f}, which this plan changed)" if f else
                "  (the gate named no test or file: held until the plan changes)")
    lines = "\n".join(f"  {t}{note(*p)}" for t, p in zip(labels(failed), failed))
    way = ("These live in files this plan changed, so the plan may have added the "
           "race. Fix or quarantine them on the plan branch. Land refuses until the "
           "plan changes each file named above (for a whole gate, any file)."
           if any(key(*p) in held for p in failed) else
           "Fix or quarantine the flaky test on the plan branch, or run "
           f"`{lsg.land_cmd(plan_dir)}` again. A flaky gate never counts as a pass on its own.")
    return (f"LAND PARKED — {why}. Nothing was pushed and {left}."
            f"\n\nFailed, then passed:\n{lines}\n\n{way}{extra}")


def park_flaky(plan_dir, ctx, st, flaky, again):
    _per_gate(st)
    gids = [r["gate_id"] for r in flaky]
    failed = [(r["gate_id"], n) for r in flaky for n, _ in _of(r)]
    held = st.get("plan_touched_flakes") or {}
    walls = ", ".join(f"{g} {again[g]['wall_s']} s on the re-run" for g in gids)
    return park(plan_dir, ctx, st, "gate-flaky",
                brief(plan_dir, st, f"{gids} failed, then passed when land ran them "
                      "again on the same tree", failed, f"\n\n{walls}"),
                failed=gids, tests=labels(failed),
                plan_touched=sorted(t for t, p in zip(labels(failed), failed)
                                    if key(*p) in held))


def _patch(root, base, head, f):
    """The plan's patch for ``f`` (``base..head``), without line numbers or blob ids,
    so the base moving elsewhere in ``f`` does not read as the plan changing it.
    None when git cannot say."""
    rc, out, _ = lst.git(["diff", "--no-renames", "-U0", base, head, "--", f], root, strip=False)
    if rc != 0:
        return None
    lines = out.splitlines()
    at = next((i for i, ln in enumerate(lines) if ln.startswith("@@")), 0)
    return [ln for ln in lines[at:] if not ln.startswith(("@@", "index "))]


def changed(ctx, st, e):
    """``(tree, own)``: the files changed since ``e`` (a record, or a round's
    candidate) outside ``_plans/<slug>/``, and those of them whose plan patch
    (base to plan head) is different now: the plan's own change, judged by
    content, so main editing the same file neither hides nor fakes it. The plan
    head holds the base (land merges it in first), so its diff is the tree's.
    None for both when git cannot say; a file git cannot diff is not ``own``."""
    a = lst.range_files(ctx["root"], e.get("plan_head"), st.get("plan_head"))
    if a is None:
        return None, None
    tree = {p for p in a if not p.startswith(f"_plans/{ctx['slug']}/")}
    then = (ctx["root"], e.get("expected"), e.get("plan_head"))
    now = (ctx["root"], st.get("expected"), st.get("plan_head"))
    own = set()
    for f in tree:
        old, new = _patch(*then, f), _patch(*now, f)
        if old is not None and new is not None and old != new:
            own.add(f)
    return tree, own


def _same_round(ctx, st):
    rounds = (st.get("repair") or {}).get("rounds") or []
    same = rounds and not changed(ctx, st, rounds[-1]["candidate"])[0]
    return rounds[-1]["directive"]["failing"] if same else []


def unrepaired(ctx, st):
    """The last repair round's failure names, when its tree is unchanged (outside
    the plan record): the gates went green, but nothing repaired them. The round
    keeps the excerpt, not the log (a later run overwrote it)."""
    return [n for f in _same_round(ctx, st)
            for n, _ in names(f["gate"], f["tests"], f["excerpt"])]


def flake_hold(plan_dir, ctx, st):
    """Called after a green verdict: judge every record (module docstring), then a
    ``gate-flaky`` park while any hold is unresolved, else None. A diff git cannot
    answer counts as the same tree (fail closed)."""
    _per_gate(st)
    for t, e in list((st.get("named_failures") or {}).items()):
        if not changed(ctx, st, e)[0]:
            _flake(st, e["gate"], e["name"])
        st["named_failures"].pop(t, None)
    held = st.get("plan_touched_flakes") or {}
    for t, e in list(held.items()):
        tree, own = changed(ctx, st, e)
        if not tree:
            _flake(st, e["gate"], e["name"])
        elif not e.get("flaky") or (own and (e["file"] is None or e["file"] in own)):
            del held[t]
    save(plan_dir, ctx, st)
    if not held:
        return None
    pairs = sorted((e["gate"], e["name"]) for e in held.values())
    return park(plan_dir, ctx, st, "gate-flaky",
                brief(plan_dir, st, "the gates are green, but a check this plan changed "
                      "failed on an earlier land and nothing changed it since", pairs),
                failed=sorted({g for g, _ in pairs}), tests=sorted(labels(pairs)),
                plan_touched=sorted(labels(pairs)))


def flaky_note(st):
    """One line for any later review brief of this plan: every red-then-green check."""
    found = st.get("red_then_green") or []
    return f"Failed, then passed on a re-run (flaky): {', '.join(found)}" if found else None
