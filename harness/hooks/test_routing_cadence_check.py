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
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.main()
    return buf.getvalue().strip()


def main():
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
