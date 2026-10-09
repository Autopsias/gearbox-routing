"""model_prices: one billing table by served model id, read by the routing retro and /cost-audit."""
import pathlib
import re

import pytest
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import model_prices  # noqa: E402

SSOT = """version: 1
prices:
  opus:    { in:  4.00, out: 20.00 }
model_prices:
  claude-opus-5-5:  { in: 4.00,  out: 20.00, cache_read: 0.20 }
  claude-opus-5:    { in: 5.00,  out: 25.00, cache_read: 0.50 }
  claude-haiku-4-5: { in: 1.00,  out:  5.00, cache_read: 0.10, cache_write_5m: 9.9, cache_write_1h: 7.7 }
task_classes:
  mechanical: { tier: cheap_fast }
"""


def test_rates_match_the_served_id_exactly(tmp_path):
    p = tmp_path / "ssot.yaml"
    p.write_text(SSOT)
    t = model_prices.load(str(p))
    assert t["claude-opus-5-5"] == (4.0, 20.0, 0.2, 5.0, 8.0)   # writes default to 1.25x / 2x of in
    assert model_prices.rates(t, "claude-opus-5-5") == (4.0, 20.0, 0.2, 5.0, 8.0)
    assert model_prices.rates(t, "claude-opus-5") == (5.0, 25.0, 0.5, 6.25, 10.0)   # history keeps its own rate
    assert model_prices.rates(t, "claude-opus-5[1m]") == (5.0, 25.0, 0.5, 6.25, 10.0)
    assert model_prices.rates(t, "claude-haiku-4-5-20251001") == (1.0, 5.0, 0.1, 9.9, 7.7)   # explicit row wins
    assert model_prices.rates(t, "claude-opus-5-6") is None          # a new model is unknown, not Opus 5
    assert model_prices.rates(t, None) is None


def test_the_block_ends_at_the_next_top_level_key_and_is_optional(tmp_path):
    p = tmp_path / "ssot.yaml"
    p.write_text(SSOT)
    assert set(model_prices.load(str(p))) == {"claude-opus-5-5", "claude-opus-5", "claude-haiku-4-5"}
    p.write_text("prices:\n  opus: { in: 4.00, out: 20.00 }\n")
    assert model_prices.load(str(p)) == {}


def _public_example_ssot():
    """True when the SSOT beside this tree is the public export's EXAMPLE policy.

    The public export mirrors its hand-maintained example policy (semver
    `version: "X.Y.Z"`) into this layout; it prices example ids, not the models
    this deployment runs. The live SSOT carries a bare integer version."""
    p = pathlib.Path(__file__).resolve().parents[1] / "model-routing.yaml"
    return not p.exists() or re.search(r'^version: "\d+\.\d+\.\d+"', p.read_text(), re.M) is not None


LONG = SSOT.replace(
    "  claude-haiku-4-5:",
    "  claude-haiku-5-5: { in: 0.10, out: 0.50, cache_read: 0.01, cache_write_5m: 0.125, cache_write_1h: 0.20,"
    " long_prompt: { over: 100000, in: 0.50, out: 2.50, cache_read: 0.05, cache_write_5m: 0.625, cache_write_1h: 1.00 } }\n"
    "  claude-haiku-4-5:")
SHORT_H = (0.10, 0.50, 0.01, 0.125, 0.20)
LONG_H = (0.50, 2.50, 0.05, 0.625, 1.00)


def test_a_haiku_request_over_100k_prompt_tokens_bills_the_long_row(tmp_path):
    p = tmp_path / "ssot.yaml"
    p.write_text(LONG)
    t = model_prices.load(str(p))
    assert model_prices.rates_for(t, "claude-haiku-5-5", 99_999) == SHORT_H
    assert model_prices.rates_for(t, "claude-haiku-5-5", 100_000) == SHORT_H   # "over" 100,000, not at it
    assert model_prices.rates_for(t, "claude-haiku-5-5-20261001", 100_001) == LONG_H
    assert model_prices.rates(t, "claude-haiku-5-5") == SHORT_H                # no size: short row
    assert model_prices.rates_for(t, "claude-haiku-5-5", None) == SHORT_H
    # a model with no long row is unaffected by size, and the long entry never matches an id
    assert model_prices.rates_for(t, "claude-opus-5-5", 900_000) == model_prices.rates(t, "claude-opus-5-5")
    assert model_prices.rates_for(t, "claude-haiku-4-5", 900_000) == (1.0, 5.0, 0.1, 9.9, 7.7)
    assert model_prices.rates_for(t, "claude-opus-5-6", 900_000) is None
    assert model_prices.rates(t, "claude-haiku-5-5>100000") is None


def test_the_live_ssot_haiku_55_has_the_vendor_long_prompt_row():
    root = pathlib.Path(__file__).resolve().parents[1]
    if not (root / "model-routing.yaml").exists() or _public_example_ssot():
        pytest.skip("no live model-routing.yaml in this layout")
    t = model_prices.load(str(root / "model-routing.yaml"))
    assert model_prices.rates_for(t, "claude-haiku-5-5", 99_999) == SHORT_H
    assert model_prices.rates_for(t, "claude-haiku-5-5", 100_001) == LONG_H


@pytest.mark.skipif(_public_example_ssot(),
                    reason="no live model-routing.yaml in this layout (public export ships an example policy)")
def test_the_live_ssot_prices_every_model_the_harness_runs():
    root = pathlib.Path(__file__).resolve().parents[1]
    t = model_prices.load(str(root / "model-routing.yaml"))
    for mid in ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5",
                "claude-haiku-4-5-20251001"):
        assert model_prices.rates(t, mid), mid
    assert model_prices.rates(t, "claude-fable-5-1")[2] == 0.25   # the 4x cache-read error, once


@pytest.mark.skipif(
    not (pathlib.Path(__file__).resolve().parents[1] / "model-routing.yaml").exists(),
    reason="no model-routing.yaml in this layout (public export)")
def test_the_live_ssot_write_rates_are_1_25x_and_2x_of_input():
    root = pathlib.Path(__file__).resolve().parents[1]
    for mid, (inp, _, _, w5, w1) in model_prices.load(str(root / "model-routing.yaml")).items():
        assert (w5, w1) == (inp * 1.25, inp * 2), mid
