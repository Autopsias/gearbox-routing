import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import wa_ledger as ledger  # noqa: E402

LONG = "x" * 3000


def row(date, source, decision="none needed", result="none needed", verdicts="DROP 1"):
    return "| %s | %s | Author | https://e.x/%s | %s | big | `r.md` | %s | %s |" % (
        date, source, date, verdicts, decision, result)


def make(tmp_path):
    """A ledger in the mixed layout the real file had: a row below the overrides."""
    text = "\n".join([
        "# Sources already judged", "",
        "| Date | Source | Author | URL | Verdicts | Largest item | Report | Owner's decision | Result |",
        "|---|---|---|---|---|---|---|---|---|",
        row("2026-09-01", "closed one", "built 2026-09-02", "due 2026-09-10: count. Ran 2026-09-10: 3 of 4, closed"),
        row("2026-09-02", "past due", "built 2026-09-03", "due 2026-09-20: count it " + LONG),
        row("2026-09-03", "pipes", verdicts="ADOPT 1 (a | b) · DROP 2"),
        row("2026-09-03", "shut later", "planned", "open Closed 2026-09-13: the plan landed " + "y" * 300),
        row("2026-09-03", "new due", "built", "open: count it. Checked 2026-09-10: still open. Due 2026-12-08: re-count"),
        row("2026-09-04", "retro of 2026-09-01", "approved", "no measurement defined"),
        row("2026-09-05", "still open", "built 2026-09-06", "open"),
        row("2026-09-05", "undecided", "open", "none needed"),
        "", ledger.HEADING, "", "Format: one line each.", "",
        '- 2026-09-06 · A · the run said: x · the owner said: "y" · absorbed 2026-09-07',
        '- 2026-09-08 · B · the run said: x · the owner said: "z"',
        row("2026-09-09", "stray row"),
    ]) + "\n"
    p = tmp_path / "LEDGER.md"
    p.write_text(text, encoding="utf-8")
    return str(p)


def run(path, *args, stdin=""):
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = io.StringIO(stdin), io.StringIO()
    try:
        ledger.main(["--ledger", path, *args])
        return sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_in, old_out


def test_cells_keep_a_pipe_inside_verdicts(tmp_path):
    out = run(make(tmp_path), "row", "pipes", "--field", "verdicts")
    assert out.strip() == "verdicts: ADOPT 1 (a | b) · DROP 2"


def test_row_prints_one_cell_in_full_and_lists_stay_short(tmp_path):
    p = make(tmp_path)
    assert LONG in run(p, "row", "past due", "--field", "result")       # nothing cut
    listed = run(p, "find", "past due") + run(p, "open", "--today", "2026-11-17")
    assert LONG not in listed and all(len(line) <= ledger.LINE_CAP for line in listed.split("\n"))


@pytest.mark.parametrize("source,shown", [("past due", True), ("still open", True), ("undecided", True),
                                          ("closed one", False), ("retro", False),
                                          ("shut later", False), ("new due", False)])
def test_open_tells_open_from_closed(tmp_path, source, shown):
    assert (source in run(make(tmp_path), "open", "--today", "2026-11-17")) is shown


def test_open_waits_for_the_due_date(tmp_path):
    assert "past due" not in run(make(tmp_path), "open", "--today", "2026-09-19")


def test_overrides_hide_absorbed_lines(tmp_path):
    p = make(tmp_path)
    assert run(p, "overrides").count("- 2026") == 1 and run(p, "overrides", "--all").count("- 2026") == 2
    with pytest.raises(SystemExit):
        run(p, "absorb", " A ", "edit 1")                                # A is absorbed already: no open match
    run(p, "absorb", " B ", "edit 1", "--today", "2026-09-20")
    assert "no override" in run(p, "overrides")
    assert 'the owner said: "z" · absorbed 2026-09-20 (edit 1)' in open(p, encoding="utf-8").read()


def test_row_refuses_an_ambiguous_match(tmp_path):
    with pytest.raises(SystemExit):
        run(make(tmp_path), "row", "2026-09")
    # a full date names its own row, also when another row's source cites that date
    assert run(make(tmp_path), "row", "2026-09-01", "--field", "source").strip() == "source: closed one"


def test_writes_land_in_the_right_place(tmp_path):
    p = make(tmp_path)
    run(p, "add-row", stdin=row("2026-09-10", "new row") + "\n")
    run(p, "add-override", '- 2026-09-11 · C · the run said: x · the owner said: "w"')
    run(p, "note", "still open", "Checked 2026-09-12: done, closed.")
    lines = open(p, encoding="utf-8").read().split("\n")
    head = lines.index(ledger.HEADING)
    assert lines.index(row("2026-09-10", "new row")) < head            # in the table, not at the file end
    assert lines.index(row("2026-09-09", "stray row")) < lines.index(row("2026-09-10", "new row"))  # stray moved first
    assert lines.index('- 2026-09-11 · C · the run said: x · the owner said: "w"') > head
    still = run(p, "open", "--today", "2026-11-17")
    assert "still open" not in still and "undecided" in still          # a result note closes a result, not a decision
    run(p, "note", "undecided", "planned (plan x)", "--field", "decision")
    assert "undecided" not in run(p, "open", "--today", "2026-11-17")
    assert "| planned (plan x) | none needed |" in open(p, encoding="utf-8").read()   # "open" replaced, result kept
    run(p, "note", "undecided", "built 2026-09-21", "--field", "decision")
    assert "| planned (plan x) · built 2026-09-21 |" in open(p, encoding="utf-8").read()   # two notes stay apart


def test_add_row_works_on_a_ledger_with_no_row_yet(tmp_path):
    p = tmp_path / "LEDGER.md"
    p.write_text("# T\n\n| Date | S |\n|---|---|\n\n%s\n\nFormat: x\n" % ledger.HEADING, encoding="utf-8")
    run(str(p), "add-row", stdin=row("2026-09-10", "first row"))
    lines = p.read_text(encoding="utf-8").split("\n")
    assert lines[4] == row("2026-09-10", "first row")                   # right below the separator


@pytest.mark.parametrize("bad", ["not a row", "| 2026-09-10 | too | few | cells |", "| a |\n| b |"])
def test_add_row_refuses_a_bad_row(tmp_path, bad):
    p = make(tmp_path)
    before = open(p, encoding="utf-8").read()
    with pytest.raises((SystemExit, ValueError)):
        run(p, "add-row", stdin=bad)
    assert open(p, encoding="utf-8").read() == before


def test_normalize_moves_stray_rows_only_with_write(tmp_path):
    p = make(tmp_path)
    before = open(p, encoding="utf-8").read()
    assert "1 table rows" in run(p, "normalize") and open(p, encoding="utf-8").read() == before
    run(p, "normalize", "--write")
    lines = open(p, encoding="utf-8").read().split("\n")
    assert lines.index(row("2026-09-09", "stray row")) < lines.index(ledger.HEADING)
    assert sum(1 for line in lines if ledger.ROW.match(line)) == 9      # no row lost or doubled
    assert "0 table rows" in run(p, "normalize")


def test_retro_due_counts_rows_and_live_overrides(tmp_path):
    out = run(make(tmp_path), "retro-due")
    assert "rows after the last retro: 3" in out and "not absorbed: 1" in out and "Enough" not in out


FILLED = "## Above the target\n\n- example-project: the same check — TRIAL\n\n## Drop challenge\n\n- none: no DROP\n\n## Principles\n\n- none: the source argues no principle\n"


@pytest.mark.parametrize("report,source,ok", [
    (FILLED, "a source", True),
    (None, "a source", False),                                          # no report file yet
    (FILLED.replace("## Principles", "## Other"), "a source", False),   # section missing
    (FILLED.replace("- example-project: the same check — TRIAL", "- <where else the idea applies>"), "a source", False),
    (FILLED.replace("- example-project: the same check — TRIAL", ""), "a source", False),   # empty section
    (FILLED.replace("## Drop challenge", "## Other"), "a source", False),   # drop challenge missing
    (None, "retro", True),                                              # a retro row has no report to check
])
@pytest.mark.parametrize("ticks", [True, False])                       # a plain path is checked too
def test_add_row_refuses_a_report_that_skips_the_big_picture(tmp_path, report, source, ok, ticks):
    d = tmp_path / "docs" / "worth-adopting"
    d.mkdir(parents=True)
    p = str(d / "LEDGER.md")
    open(p, "w", encoding="utf-8").write(open(make(tmp_path), encoding="utf-8").read())
    if report is not None:
        (d / "r.md").write_text(report, encoding="utf-8")
    new = row("2026-09-10", source).replace("`r.md`", "`docs/worth-adopting/r.md`" if ticks else "docs/worth-adopting/r.md")
    if ok:
        run(p, "add-row", stdin=new)
        assert new in open(p, encoding="utf-8").read()
    else:
        before = open(p, encoding="utf-8").read()
        with pytest.raises(SystemExit):
            run(p, "add-row", stdin=new)
        assert open(p, encoding="utf-8").read() == before
