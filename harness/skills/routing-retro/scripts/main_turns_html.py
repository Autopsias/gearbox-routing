"""HTML for the 'Main-session turns' section of the retro report (input: main_session_turns.py JSON)."""
import html


def _e(v):
    """Escape one value; a missing measurement reads n/a, never None."""
    return html.escape("n/a" if v is None else str(v))


def _pct(v):
    return "n/a" if v is None else "%d%%" % round(v * 100)


def _cell(c):
    return ("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (_e(c["task_type"]), _e(c["model"]), _e(c["effort"]), _e(c["turns"]), _e(c["sessions"]),
               _pct(c["pushback_rate"]), _pct(c["moved_up_after_rate"]), _e(c["nudges_shown"]),
               _pct(c["followed_share"]), _e(c["status"])))


def _proposal(p):
    detail = ", ".join("%s %s" % (k, v) for k, v in p.items() if k not in ("kind", "task_type", "status"))
    return "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (_e(p["kind"]), _e(p["task_type"]), _e(p["status"]), _e(detail))


def section(mt):
    """One section. Empty or missing input renders a plain 'no records' line, never a blank table."""
    if not mt:
        return '<h2>Main-session turns</h2><p class="empty">No turn records were given.</p>'
    h = mt.get("health", {})
    verdict = mt.get("display_verdict")
    banner = (' <strong class="apex">Re-probe the payload facts: the hook may have stopped recording.</strong>'
              if verdict == "broken" else "")
    if mt.get("ledger_missing"):
        banner += ' <strong class="apex">The ledger file %s does not exist.</strong>' % _e(mt.get("ledger"))
    cells = "".join(_cell(c) for c in mt.get("cells", [])) or "<tr><td colspan='10'>no records</td></tr>"
    props = "".join(_proposal(p) for p in mt.get("table_proposals", [])) or "<tr><td colspan='4'>none</td></tr>"
    return (
        "<h2>Main-session turns</h2>"
        "<p>Display verdict: <strong>%s</strong>.%s Lines %s, model unknown %s, effort unknown %s, last 7 days %s. "
        "Nudges shown %s, known outcomes %s. Malformed %s, skipped %s (headless or not the owner's model switch).</p>"
        "<table><thead><tr><th>Type</th><th>Model</th><th>Effort</th><th>Turns</th><th>Sessions</th><th>Pushback</th>"
        "<th>Moved up after</th><th>Nudges</th><th>Followed</th><th>Status</th></tr></thead><tbody>%s</tbody></table>"
        "<h3>Table proposals (evidence only; the owner approves)</h3>"
        "<table><thead><tr><th>Kind</th><th>Type</th><th>Status</th><th>Detail</th></tr></thead><tbody>%s</tbody></table>"
        % (_e(verdict), banner, _e(h.get("lines")), _pct(h.get("model_null_share")), _pct(h.get("effort_null_share")),
           _e(h.get("lines_last_7_days")), _e(h.get("nudges_shown")), _e(h.get("followed_n")), _e(mt.get("malformed")), _e(mt.get("skipped")), cells, props))
