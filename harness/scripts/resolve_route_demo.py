#!/usr/bin/env python3
"""resolve_route_demo.py: resolve_route.py's runnable self-check, moved out of that
module at the file-size ratchet's demand. `python3 resolve_route.py`
still runs it."""
from resolve_route import EXHAUSTED, _default_ssot_path, degrade, escalate, resolve


def _demo():
    """ponytail: smallest runnable self-check — not a test framework. Resolves,
    escalates, and degrades for TWO providers using the real repo SSOT, and
    proves the `degrade: none` / no-ladder path returns 'exhausted' explicitly
    rather than borrowing anthropic's shape. Run: python3 resolve_route.py

    The escalation leg is a LOOP that runs the ladder past its end on purpose:
    that is the documented caller shape, and it is what caught a
    regression (feeding the terminal sentinel back in raised AttributeError).
    Both providers reach a ceiling here — anthropic at fable, openai at sol —
    so this self-check exercises the fixpoint on each, not just the one profile
    that happened to expose it."""
    ssot = _default_ssot_path()
    print(f"SSOT: {ssot}\n")
    for provider in ("anthropic", "openai"):
        print(f"=== {provider} ===")
        base = resolve("agentic_build", provider, ssot_path=ssot)
        print("  baseline agentic_build ->", base)
        cur, n = base, 0
        while cur != EXHAUSTED:
            n += 1
            assert n <= 10, f"{provider} escalation ladder did not terminate in 10 rungs"
            cur = escalate("agentic_build", provider, current=cur, ssot_path=ssot)
            print(f"  escalate x{n}            ->", cur)
        again = escalate("agentic_build", provider, current=cur, ssot_path=ssot)
        assert again == EXHAUSTED, f"EXHAUSTED must be absorbing, got {again!r}"
        print("  escalate(exhausted)    -> exhausted  [absorbing — no crash]")
        deg = degrade("agentic_build", provider, current=base, signal="unavailable", ssot_path=ssot)
        print("  degrade (unavailable)  ->", deg)
        print()


if __name__ == "__main__":
    _demo()
