"""safe_json and the readers moved onto it: valid JSON of the wrong shape reads
as absent and never raises, and an empty value ("" / [] / {}) is a present value.

One table per reader. Each table carries the same shapes: ``[]``, ``"x"``, ``1``,
a list where a str id belongs, a null optional field, and an empty-but-present
value. Closes plan-review fids 738b8623b1, 40aa2e59a9, 4f91b0ac1d, a83bc6d553
(644f2f2387 is in scripts/fleet/test_explain.py).

    pytest skills/plan-execute/scripts/test_safe_json.py -q
"""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import attestation  # noqa: E402
import findings_digest as fd  # noqa: E402
import land_at_land as lal  # noqa: E402
import run_state_io as rsi  # noqa: E402
import safe_json as sj  # noqa: E402

# One row each: bad JSON, [], a string, a number, null, an object with a list where a
# word is expected, bytes that are not UTF-8, then two good rows (one blank line between).
BROKEN_ROWS = (b'{bad\n[]\n"x"\n1\nnull\n{"event": ["a"]}\n\xff{\n\n'
               b'{"event": "go"}\n{"event": "stop"}\n')


@pytest.mark.parametrize("raw", [BROKEN_ROWS, BROKEN_ROWS.decode("utf-8", "replace")])
def test_load_rows_skips_and_counts_every_broken_row(raw):
    rows, skipped = sj.load_rows(raw)
    assert skipped == 6                     # bad json, [], "x", 1, null, \xff{ ; the blank line is neither
    assert [sj.get_str(r, "event") for r in rows] == [None, "go", "stop"]   # a list-valued word reads as absent


def test_load_rows_skips_a_row_nested_too_deep(monkeypatch):
    real = sj.json.loads        # 3.11/3.12 raise RecursionError natively on 3.14 it does not: force it
    def deep(s, *a):
        if s.startswith("[[["):
            raise RecursionError
        return real(s, *a)
    monkeypatch.setattr(sj.json, "loads", deep)
    assert sj.load_rows("[" * 100000 + '\n{"a": 1}\n') == ([{"a": 1}], 1)


def test_load_rows_on_a_clean_file_keeps_every_row():
    assert sj.load_rows('{"a": 1}\n{"a": 2}\n') == ([{"a": 1}, {"a": 2}], 0)
    assert sj.load_rows(b"") == ([], 0)
    row = '{"a": "x\u2028y"}\n'      # a raw U+2028 inside a string is not a row break, in str or bytes
    assert sj.load_rows(row) == sj.load_rows(row.encode()) == ([{"a": "x\u2028y"}], 0)


# --- the helper -------------------------------------------------------------
@pytest.mark.parametrize("text, want", [
    ('{"a": 1}', {"a": 1}), ("{}", {}),            # an empty object is a dict
    ("[]", None), ('"x"', None), ("1", None), ("null", None),
    ('{"a": 1', None), ("", None), (b"\xff", None), (None, None),
])
def test_load_dict_returns_only_an_object(text, want):
    assert sj.load_dict(text) == want


@pytest.mark.parametrize("getter, value, want", [
    (sj.get_str, "", ""), (sj.get_str, ["id"], None), (sj.get_str, 1, None),
    (sj.get_int, 0, 0), (sj.get_int, True, None), (sj.get_int, "1", None),
    (sj.get_bool, False, False), (sj.get_bool, 0, None),
    (sj.get_list, [], []), (sj.get_list, (), None), (sj.get_list, "ab", None),
    (sj.get_dict, {}, {}), (sj.get_dict, [], None),
    (sj.get_str, None, None),
])
def test_getters_keep_empty_values_and_reject_wrong_types(getter, value, want):
    assert getter({"k": value}, "k") == want
    assert getter({"k": value}, "k", "D") == ("D" if want is None else want)


@pytest.mark.parametrize("d", [[], "x", 1, None])
def test_getters_on_a_non_dict_return_the_default(d):
    assert sj.get_str(d, "k", "D") == "D" and sj.as_dict(d) is None


@pytest.mark.parametrize("value, want", [
    ("2026-10-18T10:00:00+00:00", datetime(2026, 10, 18, 10, tzinfo=UTC)),
    ("2026-10-18T10:00:00Z", datetime(2026, 10, 18, 10, tzinfo=UTC)),
    ("2026-10-18T10:00:00", datetime(2026, 10, 18, 10, tzinfo=UTC)),   # naive reads as UTC
    ("", None), ("yesterday", None), (None, None), (1, None), (["2026-10-18"], None),
])
def test_parse_iso_aware_is_always_aware_or_none(value, want):
    got = sj.parse_iso_aware(value)
    assert got == want and (got is None or got.tzinfo is not None)


# --- attestation.ledger_usage (fid 40aa2e59a9) ------------------------------
USAGE = {"source": "transcript", "input_tokens": 1, "output_tokens": 2, "cache_read_tokens": 0,
         "cache_creation_tokens": 0, "cost_usd": 0.5, "cost_source": "reported", "scope": "worker"}


@pytest.mark.parametrize("receipt, want", [
    ([], attestation.UNAVAILABLE), ("x", attestation.UNAVAILABLE), (1, attestation.UNAVAILABLE),
    ({"usage": "x"}, attestation.UNAVAILABLE),
    ({"usage": USAGE, "attested": ["m"]}, USAGE),               # list where an object belongs
    ({"usage": USAGE, "attested": {"model": ["m"]}}, USAGE),    # list-valued model id
    ({"usage": USAGE, "attested": None}, USAGE),                # null optional field
    ({"usage": dict(USAGE, source="")}, dict(USAGE, source="")),  # "" is a source, not absent
])
def test_ledger_usage_never_raises_on_a_malformed_receipt(receipt, want):
    assert attestation.ledger_usage(receipt) == want



@pytest.mark.parametrize("receipts, want", [
    ([], None), ("x", None), (1, None), ({"s01": "x"}, None), ({"s01": ["r"]}, None),
    (None, None), ({"s01": {}}, {}),                            # an empty receipt is present
])
def test_get_receipt_returns_a_dict_or_none(tmp_path, receipts, want):
    (tmp_path / "run_state.json").write_text(json.dumps({"dispatch_receipts": receipts}))
    assert rsi.get_receipt(tmp_path, "s01") == want


# --- findings_digest: checkpoint reader at dispatch (fid 4f91b0ac1d) --------
TS = "2026-10-18T10:00:00+00:00"


def _checkpoint(**kw):
    return {"event": "checkpoint_reached", "kind": "pre_dispatch", "ts": TS, **kw}


@pytest.mark.parametrize("line, found", [
    ("[]", False), ('"x"', False), ("1", False),
    (json.dumps(_checkpoint(session_ids=1)), False),            # was TypeError at dispatch
    (json.dumps(_checkpoint(session_ids="s01x")), False),       # was a substring match
    (json.dumps(_checkpoint(session_ids=[["s01"]])), False),    # list-valued id
    (json.dumps(_checkpoint(session_ids=None)), False),         # null optional field
    (json.dumps(_checkpoint(session_ids=["s01"])), True),
])
def test_current_checkpoint_never_raises_on_a_malformed_event(tmp_path, line, found):
    (tmp_path / "run.ndjson").write_text(line + "\n")
    assert (fd.current_checkpoint(tmp_path, "s01") is not None) is found


def _answer(tmp_path, **kw):
    (tmp_path / "run.ndjson").write_text(json.dumps(_checkpoint(session_ids=["s01"])) + "\n")
    rec = {"session": "s01", "answers_checkpoint": TS, "at": "2026-10-18T11:00:00",
           "given": True, "answer": "go", **kw}
    (tmp_path / fd.ANSWERS_FILE).write_text(json.dumps(rec) + "\n")


@pytest.mark.parametrize("kw, want", [
    ({"session": ["s01"]}, None),                               # list-valued id: not this session
    ({"at": None}, None), ({"at": 1}, None),                    # no time: fail closed
    ({"answer": ""}, "ok"),                                     # "" is an answer, not a missing one
])
def test_live_answer_reads_a_malformed_answer_safely(tmp_path, kw, want):
    _answer(tmp_path, **kw)
    got = fd.live_answer(tmp_path, "s01")
    assert (got and got[0]) == want


def test_an_empty_answer_is_shown_as_given(tmp_path):
    _answer(tmp_path, answer="")
    assert "resumed without an answer" not in fd.digest(tmp_path, "s01")


def test_naive_and_aware_stamps_compare(tmp_path):
    _answer(tmp_path)                                           # answer `at` is naive
    (tmp_path / "_changelog.ndjson").write_text(json.dumps(
        {"op": "redispatch", "session": "s01", "at": "2026-10-18T09:00:00+00:00"}) + "\n")
    assert fd.live_answer(tmp_path, "s01")[0] == "ok"           # the older entry does not retire it


# --- land_at_land: at-land descriptors (fids 738b8623b1, a83bc6d553) --------
def _flagged(tmp_path, entry):
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "eval-gates.json").write_text(json.dumps({"x": entry}))
    return dict(lal.at_land_gates(tmp_path, "/plan/tree")).get("x")


ARGV = {"kind": "argv", "argv": ["true"], "at_land": True}
SKILL = {"kind": "skill", "skill": "review", "at_land": True}


@pytest.mark.parametrize("entry", [
    {"kind": "skill", "at_land": True},                         # was KeyError
    dict(SKILL, skill=["review"]),                              # list-valued skill id
    dict(SKILL, skill=""),                                      # "" is present, and not a skill
    dict(ARGV, argv="true"), dict(ARGV, argv=[1]), dict(ARGV, argv=[]),
    {"at_land": True},                                          # an argv gate with no argv
    dict(ARGV, cwd=1), dict(ARGV, cwd=[]), dict(ARGV, cwd=""),  # empty cwd is not the root
])
def test_a_malformed_at_land_descriptor_parks_instead_of_crashing(tmp_path, entry):
    with pytest.raises(lal.Unreadable):
        _flagged(tmp_path, entry)


@pytest.mark.parametrize("entry, want", [
    ([], None), ("x", None), (1, None),                         # not a gate: never flagged
    (dict(ARGV, cwd=None), ("argv", "/plan/tree")),             # null cwd is the root
    (dict(ARGV, cwd="sub"), ("argv", "/plan/tree/sub")),
    (SKILL, ("skill", None)),
])
def test_well_formed_and_non_gate_entries(tmp_path, entry, want):
    g = _flagged(tmp_path, entry)
    assert (g and (g["kind"], g.get("cwd"))) == want


def test_review_rounds_survives_broken_rows(tmp_path):
    sys.path.insert(0, str(SCRIPTS.parent.parent.parent / "scripts"))
    import review_rounds
    led = tmp_path / "_plans" / "p" / ("_verify" + "_state") / "s01.gate.findings.ndjson"
    led.parent.mkdir(parents=True)
    led.write_bytes(BROKEN_ROWS + b'{"kind": "surface", "attempt": "2", "hashes": 5}\n'
                    b'{"kind": "surface", "attempt": 1, "note": "a\xffb"}\n{"kind": ["surface"]}\n')
    out = review_rounds.rows([str(tmp_path)], None)                    # no raise
    assert (out[0]["files"], out[0]["skipped"]) == (0, 7)             # a list-valued kind is counted; the bad-byte row is kept
    assert "malformed ledger rows ignored: 7" in review_rounds.report(out)
    led.write_bytes(b'{"kind": "surface", "attempt": 1, "hashes": {"a": 1}\n{"kind": "surface" BAD\n')
    out = review_rounds.rows([str(tmp_path)], None)                    # no surface row survives: still counted
    assert "malformed ledger rows ignored: 2" in review_rounds.report(out)
    assert "ledgers: 0; not measured (no line count): 0" in review_rounds.report(out)   # the entry holds no run


# ---- resolve_inside: a name from outside cannot leave its folder (plan review-cause-checks s04) ----
@pytest.mark.parametrize("name", ["../x.json", "a/../../x.json", "/etc/passwd", "", "..", "."])
def test_resolve_inside_refuses_a_name_that_leaves_base(tmp_path, name):
    with pytest.raises(ValueError):
        sj.resolve_inside(tmp_path, name)


def test_resolve_inside_refuses_a_symlink_out_of_base(tmp_path):
    base, outside = tmp_path / "base", tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    (base / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        sj.resolve_inside(base, "link/x.json")


def test_resolve_inside_keeps_a_valid_name_unchanged(tmp_path):
    assert sj.resolve_inside(tmp_path, "s01.json") == tmp_path / "s01.json"
    assert sj.resolve_inside(tmp_path, "sub/s01.json") == tmp_path / "sub" / "s01.json"
    assert sj.resolve_inside(tmp_path, " x.json") == tmp_path / " x.json"


@pytest.mark.parametrize("call", [
    lambda d: __import__("verify_paths").verify_state_path(d, "../../esc"),
    lambda d: __import__("verify_paths")._feedback_path(d, "../esc"),
    lambda d: __import__("ship_state_io").ship_state_path(d, "/abs"),
    lambda d: __import__("closeout_pipeline").load_closeout(d, "../esc"),
    lambda d: __import__("replan").record_path(d, "../esc"),
    lambda d: __import__("locked_checks").snapshot_path(d, "../esc"),
])
def test_write_sites_refuse_an_escaping_session_id(tmp_path, call):
    with pytest.raises(ValueError):
        call(tmp_path)
