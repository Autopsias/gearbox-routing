#!/usr/bin/env python3
"""
Office Trust Layer — Trust Review · TR-05: the language pass (voice CHECK).

ORDER MATTERS: accuracy first, language last. The reviewer's findings and the
readiness verdict are PROSE — a human reads them and decides whether a file can
travel. If that prose reads like generic AI boilerplate, the human trusts it less,
and the trust layer undercuts its own point. So AFTER the loop has converged on
accuracy (readiness is `ready` or `ready-with-limitations` — i.e. there's no open
must-fix left to argue about), we run a LANGUAGE pass over the review prose:

  CHECK  — scan the reviewer/verdict prose for AI tells (the `voice` skill's §4
           banned list) and report them. We do NOT rewrite here.
  REWRITE — if tells are found, OFFER the `voice` skill in REWRITE mode (run live
           in-session: `voice` "rewrite in my voice"). The offer is part of the
           result; the human (or the session) decides whether to take it.

Why a stdlib scanner *and* the `voice` skill: the live `voice` REWRITE is the
real instrument (it uses the installed voice skill, when present). This module is the
deterministic CHECK that (a) runs in the harness with no model call, (b) decides
whether the language pass is even WARRANTED, and (c) produces the offer. When the
session runs it live, it spawns `voice` in CHECK then (if the human accepts)
REWRITE. The banned vocabulary here is a faithful subset of `voice` §4.

GATING: `language_pass(..., readiness=...)` returns status `skipped` with reason
"accuracy not converged" unless readiness is in {ready, ready-with-limitations}.
Accuracy is never traded for prose.

Usage (library):
    from language_pass import language_pass, collect_review_prose, scan_ai_tells
"""
from __future__ import annotations

import json
import os
import re
import sys

# Readiness states at which the language pass is allowed to run (accuracy converged).
LANGUAGE_PASS_AFTER = ("ready", "ready-with-limitations")

# ---- AI tells (a faithful subset of the `voice` skill §4 banned list) ---------
# Single-word vocabulary tells (matched whole-word, case-insensitive).
_AI_VOCAB = (
    # verbs
    "delve", "foster", "harness", "unleash", "underscore", "facilitate",
    "utilize", "embark", "weave",
    # adjectives
    "multifaceted", "nuanced", "vibrant", "adept", "meticulous", "profound",
    "seamless",
    # nouns
    "tapestry", "realm", "prowess", "testament",
    # corporate jargon
    "synergies", "holistic", "robust", "scalable", "transformative",
    "game-changing", "revolutionary", "streamline", "empower",
    # intensifiers without numbers
    "significantly", "effectively", "substantially", "considerably",
    # mechanical transitions
    "moreover", "furthermore", "additionally", "consequently",
)
# Multi-word / phrase tells.
_AI_PHRASES = (
    "in today's fast-paced world", "in an ever-changing landscape",
    "it is evident that", "there is no doubt that", "it is worth noting that",
    "at the end of the day", "let me unpack", "let's dive in",
    "plays a crucial role", "plays a critical role", "plays an important role",
    "warm regards",
)
# Structural tells (regex).
_AI_STRUCT = (
    (r"it'?s not just .+?,? it'?s\b", "parallel-contrast ('it's not just X, it's Y')"),
    (r"this isn'?t about .+?,? it'?s about\b", "parallel-contrast ('this isn't about X, it's about Y')"),
    (r"\bcapable of\b", "'capable of' → use 'able to'"),
)


def _whole_word(term: str) -> re.Pattern:
    # \b doesn't play well with a trailing hyphenated token; build a tolerant edge.
    # Allow common inflections (delve/delves/delved/delving, underscore/underscores)
    # so the scan catches the tell regardless of conjugation.
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?:s|es|ed|ing|d)?(?![A-Za-z0-9])",
                      re.IGNORECASE)


def scan_ai_tells(text: str) -> list[dict]:
    """Return a list of AI-tell hits in `text`. Each hit:
    {tell, kind, count, sample}. Deterministic order (vocab, phrase, struct)."""
    hits: list[dict] = []
    low = text.lower()
    for term in _AI_VOCAB:
        m = _whole_word(term).findall(text)
        if m:
            hits.append({"tell": term, "kind": "vocabulary", "count": len(m),
                         "sample": _context(text, term)})
    for ph in _AI_PHRASES:
        c = low.count(ph)
        if c:
            hits.append({"tell": ph, "kind": "phrase", "count": c,
                         "sample": _context(text, ph)})
    for pat, label in _AI_STRUCT:
        m = list(re.finditer(pat, text, re.IGNORECASE))
        if m:
            hits.append({"tell": label, "kind": "structural", "count": len(m),
                         "sample": text[max(0, m[0].start() - 20): m[0].end() + 20].strip()})
    return hits


def _context(text: str, term: str, width: int = 30) -> str:
    i = text.lower().find(term.lower())
    if i < 0:
        return ""
    return text[max(0, i - width): i + len(term) + width].strip()


def collect_review_prose(result: dict) -> str:
    """Pull the human-facing PROSE out of a review-result dict: the readiness
    reason, the must-own descriptions, the limitations, and each finding's
    evidence + suggested_repair. This is the text a human reads — so it's the
    text the language pass checks."""
    parts: list[str] = []
    if result.get("readiness_reason"):
        parts.append(result["readiness_reason"])
    if result.get("status_reason"):
        parts.append(result["status_reason"])
    for m in result.get("must_own", []):
        parts.append(m.get("what", ""))
    for lim in result.get("limitations", []):
        parts.append(lim)
    for f in result.get("findings", []):
        parts.append(f.get("evidence", "") or "")
        parts.append(f.get("suggested_repair", "") or "")
    return "\n".join(p for p in parts if p)


def language_pass(result: dict, *, readiness: str | None = None,
                  out_dir: str | None = None, write_files: bool = False) -> dict:
    """Run the TR-05 language CHECK over a review result's prose.

    GATED: only runs when accuracy has converged (readiness in
    {ready, ready-with-limitations}); otherwise status `skipped`.

    Returns: {status, readiness, tells[], offer, prose_chars}. `offer` is the
    voice-REWRITE offer — present (and `rewrite_recommended` True) iff tells were
    found. The live REWRITE is performed by the `voice` skill in-session."""
    readiness = readiness if readiness is not None else result.get("readiness", "")
    if readiness not in LANGUAGE_PASS_AFTER:
        out = {"skill": "trust-review", "phase": "language-pass", "status": "skipped",
               "readiness": readiness,
               "reason": "accuracy not converged (readiness=%r) — language pass runs "
                         "only after the loop reaches ready / ready-with-limitations; "
                         "accuracy is never traded for prose." % readiness,
               "tells": [], "rewrite_recommended": False}
        if write_files and out_dir:
            _write(out_dir, "language-pass.json", out)
        return out

    prose = collect_review_prose(result)
    tells = scan_ai_tells(prose)
    rewrite = bool(tells)
    out = {
        "skill": "trust-review", "phase": "language-pass", "status": "ok",
        "readiness": readiness,
        "mode": "CHECK",
        "prose_chars": len(prose),
        "tells": tells,
        "tell_count": sum(t["count"] for t in tells),
        "rewrite_recommended": rewrite,
        "offer": (
            "%d AI-tell(s) found in the review prose. Offer a voice REWRITE: run the "
            "`voice` skill in REWRITE mode over review-result.md (\"rewrite in my "
            "voice\"). CHECK only — the prose was NOT rewritten here." % len(tells)
            if rewrite else
            "No AI tells found in the review prose — it reads clean; no REWRITE needed."),
        "voice_skill": {"name": "voice", "check_mode": "CHECK", "rewrite_mode": "REWRITE",
                        "note": "live CHECK/REWRITE uses the installed voice skill, when "
                                "present; this scanner is the deterministic gate."},
    }
    if write_files and out_dir:
        _write(out_dir, "language-pass.json", out)
        _write_md(out_dir, "language-pass.md", _md(out))
    return out


def _md(out: dict) -> str:
    lines = ["# Trust Review — language pass (TR-05, voice CHECK)\n",
             "Readiness: **%s** · mode: %s · prose scanned: %d chars\n"
             % (out["readiness"], out.get("mode", "—"), out.get("prose_chars", 0))]
    if not out["tells"]:
        lines.append("No AI tells found — the review prose reads clean.\n")
        return "\n".join(lines)
    lines.append("\n| tell | kind | count | context |")
    lines.append("|---|---|---|---|")
    for t in out["tells"]:
        lines.append("| %s | %s | %d | %s |"
                     % (t["tell"], t["kind"], t["count"], (t.get("sample") or "")[:50]))
    lines.append("\n> %s" % out["offer"])
    return "\n".join(lines) + "\n"


def _write(out_dir, name, obj):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")


def _write_md(out_dir, name, text):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="TR-05 language pass (voice CHECK) over a review result")
    ap.add_argument("--result", required=True, help="review-result.json")
    ap.add_argument("--out", help="dir to write language-pass.{json,md}")
    args = ap.parse_args(argv)
    with open(args.result, encoding="utf-8") as fh:
        result = json.load(fh)
    out = language_pass(result, out_dir=args.out, write_files=bool(args.out))
    print(json.dumps({k: out[k] for k in ("status", "readiness", "tell_count",
          "rewrite_recommended") if k in out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
