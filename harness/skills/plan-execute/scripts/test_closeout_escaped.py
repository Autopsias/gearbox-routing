"""extract() reads a closeout handed over HTML-escaped, or written as bare JSON with no tags."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import closeout_pipeline as cp  # noqa: E402

BODY = '{"session": "s04", "result": "DONE", "notes": {"a": "x < y"}}'
REAL = f"Done.\n\n<plan-execute-closeout>\n{BODY}\n</plan-execute-closeout>\n"
ESCAPED = REAL.replace("<", "&lt;").replace(">", "&gt;")


def test_escaped_block_gives_the_same_json_as_the_real_block():
    real, escaped = cp.extract(REAL), cp.extract(ESCAPED)
    assert real["status"] == escaped["status"] == "ok"
    assert real["json_text"] == escaped["json_text"] == BODY


def test_no_block_still_reads_missing():
    assert cp.extract("Done. &lt;b&gt;no closeout&lt;/b&gt;")["status"] == "missing"


def test_bare_json_object_with_a_session_is_the_closeout():
    got = cp.extract("\n" + BODY + "\n")
    assert got["status"] == "ok" and got["json_text"] == BODY


def test_text_notes_become_the_session_note_and_other_shapes_still_fail():
    base = '{"session": "s04", "result": "DONE", "items_completed": [], "items_blocked": [], "notes": %s}'
    got = cp.parse_and_validate_schema(base % '"one line"')
    assert got["status"] == "ok" and got["parsed"]["notes"] == {"s04": "one line"}, got
    bad = cp.parse_and_validate_schema(base % '["one line"]')
    assert bad["status"] == "schema_error" and "'notes' must be dict, got list" in bad["violations"]


def test_bare_text_that_is_not_a_session_object_still_reads_missing():
    for raw in ('{"result": "DONE"}', '[{"session": "s04"}]', "{not json", "Done. {\"session\": \"s04\"}"):
        assert cp.extract(raw)["status"] == "missing", raw
