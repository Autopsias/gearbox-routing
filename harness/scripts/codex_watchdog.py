#!/usr/bin/env python3
"""No-OUTPUT watchdog for the Codex background invocation used by
/adversarial-review and /plan-harden (OR-02, S05 2026-07-03).

Root incident: the Codex sidecar once sat silent for 1h43m at 0% CPU inside a
plan-harden run before a human noticed — the loop only checked whether the
`Bash(run_in_background)` shell had EXITED, never whether the raw-output file
was still GROWING. A process can be alive (shell not exited) yet produce zero
bytes for an unbounded time; that is the hang this watches for.

This module is a pure decision function (no polling loop, no subprocess) so
it's cheap to call from the orchestrator once per turn and to unit test.
State is a tiny JSON sidecar (`<raw_path>.watchdog.json`) tracking the last
size we saw and when we saw it — the orchestrator doesn't need to hold state
across turns itself.

Usage (from the orchestrator, once per poll turn):

    python3 codex_watchdog.py check /tmp/adversarial-review-...-codex.raw.md \
        --no-output-window-s 600

Exit 0 + JSON {"status": "growing"|"stalled_ok"|"hung", ...}. "hung" means:
kill the Bash shell, degrade to Claude-only findings, notify. Never a
network call, never touches the Codex process itself — the caller (the
orchestrator, which owns the Bash shell id) does the kill.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

DEFAULT_NO_OUTPUT_WINDOW_S = 600.0  # 10 minutes of zero growth => hung


def _sidecar_path(raw_path):
    return Path(str(raw_path) + ".watchdog.json")


def _load_state(raw_path):
    sc = _sidecar_path(raw_path)
    if sc.exists():
        try:
            return json.loads(sc.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _save_state(raw_path, state):
    _sidecar_path(raw_path).write_text(json.dumps(state))


def check(raw_path, now=None, no_output_window_s=DEFAULT_NO_OUTPUT_WINDOW_S, state=None):
    """Pure decision function — given the raw-output path (or a size/state
    triple for testing), decide growing / stalled_ok / hung / missing.

    `state` overrides the on-disk sidecar (for tests); when None, loads +
    persists the sidecar as a side effect (the normal CLI path).
    """
    now = time.time() if now is None else now
    p = Path(raw_path)
    persist = state is None
    if not p.exists():
        return {"status": "missing", "reason": f"{raw_path} does not exist yet"}

    size = p.stat().st_size
    prev = state if state is not None else (_load_state(raw_path) or {})
    prev_size = prev.get("size")
    prev_seen_growth_at = prev.get("last_growth_at", now)

    if prev_size is None or size > prev_size:
        # First observation, or output grew since last check: reset the clock.
        new_state = {"size": size, "last_growth_at": now, "last_checked_at": now}
        if persist:
            _save_state(raw_path, new_state)
        return {"status": "growing", "size": size, "last_growth_at": now}

    # No growth since last check.
    stalled_for = now - prev_seen_growth_at
    new_state = {"size": size, "last_growth_at": prev_seen_growth_at, "last_checked_at": now}
    if persist:
        _save_state(raw_path, new_state)

    if stalled_for >= no_output_window_s:
        return {
            "status": "hung",
            "size": size,
            "stalled_for_seconds": stalled_for,
            "no_output_window_s": no_output_window_s,
            "reason": f"no growth in raw output for {stalled_for:.0f}s "
            f"(>= {no_output_window_s:.0f}s window) — kill, degrade to "
            "Claude-only, notify",
        }
    return {
        "status": "stalled_ok",
        "size": size,
        "stalled_for_seconds": stalled_for,
        "no_output_window_s": no_output_window_s,
    }


def reset(raw_path):
    """Clear the sidecar (call once at the START of a fresh Codex background
    launch, so a stale sidecar from a prior review doesn't false-positive)."""
    sc = _sidecar_path(raw_path)
    if sc.exists():
        sc.unlink()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("raw_path")
    c.add_argument("--no-output-window-s", type=float, default=DEFAULT_NO_OUTPUT_WINDOW_S)

    r = sub.add_parser("reset")
    r.add_argument("raw_path")

    args = ap.parse_args(argv)
    if args.cmd == "check":
        result = check(args.raw_path, no_output_window_s=args.no_output_window_s)
    elif args.cmd == "reset":
        reset(args.raw_path)
        result = {"status": "reset"}
    print(json.dumps(result, indent=2))
    return 1 if result.get("status") == "hung" else 0


if __name__ == "__main__":
    sys.exit(main())
