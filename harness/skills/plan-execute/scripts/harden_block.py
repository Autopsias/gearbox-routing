"""/plan-harden summary block in PLAN.html: not builder output, so drift ignores it."""
import re

# /plan-harden writes its summary between these markers after the last rebuild
# (commands/plan-harden.md §4.2). It is not builder output: drift checks ignore it
# and every re-render carries it over, so a hardened plan can still be mutated.
BLOCK = re.compile(
    r"<!-- PLAN-HARDEN-SUMMARY:BEGIN -->.*?<!-- PLAN-HARDEN-SUMMARY:END -->\n?", re.S
)


def without(html):
    return BLOCK.sub("", html, count=1)


def carry(prior_html, html):
    m = BLOCK.search(prior_html)
    if not m or "</main>" not in html:
        return html
    block = m.group(0) if m.group(0).endswith("\n") else m.group(0) + "\n"
    return html.replace("</main>", block + "</main>", 1)
