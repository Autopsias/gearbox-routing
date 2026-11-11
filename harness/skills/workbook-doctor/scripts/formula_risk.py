#!/usr/bin/env python3
"""
Office Trust Layer — Workbook Doctor · WD-02: expanded formula-risk scan.

The headline failure mode (review-1 finding 5): a formula CONSISTENTLY WRONG
across a whole row — e.g. `=$C$5/$B$5-1` filled across every year. A naive
sibling-shape / consistency check PASSES it (every cell is structurally
identical). So this scanner runs a POSITIVE model of how references SHOULD
advance over the time axis, on top of the shape check.

Checks (deterministic):
  1. shape_consistency        — R1C1 sibling agreement (reported; NOT trusted alone)
  2. roll_forward             — period-over-period reference advancement (the headline)
  3. repeated_literal_ref     — the same absolute ref repeated along a time axis
  4. hardcode_in_calc_zone    — a typed number where its neighbours are formulas
  5. hardcode_in_formula      — a magic numeric constant embedded in a calc formula
  6. named_range / table      — broken / #REF! defined names; undefined-name refs
  7. dependency graph         — precedent edges; dangling (-> empty) precedents; cycles
  8. recalc capability        — xlsx/recalc.py error map; graceful "recalc unverified"
  9. source_packet_link       — output numbers traced to unsupported/conflicting claims

Enums (severity/review_status/issue_lifecycle/status) per references/conventions.md.

Usage:
  python formula_risk.py <workbook.xlsx> [--map workbook-map.json] [--out DIR]
                         [--source-packet PACKET.json] [--run-id ID] [--no-recalc]
"""
import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refs as R  # noqa: E402

YEAR_RE = re.compile(r"^(?:FY)?\s*(19|20)\d{2}$", re.IGNORECASE)
QTR_RE = re.compile(r"^Q[1-4][ \-/]?(?:FY)?\s*\d{0,4}$", re.IGNORECASE)
MON_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[ \-]?\d{0,4}$", re.IGNORECASE)
SAFE_CONSTANTS = {0, 1, -1, 2, -2, 10, 100, 1000, 12, 4, 365, 24, 60, 0.5}


def is_formula(v):
    return isinstance(v, str) and v.startswith("=")


def period_like(v):
    if isinstance(v, (_dt.datetime, _dt.date)):
        return True
    if isinstance(v, (int, float)):
        return 1990 <= v <= 2100  # bare year
    if isinstance(v, str):
        s = v.strip()
        return bool(YEAR_RE.match(s) or QTR_RE.match(s) or MON_RE.match(s))
    return False


def detect_period_bands(ws, max_row, max_col):
    """Find header rows with a run of >=3 adjacent period-like columns.

    Returns list of {header_row, cols:[idx...], labels:[...]}.
    """
    bands = []
    for r in range(1, min(max_row, 40) + 1):
        run = []
        runs = []
        for c in range(1, max_col + 1):
            if period_like(ws.cell(row=r, column=c).value):
                run.append(c)
            else:
                if len(run) >= 3:
                    runs.append(list(run))
                run = []
        if len(run) >= 3:
            runs.append(list(run))
        for cols in runs:
            bands.append({
                "header_row": r,
                "cols": cols,
                "labels": [str(ws.cell(row=r, column=c).value) for c in cols],
            })
    return bands


def collect_formulas(ws, max_row, max_col):
    cells = {}
    for r in range(1, max_row + 1):
        for c in range(1, max_col + 1):
            v = ws.cell(row=r, column=c).value
            if is_formula(v):
                cells[(r, c)] = v
    return cells


def scan_sheet(ws, findings, add, period_bands):
    name = ws.title
    max_row = ws.max_row or 0
    max_col = ws.max_column or 0
    formulas = collect_formulas(ws, max_row, max_col)

    # ---------- 1+2+3: shape consistency, roll-forward, repeated literal ref ----------
    for band in period_bands:
        cols = band["cols"]
        lo, hi = min(cols), max(cols)
        # rows that carry formulas in >=2 band columns = candidate time-series rows
        rows = {}
        for (r, c) in formulas:
            if c in cols:
                rows.setdefault(r, {})[c] = formulas[(r, c)]
        for r, byc in rows.items():
            present = sorted(byc)
            if len(present) < 2:
                continue
            # shape signature per cell (R1C1) — for the *reported* consistency check
            sigs = {c: R.shape_signature(byc[c], r, c) for c in present}
            shape_consistent = len(set(sigs.values())) == 1
            # outlier shape (a single odd cell) -> should-fix
            if not shape_consistent:
                from collections import Counter
                common, _n = Counter(sigs.values()).most_common(1)[0]
                for c in present:
                    if sigs[c] != common:
                        add("shape_consistency", name,
                            "%s%d" % (get_column_letter(c), r), "should-fix",
                            "Formula breaks the shape of its row siblings — likely an inconsistent/overwritten cell.",
                            detail={"signature": sigs[c], "siblings": common})

            # ROLL-FORWARD: across adjacent band columns, refs landing inside the band
            # should advance by +1 column per period step. Stuck refs == the headline bug.
            stuck_cells = []
            partial_cells = []
            for i in range(1, len(present)):
                cprev, cur = present[i - 1], present[i]
                step = cur - cprev if False else (cur - cprev)  # column distance
                prev_band = R.referenced_columns(byc[cprev], within=(lo, hi))
                cur_band = R.referenced_columns(byc[cur], within=(lo, hi))
                if not prev_band or not cur_band:
                    continue
                expected = sorted(x + step for x in prev_band)
                if cur_band == expected:
                    continue  # advances correctly
                # how many of the refs are simply stuck (identical to prev)?
                if cur_band == prev_band:
                    stuck_cells.append(cur)
                else:
                    partial_cells.append(cur)
            for c in stuck_cells:
                add("roll_forward", name, "%s%d" % (get_column_letter(c), r), "must-fix",
                    "Reference does not roll forward with the period — formula points at the SAME cells as the "
                    "prior period instead of advancing. Consistently wrong across the row; a shape/consistency "
                    "check passes it.",
                    detail={"shape_consistent": shape_consistent, "period_label": band["labels"]})
            for c in partial_cells:
                add("roll_forward", name, "%s%d" % (get_column_letter(c), r), "should-fix",
                    "Reference advances irregularly across the period axis (some refs do not shift by one period) — "
                    "verify the fill or an intended base-period anchor.",
                    detail={"shape_consistent": shape_consistent})

            # repeated identical ABSOLUTE ref across the row (the bug's signature)
            abs_ref_sets = []
            for c in present:
                aset = frozenset(
                    (x["col_idx"], x["row"]) for x in R.parse_cell_refs(byc[c])
                    if (x["col_abs"] and x["row_abs"] and lo <= x["col_idx"] <= hi)
                )
                abs_ref_sets.append(aset)
            nonempty = [s for s in abs_ref_sets if s]
            if len(nonempty) >= 3 and len(set(nonempty)) == 1:
                anchors = ",".join("%s%d" % (get_column_letter(cc), rr) for (cc, rr) in sorted(next(iter(set(nonempty)))))
                add("repeated_literal_ref", name,
                    "row %d cols %s-%s" % (r, get_column_letter(lo), get_column_letter(hi)),
                    "should-fix",
                    "The same absolute reference(s) {%s} repeat across every period column — the signature of an "
                    "absolute ref filled across a time axis (it can never roll forward)." % anchors)

    # ---------- 4+5: hardcodes ----------
    # 4. literal number sitting in a calc zone (a row/column where neighbours are formulas)
    # column-wise calc zones
    for c in range(1, max_col + 1):
        col_cells = [(r, ws.cell(row=r, column=c).value) for r in range(1, max_row + 1)]
        col_cells = [(r, v) for (r, v) in col_cells if v is not None and not (isinstance(v, str) and v.strip() == "")]
        forms = [r for (r, v) in col_cells if is_formula(v)]
        nums = [(r, v) for (r, v) in col_cells if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(forms) >= 2 and nums and len(forms) >= len(col_cells) * 0.5:
            # the column is a calc zone; numeric literals interleaved with formulas are suspect
            fmin, fmax = min(forms), max(forms)
            for (r, v) in nums:
                if fmin <= r <= fmax:  # literal sits *inside* the formula run
                    add("hardcode_in_calc_zone", name, "%s%d" % (get_column_letter(c), r), "must-fix",
                        "Hardcoded number (%s) inside a calculated column where its neighbours are formulas — "
                        "it won't recompute when inputs change and can silently disagree with the formula logic." % v,
                        detail={"calc_zone_col": get_column_letter(c)})

    # 5. magic constants embedded inside formulas
    NUMLIT = re.compile(r"(?<![A-Za-z0-9_$.!])\d+(?:\.\d+)?(?![A-Za-z0-9_])")
    for (r, c), f in formulas.items():
        body = R.strip_strings(f)
        # remove cell refs so row/col digits aren't read as constants
        body_norm = R.CELL_RE.sub(" ", body)
        for m in NUMLIT.finditer(body_norm):
            try:
                val = float(m.group(0))
            except ValueError:
                continue
            if val in SAFE_CONSTANTS or val == int(val) and int(val) in SAFE_CONSTANTS:
                continue
            add("hardcode_in_formula", name, "%s%d" % (get_column_letter(c), r), "should-fix",
                "Magic constant %s embedded in a formula — an undocumented assumption that won't show up as an "
                "input cell. Move it to a labelled, sourced assumption cell." % m.group(0),
                detail={"formula": f})
            break  # one finding per cell is enough

    # ---------- 6: #REF! and other in-cell error strings ----------
    for (r, c), f in formulas.items():
        for errtok in ("#REF!", "#NAME?", "#DIV/0!", "#VALUE!", "#N/A", "#NULL!", "#NUM!"):
            if errtok in f:
                sev = "must-fix" if errtok in ("#REF!", "#NAME?", "#DIV/0!") else "should-fix"
                add("ref_error", name, "%s%d" % (get_column_letter(c), r), sev,
                    "Formula contains a broken-reference/error token %s — a stale or deleted reference the result "
                    "silently carries." % errtok, detail={"formula": f})
                break

    return formulas


def build_dependency_graph(wb):
    """Cross-sheet precedent graph. Node = 'Sheet!A1'. Edge = formula -> precedent cell.

    Returns nodes, edges, dangling (precedent cell is empty), cycles (simple).
    """
    # index all non-empty cells per sheet for dangling detection
    populated = {}
    for sn in wb.sheetnames:
        ws = wb[sn]
        s = set()
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if v is not None and not (isinstance(v, str) and v.strip() == ""):
                    s.add((cell.row, cell.column))
        populated[sn] = s

    edges = []
    adj = {}
    dangling = []
    for sn in wb.sheetnames:
        ws = wb[sn]
        for row in ws.iter_rows():
            for cell in row:
                if not is_formula(cell.value):
                    continue
                src = "%s!%s%d" % (sn, get_column_letter(cell.column), cell.row)
                for ref in R.parse_cell_refs(cell.value):
                    tgt_sheet = ref["sheet"] or sn
                    tgt = "%s!%s%d" % (tgt_sheet, ref["col"], ref["row"])
                    # graph same-sheet precedents precisely (for cycle detection)
                    if not ref["has_sheet"]:
                        edges.append((src, tgt))
                        adj.setdefault(src, set()).add(tgt)
                    # dangling check works for same-sheet AND cross-sheet refs,
                    # but only when we actually know the target sheet's cells.
                    if tgt_sheet in populated and (ref["row"], ref["col_idx"]) not in populated[tgt_sheet]:
                        dangling.append({"formula_cell": src, "precedent": tgt,
                                         "note": "precedent cell is empty — a moved/deleted input the formula still points at"})
    # simple cycle detection (DFS)
    cycles = []
    WHITE, GREY, BLACK = 0, 1, 2
    color = {}

    def dfs(u, stack):
        color[u] = GREY
        stack.append(u)
        for w in adj.get(u, ()):
            if color.get(w, WHITE) == GREY:
                cycles.append(stack[stack.index(w):] + [w])
            elif color.get(w, WHITE) == WHITE:
                dfs(w, stack)
        stack.pop()
        color[u] = BLACK

    for n in list(adj):
        if color.get(n, WHITE) == WHITE:
            dfs(n, [])

    return {
        "node_count": len({n for e in edges for n in e}),
        "edge_count": len(edges),
        "edges_sample": edges[:40],
        "dangling": dangling[:40],
        "dangling_count": len(dangling),
        "cycles": [c for c in cycles][:10],
    }


def check_named_ranges(wb):
    out = []
    try:
        dn = wb.defined_names
        items = dn.items() if hasattr(dn, "items") else [(d.name, d) for d in dn.definedName]
    except Exception:
        items = []
    for nm, defn in items:
        refers = getattr(defn, "value", getattr(defn, "attr_text", ""))
        refers = refers or ""
        broken = "#REF!" in str(refers)
        out.append({"name": nm, "refers_to": str(refers),
                    "ok": not broken,
                    "issue": "destination contains #REF! — broken named range" if broken else None})
    return out


def run_recalc(workbook_path, enabled=True):
    """Invoke xlsx/scripts/recalc.py (cwd = its own dir, per the import coupling).

    Graceful fallback: if it can't run (no LibreOffice / not found), return a
    'recalc unverified' marker -> caller sets status ok-with-warnings.
    """
    if not enabled:
        return {"ran": False, "note": "recalc skipped (--no-recalc)"}
    # locate the xlsx skill
    candidates = []
    env_root = os.environ.get("SKILLS_ROOT")
    if env_root:
        candidates.append(os.path.join(env_root, "xlsx", "scripts"))
    import glob as _glob
    candidates += _glob.glob("/sessions/*/mnt/.claude/skills/xlsx/scripts")
    candidates += [os.path.expanduser("~/.claude/skills/xlsx/scripts")]
    candidates += _glob.glob("/var/folders/*/T/claude-hostloop-plugins/*/skills/xlsx/scripts")
    scripts_dir = next((d for d in candidates if os.path.isfile(os.path.join(d, "recalc.py"))), None)
    if not scripts_dir:
        return {"ran": False, "note": "recalc unverified — xlsx/scripts/recalc.py not found in this environment"}
    try:
        proc = subprocess.run(
            [sys.executable, "recalc.py", os.path.abspath(workbook_path), "60"],
            cwd=scripts_dir, capture_output=True, text=True, timeout=120,
        )
        out = proc.stdout.strip()
        data = json.loads(out) if out.startswith("{") else {"error": out or proc.stderr}
        if "error" in data:
            return {"ran": False, "note": "recalc unverified — %s" % data["error"]}
        return {"ran": True, "status": data.get("status"),
                "total_errors": data.get("total_errors", 0),
                "total_formulas": data.get("total_formulas"),
                "error_summary": data.get("error_summary", {})}
    except Exception as e:
        return {"ran": False, "note": "recalc unverified — %s" % e}


# ---------- 9: source-packet linkage ----------
# Trace OUTPUT numbers back to packet claims. Two findings only, to keep the
# false-positive rate low (conventions §5): a number tied to an UNSUPPORTED
# claim (always a must-fix), and a number that carries a LOSING value from a
# conflict (should-fix). A cell that already carries the authoritative value is
# NOT flagged. Input/source/raw sheets are skipped — linkage is about what the
# *deliverable* asserts, not the inputs it reads from.
_METRIC_ALIASES = {
    "revenue_yoy": ["yoy", "year over year", "year-over-year", "y/y", "growth %", "growth%"],
    "ebitda_margin": ["ebitda margin", "ebitda %", "margin"],
    "headcount": ["headcount", "fte", "head count", "employees"],
    "revenue": ["revenue", "sales", "turnover"],
    "cloud_migration_spend": ["cloud", "migration"],
}
_SOURCE_SHEET_RE = re.compile(r"actual|source|^data$|input|raw|conflict|notes|assumption", re.IGNORECASE)


def _num(value):
    """Best-effort numeric value from a cell or a packet value string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    s = value.strip().lower().replace("€", "").replace("$", "").replace("£", "").replace(",", "")
    s = s.replace("~", "").replace("≈", "").replace("+", "")
    pct = s.endswith("%")
    s = s.replace("%", "")
    mult = 1.0
    if s.endswith("m") or s.endswith("mn"):
        s = s.rstrip("mn");
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    val = float(m.group(0)) * mult
    if pct:
        val = val / 100.0
    return val


def _close(a, b, tol=0.011):
    if a is None or b is None:
        return False
    if abs(a) > 5 and abs(b) > 5:
        return abs(a - b) <= max(1.0, abs(b) * 0.001)
    return abs(a - b) <= tol


def link_source_packet(wb, packet_path, add):
    try:
        with open(packet_path) as fh:
            packet = json.load(fh)
    except Exception as e:
        add("source_packet_link", "(packet)", os.path.basename(packet_path), "should-fix",
            "Source packet provided but could not be read (%s) — numbers were not traced to claims." % e)
        return None
    claims = packet.get("claims", [])
    conflicts = packet.get("conflicts", [])
    by_metric = {}
    for cl in claims:
        by_metric.setdefault(cl.get("metric"), []).append(cl)

    def conflict_losers(metric):
        """Return (authoritative_num, [loser_nums]) for a metric's conflict, if any."""
        for cl in claims:
            if cl.get("metric") == metric and cl.get("review_status") == "conflicting":
                auth = _num(cl.get("value"))
                losers = []
                for cf in conflicts:
                    if metric.split("_")[0] in (cf.get("topic", "").lower()):
                        for comp in cf.get("competing", []):
                            n = _num(comp.get("value"))
                            if n is not None and not _close(n, auth):
                                losers.append(n)
                return auth, losers
        return None, []

    def metric_for_label(label):
        lab = (label or "").lower()
        for metric, aliases in _METRIC_ALIASES.items():
            if any(a in lab for a in aliases):
                return metric
        return None

    linked = 0
    for sn in wb.sheetnames:
        if _SOURCE_SHEET_RE.search(sn):
            continue  # skip input/source/raw sheets — linkage is about the deliverable's claims
        ws = wb[sn]
        max_row, max_col = ws.max_row or 0, ws.max_column or 0
        for r in range(1, max_row + 1):
            label, label_col = None, None
            for c in range(1, min(max_col, 6) + 1):
                v = ws.cell(row=r, column=c).value
                if isinstance(v, str) and v.strip() and not is_formula(v):
                    label, label_col = v, c
                    break
            if not label:
                continue
            metric = metric_for_label(label)
            if not metric:
                continue
            # value cell + its literal numeric value (if any)
            value_loc, cell_num = None, None
            for c in range(label_col + 1, max_col + 1):
                v = ws.cell(row=r, column=c).value
                if v is not None and not (isinstance(v, str) and v.strip() == ""):
                    value_loc = "%s%d" % (get_column_letter(c), r)
                    cell_num = _num(v) if not is_formula(v) else None
                    break

            cands = by_metric.get(metric, [])
            unsupported = next((cl for cl in cands if cl.get("review_status") == "unsupported"), None)
            if unsupported:
                linked += 1
                add("source_packet_link", sn, value_loc or ("row %d" % r), "must-fix",
                    "'%s' (%s) traces to claim %s which is UNSUPPORTED in the source packet (value %r, no source). "
                    "Source it or drop — do not present as fact."
                    % (label.strip(), value_loc, unsupported.get("id"), unsupported.get("value")),
                    detail={"claim_id": unsupported.get("id"), "metric": metric,
                            "review_status": "unsupported", "packet_value": unsupported.get("value")})
                continue
            auth, losers = conflict_losers(metric)
            if losers and cell_num is not None and any(_close(cell_num, lo) for lo in losers):
                cl = next((c for c in cands if c.get("review_status") == "conflicting"), None)
                linked += 1
                add("source_packet_link", sn, value_loc or ("row %d" % r), "should-fix",
                    "'%s' carries %s, a NON-authoritative value from %s — the source packet's authoritative value "
                    "is %r (claim %s). Reconcile to the authoritative figure."
                    % (label.strip(), value_loc, "a conflict", (cl or {}).get("value"), (cl or {}).get("id")),
                    detail={"claim_id": (cl or {}).get("id"), "metric": metric,
                            "review_status": "conflicting", "cell_value": cell_num,
                            "authoritative": auth, "losing_values": losers})
    return {"claims_indexed": len(claims), "cells_linked": linked}


def run(workbook_path, out_dir, map_path=None, source_packet=None, run_id=None, do_recalc=True,
        max_full_load_mb=R.DEFAULT_MAX_FULL_LOAD_MB,
        cell_budget=R.DEFAULT_CELL_BUDGET):
    findings = []
    _seq = {"n": 0}

    def add(check, sheet, locator, severity, message, detail=None):
        _seq["n"] += 1
        findings.append({
            "id": "FR-%03d" % _seq["n"],
            "check": check,
            "sheet": sheet,
            "locator": locator,
            "severity": severity,
            "review_status": "needs-review",
            "issue_lifecycle": "open",
            "message": message,
            "detail": detail,
        })

    try:
        wb, load_mode, size_mb, load_note = R.open_workbook(
            workbook_path, max_full_load_mb=max_full_load_mb, cell_budget=cell_budget)
    except Exception as e:
        return _emit(out_dir, {
            "run_id": run_id, "skill": "workbook-doctor", "phase": "formula-risk",
            "status": "failed", "status_reason": "could not open workbook: %s" % e,
            "inputs": {"workbook": workbook_path}, "findings": [], "summary": {}})

    # DR-04: oversized workbook -> read-only streaming (never OOM-crash). The
    # formula scan still runs (it reads cell values + iterates formula cells),
    # but recalc is skipped (LibreOffice would re-load the whole bloated file)
    # and the limitation is recorded as a finding, never silently.
    if load_mode == "streamed":
        do_recalc = False
        add("large_workbook_streamed", "(workbook)", os.path.basename(workbook_path),
            "should-fix",
            "Workbook is %.1f MB — scanned in read-only STREAMING mode to avoid an "
            "out-of-memory crash. Recalc is skipped (recorded 'recalc unverified'); the "
            "formula-risk scan is bounded by the streaming cell budget." % size_mb,
            detail={"load_mode": load_mode, "size_mb": round(size_mb, 1),
                    "degraded_checks": ["recalc"],
                    "sampled_sheets": getattr(wb, "truncated_sheets", [])})

    period_bands_all = []
    for sn in wb.sheetnames:
        ws = wb[sn]
        bands = detect_period_bands(ws, ws.max_row or 0, ws.max_column or 0)
        for b in bands:
            b["sheet"] = sn
        period_bands_all += bands
        scan_sheet(ws, findings, add, bands)

    # named ranges / tables
    named = check_named_ranges(wb)
    for nr in named:
        if not nr["ok"]:
            add("named_range", "(workbook)", nr["name"], "should-fix", nr["issue"],
                detail={"refers_to": nr["refers_to"]})

    # dependency graph + dangling
    graph = build_dependency_graph(wb)
    for d in graph["dangling"]:
        add("dangling_reference", d["formula_cell"].split("!")[0], d["formula_cell"].split("!")[1],
            "should-fix",
            "Formula references %s, an empty cell — a stale/moved precedent the result silently carries." % d["precedent"],
            detail=d)
    for cyc in graph["cycles"]:
        add("circular_reference", cyc[0].split("!")[0], " -> ".join(cyc), "must-fix",
            "Circular reference chain — the model cannot resolve a stable value.")

    # recalc capability
    recalc = run_recalc(workbook_path, enabled=do_recalc)
    if recalc.get("ran") and recalc.get("error_summary"):
        for etype, info in recalc["error_summary"].items():
            for loc in info.get("locations", []):
                sheet = loc.split("!")[0] if "!" in loc else "(workbook)"
                cellref = loc.split("!")[1] if "!" in loc else loc
                sev = "must-fix" if etype in ("#REF!", "#NAME?", "#DIV/0!") else "should-fix"
                add("recalc_error", sheet, cellref, sev,
                    "Live recalculation produced %s — a real error in the computed result." % etype,
                    detail={"error_type": etype})

    # source-packet linkage
    link = None
    if source_packet:
        link = link_source_packet(wb, source_packet, add)

    # ---- status ----
    sev_counts = {"must-fix": 0, "should-fix": 0, "polish": 0}
    by_check = {}
    for f in findings:
        sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
        by_check[f["check"]] = by_check.get(f["check"], 0) + 1

    recalc_unverified = not recalc.get("ran", False)
    if recalc_unverified:
        status = "ok-with-warnings"
        reason = "%d formula risk(s) found; RECALC UNVERIFIED (%s)." % (len(findings), recalc.get("note", "no recalc"))
    elif findings:
        status = "ok-with-warnings"
        reason = "%d formula risk(s): %d must-fix, %d should-fix." % (
            len(findings), sev_counts["must-fix"], sev_counts["should-fix"])
    else:
        status = "ok"
        reason = "no formula risks detected; recalc clean."

    # rank findings: must-fix first, then should-fix, then polish; stable by id
    order = {"must-fix": 0, "should-fix": 1, "polish": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 9), f["id"]))

    doc = {
        "run_id": run_id, "skill": "workbook-doctor", "phase": "formula-risk",
        "status": status, "status_reason": reason,
        "load_mode": load_mode, "size_mb": round(size_mb, 1),
        "inputs": {"workbook": os.path.basename(workbook_path),
                   "workbook_map": os.path.basename(map_path) if map_path else None,
                   "source_packet": os.path.basename(source_packet) if source_packet else None},
        "recalc": recalc,
        "period_bands": [{"sheet": b["sheet"], "header_row": b["header_row"],
                          "cols": [get_column_letter(c) for c in b["cols"]],
                          "labels": b["labels"]} for b in period_bands_all],
        "named_ranges": named,
        "dependency_graph": graph,
        "source_link": link,
        "checks_run": ["shape_consistency", "roll_forward", "repeated_literal_ref",
                       "hardcode_in_calc_zone", "hardcode_in_formula", "ref_error",
                       "named_range", "dangling_reference", "circular_reference",
                       "recalc_error"] + (["source_packet_link"] if source_packet else []),
        "findings": findings,
        "summary": {"finding_count": len(findings), "by_severity": sev_counts, "by_check": by_check},
    }
    return _emit(out_dir, doc)


def _emit(out_dir, doc):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "formula-risk.json"), "w") as fh:
        json.dump(doc, fh, indent=2, default=str)
    with open(os.path.join(out_dir, "formula-risk.md"), "w") as fh:
        fh.write(_markdown(doc))
    return doc


def _markdown(doc):
    L = ["# Formula-Risk Scan", ""]
    L.append("- **Run:** `%s`" % doc.get("run_id"))
    L.append("- **Workbook:** `%s`" % doc["inputs"]["workbook"])
    if doc["inputs"].get("source_packet"):
        L.append("- **Source packet:** `%s`" % doc["inputs"]["source_packet"])
    rc = doc.get("recalc", {})
    L.append("- **Recalc:** %s" % ("ran — %s, %s errors" % (rc.get("status"), rc.get("total_errors"))
                                    if rc.get("ran") else "**UNVERIFIED** — %s" % rc.get("note")))
    L.append("- **Status:** `%s` — %s" % (doc["status"], doc["status_reason"]))
    L.append("")
    if doc.get("period_bands"):
        L.append("## Time axis detected")
        L.append("")
        for b in doc["period_bands"]:
            L.append("- `%s` row %s: %s" % (b["sheet"], b["header_row"], ", ".join(b["labels"])))
        L.append("")
    L.append("## Ranked formula risks (%d)" % len(doc["findings"]))
    L.append("")
    if not doc["findings"]:
        L.append("_None detected._")
    else:
        L.append("| ID | Severity | Check | Sheet | Where | Risk |")
        L.append("|---|---|---|---|---|---|")
        for f in doc["findings"]:
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                f["id"], f["severity"], f["check"], f["sheet"], f["locator"],
                f["message"].split(" — ")[0] if " — " in f["message"] else f["message"][:90]))
    L.append("")
    g = doc.get("dependency_graph", {})
    L.append("## Dependency graph")
    L.append("")
    L.append("- %d precedent edges across %d cells; %d dangling precedent(s); %d cycle(s)." % (
        g.get("edge_count", 0), g.get("node_count", 0), g.get("dangling_count", 0), len(g.get("cycles", []))))
    L.append("")
    L.append("> The **roll-forward** check is a positive model of period-over-period reference advancement: "
             "it catches a formula that is *consistently wrong across a row* — the case a sibling-shape/consistency "
             "check passes (review-1 finding 5).")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--map", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--source-packet", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--no-recalc", action="store_true")
    ap.add_argument("--max-full-load-mb", type=float, default=R.DEFAULT_MAX_FULL_LOAD_MB,
                    help="workbooks larger than this are scanned in read-only streaming mode "
                         "(avoids an OOM crash); default %(default)s")
    ap.add_argument("--scan-cell-budget", type=int, default=R.DEFAULT_CELL_BUDGET,
                    help="max cells scanned per sheet in streaming mode (sampling cap)")
    args = ap.parse_args()
    run_id = args.run_id or (_dt.datetime.now().strftime("%Y-%m-%dT%H%M") + "_workbook")
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.workbook)), ".trust", run_id)
    doc = run(args.workbook, out_dir, map_path=args.map, source_packet=args.source_packet,
              run_id=run_id, do_recalc=not args.no_recalc,
              max_full_load_mb=args.max_full_load_mb, cell_budget=args.scan_cell_budget)
    print(json.dumps({"status": doc["status"], "findings": len(doc["findings"]),
                      "by_check": doc["summary"]["by_check"], "out": out_dir}, indent=2))
    sys.exit(0 if doc["status"] in ("ok", "ok-with-warnings") else 1)


if __name__ == "__main__":
    main()
