#!/usr/bin/env python3
"""
Build the Workbook Doctor defect fixtures (WD-02 acceptance gate).

One small, focused workbook PER DEFECT TYPE, each paired with the answer key in
answer_key.json:

  roll_forward.xlsx     — the headline: =$C$5/$B$5-1 filled across every year
                          (consistently wrong; a shape/consistency check passes it).
                          Includes a CORRECT roll-forward row as a contrast.
  hardcoded_output.xlsx — a typed number where its column-siblings are formulas,
                          plus a magic constant baked into a formula.
  stale_reference.xlsx  — a formula pointing at an empty/removed precedent, and a
                          formula carrying a #REF! token.
  mixed_unit.xlsx       — one column mixing € / % / absolute units.

Plus the worked-run model that ties to the S03 source packet:
  q3_board_model.xlsx   — a compact Q3 board workbook whose YoY cell is the
                          unsupported +12% (packet claim C6) and whose headcount
                          carries the conflicting 1,180 (CONFLICT-003).

Usage: python build_fixtures.py <out_dir>
Writes the .xlsx files into <out_dir> and returns their paths.
"""
import os
import sys

from openpyxl import Workbook


def _save(wb, out_dir, name):
    path = os.path.join(out_dir, name)
    wb.save(path)
    return path


def build_roll_forward(out_dir):
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"
    ws["A1"] = "Annual model — YoY growth"
    # period header on row 4, years across B..E
    ws["A4"] = "Metric"
    for i, yr in enumerate([2023, 2024, 2025, 2026]):
        ws.cell(row=4, column=2 + i, value=yr)
    # revenue literals on row 5 (the base the growth rows reference)
    ws["A5"] = "Revenue"
    for i, v in enumerate([100, 120, 140, 165]):
        ws.cell(row=5, column=2 + i, value=v)
    # row 7 — BUGGY: =$C$5/$B$5-1 filled across every year (refs never advance)
    ws["A7"] = "YoY growth % (BUG)"
    for col in ("C", "D", "E"):
        ws["%s7" % col] = "=$C$5/$B$5-1"
    # row 8 — CORRECT contrast: refs advance one period each step
    ws["A8"] = "YoY growth % (correct)"
    ws["C8"] = "=C5/B5-1"
    ws["D8"] = "=D5/C5-1"
    ws["E8"] = "=E5/D5-1"
    return _save(wb, out_dir, "roll_forward.xlsx")


def build_hardcoded_output(out_dir):
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A3"] = "Region"
    for i, h in enumerate(["Q1", "Q2", "Q3", "Total"]):
        ws.cell(row=3, column=2 + i, value=h)
    # North / South use =SUM; East's Total is HARDCODED (and wrong: should be 21)
    ws["A4"] = "North"; ws["B4"] = 10; ws["C4"] = 12; ws["D4"] = 15; ws["E4"] = "=SUM(B4:D4)"
    ws["A5"] = "South"; ws["B5"] = 8;  ws["C5"] = 9;  ws["D5"] = 11; ws["E5"] = "=SUM(B5:D5)"
    ws["A6"] = "East";  ws["B6"] = 5;  ws["C6"] = 7;  ws["D6"] = 9;  ws["E6"] = 32  # HARDCODE in calc column
    ws["A7"] = "Total"; ws["B7"] = "=SUM(B4:B6)"; ws["C7"] = "=SUM(C4:C6)"
    ws["D7"] = "=SUM(D4:D6)"; ws["E7"] = "=SUM(E4:E6)"
    # magic constant baked into a formula
    ws["A8"] = "Adjusted total"; ws["E8"] = "=E7*1.15"  # 1.15 magic constant
    return _save(wb, out_dir, "hardcoded_output.xlsx")


def build_stale_reference(out_dir):
    wb = Workbook()
    data = wb.active
    data.title = "Data"
    data["A1"] = "Metric"; data["B1"] = 2025; data["C1"] = 2026
    data["A2"] = "Revenue"; data["B2"] = 100; data["C2"] = 120
    # only rows 1-2 are populated — row 10 is empty
    rep = wb.create_sheet("Report")
    rep["A1"] = "Latest revenue"; rep["B1"] = "=Data!C2"          # valid
    rep["A2"] = "Pulled (stale)"; rep["B2"] = "=Data!C10"          # dangling: C10 empty
    rep["A3"] = "Broken total";  rep["B3"] = "=Data!#REF!+1"       # broken reference token
    return _save(wb, out_dir, "stale_reference.xlsx")


def build_mixed_unit(out_dir):
    wb = Workbook()
    ws = wb.active
    ws.title = "Mixed"
    ws["A1"] = "KPI"; ws["B1"] = "Q3 figure"
    ws["A2"] = "Revenue";   ws["B2"] = "€48.2M"   # eur / millions (text)
    ws["A3"] = "Margin";    ws["B3"] = "18.5%"    # percent (text)
    ws["A4"] = "EBITDA";    ws["B4"] = "€8.9M"    # eur / millions (text)
    ws["A5"] = "Headcount"; ws["B5"] = 1195       # bare number
    return _save(wb, out_dir, "mixed_unit.xlsx")


def build_q3_board_model(out_dir):
    """Worked-run workbook tied to the S03 source packet (2026-05-29T1615_q3-boardpack)."""
    wb = Workbook()
    act = wb.active
    act.title = "Actuals"
    act["A1"] = "Q3 FY26 actuals (FP&A close pack)"
    act["A3"] = "Metric"; act["B3"] = "Q3-FY26"
    act["A4"] = "Revenue (€M)";              act["B4"] = 48.2
    act["A5"] = "EBITDA margin";             act["B5"] = 0.185
    act["A6"] = "Headcount";                 act["B6"] = 1195
    act["A7"] = "Cloud migration spend (€M)"; act["B7"] = 2.1

    board = wb.create_sheet("Board")
    board["A1"] = "Q3 FY26 board summary"
    board["A3"] = "Line"; board["B3"] = "Value"
    board["A4"] = "Revenue (€M)";    board["B4"] = "=Actuals!B4"
    board["A5"] = "Revenue YoY %";   board["B5"] = 0.12       # UNSUPPORTED (+12% YoY = packet C6)
    board["A6"] = "EBITDA margin";   board["B6"] = "=Actuals!B5"
    board["A7"] = "Headcount";       board["B7"] = 1180       # CONFLICTING (actual 1,195 — CONFLICT-003)
    board["A8"] = "Cloud spend (€M)"; board["B8"] = "=Actuals!B7"
    return _save(wb, out_dir, "q3_board_model.xlsx")


def build_verdict_flip(out_dir):
    """WD-03 verdict-flip workbook: a deliverable resting on exactly ONE
    load-bearing assumption (Revenue YoY %). No formulas -> no calc-zone or
    roll-forward confounders; the ONLY finding comes from source-packet linkage
    on B5. With the assumption UNSUPPORTED the verdict is not-ready; sourcing it
    (or waiving it) flips the verdict. Revenue/Headcount carry the authoritative
    values so they are never flagged."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Outlook"
    ws["A1"] = "FY26 board outlook"
    ws["A3"] = "Line"; ws["B3"] = "Value"
    ws["A4"] = "Revenue (€M)";  ws["B4"] = 48.2    # authoritative (packet C1) — not flagged
    ws["A5"] = "Revenue YoY %"; ws["B5"] = 0.12    # the load-bearing assumption (packet C6)
    ws["A6"] = "Headcount";     ws["B6"] = 1195    # authoritative (packet C4) — not flagged
    return _save(wb, out_dir, "verdict_flip.xlsx")


BUILDERS = {
    "roll_forward": build_roll_forward,
    "hardcoded_output": build_hardcoded_output,
    "stale_reference": build_stale_reference,
    "mixed_unit": build_mixed_unit,
    "q3_board_model": build_q3_board_model,
    "verdict_flip": build_verdict_flip,
}


def build_all(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    return {k: fn(out_dir) for k, fn in BUILDERS.items()}


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "defects")
    paths = build_all(target)
    for k, p in paths.items():
        print("%-18s -> %s" % (k, p))
