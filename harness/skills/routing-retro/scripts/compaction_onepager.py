"""The compaction did-it-help ONE-PAGER — a page a person reads in ten seconds.

WHY THIS EXISTS, and why it is not the other renderer. `render_report.py`'s
compaction section is a faithful dump: it prints the verdict line, then every
table, then the caveats. Faithful and unreadable — the operator's verdict on the
first version was that it was "hardly understandable", and he was right. The
verdict line alone is ~1500 characters of `||`-separated clauses.

The shape here is the one the operator's own COS one-pagers use, and it was
adopted because those work: a banner stating the outcome in ONE sentence before
any detail, a grid of large-number tiles, a short table, at most three decision
cards, and a provenance footer that carries the caveats instead of scattering
them through the prose. Nothing is dropped — the same numbers appear, ordered by
what a reader needs first rather than by what the JSON happens to contain.

Kept OUT of render_report.py deliberately: that file was at 446 of its 500-line
bound, and this page is a separate concern with a separate audience (a person,
versus the machine-readable retro block). Extracting later under ratchet pressure
is how the last four modules were born; starting separate is cheaper.
"""
from html import escape


def _n(value, dash="n/a"):
    """A number a person can read, or an honest dash — never a bare `None`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return dash
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}".rstrip("0").rstrip(".")


def _when(iso):
    """`2026-08-22T18:58:10+00:00` is a machine's idea of a date."""
    text = str(iso or "")
    if len(text) >= 16 and "T" in text:
        return text[:10] + " " + text[11:16] + " UTC"
    return text or "unknown"


def _usd(value):
    return "n/a" if not isinstance(value, (int, float)) or isinstance(value, bool) else f"${value:,.2f}"


_VERDICT_CLASS = {
    "helped": "p-help",
    "no_improvement": "p-bad",
    "underpowered": "p-under",
    "confounded": "p-conf",
    "no_baseline": "p-under",
    "no_exposure": "p-under",
    "activation_unknown": "p-under",
}

# What each verdict MEANS, in the reader's terms. The vocabulary is precise and
# unhelpful on its own: "confounded" tells a reader nothing about what to do.
_MEANS = {
    "helped": "The measured contrast is real and points the right way.",
    "no_improvement": "The contrast was measurable and did not favour the change.",
    "underpowered": "The sample cannot answer yet — not a failure, a shortage.",
    "confounded": "No contrast isolates this change. More data cannot fix it.",
    "no_baseline": "Too little was observed before it went live, and waiting cannot add any.",
    "no_exposure": "Never went live, so there is nothing to measure.",
    "activation_unknown": "It went live, but we cannot tell when.",
}

_LABEL = {
    "hooks": "compaction hooks",
    "base_context": "base context",
    "repo_diet": "repo diet",
    "routing": "routing",
}


_CSS = """:root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#17202a;background:#f3f5f7}
*{box-sizing:border-box}
body{margin:0;padding:44px;max-width:none}
.page{max-width:1000px;margin:auto;background:#fff;border-radius:24px;padding:48px;
      box-shadow:0 16px 50px #17202a18}
h1{font-size:40px;margin:0 0 8px;letter-spacing:-.5px}
.sub{color:#667085;font-size:17px}
.banner{margin:28px 0;padding:22px 24px;border-left:8px solid #c0392b;background:#fff1ef;
        border-radius:12px;font-size:19px;line-height:1.45}
.banner b{display:block;font-size:21px;margin-bottom:6px}
.banner.ok{border-left-color:#147d45;background:#f0f9f4}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.tile{border:1px solid #dfe4ea;border-radius:16px;padding:18px}
.n{font-size:31px;font-weight:800;line-height:1.1}
.label{color:#667085;font-size:13px;margin-top:6px;line-height:1.35}
.ok{color:#147d45}.bad{color:#b42318}.mut{color:#667085}
.section{margin-top:32px}
.section h2{font-size:20px;margin:0 0 14px}
.scroll{overflow-x:auto}
/* The detail stylesheet sets `th,td{border:1px solid}` and `table{display:block}`.
   A `border-bottom` here would leave its other three sides standing, so these
   reset the shorthand explicitly. Only visible by rendering. */
.page table{width:100%;border-collapse:collapse;font-size:14px;display:table}
.page th{text-align:left;color:#667085;font-weight:600;font-size:12px;text-transform:uppercase;
   letter-spacing:.4px;padding:0 10px 8px 0;border:0;border-bottom:1px solid #e9edf1}
.page td{padding:12px 10px 12px 0;border:0;border-bottom:1px solid #f1f4f7;
   vertical-align:top;font-size:14px}
.page td:first-child{font-weight:700;white-space:nowrap}
.pill{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;
      font-weight:600;white-space:nowrap}
.p-help{background:#e6f4ec;color:#147d45}
.p-bad{background:#fdecea;color:#b42318}
.p-under{background:#eef2f6;color:#475467}
.p-conf{background:#fff6d8;color:#8a6100}
.means{color:#475467;line-height:1.45}
.card{border:1px solid #dfe4ea;border-left:5px solid #2b6cb0;border-radius:14px;
      padding:18px 20px;margin-bottom:12px}
.card h3{margin:0 0 8px;font-size:16px}
.card p{margin:0 0 8px;font-size:14px;line-height:1.5;color:#344054}
.card .meta{font-size:13px;color:#667085}
.foot{margin-top:34px;padding-top:16px;border-top:1px solid #e9edf1;color:#667085;
      font-size:12.5px;line-height:1.6}
@media (max-width:820px){.grid{grid-template-columns:repeat(2,1fr)}body{padding:16px}
                         .page{padding:24px}}"""


def _banner(comp):
    """One sentence naming the outcome, before any number."""
    blind = comp.get("blindness") or {}
    ivs = comp.get("interventions") or {}
    answered = [k for k, v in ivs.items() if (v or {}).get("verdict") in ("helped", "no_improvement")]
    unread = blind.get("measurement_unavailable_records") or 0
    bits = []
    if unread:
        bits.append(
            f"{unread} pre-compaction decision(s) could not read the context and allowed by "
            "default, so the hook never measured real headroom there."
        )
    if len(answered) < len(ivs):
        bits.append(
            f"Only {len(answered)} of {len(ivs)} changes can be judged today; the rest share "
            "a deploy or lack the sample."
        )
    headline = (
        "The instrument is partly blind, and most changes cannot be judged yet."
        if unread else
        f"{len(answered)} of {len(ivs)} changes have an answer."
    )
    cls = "banner" if (unread or len(answered) < len(ivs)) else "banner ok"
    return f'<div class="{cls}"><b>{escape(headline)}</b>{escape(" ".join(bits))}</div>'


def _tiles(comp):
    blind = comp.get("blindness") or {}
    pred = comp.get("prediction") or {}
    ivs = comp.get("interventions") or {}
    types = comp.get("type_table") or []
    compactions = sum(t.get("compactions") or 0 for t in types)
    vetoes = sum(t.get("vetoes") or 0 for t in types)
    answered = sum(1 for v in ivs.values() if (v or {}).get("verdict") in ("helped", "no_improvement"))
    unread = blind.get("measurement_unavailable_records") or 0
    ledgers = (comp.get("ledgers") or {}).get("decisions") or {}
    realized = pred.get("realized_pct")
    tiles = [
        (_n(comp.get("observed_sessions")), "", f"Sessions observed<br><span class='mut'>of {_n(comp.get('scanned_sessions'))} scanned</span>"),
        (_n(compactions), "", "Compactions recorded"),
        (_n(vetoes), "bad" if not vetoes else "", "Vetoes fired"),
        (_n(unread), "bad" if unread else "ok", "Blind reads<br><span class='mut'>context unreadable</span>"),
        (f"{answered}<span class='mut'> / {len(ivs)}</span>", "", "Changes with a verdict"),
        (f"{_n(pred.get('predicted_pct'))}%", "", "Predicted saving"),
        (f"{_n(realized)}%" if isinstance(realized, (int, float)) else "n/a",
         "bad" if isinstance(realized, (int, float)) and realized < 0 else "", "Realized"),
        (_n(ledgers.get("records")), "", f"Ledger records<br><span class='mut'>{_n(ledgers.get('rejected_lines'))} rejected</span>"),
    ]
    cells = "".join(
        f'<div class="tile"><div class="n {cls}">{n}</div><div class="label">{lab}</div></div>'
        for n, cls, lab in tiles
    )
    return f'<div class="grid">{cells}</div>'


def _table(comp):
    ivs = comp.get("interventions") or {}
    order = sorted(ivs, key=lambda k: ["helped", "no_improvement", "underpowered",
                                       "confounded", "no_baseline", "no_exposure",
                                       "activation_unknown"]
                   .index((ivs[k] or {}).get("verdict", "confounded")))
    rows = []
    for key in order:
        v = ivs[key] or {}
        verdict = v.get("verdict", "?")
        before, after = v.get("before") or {}, v.get("after") or {}
        cost = ""
        if isinstance(before.get("cost_usd_mean"), (int, float)) and isinstance(after.get("cost_usd_mean"), (int, float)):
            cost = f" Cost/session {_usd(before['cost_usd_mean'])} \u2192 {_usd(after['cost_usd_mean'])}."
        why = (v.get("why") or "").strip()
        rows.append(
            f'<tr><td>{escape(_LABEL.get(key, key))}</td>'
            f'<td><span class="pill {_VERDICT_CLASS.get(verdict, "p-under")}">{escape(verdict)}</span></td>'
            f'<td>{_n(v.get("n_before"))} &rarr; {_n(v.get("n_after"))}</td>'
            f'<td class="means"><b>{escape(_MEANS.get(verdict, ""))}</b>{escape(cost)} {escape(why)}</td></tr>'
        )
    return (
        '<section class="section"><h2>The changes</h2><div class="scroll"><table>'
        "<tr><th>Change</th><th>Verdict</th><th>Before / after</th><th>What that means</th></tr>"
        + "".join(rows) + "</table></div></section>"
    )


def _cards(comp):
    cards = (comp.get("decision_card") or [])[:3]
    if not cards:
        return ""
    out = []
    for i, c in enumerate(cards, 1):
        out.append(
            f'<div class="card"><h3>{i} &middot; {escape(str(c.get("title", "")))}</h3>'
            f'<p>{escape(str(c.get("body", "")))}</p>'
            f'<p class="meta"><b>Cost:</b> {escape(str(c.get("cost", "")))} '
            f'&nbsp;&middot;&nbsp; <b>Do nothing:</b> {escape(str(c.get("do_nothing", "")))}</p></div>'
        )
    return '<section class="section"><h2>What to do — pick one</h2>' + "".join(out) + "</section>"


def _footer(comp):
    """The caveats live HERE, together, instead of interrupting the reading."""
    blind = comp.get("blindness") or {}
    bundles = comp.get("same_deploy_bundles") or []
    parts = [
        "<b>Provenance.</b> Cohorts are computed per intervention from its OWN activation row — "
        "never one boundary since a deploy. A session that straddles an activation is dropped "
        "from that comparison and counted separately.",
    ]
    if bundles:
        for b in bundles:
            parts.append(
                "<b>Same-deploy bundle:</b> " + escape(" + ".join(str(x) for x in b))
                + " went live together, so their cohorts are identical. No amount of further "
                "data separates them — only a staggered re-deploy can.")
    if blind.get("measurement_unavailable_records"):
        parts.append(
            f"<b>Instrument blind:</b> {_n(blind['measurement_unavailable_records'])} record(s) "
            "where the hook could not read the context and allowed by default.")
    parts.append(
        f"Generated {escape(_when(comp.get('generated_at')))} at policy_version "
        f"{escape(str(comp.get('policy_version') or '?'))} by compaction_report.py. "
        "The full verdict line, with every co-activation caveat, is in compaction.json.")
    return '<div class="foot">' + "<br>".join(p for p in parts if p) + "</div>"


def render(comp, detail_html="", detail_css=""):
    """The whole page. `comp` is compaction_report.py's block; None renders a stub.

    `detail_html` is the FULL existing compaction section, rendered below the
    summary. Six rounds of review pinned nine separate things that must reach
    this page — the type table with its paired-population statement, every
    blindness counter named rather than footnoted, the silence classification,
    policy_version, "no records" for an empty table. The one-pager's job is to
    put the answer FIRST, not to drop the evidence: summary on top, detail
    underneath, nothing lost. The caller passes it in so this module never
    imports its caller.
    """
    if not isinstance(comp, dict) or not comp:
        body = '<div class="banner"><b>No compaction data.</b>The report ran with no ledger to read.</div>'
        return _shell(body + detail_html, extra_css=detail_css)
    detail = (
        '<section class="section"><h2>The detail behind it</h2>' + detail_html + "</section>"
        if detail_html else ""
    )
    return _shell(
        _banner(comp) + _tiles(comp) + _table(comp) + _cards(comp) + detail + _footer(comp),
        extra_css=detail_css,
        sub=f"{_n(comp.get('observed_sessions'))} observed sessions · "
            f"measured {escape(_when(comp.get('generated_at')))}",
    )


def _shell(body, sub="", extra_css=""):
    """`extra_css` is emitted FIRST on purpose. It is the detail section's own
    stylesheet and it defines the same class names (.card, table, th, td, .pill).
    Emitted second it won on every shared selector and silently restyled the
    summary — the decision cards lost their accent bar and the verdict table
    grew full cell borders. Caught by LOOKING at the rendered page, which no
    assertion here would have."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Compaction — did it help?</title>
<style>
{extra_css}
{_CSS}
</style></head><body><main class="page">
<h1>Compaction — did it help?</h1>
<div class="sub">{sub}</div>
{body}
</main></body></html>"""
