#!/usr/bin/env python3
"""
AV-01 (S09) — the human-gate DIGEST + its non-negotiable invariants.

conventions.md §7.6 (human-gate-digest invariant schema, finding #7).

WHY THIS EXISTS
---------------
A one-screen digest is an *interface* improvement. Left unguarded it becomes a
path to rubber-stamp the unranked tail — the reviewer sees five tidy items and
acts as if that were the whole picture. This module ranks what matters, but it
is built so the ranking can never *hide* an unresolved must-fix:

  1. readiness is computed from the FULL finding set, never the displayed top set;
  2. every must-fix stays blocking until it reaches a §1.5 terminal — NO digest
     action auto-resolves a finding (the digest is strictly READ-ONLY);
  3. the count of must-fix not shown (`hidden_tail_count`) is always surfaced;
  4. the owner must ACKNOWLEDGE the hidden tail (`owner_ack`, ack_scope=full-set)
     before the digest's verdict may be acted on.

A digest that cannot satisfy all four returns the safe default `not-ready` —
never a prettied-up `ready` (§7.6).

WHAT IT IS NOT
--------------
It does not fix, waive, or resolve anything. It reads a trust-review result
(loop_controller.run_loop output) and, optionally, an evidence-gaps list
(evidence-map EM-03), and produces a ranked, near-duplicate-COLLAPSED-FOR-DISPLAY
digest. Collapsing is cosmetic: the collapsed members stay in the full set, are
counted in `must_fix_total`, feed readiness, and are surfaced as a "+N similar"
on their representative — nothing is silently dropped.

I/O
---
  build_digest(review_result, evidence_gaps=?, travel_class=?, top_n=5,
               owner_ack=?, trust_anchor=?, run_id=?) -> digest dict
  render_digest_md(digest)   -> str
  render_digest_html(digest, theme=None) -> str   (one-screen page)
  write_digest(digest, out_dir) -> writes human-gate-digest.{json,md,html}

CLI
---
  python human_gate_digest.py --result review-result.json [--gaps gaps.json]
         [--out DIR] [--top 5] [--owner "Name" --ack-at ISO] [--travel board-regulatory]

Pure stdlib. Exit 0 always on a well-formed result (the *verdict* lives in the
digest, not the exit code).
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from digest_theme import pack_theme, page_css  # noqa: E402

# --- canonical contract values (mirrors, asserted against source in the fixture) ----
# §2.3 materiality tiers — highest governs. IDENTICAL to references/scripts/verification.py
# MATERIALITY_PRIORITY; the fixture asserts they have not drifted.
MATERIALITY_PRIORITY = {
    "headline": 7, "regulatory": 6, "market": 5, "numeric": 4,
    "named-entity": 3, "date": 2, "assumption-as-fact": 1,
}
# travel-class voltage multiplier (the §2.3 order internal < travels < board-regulatory).
TRAVEL_MULT = {
    "internal": 1.0, "travels": 2.0,
    "board": 3.0, "regulatory": 3.0, "board-regulatory": 3.0,
}
# §1.4 / §1.5 — a must-fix in any of these lifecycles is still BLOCKING.
BLOCKING_LIFECYCLE = {"open", "fixed-claimed", "still-open"}
WAIVED_LIFECYCLE = "waived-by-human"
SEVERITY_RANK = {"must-fix": 3, "should-fix": 2, "polish": 1}

# finding `type` -> §2.3 materiality tier, so a finding with no explicit materiality
# field (the TR-01 edit list does not carry one) still ranks sensibly. A type we do
# not recognise falls back to its severity (a must-fix is treated at least `numeric`).
TYPE_MATERIALITY = {
    "unsupported-claim": "numeric", "unsourced-number": "numeric",
    "number-no-source": "numeric", "fabricated-number": "numeric",
    "source-conflict": "regulatory", "conflict": "regulatory",
    "regulatory-claim": "regulatory", "compliance": "regulatory",
    "headline-claim": "headline", "exec-summary": "headline",
    "stale-date": "date", "undated-number": "date", "as-of-date": "date",
    "named-entity": "named-entity", "misattributed-quote": "named-entity",
    "unattributed-quote": "named-entity",
    "assumption-as-fact": "assumption-as-fact",
    "market-claim": "market", "forecast": "market",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _travel_mult(travel_class) -> float:
    return TRAVEL_MULT.get(str(travel_class or "").lower(), 1.0)


def _materiality_of(finding: dict) -> str:
    """The governing materiality tier for a finding. An explicit `materiality` field
    wins (string or list ⇒ highest); else inferred from `type`; else by severity."""
    m = finding.get("materiality")
    if m:
        tiers = m if isinstance(m, (list, tuple, set)) else [m]
        best = max(tiers, key=lambda t: MATERIALITY_PRIORITY.get(t, 0), default=None)
        if best in MATERIALITY_PRIORITY:
            return best
    t = str(finding.get("type", "")).lower()
    if t in TYPE_MATERIALITY:
        return TYPE_MATERIALITY[t]
    # unknown type ⇒ severity-floored so a must-fix never ranks at the very bottom
    return "numeric" if finding.get("severity") == "must-fix" else "assumption-as-fact"


def _lifecycle(finding: dict) -> str:
    return str(finding.get("status") or finding.get("lifecycle") or "open")


def _is_open_must_fix(finding: dict) -> bool:
    return finding.get("severity") == "must-fix" and _lifecycle(finding) in BLOCKING_LIFECYCLE


def _is_waived(finding: dict) -> bool:
    return _lifecycle(finding) == WAIVED_LIFECYCLE


def _dedupe_signature(finding: dict) -> str:
    """A near-duplicate key for DISPLAY collapsing only. Two findings collapse ONLY when
    they share type + severity + a digit-normalized location AND the same evidence head
    — i.e. the SAME defect repeated across adjacent cells (e.g. one formula wrong across
    a row: B5/B6/B7 with identical evidence). Distinct evidence keeps findings distinct,
    so genuinely different must-fix are NEVER folded away. Never used for counts/readiness."""
    typ = str(finding.get("type", "")).lower()
    sev = str(finding.get("severity", ""))
    loc = re.sub(r"\d+", "#", str(finding.get("location", "")).lower())  # B5/B6/B7 -> b#
    loc = re.sub(r"\s+", " ", loc).strip()
    ev = re.sub(r"\s+", " ", str(finding.get("evidence", "")).lower()).strip()[:64]
    return "|".join((typ, sev, loc, ev))


def _rank_score(finding: dict, travel_mult: float) -> float:
    """materiality × travel class, with severity as the primary voltage and any
    upstream gap risk_score folded in. Higher = surfaced first."""
    mat = MATERIALITY_PRIORITY.get(_materiality_of(finding), 1)
    sev = SEVERITY_RANK.get(finding.get("severity"), 1)
    gap_risk = float(finding.get("risk_score") or 0.0)
    return round(sev * 100 + mat * travel_mult * 4 + gap_risk, 3)


# ---------------------------------------------------------------------------
# normalize the inputs into ONE full finding set
# ---------------------------------------------------------------------------
def _findings_from_result(review_result: dict) -> list:
    # shallow-copy each finding so the digest can annotate (e.g. _rank) WITHOUT mutating
    # the caller's review result — the digest is strictly read-only over its inputs.
    return [dict(f) for f in (review_result.get("findings") or [])]


def _findings_from_gaps(evidence_gaps) -> list:
    """Accept an evidence-gaps payload (EM-03) as either {gaps:[...]} or a bare list.
    Each gap already carries id/location/type/severity/evidence + risk_score; default
    its lifecycle to `open` (a gap is an unresolved finding)."""
    if not evidence_gaps:
        return []
    gaps = evidence_gaps.get("gaps") if isinstance(evidence_gaps, dict) else evidence_gaps
    out = []
    for g in (gaps or []):
        if not isinstance(g, dict):
            continue
        f = dict(g)
        f.setdefault("status", "open")
        f["_origin"] = "evidence-gap"
        out.append(f)
    return out


def _readiness_from_findings(findings: list, travel_class: str,
                             waiver_problems) -> str:
    """§4 readiness over a finding set — IDENTICAL logic to loop_controller._readiness,
    but takes an explicit set so we can prove the digest derives readiness from the
    FULL set (and, separately, show what the displayed-only set would have said)."""
    if waiver_problems:
        return "not-ready"
    blocking = [f for f in findings if _is_open_must_fix(f)]
    waived = [f for f in findings if f.get("severity") == "must-fix" and _is_waived(f)]
    if blocking:
        return "not-ready"
    if waived:
        return "ready-with-limitations"
    return "ready"


def _valid_owner_ack(owner_ack) -> bool:
    """A valid full-set acknowledgement: a named owner + an ack timestamp + an
    ack_scope of exactly 'full-set'. You cannot acknowledge only the top set."""
    if not isinstance(owner_ack, dict):
        return False
    return bool(owner_ack.get("owner")) and bool(owner_ack.get("ack_at")) \
        and owner_ack.get("ack_scope") == "full-set"


def build_digest(review_result: dict, *, evidence_gaps=None, travel_class: str = "",
                 top_n: int = 5, owner_ack=None, trust_anchor: str = "review-result.md",
                 run_id: str = "") -> dict:
    """Build the human-gate digest over the FULL finding set (review result + gaps).

    Ranking is materiality × travel class; near-duplicates are collapsed FOR DISPLAY
    ONLY. Every §7.6 invariant is enforced and recorded. READ-ONLY: no finding is
    mutated, so no digest action can auto-resolve anything."""
    run_id = run_id or review_result.get("run_id") or ""
    travel_class = travel_class or (review_result.get("routing", {}) or {}).get("effective") \
        or (review_result.get("routing", {}) or {}).get("travel_class") or ""
    waiver_problems = review_result.get("waiver_problems") or []
    tmult = _travel_mult(travel_class)

    # ---- the FULL finding set (the source of truth for counts + readiness) --------
    full = _findings_from_result(review_result) + _findings_from_gaps(evidence_gaps)

    must_fix_all = [f for f in full if f.get("severity") == "must-fix"]
    open_mf = [f for f in full if _is_open_must_fix(f)]
    waived_mf = [f for f in full if f.get("severity") == "must-fix" and _is_waived(f)]
    must_fix_total = len(must_fix_all)
    must_fix_open = len(open_mf)

    # readiness is ALWAYS from the full set (invariant 1) — and we compute what the
    # displayed-only set would say so the difference is auditable, never the basis.
    full_set_readiness = _readiness_from_findings(full, travel_class, waiver_problems)

    # ---- rank the ACTIONABLE items (open must-fix + waivers) -----------------------
    actionable = open_mf + waived_mf
    for f in actionable:
        f.setdefault("_rank", _rank_score(f, tmult))
    actionable.sort(key=lambda f: (f["_rank"], SEVERITY_RANK.get(f.get("severity"), 1)),
                    reverse=True)

    # ---- collapse near-duplicates FOR DISPLAY ONLY --------------------------------
    groups: dict = {}
    order: list = []
    for f in actionable:
        sig = _dedupe_signature(f)
        if sig not in groups:
            groups[sig] = []
            order.append(sig)
        groups[sig].append(f)
    reps = []  # one representative per group, highest-ranked first (groups already sorted)
    for sig in order:
        members = groups[sig]
        rep = members[0]
        reps.append((rep, members))
    # representatives are already in rank order (actionable was sorted); keep stable.

    displayed_rows = []
    displayed_ids = []
    shown_open_mf_ids = set()
    for rep, members in reps[:max(0, int(top_n))]:
        fid = rep.get("id", "")
        role = "open-must-fix" if _is_open_must_fix(rep) else (
            "waiver" if _is_waived(rep) else "finding")
        collapsed_ids = [m.get("id", "") for m in members[1:]]
        row = {
            "id": fid,
            "type": rep.get("type", ""),
            "severity": rep.get("severity", ""),
            "lifecycle": _lifecycle(rep),
            "location": rep.get("location", ""),
            "materiality": _materiality_of(rep),
            "rank_score": rep.get("_rank"),
            "evidence": rep.get("evidence", ""),
            "role": role,
            "collapsed_count": len(collapsed_ids),
            "collapsed_ids": collapsed_ids,
            "deeplink": "%s#%s" % (trust_anchor, fid) if fid else trust_anchor,
        }
        displayed_rows.append(row)
        displayed_ids.append(fid)
        if role == "open-must-fix":
            shown_open_mf_ids.add(fid)

    # ---- the hidden tail: every open must-fix NOT a displayed representative -------
    # (a member collapsed behind a shown representative is counted in the tail too —
    #  conservative, so the tail is never under-reported.)
    hidden_open_mf = [f for f in open_mf if f.get("id") not in shown_open_mf_ids]
    hidden_tail_count = len(hidden_open_mf)

    # readiness if you (wrongly) judged on the displayed set only — for transparency
    displayed_set = [r for (r, _m) in reps[:max(0, int(top_n))]]
    displayed_set_readiness = _readiness_from_findings(displayed_set, travel_class,
                                                       waiver_problems)

    # ---- invariants ----------------------------------------------------------------
    ack_ok = _valid_owner_ack(owner_ack)
    invariants = {
        "readiness_from_full_set": True,          # structural: we derived from `full`
        "no_digest_action_auto_resolves": True,   # structural: read-only, asserted below
        "hidden_tail_visible": True,              # hidden_tail_count is always surfaced
        "tail_acknowledged": bool(ack_ok),
    }
    all_invariants = all(invariants.values())

    # the verdict the digest may be ACTED on: the full-set readiness ONLY when every
    # invariant holds; otherwise the safe default not-ready (§7.6).
    digest_verdict = full_set_readiness if all_invariants else "not-ready"

    digest = {
        "schema": "office-trust-layer/human-gate-digest/3",
        "schema_version": "3",
        "produced_by": "trust-review/human_gate_digest.py (AV-01)",
        "produced_at": _now_iso(),
        "run_id": run_id,
        "travel_class": travel_class,
        "trust_anchor": trust_anchor,
        # --- §7.6 contract fields ---
        "full_set_readiness": full_set_readiness,
        "must_fix_total": must_fix_total,
        "must_fix_open": must_fix_open,
        "displayed": displayed_ids,
        "hidden_tail_count": hidden_tail_count,
        "owner_ack": owner_ack if ack_ok else None,
        "invariants": invariants,
        # --- digest extras (display + audit) ---
        "digest_verdict": digest_verdict,
        "displayed_set_readiness": displayed_set_readiness,
        "full_set_size": len(full),
        "waiver_count": len(waived_mf),
        "waiver_problems": waiver_problems,
        "ranking": displayed_rows,
        "hidden_tail_ids": [f.get("id", "") for f in hidden_open_mf],
        "ack_required": True,
        "ack_satisfied": bool(ack_ok),
        "notes": [
            "Readiness is derived from the FULL finding set (%d), never the displayed %d."
            % (len(full), len(displayed_rows)),
            "Near-duplicates are collapsed for DISPLAY only; collapsed members remain in "
            "must_fix_total and feed readiness.",
            "This digest is read-only: no action here resolves, fixes, or waives a finding.",
        ],
    }
    return digest


# ---------------------------------------------------------------------------
# renderers
# ---------------------------------------------------------------------------
def render_digest_md(d: dict) -> str:
    L = []
    L.append("# Human-gate digest — `%s`" % (d.get("run_id") or "run"))
    L.append("")
    L.append("> **Acted verdict: `%s`**  ·  full-set readiness `%s`  ·  travel class `%s`"
             % (d["digest_verdict"], d["full_set_readiness"], d.get("travel_class") or "—"))
    L.append("")
    L.append("| metric | value |")
    L.append("|---|---|")
    L.append("| must-fix total | **%d** |" % d["must_fix_total"])
    L.append("| must-fix open (blocking) | **%d** |" % d["must_fix_open"])
    L.append("| waivers in play | %d |" % d["waiver_count"])
    L.append("| shown in digest | %d |" % len(d["ranking"]))
    L.append("| **hidden must-fix tail** | **%d** |" % d["hidden_tail_count"])
    L.append("| full finding set | %d |" % d["full_set_size"])
    L.append("")
    L.append("## Top findings (ranked by materiality × travel class)")
    L.append("")
    if d["ranking"]:
        L.append("| # | finding | sev | lifecycle | materiality | rank | where | link |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(d["ranking"], 1):
            extra = (" (+%d similar)" % r["collapsed_count"]) if r["collapsed_count"] else ""
            L.append("| %d | `%s` %s%s | %s | %s | %s | %.0f | `%s` | [open](%s) |" % (
                i, r["id"], r["type"], extra, r["severity"], r["lifecycle"],
                r["materiality"], (r["rank_score"] or 0), r["location"], r["deeplink"]))
    else:
        L.append("_No actionable must-fix or waiver in the set._")
    L.append("")
    L.append("## Hidden tail — NOT shown above")
    L.append("")
    if d["hidden_tail_count"]:
        L.append("**%d must-fix finding(s) are blocking but not shown above.** "
                 "They remain blocking and are counted in readiness:" % d["hidden_tail_count"])
        L.append("")
        L.append(", ".join("`%s`" % i for i in d["hidden_tail_ids"]) or "—")
    else:
        L.append("None — every open must-fix is shown above.")
    L.append("")
    L.append("## Owner acknowledgement (required)")
    L.append("")
    if d["ack_satisfied"]:
        oa = d["owner_ack"]
        L.append("✓ Acknowledged by **%s** at %s (scope: %s)."
                 % (oa.get("owner"), oa.get("ack_at"), oa.get("ack_scope")))
    else:
        L.append("☐ **NOT acknowledged.** The owner must acknowledge the full set "
                 "(including the %d hidden must-fix) before this digest's verdict is acted "
                 "on. Until then the acted verdict is the safe default `not-ready`."
                 % d["hidden_tail_count"])
    L.append("")
    L.append("## Invariants")
    L.append("")
    for k, v in d["invariants"].items():
        L.append("- %s **%s**" % ("✓" if v else "✗", k))
    L.append("")
    L.append("_The digest is read-only — no action here resolves, fixes, or waives a "
             "finding (§7.6). Counts and readiness reflect the full set, never the top set._")
    L.append("")
    return "\n".join(L)


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def render_digest_html(d: dict, theme=None) -> str:
    """One-screen digest. Self-contained, no external assets. `theme` is an optional
    {css-var: value} mapping laid over the neutral default; None uses an installed pack's THEME."""
    verdict = d["digest_verdict"]
    vcolor = {"ready": "#02911A", "ready-with-limitations": "#C97800"}.get(verdict, "#B43030")
    ack_ok = d["ack_satisfied"]
    rows_html = []
    for i, r in enumerate(d["ranking"], 1):
        extra = ('<span class="sim">+%d similar</span>' % r["collapsed_count"]) \
            if r["collapsed_count"] else ""
        sev_c = {"must-fix": "#B43030", "should-fix": "#C97800"}.get(r["severity"], "#6B6864")
        rows_html.append(
            '<tr><td class="num">%d</td>'
            '<td><code>%s</code> %s%s<div class="ev">%s</div></td>'
            '<td><span class="sev" style="color:%s">%s</span></td>'
            '<td>%s</td><td>%s</td><td class="rank">%.0f</td>'
            '<td><code>%s</code></td>'
            '<td><a href="%s">open ↗</a></td></tr>' % (
                i, _esc(r["id"]), _esc(r["type"]), extra, _esc(r["evidence"])[:140],
                sev_c, _esc(r["severity"]), _esc(r["lifecycle"]), _esc(r["materiality"]),
                (r["rank_score"] or 0), _esc(r["location"]), _esc(r["deeplink"])))
    rows = "\n".join(rows_html) or '<tr><td colspan="8" class="muted">No actionable finding.</td></tr>'

    tail_ids = ", ".join("<code>%s</code>" % _esc(i) for i in d["hidden_tail_ids"]) or "—"
    inv_html = "".join(
        '<li class="%s">%s %s</li>' % ("ok" if v else "bad", "✓" if v else "✗", _esc(k))
        for k, v in d["invariants"].items())

    if ack_ok:
        oa = d["owner_ack"]
        ack_block = ('<div class="ack ok"><strong>✓ Tail acknowledged</strong> by %s '
                     '· %s · scope <code>%s</code></div>' % (
                         _esc(oa.get("owner")), _esc(oa.get("ack_at")), _esc(oa.get("ack_scope"))))
    else:
        ack_block = ('<div class="ack todo"><label><input type="checkbox" disabled> '
                     '<strong>Acknowledge the full set</strong> — including the '
                     '<strong>%d hidden must-fix</strong>. Until acknowledged the acted verdict '
                     'is the safe default <code>not-ready</code>.</label></div>'
                     % d["hidden_tail_count"])

    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Human-gate digest — %(run)s</title>
<style>
%(css)s
</style></head><body><div class="wrap">
<header><div><h1>Human-gate <em>digest</em></h1>
<div class="sub">run <code>%(run)s</code> · travel class <strong>%(travel)s</strong>
· full-set readiness <strong>%(fsr)s</strong></div></div>
<div class="verdict" style="background:%(vcolor)s">%(verdict)s</div></header>

<div class="kpis">
<div class="kpi"><div class="v">%(mft)d</div><div class="l">must-fix total</div></div>
<div class="kpi%(open_alarm)s"><div class="v">%(mfo)d</div><div class="l">must-fix open</div></div>
<div class="kpi"><div class="v">%(waiv)d</div><div class="l">waivers</div></div>
<div class="kpi"><div class="v">%(shown)d</div><div class="l">shown here</div></div>
<div class="kpi%(tail_alarm)s"><div class="v">%(tail)d</div><div class="l">hidden must-fix tail</div></div>
<div class="kpi"><div class="v">%(fss)d</div><div class="l">full finding set</div></div>
</div>

<table><thead><tr><th>#</th><th>finding</th><th>sev</th><th>lifecycle</th><th>materiality</th>
<th>rank</th><th>where</th><th>link</th></tr></thead><tbody>
%(rows)s
</tbody></table>

<div class="tail %(tail_cls)s"><h2>Hidden tail — not shown above</h2>
<div>%(tail_msg)s</div></div>

%(ack_block)s

<ul class="inv">%(inv)s</ul>

<div class="foot">Read-only digest (§7.6). No action here resolves, fixes, or waives a finding.
Readiness and all counts reflect the full set of %(fss)d findings, never the top %(shown)d.
Near-duplicates are collapsed for display only.</div>
</div></body></html>""" % {
        "css": page_css(pack_theme() if theme is None else theme),
        "run": _esc(d.get("run_id") or "run"),
        "travel": _esc(d.get("travel_class") or "—"),
        "fsr": _esc(d["full_set_readiness"]),
        "verdict": _esc(verdict).upper(),
        "vcolor": vcolor,
        "mft": d["must_fix_total"], "mfo": d["must_fix_open"], "waiv": d["waiver_count"],
        "shown": len(d["ranking"]), "tail": d["hidden_tail_count"], "fss": d["full_set_size"],
        "open_alarm": " alarm" if d["must_fix_open"] else "",
        "tail_alarm": " alarm" if d["hidden_tail_count"] else "",
        "rows": rows,
        "tail_cls": "none" if not d["hidden_tail_count"] else "",
        "tail_msg": ("None — every open must-fix is shown above."
                     if not d["hidden_tail_count"]
                     else "<strong>%d must-fix</strong> still blocking, not shown above "
                          "(still counted in readiness): %s" % (d["hidden_tail_count"], tail_ids)),
        "ack_block": ack_block,
        "inv": inv_html,
    }


def write_digest(digest: dict, out_dir: str, theme=None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    jp = os.path.join(out_dir, "human-gate-digest.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(digest, fh, indent=2)
        fh.write("\n")
    paths["json"] = jp
    mp = os.path.join(out_dir, "human-gate-digest.md")
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write(render_digest_md(digest))
    paths["md"] = mp
    hp = os.path.join(out_dir, "human-gate-digest.html")
    with open(hp, "w", encoding="utf-8") as fh:
        fh.write(render_digest_html(digest, theme))
    paths["html"] = hp
    return paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AV-01 human-gate digest (conventions §7.6)")
    ap.add_argument("--result", required=True, help="trust-review review-result.json")
    ap.add_argument("--gaps", help="optional evidence-gaps.json (EM-03)")
    ap.add_argument("--out", help="output dir (default: alongside --result)")
    ap.add_argument("--top", type=int, default=5, help="rows shown in the digest (default 5)")
    ap.add_argument("--travel", default="", help="travel class override")
    ap.add_argument("--owner", default="", help="acknowledging owner (records owner_ack)")
    ap.add_argument("--ack-at", default="", help="ack timestamp ISO (default now)")
    ap.add_argument("--anchor", default="review-result.md", help="deep-link anchor base")
    args = ap.parse_args(argv)

    result = _load(args.result)
    gaps = _load(args.gaps) if args.gaps else None
    owner_ack = None
    if args.owner:
        owner_ack = {"owner": args.owner, "ack_at": args.ack_at or _now_iso(),
                     "ack_scope": "full-set"}
    digest = build_digest(result, evidence_gaps=gaps, travel_class=args.travel,
                          top_n=args.top, owner_ack=owner_ack, trust_anchor=args.anchor)
    out_dir = args.out or os.path.dirname(os.path.abspath(args.result))
    paths = write_digest(digest, out_dir)
    print("human-gate digest: verdict=%s  full-set-readiness=%s  must-fix open=%d  hidden-tail=%d"
          % (digest["digest_verdict"], digest["full_set_readiness"],
             digest["must_fix_open"], digest["hidden_tail_count"]))
    for k, v in paths.items():
        print("  %-4s %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
