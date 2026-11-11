#!/usr/bin/env python3
"""
test_egress_sanitizer_redaction_goldens.py — token-by-token redaction goldens for
`egress_sanitizer.py` (SV-03).

WHY THIS FILE EXISTS
--------------------
`egress_sanitizer.sanitize()` decides what leaves the machine: `verification.py`
imports it as `ES` and runs it before a claim becomes an external web query. Until
this file, nothing in the repo asserted that it redacts ANYTHING. That failure mode
is silent — a pattern stops matching, a client name or a deal size egresses, and no
run turns red. The module's own docstring promises determinism ("a fixture can assert
it token-by-token"); this is that fixture.

Every input below is SYNTHETIC. The names, code-names, addresses, account numbers and
amounts are invented; `example.invalid` is the reserved never-resolving TLD (RFC 2606).
No real secret, client or counterparty appears here or in any comment.

Each golden carries the exact expected egressed query, so a weakened pattern changes a
string and fails. Two known-negatives keep the suite from being vacuous: clean text must
pass through UNCHANGED, and empty input must stay empty — a sanitizer that mangles clean
prose is broken too.

Run:  python3 -m pytest test_egress_sanitizer_redaction_goldens.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import egress_sanitizer as ES  # noqa: E402

CONF = "confidential"


def _san(claim, *, banned=(), entities=(), sensitivity=CONF, mode=None, coarsen_pct=False):
    return ES.EgressSanitizer(
        banned_tokens=banned, coarsen_percentages=coarsen_pct
    ).sanitize(claim, sensitivity=sensitivity, entities=entities, mode=mode)


# ---------------------------------------------------------------------------
# 1. Masking classes (MODE_MASK_REQUIRED — confidential / restricted)
# ---------------------------------------------------------------------------
# id, claim, kwargs, expected egressed query, expected mask_map
MASK_GOLDENS = [
    (
        "codename",
        "Board approved Project Peninsula last week.",
        {},
        "Board approved <ENTITY_1> last week.",
        {"<ENTITY_1>": "Project Peninsula"},
    ),
    (
        "codename-deal-keyword",
        "Deal Falcon reached exclusivity.",
        {},
        "<ENTITY_1> reached exclusivity.",
        {"<ENTITY_1>": "Deal Falcon"},
    ),
    (
        "email",
        "Queries go to deal.desk@example.invalid only.",
        {},
        "Queries go to <ENTITY_1> only.",
        {"<ENTITY_1>": "deal.desk@example.invalid"},
    ),
    (
        "person-run-heuristic",
        "Ana Sofia Martins signed off with Bob Vance.",
        {},
        "<PERSON_1> signed off with <PERSON_2>.",
        {"<PERSON_1>": "Ana Sofia Martins", "<PERSON_2>": "Bob Vance"},
    ),
    (
        "caller-entities-person-vs-org",
        "Ana Martins met Northwind Holdings SGPS.",
        {"entities": ["Ana Martins", "Northwind Holdings SGPS"]},
        "<PERSON_1> met <ENTITY_1>.",
        {"<PERSON_1>": "Ana Martins", "<ENTITY_1>": "Northwind Holdings SGPS"},
    ),
    (
        "account-number",
        "Settlement rides on account 998877665544.",
        {},
        "Settlement rides on account <ENTITY_1>.",
        {"<ENTITY_1>": "998877665544"},
    ),
    (
        "banned-token-masked-case-insensitively",
        "The peninsula thesis holds.",
        {"banned": ["Peninsula"]},
        "The <BANNED_1> thesis holds.",
        {"<BANNED_1>": "Peninsula"},
    ),
    (
        "banned-longest-first",
        "Project Peninsula North is live.",
        {"banned": ["Peninsula", "Project Peninsula North"]},
        "<BANNED_1> is live.",
        {"<BANNED_1>": "Project Peninsula North"},
    ),
]


@pytest.mark.parametrize("name,claim,kw,expected,mask_map", MASK_GOLDENS,
                         ids=[g[0] for g in MASK_GOLDENS])
def test_mask_golden(name, claim, kw, expected, mask_map):
    res = _san(claim, **kw)
    assert res.egressed_query == expected
    assert res.mask_map == mask_map
    assert not res.blocked and not res.skipped
    # the reverse map is local-only: it must never reach the manifest record.
    rec = ES.egress_record(res, sensitivity=CONF, mode=ES.MODE_MASK_REQUIRED, egress_allowed=True)
    for original in mask_map.values():
        assert original not in repr(rec)


# ---------------------------------------------------------------------------
# 2. Numeric coarsening — every bucket, plus the ValueError fallback
# ---------------------------------------------------------------------------
BUCKETS = [
    ("2", "trillion", "order-of-EUR-trillions"),
    ("150", "bn", "hundreds-of-EUR-billions"),
    ("21", "bn", "tens-of-EUR-billions"),
    ("4", "bn", "single-digit-EUR-billions"),
    ("300", "million", "hundreds-of-EUR-millions"),
    ("4.2", "m", "EUR-millions"),
    ("45", "k", "EUR-thousands"),
    ("12", "", "a-small-amount"),
]


@pytest.mark.parametrize("num,scale,bucket", BUCKETS, ids=[b[2] for b in BUCKETS])
def test_coarsen_bucket(num, scale, bucket):
    assert ES._coarsen_amount(num, scale, "EUR") == bucket


@pytest.mark.parametrize("bad", ["1.2.3", "1,2,3.4.5", "..", ""])
def test_coarsen_valueerror_fallback_coarsens_rather_than_leaks(bad):
    """A number float() cannot parse must NOT fall back to the raw digits."""
    out = ES._coarsen_amount(bad, "b", "EUR")
    assert out == "a-small-amount"
    assert bad not in out or bad == ""


def test_coarsen_valueerror_fallback_in_a_real_claim():
    res = _san("Consideration was 1.2.3b at signing.")
    assert res.egressed_query == "Consideration was a-small-amount at signing."
    assert "1.2.3" not in res.egressed_query


def test_currency_amount_in_claim_is_coarsened():
    res = _san("Enterprise value reached $4.2m.")
    assert res.egressed_query == "Enterprise value reached USD-millions."
    assert res.coarsened == ["USD-millions"]


def test_percentages_kept_by_default_and_coarsened_on_request():
    claim = "Margin improved to 12.5% year on year."
    assert _san(claim).egressed_query == claim
    assert _san(claim, coarsen_pct=True).egressed_query == (
        "Margin improved to a-percentage year on year."
    )


# ---------------------------------------------------------------------------
# 3. Source-label removal — ALWAYS, in every mode (never reveal provenance)
# ---------------------------------------------------------------------------
LABEL_GOLDENS = [
    ("source-id", "Revenue rose [S3] last year.", "Revenue rose last year.", "[S3]"),
    ("filename", "See northwind_model_v2.xlsx for detail.", "See for detail.",
     "northwind_model_v2.xlsx"),
    ("provenance-phrase", "Margins fell per the board pack.", "Margins fell.",
     "per the board pack"),
]


@pytest.mark.parametrize("name,claim,expected,label", LABEL_GOLDENS,
                         ids=[g[0] for g in LABEL_GOLDENS])
@pytest.mark.parametrize("mode", [ES.MODE_ALLOW_LIGHT, ES.MODE_MASK_REQUIRED])
def test_source_labels_removed_in_every_mode(name, claim, expected, label, mode):
    res = _san(claim, mode=mode)
    assert res.egressed_query == expected
    assert label in res.removed_labels


# ---------------------------------------------------------------------------
# 4. Hard-fail / no-egress branches
# ---------------------------------------------------------------------------
def test_skipped_mode_egresses_nothing():
    res = _san("Project Peninsula is priced at $4.2m.", mode=ES.MODE_SKIPPED)
    assert res.skipped is True
    assert res.egressed_query == ""
    assert "skipped-egress" in res.block_reason


def test_banned_token_surviving_sanitisation_blocks_egress():
    # 'value' is the label the coarsener itself emits for an uncurrencied magnitude,
    # so it reappears AFTER masking — exactly the survival case the guard is for.
    res = _san("Order book reached 45000 in the quarter.", banned=["value"])
    assert res.blocked is True
    assert res.egressed_query == ""
    assert res.block_reason == "banned token survived sanitisation: 'value'"


def test_egress_gate_blocks_restricted_board_without_waiver():
    gate = ES.egress_gate("restricted", "board-regulatory", waiver=None)
    assert gate == {
        "status": "blocked", "egress_allowed": False, "mode": ES.MODE_SKIPPED,
        "reason": gate["reason"], "waiver_valid": False,
    }
    ok = ES.egress_gate("restricted", "board-regulatory", waiver={
        "owner": "A. Owner", "scope": "one claim", "rationale": "audit", "signoff": "approved"})
    assert ok["status"] == "ok" and ok["mode"] == ES.MODE_MASK_REQUIRED


# ---------------------------------------------------------------------------
# 5. Light mode — substance kept, provenance + banned tokens still stripped
# ---------------------------------------------------------------------------
def test_light_mode_keeps_substance_but_strips_banned_and_labels():
    res = _san("Northwind grew 12% to $4.2m per the board pack.",
               banned=["Northwind"], mode=ES.MODE_ALLOW_LIGHT)
    assert res.egressed_query == "<BANNED_1> grew 12% to $4.2m."
    assert res.coarsened == []          # light mode does not coarsen
    assert res.removed_labels == ["per the board pack"]


# ---------------------------------------------------------------------------
# 6. Known negatives — a sanitizer that mangles clean text is also broken
# ---------------------------------------------------------------------------
def test_empty_input_stays_empty():
    res = _san("")
    assert res.egressed_query == ""
    assert res.masked == [] and res.coarsened == [] and res.removed_labels == []
    assert not res.blocked and not res.skipped


CLEAN = [
    "Growth was driven by pricing.",
    "The thesis rests on volume, not price.",
    "Margin improved year on year.",
]


@pytest.mark.parametrize("claim", CLEAN)
@pytest.mark.parametrize("mode", [ES.MODE_ALLOW_LIGHT, ES.MODE_MASK_REQUIRED])
def test_clean_text_passes_through_unchanged(claim, mode):
    res = _san(claim, mode=mode)
    assert res.egressed_query == claim
    assert res.masked == [] and res.coarsened == [] and res.removed_labels == []
    rec = ES.egress_record(res, sensitivity=CONF, mode=mode, egress_allowed=True)
    assert rec["diff"] == "no transformation needed"


# ---------------------------------------------------------------------------
# 7. KNOWN DEFECT — locked in so a fix is visible, not so it is blessed
# ---------------------------------------------------------------------------
# `_SCALE` has no trailing word boundary and `_MONEY_RE` swallows the space before it,
# so the coarsener eats leading letters (and the separating space) off the FOLLOWING
# word: "3 team members" -> "order-of-value-trillionseam members" ("t" read as
# trillions), "21 billion euros" -> "tens-of-value-billionsillion euros".
# The magnitude is still coarsened — this over-masks, it does not leak — but the
# egressed query is corrupted. If you fix `_SCALE` (add \b) these two assertions will
# fail: that is the fix landing, update them.
def test_known_defect_scale_letter_eats_the_following_word():
    assert _san("The 3 team members reviewed it.").egressed_query == (
        "The order-of-value-trillionseam members reviewed it."
    )
    assert _san("Exposure of 21 billion euros remains.").egressed_query == (
        "Exposure of tens-of-value-billionsillion euros remains."
    )
