#!/usr/bin/env python3
"""
S08 / DR-06 regression — the Trust Review CLI drives the DOCUMENT surface per-format.

The DR-06 dogfood (`_t2d_reorg_deck_2026-05-24.html`) exposed a real gap: a
`document-verdict.json` carries `surface: "doc"` + `format: "html"` (DD-04
propagates them) and `run_loop()` accepts `surface`/`fmt`, but the
`loop_controller.py` CLI entrypoint (`_main`, the documented way to drive the
loop) had NO `--surface`/`--format` flag and ignored those verdict fields — so
driving a document-verdict through the CLI ran the loop as `surface="both"`,
losing the per-format reviewer prompt (the DOC-PBW rows) and the per-format
reporting DD-06 / review #6 require.

This fixture drives the CLI entrypoint by subprocess and asserts:

  A. a document-verdict (surface=doc, format=html) driven through the CLI records
     `surface=doc` + `format=html` on the result AND on every round log;
  B. a workbook-verdict (NO surface key) still records `surface=both` /
     `format=null` — byte-for-byte the pre-fix behaviour (additive, no regression);
  C. an explicit `--surface excel --format` override wins over the verdict value.

Exit 0 = PASS. Exit 1 = FAIL. Wired into run_fixtures.sh under [document-doctor].
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LOOP = os.path.join(PLUGIN_ROOT, "skills", "trust-review", "scripts", "loop_controller.py")

_errs = []


def check(cond, msg):
    if not cond:
        _errs.append(msg)
    return cond


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _run_cli(verdict_path, out_dir, run_id, extra=None):
    cmd = [sys.executable, LOOP, "--verdict", verdict_path, "--out", out_dir, "--run-id", run_id]
    if extra:
        cmd += extra
    subprocess.run(cmd, capture_output=True, text=True, check=False)
    rp = os.path.join(out_dir, "review-result.json")
    if not os.path.isfile(rp):
        return None, []
    res = json.load(open(rp, encoding="utf-8"))
    rounds = []
    i = 1
    while True:
        p = os.path.join(out_dir, "review-round-%d.json" % i)
        if not os.path.isfile(p):
            break
        rounds.append(json.load(open(p, encoding="utf-8")))
        i += 1
    return res, rounds


def run():
    work = tempfile.mkdtemp(prefix="dr06_cli_")
    # a clean verdict (repair_plan empty -> 0 edits -> loop converges) is enough;
    # we only assert how surface/format are wired, not the readiness outcome.
    doc_v = {"run_id": "t-doc", "skill": "document-doctor", "phase": "document-verdict",
             "surface": "doc", "format": "html", "status": "ok-with-warnings",
             "readiness": "ready", "readiness_reason": "clean",
             "inputs": {"travel_class": "Travels", "document": "x.html"},
             "repair_plan": [], "must_own": []}
    wb_v = {"run_id": "t-wb", "skill": "workbook-doctor", "phase": "workbook-verdict",
            "status": "ok-with-warnings", "readiness": "ready", "readiness_reason": "clean",
            "inputs": {"travel_class": "Travels", "workbook": "x.xlsx"},
            "repair_plan": [], "must_own": []}
    dvp = os.path.join(work, "document-verdict.json"); _write(dvp, doc_v)
    wvp = os.path.join(work, "workbook-verdict.json"); _write(wvp, wb_v)

    # A. document-verdict via the CLI -> surface=doc, format=html
    res, rounds = _run_cli(dvp, os.path.join(work, "doc"), "t-doc")
    if check(res is not None, "A: the doc CLI run produced no review-result.json"):
        check(res.get("surface") == "doc",
              "A: doc-verdict via CLI must record surface=doc, got %r" % res.get("surface"))
        check(res.get("format") == "html",
              "A: doc-verdict via CLI must record format=html, got %r" % res.get("format"))
        check(rounds and all(r.get("surface") == "doc" and r.get("format") == "html" for r in rounds),
              "A: every round must record surface=doc + format=html (per-format reporting)")

    # B. workbook-verdict (no surface key) -> surface=both, format null (unchanged)
    res2, _ = _run_cli(wvp, os.path.join(work, "wb"), "t-wb")
    if check(res2 is not None, "B: the workbook CLI run produced no review-result.json"):
        check(res2.get("surface") == "both",
              "B: workbook-verdict (no surface key) must stay surface=both, got %r" % res2.get("surface"))
        check(res2.get("format") in (None, "null"),
              "B: workbook-verdict must have format=null, got %r" % res2.get("format"))

    # C. explicit --surface/--format override wins over the verdict
    res3, _ = _run_cli(dvp, os.path.join(work, "ovr"), "t-ovr",
                       extra=["--surface", "excel", "--format", "docx"])
    if check(res3 is not None, "C: the override CLI run produced no review-result.json"):
        check(res3.get("surface") == "excel",
              "C: --surface override must win, got %r" % res3.get("surface"))

    if _errs:
        print("FAIL — DR-06 doc-loop CLI surface/format regression:")
        for e in _errs:
            print("  -", e)
        return 1
    print("PASS — DR-06: the Trust Review CLI drives the document surface per-format "
          "(doc-verdict -> surface=doc/format=html on result + every round; workbook-verdict "
          "stays surface=both unchanged; explicit --surface override wins).")
    return 0


if __name__ == "__main__":
    sys.exit(run())
