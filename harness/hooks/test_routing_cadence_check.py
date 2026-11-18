#!/usr/bin/env python3
"""Self-check for routing-cadence-check.py.

Plant/allow-control pairs: growth-over-threshold fires, growth-under stays
silent, truncation fires, zero mean_record_size stays silent even with
growth. Run directly: `python3 hooks/test_routing_cadence_check.py`.
"""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "routing-cadence-check.py"


def load():
    spec = importlib.util.spec_from_file_location("routing_cadence_check_test_target", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mod_const(name):
    return getattr(load(), name)


def run_with(tmp, ledger_bytes, stamp):
    mod = load()
    ledger = tmp / "outcomes.ndjson"
    stamp_path = tmp / ".last-aggregated"
    ledger.write_bytes(b"x" * ledger_bytes)
    stamp_path.write_text(json.dumps(stamp))
    mod.LEDGER = str(ledger)
    mod.STAMP = str(stamp_path)
    mod.REVIEW_STAMP = mod.REVIEW_NOTES = str(tmp / "absent")  # review reminder silent here
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.main()
    return buf.getvalue().strip()


def run_review(tmp, stamp_age_days, notes_exist):
    import os
    import time
    mod = load()
    mod.LEDGER = mod.STAMP = str(tmp / "absent-ledger")
    mod.REVIEW_STAMP = str(tmp / "review-stamp")
    mod.REVIEW_NOTES = str(tmp / "notes.md")
    for p in (mod.REVIEW_STAMP, mod.REVIEW_NOTES):
        if os.path.exists(p):
            os.remove(p)
    if notes_exist:
        Path(mod.REVIEW_NOTES).write_text("x")
    if stamp_age_days is not None:
        Path(mod.REVIEW_STAMP).write_text("x")
        t = time.time() - stamp_age_days * 86400
        os.utime(mod.REVIEW_STAMP, (t, t))
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.main()
    return buf.getvalue().strip()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        assert run_review(tmp, 5, True) == "", "fresh stamp must be silent"
        out = run_review(tmp, 40, True)
        assert json.loads(out)["systemMessage"].count("last whole-history review: 40 days ago") == 1, out
        assert json.loads(out)["systemMessage"].endswith("/myusage-self-assessment")
        assert "never recorded" in run_review(tmp, None, True)
        assert run_review(tmp, None, False) == "", "no stamp and no notes must be silent"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # PLANT: growth >= 30 * mean_record_size fires.
        out = run_with(tmp, 1000 + 30 * 50, {"offset": 1000, "mean_record_size": 50})
        assert "roughly 30+" in out, f"expected fire, got: {out!r}"

        # ALLOW: growth just under threshold stays silent.
        out = run_with(tmp, 1000 + 29 * 50, {"offset": 1000, "mean_record_size": 50})
        assert out == "", f"expected silence, got: {out!r}"

        # PLANT: ledger smaller than stamped offset -> truncation warning.
        out = run_with(tmp, 500, {"offset": 1000, "mean_record_size": 50})
        assert "truncated" in out, f"expected truncation warning, got: {out!r}"

        # PLANT: the SEEDED stamp is exactly
        # {"offset": 0, ..., "mean_record_size": 0}, and returning early on that
        # made the nudge unfireable — only /routing-retro writes a real mean, and
        # this nudge is what prompts /routing-retro. A bootstrap deadlock. With no
        # mean the hook now sizes the threshold from BOOTSTRAP_RECORD_SIZE.
        seeded = {"offset": 0, "size": 0, "mtime": 0, "mean_record_size": 0}
        big = 30 * mod_const("BOOTSTRAP_RECORD_SIZE")
        out = run_with(tmp, big, seeded)
        assert "roughly 30+" in out, f"expected the bootstrap fire, got: {out!r}"
        assert "estimated" in out, "an estimated threshold must say so"

        # ALLOW CONTROL: one byte under the same bootstrap threshold stays silent,
        # so the fire above is not simply "always fires".
        out = run_with(tmp, big - 1, seeded)
        assert out == "", f"expected silence just under bootstrap, got: {out!r}"

        # ...and a stamp WITH a real mean is not described as estimated.
        out = run_with(tmp, 1000 + 30 * 50, {"offset": 1000, "mean_record_size": 50})
        assert "estimated" not in out, out

        # ALLOW: exactly at offset (no growth) -> silent.
        out = run_with(tmp, 1000, {"offset": 1000, "mean_record_size": 50})
        assert out == "", f"expected silence at zero growth, got: {out!r}"

    print("routing-cadence-check: all checks passed")


if __name__ == "__main__":
    main()
