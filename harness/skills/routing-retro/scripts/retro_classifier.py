#!/usr/bin/env python3
"""retro_classifier.py — retro-classification of the 'before' cohort.

Extracted from compaction_retro.py when the file crossed its size bound; same
functions, same import surface (compaction_retro re-exports them, so
`cr.retro_classify` / `cr.load_classifier` still resolve for every caller).
"""

import os


def retro_classify(first_prompt, classifier):
    """Policy files only exist after the classifier shipped, so a per-type
    before/after would read 'underpowered' forever BY CONSTRUCTION. The
    classifier is a pure function of the first prompt's text, so apply the same
    function to historical sessions and tag the result type_source: 'retro'."""
    if not first_prompt or classifier is None:
        return None
    try:
        return classifier(first_prompt)
    except Exception:                                  # noqa: BLE001 - never fatal
        return None


def load_classifier():
    """hooks/compact-policy.py's own `classify` — imported, never reimplemented,
    so a retro-classified type can never drift from the live one."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    for cand in (os.path.join(root, "hooks", "compact-policy.py"),
                 os.path.expanduser("~/.claude/hooks/compact-policy.py")):
        if not os.path.exists(cand):
            continue
        import importlib.util                          # noqa: PLC0415
        spec = importlib.util.spec_from_file_location("_compact_policy", cand)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:                              # noqa: BLE001
            continue
        fn = getattr(mod, "classify", None)
        if callable(fn):
            return fn
    return None
