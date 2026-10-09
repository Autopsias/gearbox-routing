#!/usr/bin/env python3
"""repo-health renderer: scorecard.json + history.jsonl -> HEALTH.html.

Invoked as `health.py render` (health.py adds this dir to sys.path).
Deterministic string-substitution over assets/template.html; no external deps.
Re-rendering the same run (same date+commit) replaces its history entry
instead of appending a duplicate.
"""
import html
import json
from pathlib import Path

from common import parse_json, typed
from health import LAYERS, health_dir, load_card, staleness

GLYPH = {"pass": "✓", "warn": "!", "fail": "✕", "na": "–", "pending": "○"}
CHIP = {"pass": "c-pass", "warn": "c-warn", "fail": "c-fail", "na": "c-na",
        "pending": "c-pending"}
VERDICT_CLASS = {"AT RISK": "v-crit", "NEEDS ATTENTION": "v-warn", "HEALTHY": "v-good"}
VERDICT_DOT = {"AT RISK": "d-red", "NEEDS ATTENTION": "d-amber", "HEALTHY": "d-green"}
SHORT = {
    "sec.workflow-permissions": "permissions", "sec.action-pinning": "action pinning",
    "sec.dangerous-workflow": "workflow patterns", "sec.tracked-sensitive": "tracked secrets",
    "sec.secrets-history": "history scan", "sec.dep-vulns": "dep vulns",
    "sec.sast": "SAST", "sec.dep-update-config": "dep updates",
    "sec.vendor-pins": "vendor pins", "sec.diff-review": "diff review",
    "sec.supply-chain": "supply chain", "ci.server-side-gate": "server-side gate",
    "cq.lint": "lint", "cq.complexity": "complexity", "cq.file-size": "file size",
    "cq.function-length": "function length",
    "test.suite": "suite green", "test.runtime": "runtime",
    "test.collection-cost": "collection", "test.parallel-safety": "parallel-safe",
    "ci.wall-clock": "CI wall-clock",
    "ci.timeouts": "timeouts", "ci.concurrency": "concurrency",
    "ci.caching": "caching", "ci.retention": "retention", "ci.pre-commit": "local gate",
    "cq.slop": "slop scan", "cq.ratchet": "ratchet",
    "hyg.dep-unused": "unused deps", "hyg.dep-freshness": "dep freshness",
    "hyg.unmerged-work": "forgotten work", "hyg.notebook-outputs": "notebooks",
    "ai.agents-md": "AGENTS.md", "ai.verify-command": "verify loop",
    "ai.lockfiles": "lockfiles",
    "hyg.readme": "README", "hyg.tracked-junk": "junk", "hyg.large-files": "large files",
    "hyg.todo-density": "TODOs", "hyg.activity": "activity",
}


def esc(s):
    return html.escape(str(s), quote=True)


VALUE = {"pass": 1.0, "warn": 0.5, "fail": 0.0}          # na/pending excluded
WEIGHT = {"blocking": 2, "advisory": 1}


def blocking(c):
    """Does this check count as blocking? Anything not explicitly `advisory` does.

    Asked this way round, and never as `WEIGHT[c["tier"]]` or `tier ==
    "blocking"`, because a tier outside the vocabulary has to land SOMEWHERE and
    the light side is the side that hides it. `WEIGHT['']` was a straight
    KeyError out of the renderer; `tier == "blocking"` was worse — silent, and it
    let an untiered failure skip the score cap. certified() deliberately does not
    repair a bad tier (a measured `fail` is not thrown away over a routing
    problem), so this is where the collector's score stops depending on one.
    """
    return c["tier"] != "advisory"


def points(checks):
    """Weighted score 0-100 over applicable+run checks; None if none apply."""
    scored = [c for c in checks if c["status"] in VALUE]
    if not scored:
        return None
    w = [WEIGHT["blocking"] if blocking(c) else WEIGHT["advisory"] for c in scored]
    got = sum(wi * VALUE[c["status"]] for wi, c in zip(w, scored))
    return round(100 * got / sum(w))


def overall_score(checks):
    """Gated overall: blocking fail caps at 59; blocking warn/pending caps at 89."""
    raw = points(checks) or 0
    if any(blocking(c) and c["status"] == "fail" for c in checks):
        return min(raw, 59), "capped: blocking failure"
    if any(blocking(c) and c["status"] in ("warn", "pending") for c in checks):
        return min(raw, 89), "capped: blocking check unresolved"
    return raw, ""


def band(n):
    return "red" if n < 50 else ("amber" if n < 90 else "green")


def score(card):
    checks = card["checks"]
    bf = [c for c in checks if blocking(c) and c["status"] == "fail"]
    af = [c for c in checks if not blocking(c) and c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    pend = [c for c in checks if c["status"] == "pending"]
    layers = {}
    for key in LAYERS:
        mine = [c for c in checks if c["layer"] == key]
        if any(blocking(c) and c["status"] == "fail" for c in mine):
            layers[key] = "red"
        elif any(c["status"] in ("fail", "warn", "pending") for c in mine):
            layers[key] = "amber"
        else:
            layers[key] = "green"
    pend_blocking = any(blocking(c) for c in pend)
    verdict = "AT RISK" if bf else (
        "NEEDS ATTENTION" if af or pend_blocking else "HEALTHY")
    return bf, af, warns, pend, layers, verdict


def stats_html(bf, af, warns, passes):
    blocks = [(len(bf), "Blocking", "crit"), (len(af), "Advisory fails", "warn"),
              (len(warns), "Warnings", "warn"), (passes, "Pass", "ok")]
    return "\n".join(
        f'    <div class="stat {cls if n else ""}"><div class="n num">{n}</div>'
        f'<div class="l">{label}</div></div>'
        for n, label, cls in blocks)


def board_html(card, layers, history):
    state_word = {"green": ("clear", "st-green"), "amber": ("open items", "st-amber"),
                  "red": ("blocking", "st-red")}
    out = []
    for key, name in LAYERS.items():
        mine = [c for c in card["checks"] if c["layer"] == key]
        lsc = points(mine)
        lscore = f'<span class="lscore num">{lsc}</span>' if lsc is not None else ""
        word, wcls = state_word[layers[key]]
        recent = history[-8:]
        sq = '<span class="sq"></span>' * (8 - len(recent))
        sq += "".join(
            f'<span class="sq q-{typed(h.get("layers"), dict, {}).get(key, "")}"></span>'
            for h in recent)
        sq += f'<span class="sq q-{layers[key]}"></span>'
        chips = "".join(
            f'<span class="chip {CHIP[c["status"]]}" title="{esc(c["id"])} — {esc(c["detail"])}">'
            f'<span class="g">{GLYPH[c["status"]]}</span>{esc(SHORT.get(c["id"], c["id"]))}</span>'
            for c in mine)
        out.append(
            f'    <section class="layer"><div class="layer-head">'
            f'<h3>{esc(name)}</h3><span class="layer-state {wcls}">{word}</span>'
            f'<div class="trend">{lscore}{sq}</div></div>'
            f'<div class="chips">{chips}</div></section>')
    return "\n".join(out)


def finding_html(c):
    fcls = "f-fail" if c["status"] == "fail" else "f-warn"
    glyph = GLYPH[c["status"]]     # total: certified() proves the status is one
    detail, paths = esc(c["detail"]), ""
    if ": " in c["detail"]:
        label, tail = c["detail"].split(": ", 1)
        segs = tail.split(", ")
        if segs and "/" in segs[0] and sum("/" in s for s in segs) > len(segs) / 2:
            detail = esc(label) + ":"
            paths = f'<code class="paths">{esc(tail)}</code>'
    fix = f'<div class="ffix">→ fix: <code>{esc(c["fix"])}</code></div>' if c["fix"] else ""
    layer = esc(LAYERS.get(c["layer"], c["layer"]).lower())
    return (f'    <li class="finding {fcls}"><div class="fhead">'
            f'<span class="fglyph">{glyph}</span>'
            f'<span class="ftitle">{esc(c["title"])}</span>'
            f'<span class="fid mono">{esc(c["id"])} · {layer}</span></div>'
            f'<p class="fbody">{detail}{paths}</p>{fix}</li>')


def findings_list(items, empty_msg):
    if not items:
        return f'  <div class="allclear">{esc(empty_msg)}</div>'
    return ('  <ul class="findings">\n'
            + "\n".join(finding_html(c) for c in items) + "\n  </ul>")


def decision_html(bf, af, warns, pend_blocking):
    if bf or pend_blocking:
        n = len(bf) + len(pend_blocking)
        intro = "Blocking items are open. Pick one:"
        opts = [f'<span class="rec">Recommended:</span> fix (or run) the {n} blocking '
                'item(s) above now — each lists its command or fix route.',
                'Then batch the advisory items through the routed skills '
                '(/code-quality --fix, /test-orchestrate, /ci-orchestrate).',
                'Accept the risk explicitly and re-run repo-health within a week.']
    elif af or warns:
        intro = "No blocking items. Pick one:"
        opts = ['<span class="rec">Recommended:</span> dispatch the routed fixes for the '
                'advisory items above in one batch.',
                'Accept the current state and re-run repo-health in ~4 weeks.',
                'Adopt the missing gates (ratchet/CI) so these stop recurring.']
    else:
        intro = "Everything green."
        opts = ['<span class="rec">Recommended:</span> nothing to do. '
                'Re-run repo-health in ~4 weeks or after a large change.']
    lis = "\n".join(f"      <li>{o}</li>" for o in opts)
    return f"    <p>{intro}</p>\n    <ol>\n{lis}\n    </ol>"


def history_html(history, card, verdict, score_n, stale=""):
    """The trend strip, with every unreliable run marked AS unreliable.

    A run whose page carried a staleness banner is plotted with a `?` and the
    reason on hover. Without it the strip is a row of equally-confident dots and
    the reader cannot tell a measured 82 from one taken against a tree that no
    longer exists.
    """
    runs = [(h.get("ts") or "", h.get("verdict"), h.get("score"), "",
             typed(h.get("stale"), str, "")) for h in history[-11:]]
    runs.append((card["date"], verdict, score_n, "now", stale))
    return "\n".join(
        f'    <div class="run {now}{" unsure" if st else ""}"'
        f'{f" title={esc(st)!r}" if st else ""}>'
        f'<span class="dot {VERDICT_DOT.get(v, "")}"></span>'
        f'<span class="d num">{"" if s is None else s}{"?" if st else ""}</span>'
        f'<span class="d">{esc(ts[5:] if len(ts) > 5 else ts)}</span></div>'
        for ts, v, s, now, st in runs)


def load_history(repo, card):
    f = health_dir(repo) / "history.jsonl"
    if not f.exists():
        return []
    # history.jsonl is append-only RUNTIME output, rewritten on every render, so a
    # final line truncated by an interrupted write is realistic — and it used to
    # take the whole render down with a JSONDecodeError. Each line is parsed and
    # type-checked in one step; anything that is not an object is dropped, and the
    # keys are read with .get() because a line can be short as well as unparseable.
    hist = [h for h in (parse_json(ln) for ln in f.read_text().splitlines() if ln.strip())
            if h is not None]
    # re-render of the same run: drop its old entry, it gets re-appended
    return [h for h in hist
            if not (h.get("ts") == card["date"] and h.get("commit") == card["commit"])]


def render(repo):
    card = load_card(repo)
    bf, af, warns, pend, layers, verdict = score(card)
    score_n, cap_note = overall_score(card["checks"])
    history = load_history(repo, card)
    pend_blocking = [c for c in pend if blocking(c)]
    pend_advisory = [c for c in pend if not blocking(c)]
    for c in pend:
        c["detail"] = "NOT RUN — " + c["detail"]
    passes = sum(1 for c in card["checks"] if c["status"] == "pass")
    nas = sum(1 for c in card["checks"] if c["status"] == "na")
    checked = (f"{len(card['checks'])} checks · {passes} pass · {len(pend)} not run · "
               f"{nas} not applicable in this repo")
    stale = staleness(repo, card)
    stale_html = f'<div class="stale">! {esc(stale)}</div>' if stale else ""
    tpl = (Path(__file__).parent.parent / "assets" / "template.html").read_text()
    page = (tpl.replace("{{TITLE}}", esc(f"{card['repo']} Health"))
            .replace("{{REPO}}", esc(card["repo"]))
            .replace("{{DATE}}", esc(card["date"]))
            .replace("{{COMMIT}}", esc(card["commit"]))
            .replace("{{REPO_PATH}}", esc(card["repo_path"]))
            .replace("{{STALE}}", stale_html)
            .replace("{{VERDICT}}", esc(verdict))
            .replace("{{VERDICT_CLASS}}", VERDICT_CLASS[verdict])
            .replace("{{SCORE}}", str(score_n))
            .replace("{{SCORE_BAND}}", band(score_n))
            .replace("{{SCORE_NOTE}}", esc(cap_note or "aim: 100"))
            .replace("{{CHECKED_LINE}}", esc(checked))
            .replace("<!-- INSERT_STATS -->", stats_html(bf, af, warns, passes))
            .replace("<!-- INSERT_BOARD -->", board_html(card, layers, history))
            .replace("<!-- INSERT_BLOCKING_LIST -->",
                     findings_list(bf + pend_blocking, "No blocking findings."))
            .replace("<!-- INSERT_ADVISORY_LIST -->",
                     findings_list(af + warns + pend_advisory, "No advisory findings."))
            .replace("<!-- INSERT_DECISION_CARD -->",
                     decision_html(bf, af, warns, pend_blocking))
            .replace("<!-- INSERT_HISTORY_STRIP -->",
                     history_html(history, card, verdict, score_n, stale)))
    out = health_dir(repo) / "HEALTH.html"
    out.write_text(page if page.endswith("\n") else page + "\n")
    # `stale` rides WITH the score, never beside it: the page said this run
    # describes a tree nobody has, and a history line that drops that sentence
    # replots the number as a clean measurement on every later render.
    entry = {"ts": card["date"], "commit": card["commit"], "verdict": verdict,
             "score": score_n, "bf": len(bf), "af": len(af), "warn": len(warns),
             "layers": layers}
    if stale:
        entry["stale"] = stale
    lines = [json.dumps(h) for h in history] + [json.dumps(entry)]
    (health_dir(repo) / "history.jsonl").write_text("\n".join(lines) + "\n")
    counts = (f"{len(bf)} blocking · {len(af)} advisory fails · {len(warns)} warnings · "
              f"{passes} pass · {len(pend)} not run · {nas} n/a")
    print(f"verdict: {verdict}\n{counts}\nwrote {out}")


if __name__ == "__main__":
    # `python3 health_render.py` used to define these functions and exit 0 having
    # rendered nothing — a silent no-op with a success code, which is the exact
    # failure this skill exists to catch. The supported entrypoint is still
    # `health.py render --repo <dir>`; this one now does the same thing instead
    # of lying, takes `--repo` the same way so following the sentence above
    # cannot hand you a FileNotFoundError, and refuses two different repos
    # rather than silently rendering one of them. test_render_cli.py runs this
    # block as a subprocess — without it the no-op comes back with the suite green.
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Render a repo's .claude/health/scorecard.json to HEALTH.html.")
    ap.add_argument("repo", nargs="?", default=None, help="repo root (default: cwd)")
    ap.add_argument("--repo", dest="repo_flag", default=None, help="same, named")
    ns = ap.parse_args()
    # Resolved, not raw: `health_render.py "$(pwd)" --repo .` names ONE directory twice.
    if ns.repo and ns.repo_flag and Path(ns.repo).resolve() != Path(ns.repo_flag).resolve():
        ap.error(f"two different repos given: {ns.repo!r} and --repo {ns.repo_flag!r}")
    target = Path(ns.repo_flag or ns.repo or ".").resolve()
    # A missing scorecard is a NAMED next command, not a raw FileNotFoundError —
    # the same sentence health.py fix-routes prints for the same condition.
    if not (health_dir(target) / "scorecard.json").exists():
        sys.exit(f"no scorecard for {target} — run: health.py collect --repo {target}")
    render(target)
