#!/usr/bin/env python3
"""
Office Trust Layer — Workbook Doctor · WD-04: finance-model domain hook.

The Workbook Doctor core (WD-01/02/03) is **domain-agnostic** — it never assumes
a workbook is financial. This module is the OPT-IN extension: it inspects the
WD-01 map + WD-02 formula-risk + (optional) source packet, decides whether the
workbook is a *financial model*, and — only then — proposes which `finance:*`
Cowork validators should run on top of the generic checks.

Design contract (plan WD-04 + capability-inventory.md):
  * `enabled=False` (the `--no-finance` flag) -> hook is inert, `finance_hooks=[]`,
    even when a financial model is detected. The core never depends on it.
  * Detection is deterministic and works off the upstream JSON + packet ONLY
    (no second workbook open) — faithful to the I/O contract.
  * The hook RECOMMENDS validators (with the trigger signal + the inputs to feed
    them); it does not execute them here. `finance:*` are invokable Cowork skills
    (markdown), proven available in the session registry (capability-inventory.md);
    a session that detects a financial model invokes them via the Skill tool.

`finance:*` validators wired:
  variance-analysis · reconciliation · audit-support · financial-statements
"""
import os
import re
from pathlib import Path


_VALIDATORS = ("variance-analysis", "reconciliation", "audit-support", "financial-statements")


def installed_validators():
    """The finance:* validators this machine has, by exact skill folder name (with a SKILL.md).

    WD_FINANCE_SKILLS=1 says all four are there. An adopter without them must not be told to run them.
    """
    if os.environ.get("WD_FINANCE_SKILLS") in ("1", "true", "yes"):
        return set(_VALIDATORS)
    roots = [Path.home() / ".claude" / "skills", Path.home() / ".codex" / "skills"]
    if os.environ.get("SKILLS_ROOT"):
        roots.append(Path(os.environ["SKILLS_ROOT"]))
    found = set()
    for root in (r for r in roots if r.is_dir()):
        for name in _VALIDATORS:
            pats = (f"{name}/SKILL.md", f"*/{name}/SKILL.md", f"*/skills/{name}/SKILL.md")
            if any(next(root.glob(pat), None) for pat in pats):
                found.add(name)
    return found


def finance_skills_present():
    """True when at least one finance:* validator is installed."""
    return bool(installed_validators())


# Financial metric / line-item vocabulary (lower-cased substrings).
_FIN_TERMS = [
    "revenue", "sales", "turnover", "ebitda", "ebit", "margin", "gross profit",
    "net income", "operating income", "opex", "capex", "cash flow", "cashflow",
    "p&l", "pnl", "profit", "loss", "balance sheet", "income statement",
    "headcount", "fte", "forecast", "actuals", "budget", "variance", "yoy",
    "cogs", "arr", "mrr", "churn", "bookings", "backlog", "working capital",
    "free cash", "fcf", "depreciation", "amortis", "amortiz", "accrual",
]
_FIN_METRICS = {
    "revenue", "revenue_yoy", "ebitda_margin", "ebitda", "headcount",
    "cloud_migration_spend", "gross_margin", "opex", "capex", "cash_flow",
}


def _gather_terms(map_doc, fr_doc, packet):
    """Lower-cased text surface available WITHOUT re-opening the workbook:
    sheet names, period-band labels, finding messages, packet metrics/claims."""
    texts = []
    for s in (map_doc.get("sheets") or []):
        texts.append(str(s.get("name", "")))
        texts.append(str((s.get("classification") or {}).get("type", "")))
    for b in (fr_doc.get("period_bands") or []):
        texts.extend(str(x) for x in (b.get("labels") or []))
    for f in (map_doc.get("findings") or []) + (fr_doc.get("findings") or []):
        texts.append(str(f.get("message", "")))
    metrics = set()
    if packet:
        for cl in (packet.get("claims") or []):
            metrics.add((cl.get("metric") or "").lower())
            prov = cl.get("provenance") or {}
            texts.append(str(prov.get("claim", "")))
    return " \n ".join(texts).lower(), metrics


def detect(map_doc, fr_doc, packet):
    """Return {is_financial, score, signals[]} — deterministic, JSON-only."""
    blob, metrics = _gather_terms(map_doc, fr_doc, packet)
    signals = []

    # letter-boundary match so "arr" doesn't fire on "carries" nor "ebit" on "ebitda"
    def _term_present(t):
        return re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", blob) is not None
    term_hits = sorted({t for t in _FIN_TERMS if _term_present(t)})
    if term_hits:
        signals.append("financial terms: %s" % ", ".join(term_hits[:6]))

    metric_hits = sorted(metrics & _FIN_METRICS)
    if metric_hits:
        signals.append("packet financial metrics: %s" % ", ".join(metric_hits[:6]))

    if fr_doc.get("period_bands"):
        signals.append("time-series period axis detected")

    # actuals-vs-forecast (variance) signal — both an actuals and a forecast/budget surface
    has_actual = bool(re.search(r"actual", blob))
    has_plan = bool(re.search(r"forecast|budget|plan", blob))
    if has_actual and has_plan:
        signals.append("actuals-vs-forecast pair present")

    # a source packet carrying conflicts over financial metrics -> reconciliation signal
    conflict_metrics = []
    if packet:
        for cl in (packet.get("claims") or []):
            if (cl.get("review_status") == "conflicting") and (cl.get("metric", "").lower() in _FIN_METRICS):
                conflict_metrics.append(cl.get("metric"))
    if conflict_metrics:
        signals.append("conflicting financial figures: %s" % ", ".join(sorted(set(conflict_metrics))))

    # Score = number of independent signal categories.
    score = len(signals)
    # Financial iff at least two independent signals (terms alone is weak; a
    # single "margin" mention shouldn't tip a generic sheet into the finance path).
    is_financial = score >= 2 or bool(metric_hits)
    return {"is_financial": is_financial, "score": score, "signals": signals,
            "_term_hits": term_hits, "_metric_hits": metric_hits,
            "_has_actual": has_actual, "_has_plan": has_plan,
            "_conflict_metrics": sorted(set(conflict_metrics))}


def dispatch_plan(map_doc, fr_doc, packet, det):
    """Which finance:* validators to run, why, and what to feed them.

    Returns a list of {validator, why, inputs}. Only validators whose trigger
    signal is present are recommended (keeps the plan precise, not a blanket
    "run everything")."""
    hooks = []
    sheet_names = [s.get("name") for s in (map_doc.get("sheets") or [])]

    # variance-analysis — when there's a time axis or an actuals/forecast pair
    if fr_doc.get("period_bands") or (det["_has_actual"] and det["_has_plan"]):
        hooks.append({
            "validator": "variance-analysis",
            "why": "a time axis / actuals-vs-forecast pair is present — decompose the period-over-period movement into drivers and check the bridge ties out.",
            "inputs": "the period-band rows%s" % (
                " + the Actuals/Forecast sheets" if (det["_has_actual"] and det["_has_plan"]) else ""),
        })

    # reconciliation — when conflicting financial figures exist (multiple sources disagree)
    if det["_conflict_metrics"]:
        hooks.append({
            "validator": "reconciliation",
            "why": "conflicting figures across sources (%s) — reconcile the displayed value to the authoritative source and preserve the losing values, don't blend." % ", ".join(det["_conflict_metrics"]),
            "inputs": "the source-packet conflict log + the deliverable cells carrying the figures",
        })

    # audit-support — when there are unsupported numbers or the file is board/regulatory-bound
    has_unsupported = any(
        (f.get("detail") or {}).get("review_status") == "unsupported"
        for f in (fr_doc.get("findings") or []) if f.get("check") == "source_packet_link")
    if has_unsupported:
        hooks.append({
            "validator": "audit-support",
            "why": "unsupported number(s) are presented as fact — build the control/evidence trail (sample selection + workpaper) before the figure travels to a board.",
            "inputs": "the unsupported source_packet_link findings + their claim IDs",
        })

    # financial-statements — when P&L / balance-sheet / cash-flow line items are present
    if re.search(r"p&l|pnl|income statement|balance sheet|cash flow|cashflow", " ".join(str(x).lower() for x in sheet_names) + " " + " ".join(det["_term_hits"])):
        hooks.append({
            "validator": "financial-statements",
            "why": "statement-level line items present — check GAAP presentation and period-over-period comparison.",
            "inputs": "the statement sheet(s): %s" % ", ".join(sheet_names),
        })

    return hooks


def assess(map_doc, fr_doc, packet, enabled=True):
    """Top-level WD-04 entry. Domain-agnostic core stays intact when disabled.

    The hook is off unless a finance:* skill is installed, and it recommends only the validators that are
    installed (installed_validators()), so a recommendation never names a skill the adopter does not have. `enabled=False` forces it off.
    """
    if not enabled or not finance_skills_present():
        return {"enabled": False, "is_financial": None, "score": 0,
                "signals": [], "finance_hooks": [],
                "note": "finance domain hook off (--no-finance, or no finance:* skill installed); generic checks only."}
    det = detect(map_doc, fr_doc, packet)
    have = installed_validators()
    hooks = [h for h in dispatch_plan(map_doc, fr_doc, packet, det) if h["validator"] in have] if det["is_financial"] else []
    return {
        "enabled": True,
        "is_financial": det["is_financial"],
        "score": det["score"],
        "signals": det["signals"],
        "finance_hooks": hooks,
        "note": ("financial model detected — %d finance:* validator(s) recommended on top of generic checks."
                 % len(hooks)) if det["is_financial"]
                else "not a financial model — generic, domain-agnostic checks only.",
    }
