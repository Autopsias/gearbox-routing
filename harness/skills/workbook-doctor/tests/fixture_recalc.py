#!/usr/bin/env python3
"""
Fixture: xlsx skill's scripts/recalc.py — PROVES the contract
"recompute formulas + return an error map".

Builds a tiny workbook containing (a) a hardcode, (b) a valid formula, and
(c) a divide-by-zero formula, runs the REAL recalc.py against it, and asserts
the returned JSON detects the #DIV/0! and counts the formulas.

Contract proven:  python recalc.py <file> -> JSON {status, total_errors,
                  total_formulas, error_summary{type:{count,locations}}}
Failure mode:     LibreOffice unavailable -> recalc cannot run.
Fallback:         harness flags 'recalc unverified' (ok-with-warnings).

Usage:  SKILLS_ROOT=/path/to/skills python3 fixture_recalc.py
        (or pass the skills root as argv[1])
Exit:   0 = PASS, 1 = FAIL/blocked.
"""
import json
import os
import subprocess
import sys
import tempfile

import openpyxl


def find_recalc(skills_root):
    cand = os.path.join(skills_root, "xlsx", "scripts", "recalc.py")
    return cand if os.path.isfile(cand) else None


def main():
    skills_root = (
        sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SKILLS_ROOT", "")
    )
    if not skills_root:
        print("FAIL recalc: SKILLS_ROOT not set and no arg given")
        return 1

    recalc = find_recalc(skills_root)
    if not recalc:
        print(f"FAIL recalc: recalc.py not found under {skills_root}/xlsx/scripts/")
        return 1

    scratch = tempfile.mkdtemp(prefix="otl_recalc_", dir=os.environ.get("TMPDIR", "/tmp"))
    xlsx_path = os.path.join(scratch, "tiny.xlsx")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "value"
    ws["B1"] = "double"
    ws["A2"] = 10            # hardcode
    ws["B2"] = "=A2*2"       # valid formula -> 20
    ws["A3"] = "div0"
    ws["B3"] = "=1/0"        # divide-by-zero -> #DIV/0!
    wb.save(xlsx_path)
    wb.close()

    scripts_dir = os.path.dirname(recalc)
    env = dict(os.environ)
    # LibreOffice writes its profile under $HOME; allow an override when the
    # default home has no space (sandbox). Defaults to the real HOME otherwise.
    if os.environ.get("OTL_HOME"):
        env["HOME"] = os.environ["OTL_HOME"]

    try:
        proc = subprocess.run(
            [sys.executable, recalc, os.path.abspath(xlsx_path), "60"],
            cwd=scripts_dir,
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        print("FAIL recalc: timed out (LibreOffice may be unavailable)")
        return 1

    out = proc.stdout.strip()
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        print("FAIL recalc: non-JSON output from recalc.py")
        print("stdout:", out[:500])
        print("stderr:", proc.stderr[:500])
        return 1

    if "error" in result:
        print(f"BLOCKED recalc: {result['error']} (fallback: flag 'recalc unverified')")
        return 1

    ok = (
        result.get("status") == "errors_found"
        and result.get("total_errors", 0) >= 1
        and "#DIV/0!" in (result.get("error_summary") or {})
        and result.get("total_formulas", 0) >= 2
    )
    print("recalc result:", json.dumps(result))
    if ok:
        loc = result["error_summary"]["#DIV/0!"]["locations"]
        print(f"PASS recalc: #DIV/0! detected at {loc}; {result['total_formulas']} formulas counted")
        return 0
    print("FAIL recalc: expected errors_found with #DIV/0! and >=2 formulas")
    return 1


if __name__ == "__main__":
    sys.exit(main())
