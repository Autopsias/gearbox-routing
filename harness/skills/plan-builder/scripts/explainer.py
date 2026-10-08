"""The plan, explained — the eli5-derived explainer section of PLAN.html.

Renders the optional top-level `explainer` spec field into one static section:
a causal chain of steps (each step mapped to session ids), one qualified
analogy, up to a few defined terms, and a one-line recap. The section's LIVE
half — status coloring, the "you are here" marker, and the "Where we are"
paragraph — is computed in the browser by the template's static
`renderExplainer()` on every load, from the same `data-status` attributes the
donuts and the session arc already read. Nothing at runtime writes here, so
the explainer can never go stale.

Authoring rules for the field's CONTENT live in the eli5 skill
(`skills/eli5/SKILL.md` → "Rules for the page"); the field reference lives in
`references/schemas.md` → "Explainer". The field is optional: a spec without
it builds byte-identical to before this module existed.

Kept out of build_plan.py because that file sits at its file-size ratchet
bound (the ratchet is right: extract, don't bump).
"""

import sys

# eli5's own bound: 3 sections, 5 at the most. Advisory here — a longer chain
# renders fine, it just stops being an explainer.
CHAIN_SOFT_MAX = 5


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attr(s):
    return _esc(s).replace('"', "&quot;")


def _validate_chain(chain, session_ids):
    if not isinstance(chain, list) or len(chain) < 2:
        raise ValueError(
            "'explainer.chain' must be a list of at least 2 steps — one step is "
            "not a chain, and the chain is the whole point of the section"
        )
    for i, step in enumerate(chain, 1):
        if not isinstance(step, dict):
            raise ValueError(f"explainer.chain[{i}] must be an object")
        for f in ("label", "caption", "sessions"):
            if not step.get(f):
                raise ValueError(f"explainer.chain[{i}] missing '{f}'")
        if not isinstance(step["sessions"], list):
            raise ValueError(
                f"explainer.chain[{i}].sessions must be a list of session ids"
            )
        unknown = [s for s in step["sessions"] if s not in session_ids]
        if unknown:
            raise ValueError(
                f"explainer.chain[{i}] ({step['label']!r}) references unknown "
                f"session(s): {unknown}"
            )


def _validate_analogy_and_terms(ex):
    analogy = ex.get("analogy")
    if analogy is not None and (
        not isinstance(analogy, dict)
        or not analogy.get("text")
        or not analogy.get("breaks")
    ):
        raise ValueError(
            "'explainer.analogy' needs both 'text' and 'breaks' — an "
            "unqualified analogy plants lasting wrong ideas (eli5 rule); "
            "say where it stops being true or leave it out"
        )
    terms = ex.get("terms")
    if terms is None:
        return
    if not isinstance(terms, list):
        raise ValueError("'explainer.terms' must be a list")
    for t in terms:
        if not isinstance(t, dict) or not t.get("name") or not t.get("definition"):
            raise ValueError("each explainer.terms entry needs 'name' and 'definition'")


def _warn_advisories(spec, chain):
    if len(chain) > CHAIN_SOFT_MAX:
        print(
            f"WARNING: explainer.chain has {len(chain)} steps; eli5's bound is "
            f"{CHAIN_SOFT_MAX}. Merge steps until each one carries a cause the "
            f"next one needs.",
            file=sys.stderr,
        )
    covered = {s for step in chain for s in step["sessions"]}
    uncovered = [s.get("id") for s in spec.get("sessions", []) if s.get("id") not in covered]
    if uncovered:
        print(
            f"WARNING: explainer.chain covers no step for session(s) "
            f"{uncovered} — fine for infra/closing sessions, but the 'Where we "
            f"are' line skips them.",
            file=sys.stderr,
        )


def validate_explainer(spec):
    """Raise ValueError on a malformed `explainer` field. Absent is valid."""
    ex = spec.get("explainer")
    if ex is None:
        return
    if not isinstance(ex, dict):
        raise ValueError("'explainer' must be an object")
    session_ids = {s.get("id") for s in spec.get("sessions", [])}
    _validate_chain(ex.get("chain"), session_ids)
    _validate_analogy_and_terms(ex)
    _warn_advisories(spec, ex["chain"])


def render_explainer_section(spec):
    """Return the section's HTML, or '' when the spec has no explainer."""
    ex = spec.get("explainer")
    if not ex:
        return ""

    steps = []
    for i, step in enumerate(ex["chain"], 1):
        unlocks = step.get("unlocks", "")
        unlocks_html = (
            f'\n      <div class="step-unlocks">{_esc(unlocks)}</div>' if unlocks else ""
        )
        steps.append(
            f'''<div class="explainer-step" data-chain-step data-sessions="{_attr(" ".join(step["sessions"]))}"
         data-label="{_attr(step["label"])}" data-caption="{_attr(step["caption"])}" data-unlocks="{_attr(unlocks)}">
      <div class="step-head"><span class="step-num">{i}</span></div>
      <div class="step-label">{_esc(step["label"])}</div>
      <div class="step-caption">{_esc(step["caption"])}</div>{unlocks_html}
    </div>'''
        )
    chain_html = '\n    <div class="explainer-arrow" aria-hidden="true">&#8594;</div>\n    '.join(
        steps
    )

    analogy = ex.get("analogy")
    analogy_html = ""
    if analogy:
        analogy_html = f'''
  <div class="explainer-analogy">
    <strong>Think of it as</strong>
    {_esc(analogy["text"])}
    <span class="analogy-breaks"><strong>Where the analogy stops:</strong> {_esc(analogy["breaks"])}</span>
  </div>'''

    terms = ex.get("terms") or []
    terms_html = ""
    if terms:
        dts = "".join(
            f'<dt>{_esc(t["name"])}</dt><dd>{_esc(t["definition"])}</dd>' for t in terms
        )
        terms_html = f'''
  <dl class="explainer-terms">{dts}</dl>'''

    recap = ex.get("recap", "")
    recap_html = (
        f'\n  <p class="explainer-recap">{_esc(recap)}</p>' if recap else ""
    )

    return f'''<!-- THE PLAN, EXPLAINED — eli5-derived; live half repainted by renderExplainer() -->
<section class="plan-explained" id="plan-explained" data-role="human-display" data-recap="{_attr(recap)}">
  <h2 class="explainer-title">The plan, explained</h2>
  <div class="explainer-chain">
    {chain_html}
  </div>
  <div class="explainer-now" id="explainer-now" data-role="auto-render">
    <strong>Where we are</strong>
    <p id="explainer-now-text"></p>
  </div>{analogy_html}{terms_html}{recap_html}
</section>'''
