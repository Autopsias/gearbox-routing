#!/usr/bin/env python3
"""render_report.py — turns aggregate_outcomes.py's JSON into ONE self-contained
HTML one-pager (MON-02): the cell table inline, a decision card of AT MOST 3
proposed routing changes (rank the rest, defer them), and a did-it-help
before/after readout. No external requests, no build step — one file, per the
house recipe in references/shared/decision-card-html.md.

stdlib-only (json + string templating; ladder rung 3 — this is well under the
recipe's own "~150 lines of HTML+CSS+JS" ceiling, no templating engine
warranted for that).

Usage:
  aggregate_outcomes.py | render_report.py > report.html
  render_report.py /tmp/aggregate.json -o report.html
"""

import argparse
import html
import json
import sys

MAX_DECISION_OPTIONS = 3


def _fmt(v):
    """Human formatting for any cell value, WITHOUT escaping. ONE definition of
    the container rule, recursive over nesting: `str()` on a dict or list leaks a
    Python literal into the page — the Type-source cell rendered `{'policy': 47}`
    and the apex CHEAPER-AND-WORSE line, the highest-severity line in the
    one-pager, rendered as a list literal. Two copies of this rule is what let
    that defect exist at two call sites at once."""
    if v is None:
        # A missing measurement is n/a wherever it appears. Handled HERE, in the
        # one formatter every cell routes through, because the live page rendered
        # `p50 ctx at compaction = None` while four sibling numeric columns
        # carried the identical exposure — a call-site fix leaves the siblings.
        return "n/a"
    if isinstance(v, dict):
        return ", ".join(f"{k} {_fmt(x)}" for k, x in v.items()) or "none"
    if isinstance(v, (list, tuple)):
        return "; ".join(_fmt(x) for x in v) or "none"
    return str(v)


def _esc(v):
    """`_fmt` then escape once. Every cell in the page routes through here."""
    return html.escape(_fmt(v))


def _rank_proposals(agg):
    """proposals[] already merges fired cell-proposals + adoption-ready
    canaries (see aggregate_outcomes.py::run). Rank upgrades first (they name
    an under-modeled cell — the most time-sensitive class of finding), then
    adoption-ready canaries, then downgrade-opens; each internally sorted by N
    descending (more evidence first)."""
    order = {"upgrade": 0, "adoption-ready": 1, "downgrade-open": 2, "downgrade": 2}
    # A decision card is a list of things the operator can ACT on. cell_proposal
    # also emits a downgrade whose stage is "smoke-in-progress" -- its own
    # recommendation text says only "canary already underway, see canaries[]",
    # i.e. no action. It scored 2 like a real downgrade-open, and within a rank
    # the sort is by N descending, so a stalled entry with a big N could take one
    # of the three slots and push an ACTIONABLE proposal into the deferred list --
    # while offering the operator a card with nothing to accept. Drop it before
    # ranking; canaries[] still reports the experiment in full.
    actionable = [p for p in agg.get("proposals", [])
                  if p.get("stage") != "smoke-in-progress"]

    def key(p):
        # Take the BEST rank across both fields rather than falling back from
        # one to the other. An adoption-ready canary carries kind="canary" (the
        # EXPERIMENT's kind, from aggregate_canaries) AND stage="adoption-ready";
        # because "canary" is truthy, an `or` fallback never reached `stage`, so
        # every adoption-ready canary scored the unknown rank 9 and sorted LAST
        # — the exact opposite of the order this docstring promises.
        ranks = [order[v] for v in (p.get("kind"), p.get("stage")) if v in order]
        return (min(ranks, default=9), -(p.get("n") or (p.get("stats") or {}).get("n") or 0))

    return sorted(actionable, key=key)


def _card_html(p, idx):
    if "recommendation" in p:  # cell proposal
        title = f"{(p.get('kind') or '').upper()} — {p.get('class')} ({p.get('current_cell')})"
        body = p.get("recommendation", "")
        evidence = f"N={p.get('n')}, first-attempt pass={p.get('first_attempt_pass_rate')}, escalation={p.get('escalation_rate')}"
    else:  # adoption-ready canary
        st = p.get("stats", {})
        title = f"ADOPT CANARY — {p.get('class')} ({p.get('cell')})"
        body = f"proposal_id {p.get('proposal_id')} reached adoption-ready — apply via /routing-update"
        evidence = f"N={st.get('n')}, pass={st.get('first_attempt_pass_rate')}, attempts/success={st.get('attempts_per_success')}"
    pid = _esc(p.get("proposal_id", ""))
    return f"""
    <div class="card" data-verdict="">
      <h3>#{idx} {_esc(title)}</h3>
      <p>{_esc(body)}</p>
      <p class="evidence">{_esc(evidence)} — <code>{pid}</code></p>
      <div class="chips">
        <button class="chip accept" onclick="setVerdict(this,'accept')">Accept</button>
        <button class="chip reject" onclick="setVerdict(this,'reject')">Reject</button>
        <button class="chip modify" onclick="setVerdict(this,'modify')">Modify</button>
      </div>
    </div>"""


def _cell_row(c):
    f = c.get("facts", {})
    return (f"<tr><td>{_esc(c.get('task_class'))}</td>"
            f"<td>{_esc(c.get('model_authored'))}@{_esc(c.get('reasoning_authored') or 'unset')}</td>"
            f"<td>{_esc(f.get('n'))}</td>"
            f"<td>{_esc(f.get('first_attempt_pass_rate'))}</td>"
            f"<td>{_esc(f.get('escalation_rate'))}</td>"
            f"<td>{_esc(c.get('status'))}</td></tr>")


def _cost_section(cells):
    """Worker cost per ran cell: the median per attempt beside the median per
    completed session (all attempts summed, attributed to the first attempt's
    cell), so a cheap cell that needs rework is not read as cheap."""
    rows = "".join(
        f"<tr><td>{_esc(c.get('task_class'))}</td>"
        f"<td>{_esc(c.get('model_ran'))}@{_esc(c.get('reasoning_ran') or 'unset')}</td>"
        f"<td>{_esc(c.get('attempts'))} ({_esc(c.get('attempts_with_usage'))} with usage, "
        f"{_esc(c.get('attempts_with_cost'))} priced)</td>"
        f"<td>{_esc(_num(c.get('median_cost_per_attempt'), ' USD'))}</td>"
        f"<td>{_esc(_num(c.get('median_cost_per_completed_session'), ' USD'))} "
        f"(N={_esc(c.get('completed_sessions'))}, "
        f"{_esc(c.get('incomplete_cost_sessions'))} incomplete-cost)</td></tr>"
        for c in cells or [])
    if not rows:
        return '<h2>Cost</h2><p class="empty">No attempts in scope.</p>'
    return ("<h2>Cost per attempt and per completed session</h2><table><thead><tr>"
            "<th>Class</th><th>Cell ran</th><th>Attempts</th><th>Median per attempt</th>"
            f"<th>Median per completed session</th></tr></thead><tbody>{rows}</tbody></table>")


def _did_it_help_row(d):
    b, a = d.get("before", {}), d.get("after", {})
    return (f"<tr><td>{_esc(d.get('class'))}</td>"
            f"<td>{_esc(d.get('old_rung'))} → {_esc(d.get('new_rung'))}</td>"
            f"<td>v{_esc(d.get('ssot_version'))}</td>"
            f"<td>N={_esc(b.get('n'))}, pass={_esc(b.get('first_attempt_pass_rate'))}</td>"
            f"<td>N={_esc(a.get('n'))}, pass={_esc(a.get('first_attempt_pass_rate'))}</td>"
            f"<td class=\"verdict-{_esc(d.get('verdict'))}\">{_esc(d.get('verdict'))}</td></tr>")


_CSS = """:root { --bg:#fff; --fg:#1a1a1a; --card:#f6f6f8; --accent:#2b6cb0; --border:#ddd; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#15161a; --fg:#e8e8ea; --card:#1e1f24; --accent:#6fa8dc; --border:#333; }
}
body { background:var(--bg); color:var(--fg); font-family:system-ui,sans-serif; max-width:900px; margin:2rem auto; padding:0 1rem; }
h1 { font-size:1.4rem; } h2 { font-size:1.1rem; margin-top:2rem; }
.card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:1rem; margin:0.75rem 0; }
.evidence { font-size:0.85rem; opacity:0.8; }
.chips { margin-top:0.5rem; }
.chip { border:1px solid var(--border); background:transparent; color:var(--fg); border-radius:999px; padding:0.25rem 0.75rem; margin-right:0.5rem; cursor:pointer; }
.chip.accept.active { background:#2f855a; color:#fff; }
.chip.reject.active { background:#c53030; color:#fff; }
.chip.modify.active { background:var(--accent); color:#fff; }
table { width:100%; border-collapse:collapse; overflow-x:auto; display:block; }
th,td { border:1px solid var(--border); padding:0.4rem 0.6rem; text-align:left; font-size:0.85rem; }
.empty, .deferred { opacity:0.7; font-size:0.9rem; }
.apex { color:#c53030; font-weight:600; }
.verdict-helped { color:#2f855a; } .verdict-no_improvement { color:#c53030; } .verdict-underpowered { opacity:0.7; }
.verdict-confounded { color:#b7791f; } .verdict-no_baseline, .verdict-no_exposure, .verdict-activation_unknown { opacity:0.6; }
.verdict-line { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:0.75rem; font-size:0.9rem; }"""


def _num(v, suffix=""):
    """A missing measurement renders as n/a — never as `None%`, which reads like
    a value."""
    return "n/a" if v is None else f"{v}{suffix}"


def _coactive_flag(entry):
    """A verdict a reader takes at face value must carry its own caveat. The
    FLAG goes in the verdict cell — `helped` beside a cohort another intervention
    changed part-way through is the overclaim this session exists to prevent —
    and the numbers go under the table, because putting them in the cell forced
    a min-width that clipped the column instead of widening it."""
    return "<br>CONTRAST NOT ISOLATING &darr;" if entry.get("co_active") else ""


def _coactive_detail(iv, entry):
    notes = entry.get("co_active") or []
    if not notes:
        return ""
    return (f'<p class="evidence"><strong>{_esc(iv)} — contrast not isolating:</strong> '
            + _esc("; ".join(
                f"{n['already_exposed']} of its {n['of']} {n['side']}-cohort sessions "
                f"were already exposed to {n['intervention']}" for n in notes))
            + ". The cohorts are separable, but another intervention changed state "
              "inside this window, so the delta is a marginal effect, not this "
              "intervention's alone.</p>")


def _cohort_cell(n, stats):
    stats = stats or {}
    return (f"N={_esc(n)}, cost/sess={_esc(_num(stats.get('cost_usd_mean')))}, "
            f"base ctx p50={_esc(_num(stats.get('base_context_tokens_p50')))}, "
            f"re-read={_esc(_num(stats.get('reread_cost_pct_mean'), '%'))}")


def _adherence_section(comp, ivs):
    """The counter-metric table: could this be cheaper AND worse?"""
    adh = "".join(
        f"<tr><td>{_esc(iv)}</td>"
        f"<td>{_esc(_num(e['adherence']['before'].get('unpinned_generic_fanout_pct'), '%'))} "
        f"&rarr; {_esc(_num(e['adherence']['after'].get('unpinned_generic_fanout_pct'), '%'))} "
        f"(N={_esc(e['adherence']['before'].get('dispatches_observed'))} &rarr; "
        f"{_esc(e['adherence']['after'].get('dispatches_observed'))} dispatches)</td>"
        f"<td>{_esc(_num(e['adherence']['before'].get('closeouts_without_numbers_pct'), '%'))} "
        f"&rarr; {_esc(_num(e['adherence']['after'].get('closeouts_without_numbers_pct'), '%'))} "
        f"(N={_esc(e['adherence']['before'].get('closeouts_observed'))} &rarr; "
        f"{_esc(e['adherence']['after'].get('closeouts_observed'))} closeouts)</td></tr>"
        for iv, e in ivs.items() if e.get("adherence"))
    return (
        "<h2>Counter-metric — could this be cheaper and worse?</h2>"
        "<table><thead><tr><th>Intervention</th><th>Unpinned generic fan-out</th>"
        "<th>Closeouts with no re-measured number</th></tr></thead>"
        f"<tbody>{adh or '<tr><td colspan=3>no adherence sample</td></tr>'}</tbody></table>"
        + _adherence_verdict(comp.get("cheaper_and_worse") or {}))


def _adherence_verdict(caw):
    """The all-clear is a POSITIVE claim, so it requires a measured sample: at
    least one intervention with cost and adherence pairs on both sides. On live
    data three of four after-cells once read n/a and the only `helped`
    intervention had 0 dispatches and 0 closeouts after — and the page still
    printed the all-clear. An unexamined zero must say NOT MEASURED instead."""
    if caw.get("triggered"):
        return f'<p class="apex">{_esc(caw["signals"])}</p>'
    if caw.get("comparable_interventions"):
        return ('<p class="empty">No cheaper-and-worse signal: no intervention shows a cost '
                'fall paired with an adherence fall '
                f'({_esc(caw["comparable_interventions"])} intervention(s) comparable).</p>')
    return ('<p class="empty">Counter-metric NOT MEASURED: no intervention has a cost and '
            'adherence sample on both sides of its activation, so a cheaper-and-worse '
            'signal could not have fired — this is absence of evidence, not an all-clear.</p>')



def _ctx_pairs_note(row):
    """The two ctx columns are COMPARED, so the cell prints the one population
    both were computed over: N paired records (rows carrying ctx_before AND
    ctx_after), with one-sided records excluded and counted. Without this, the
    live page read p50-at over 7 auto rows against mean-after over those plus 4
    manual after-only rows — different sets of compactions, and the mismatch
    reversed the sign of the shrink answer."""
    n = row.get("n_ctx_pairs")
    if n is None:                    # a table built before this field existed
        return ""
    return (f'<br><span class="evidence">{_esc(n)} paired; '
            f'{_esc(row.get("ctx_one_sided_records") or 0)} one-sided excluded</span>')


def _type_table_rows(rows):
    """The per-session-type table body ('no records' rather than a blank)."""
    if not rows:
        return "<tr><td colspan='11'>no records</td></tr>"
    return "".join(
        f"<tr><td>{_esc(r['session_type'])}</td><td>{_esc(r['n'])}</td>"
        f"<td>{_esc(r['compactions'])}</td><td>{_esc(r['vetoes'])}</td>"
        f"<td>{_esc(r['allows'])}</td><td>{_esc(r['p50_ctx_at_compaction'])}</td>"
        f"<td>{_esc(r.get('p50_ctx_after_compaction'))}{_ctx_pairs_note(r)}</td>"
        f"<td>{_esc(r['cost_per_session'])}</td><td>{_esc(r['reread_pct'])}</td>"
        f"<td>{_esc(r['records_per_observed_session'])}</td>"
        f"<td>{_esc(r['type_source'])}</td></tr>" for r in rows)


def _intervention_rows(ivs):
    """One row per intervention, each against its OWN activation row."""
    return "".join(
        f"<tr><td>{_esc(iv)}</td><td>{_esc(e['status'])}</td>"
        f"<td>{_esc(e['activation_ts'])}</td>"
        f"<td>{_cohort_cell(e['n_before'], e.get('before'))}</td>"
        f"<td>{_cohort_cell(e['n_after'], e.get('after'))}</td>"
        f"<td>{_esc(e['n_spanning'])}<br><span class=\"evidence\">"
        f"{_esc(_num(e.get('spanning_cost_share_pct'), '% of spend'))}"
        + (f"<br>{_esc(e['n_undated'])} undated (no start time, in NO cohort)"
           if e.get("n_undated") else "") + "</span></td>"
        f'<td class="verdict-{_esc(e["verdict"])}">{_esc(e["verdict"])}<br>'
        f"<span class=\"evidence\">{_esc(e['why'])}{_coactive_flag(e)}</span></td></tr>"
        for iv, e in ivs.items()) or "<tr><td colspan='7'>no interventions</td></tr>"


def _compaction_section(comp, heading=True):
    """The compaction did-it-help block (s07/PF-01). Renders 'no records' rather
    than a blank or a crash on an empty ledger, and says the instrument was blind
    rather than showing a clean allow rate over records that were never taken."""
    if not comp:
        return ""
    led = comp.get("ledgers", {})
    rejected = ", ".join(f"{k}: {v.get('rejected_lines', 0)} rejected"
                         for k, v in led.items())
    trs = _type_table_rows(comp.get("type_table") or [])

    # The banner renders the SAME holes list the builder derived `blind` from
    # (compaction_blindness.BLINDNESS_HOLES), every counter with its number, zeros
    # included — a hand-picked subset once showed the red banner over three
    # zeros while the counters that raised the flag were nowhere on the page.
    b = comp.get("blindness") or {}
    holes = b.get("holes") or []
    hole_body = "; ".join(f"{_esc(h.get('count'))} {_esc(h.get('what'))}" for h in holes)
    if b.get("blind"):
        blind = (f'<p class="apex">INSTRUMENT BLINDNESS — {hole_body or _esc(b)}. '
                 f"({_esc(b.get('headless_sdk_sessions_out_of_scope'))} "
                 f"headless Agent-SDK session(s) are OUT OF SCOPE for the hooks and are "
                 f"not counted as blind spots.)</p>")
    else:
        blind = ('<p class="empty">Instrument healthy: every blindness counter is zero '
                 f"(checked: {_esc(', '.join(h.get('key', '') for h in holes)) or 'none'})."
                 '</p>')

    ivs = comp.get("interventions") or {}
    iv_rows = _intervention_rows(ivs)

    adh_section = _adherence_section(comp, ivs)

    cards = "".join(
        f'<div class="card"><h3>#{i + 1} {_esc(c["title"])}</h3><p>{_esc(c["body"])}</p>'
        f'<p class="evidence">Cost: {_esc(c["cost"])} — Do nothing: {_esc(c["do_nothing"])}</p></div>'
        for i, c in enumerate((comp.get("decision_card") or [])[:MAX_DECISION_OPTIONS])) or (
        '<p class="empty">No decision needed from this run.</p>')

    limit = "".join(_coactive_detail(iv, e) for iv, e in ivs.items()) + "".join(
        f'<p class="evidence">{_esc(e["attribution_limit"])}</p>'
        for e in ivs.values() if e.get("attribution_limit"))

    title = "<h2>Compaction — did it help?</h2>" if heading else ""
    return f"""
{title}
<p class="verdict-line"><strong>{_esc(comp.get('verdict_line'))}</strong></p>
<p class="evidence">Verdict vocabulary: {_esc(', '.join(comp.get('vocabulary') or []))}.
prediction_met is a FLAG BESIDE the verdict, never one of those words: True/False only when
a forecast and a realized number were both measurable, and n/a when no comparison was
possible — n/a is an absent measurement, not a missed forecast.
{_esc((comp.get('prediction') or {}).get('basis_note'))}</p>
{blind}
<p class="evidence">Generated {_esc(comp.get('generated_at'))} — these ledgers are LIVE and
move between runs; every number here is a snapshot at that instant.
Sessions observed by the instrument (heartbeat present): {_esc(comp.get('observed_sessions'))},
out of a universe of {_esc(comp.get('universe_sessions'))} session(s) considered
({_esc(comp.get('scanned_sessions'))} scanned transcript(s), plus heartbeat-only and
ledger-only sessions); {_esc(comp.get('excluded_unknown_sessions'))}
had no heartbeat at all and are EXCLUDED from every verdict.
Of the observed sessions: {_esc(b.get('sessions_with_records'))} left compaction records,
{_esc(b.get('sessions_no_compaction'))} had nothing to compact, and
{_esc(b.get('sessions_hook_inactive'))} ran with the hooks inactive.
Current policy_version {_esc(comp.get('policy_version'))}.
Ledger line rejects — {_esc(rejected)}. Probe lines excluded:
{_esc((comp.get('filtered') or {}).get('probe_lines_excluded'))}; stale-policy_version lines
excluded: {_esc((comp.get('filtered') or {}).get('stale_policy_version_lines'))}.</p>
<h3>Per intervention — each against its OWN activation row</h3>
<table><thead><tr><th>Intervention</th><th>Status</th><th>Activated</th><th>Before</th>
<th>After</th><th>Spanning<br>(no cohort)</th><th>Verdict</th></tr></thead>
<tbody>{iv_rows}</tbody></table>
{limit}
<h3>Compaction by session type</h3>
<table><thead><tr><th>Type</th><th>N</th><th>Compactions</th><th>Vetoes</th><th>Allows</th>
<th>p50 ctx at compaction<br>(paired records)</th><th>p50 ctx after compaction<br>(paired records)</th>
<th>Cost/session</th><th>Re-read %</th>
<th>Records / observed session</th><th>Type source</th></tr></thead>
<tbody>{trs}</tbody></table>
<p class="evidence">Both ctx columns are the SAME statistic (median) over the PAIRED
compaction records only — the rows carrying ctx_before AND ctx_after, so the two numbers
always describe the same compactions and can be compared directly. A record carrying one side alone (on live data: manual compactions write an
after with no before) is excluded from both columns and counted as one-sided in the cell.</p>
{adh_section}
<h2>Decision card (at most {MAX_DECISION_OPTIONS} options)</h2>
{cards}"""


def render(agg):
    ranked = _rank_proposals(agg)
    shown, deferred = ranked[:MAX_DECISION_OPTIONS], ranked[MAX_DECISION_OPTIONS:]
    cards = "".join(_card_html(p, i + 1) for i, p in enumerate(shown)) or (
        '<p class="empty">No proposals this run — every cell is below its minimum '
        "sample size or within the healthy range.</p>")
    deferred_note = (f'<p class="deferred">{len(deferred)} additional proposal(s) '
                      f"ranked lower and deferred — re-run after more data.</p>" if deferred else "")

    cell_rows = "".join(_cell_row(c) for c in agg.get("cells", [])) or "<tr><td colspan='6'>no cells</td></tr>"

    dih = agg.get("did_it_help", [])
    dih_rows = "".join(_did_it_help_row(d) for d in dih)
    dih_section = (
        f"<h2>Did the last adopted change help?</h2><table><thead><tr>"
        f"<th>Class</th><th>Rung change</th><th>Adopted at</th><th>Before</th>"
        f"<th>After</th><th>Verdict</th></tr></thead><tbody>{dih_rows}</tbody></table>"
        if dih else "<h2>Did the last adopted change help?</h2><p class=\"empty\">No adoptions on record yet.</p>"
    )

    apex = agg.get("apex_revisit", {})
    apex_html = (f'<p class="apex">APEX REVISIT DUE — {_esc(apex.get("message"))}</p>'
                 if apex.get("callout") else "")

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Routing retro — SSOT v{_esc(agg.get('ssot_version'))}</title>
<style>
{_CSS}
</style></head>
<body>
<h1>Routing retro — SSOT v{_esc(agg.get('ssot_version'))} — {_esc(agg.get('generated_at'))}</h1>
{apex_html}
<h2>Decision card (at most {MAX_DECISION_OPTIONS} options)</h2>
{cards}
{deferred_note}
<h2>Cells</h2>
<table><thead><tr><th>Class</th><th>Cell</th><th>N</th><th>Pass</th><th>Escalation</th><th>Status</th></tr></thead>
<tbody>{cell_rows}</tbody></table>
{_cost_section(agg.get("cost_cells"))}
{dih_section}
{_compaction_section(agg.get("compaction"))}
<script>
function setVerdict(btn, v) {{
  const card = btn.closest('.card');
  card.dataset.verdict = v;
  card.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
}}
</script>
</body></html>"""


def render_compaction_page(comp):
    """The standalone did-it-help ONE-PAGER (s07 evidence contract).

    Delegates to compaction_onepager, which renders the page a person actually
    reads — banner, metric tiles, verdict table, decision cards, provenance
    footer. This used to emit the same faithful-but-unreadable dump as the
    embedded section; the operator's verdict on it was "hardly understandable".
    The machine-readable block in `render()` is unchanged.
    """
    from compaction_onepager import render as render_onepager

    # The full section rides along BELOW the summary: six rounds of review pinned
    # nine things that must reach this page, and a redesign that drops them is a
    # regression however much better it reads.
    return render_onepager(comp, detail_html=_compaction_section(comp, heading=False),
                           detail_css=_CSS)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", nargs="?", default=None, help="path to aggregate_outcomes.py JSON (default: stdin)")
    ap.add_argument("-o", "--output", default=None, help="write HTML here (default: stdout)")
    ap.add_argument("--compaction", default=None,
                    help="compaction_report.py JSON to fold in as the Compaction section")
    ap.add_argument("--only-compaction", action="store_true",
                    help="render ONLY the compaction did-it-help one-pager")
    args = ap.parse_args()

    if args.only_compaction and not args.input:
        agg = {}
    else:
        text = open(args.input, encoding="utf-8").read() if args.input else sys.stdin.read()
        agg = json.loads(text)
    if args.compaction:
        with open(args.compaction, encoding="utf-8") as f:
            agg["compaction"] = json.load(f)
    out = render_compaction_page(agg.get("compaction")) if args.only_compaction else render(agg)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
