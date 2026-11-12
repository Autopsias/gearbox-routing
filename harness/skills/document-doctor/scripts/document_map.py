#!/usr/bin/env python3
# =============================================================================
# Office Trust Layer — Document Doctor · DD-01 extractor + structure map
#                                       · DD-07 PDF extraction-quality / OCR
#
# The prose analogue of workbook-doctor's `workbook_map.py`. INSPECT-ONLY:
# it never edits the document. It maps ONE document of one `format`
# (docx | pdf | md | html), stamps every record with `surface=doc` + `format`
# (conventions.md §1.6 — REQUIRED, never inferred at read time), and emits
# ordered sections (heading path), paragraphs, tables (cell grids where
# possible), figures, footnotes — each with a §1.6 stable locator.
#
# Per-type extractor adapters follow the f2-02 fallback chains
# (capability-inventory.md): docx python-docx→markitdown→raw-xml ;
# pdf pdf-text→markitdown→OCR ; html semantic-DOM→visible-text→raw ;
# md parser→plain-text. The chosen binding is recorded for the manifest.
#
# HTML (review finding #11): visible-text / semantic-DOM / raw modes; hidden,
# collapsed and AGENT-ONLY DOM (e.g. <details class="agent-spec">) are recorded
# SEPARATELY with an `in_scope` flag, never merged into the visible-text claim
# set, and rendered-vs-source divergence is reported.
#
# PDF (DD-07, review finding #5): per-page text coverage, image-only page count,
# table-extraction confidence, OCR availability, locator fidelity. Coverage
# below threshold AND no OCR ⇒ status `blocked` / readiness `not-ready` + a
# human-review item + a DOC-PBW-01 finding (a scan is a VERDICT, not a silent
# empty map). OCR present ⇒ engine / confidence / page provenance recorded.
#
# Contract: severity / review_status / readiness / status / locator grammar are
# defined in references/conventions.md and used VERBATIM here. Output obeys the
# §3 command I/O contract: <out>/document-map.json + <out>/document-map.md.
#
#   python document_map.py <file> --out .trust/<run_id> \
#       [--format auto|docx|pdf|md|html] [--source-packet packet.json] \
#       [--run-id <id>] [--coverage-threshold 0.10] [--max-text 4000]
#
# Keep generic — format-and-domain agnostic; no house template, no client.
# =============================================================================
import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import zipfile

SURFACE = "doc"
SUPPORTED_FORMATS = ("docx", "pdf", "md", "html")
SCHEMA_VERSION = "dd-map/1.0"

# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------


def _now_iso():
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def _sha16(b):
    if isinstance(b, str):
        b = b.encode("utf-8", "replace")
    return hashlib.sha256(b).hexdigest()[:16]


def _tool_version(modname):
    try:
        mod = __import__(modname)
        return getattr(mod, "__version__", "present")
    except Exception:
        return None


def detect_format(path, declared="auto"):
    if declared and declared != "auto":
        if declared not in SUPPORTED_FORMATS:
            raise ValueError("unsupported --format %r" % declared)
        return declared
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    alias = {"htm": "html", "markdown": "md", "mdown": "md", "text": "md", "txt": "md"}
    ext = alias.get(ext, ext)
    if ext not in SUPPORTED_FORMATS:
        raise ValueError(
            "cannot infer a supported format from %r; pass --format" % os.path.basename(path)
        )
    return ext


def _rec(fmt, **kw):
    """Stamp every emitted record with surface + format (conventions §1.6)."""
    base = {"surface": SURFACE, "format": fmt}
    base.update(kw)
    return base


# ----------------------------------------------------------------------------
# DOCX adapter — python-docx → markitdown → raw unzip/XML strip
# ----------------------------------------------------------------------------

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_docx(path, max_text):
    binding = None
    out = {
        "sections": [],
        "paragraphs": [],
        "tables": [],
        "figures": [],
        "footnotes": [],
        "notes": [],
        "status": "ok",
    }
    # --- primary: python-docx -------------------------------------------------
    ver = _tool_version("docx")
    if ver:
        try:
            import docx  # python-docx

            d = docx.Document(path)
            binding = "docx_read=python-docx@%s" % ver
            heading_stack = []  # (level, text)
            p_index = 0
            for para in d.paragraphs:
                txt = (para.text or "").strip()
                style = (para.style.name if para.style else "") or ""
                lvl = None
                m = re.match(r"Heading\s+(\d+)", style)
                if m:
                    lvl = int(m.group(1))
                elif style.lower() in ("title",):
                    lvl = 0
                if lvl is not None and txt:
                    while heading_stack and heading_stack[-1][0] >= lvl:
                        heading_stack.pop()
                    heading_stack.append((lvl, txt))
                    hpath = ">".join(t for _, t in heading_stack)
                    out["sections"].append(
                        _rec("docx", level=lvl, text=txt, heading_path=hpath,
                             locator="heading:'%s'" % hpath)
                    )
                    continue
                if txt:
                    p_index += 1
                    cur = ">".join(t for _, t in heading_stack)
                    out["paragraphs"].append(
                        _rec("docx", index=p_index, text=txt[:max_text],
                             truncated=len(txt) > max_text,
                             heading_path=cur, locator="para:%d" % p_index)
                    )
            for ti, tbl in enumerate(d.tables, 1):
                grid = []
                for r, row in enumerate(tbl.rows):
                    grid.append([(c.text or "").strip() for c in row.cells])
                ncols = max((len(r) for r in grid), default=0)
                cells = []
                for r, rowvals in enumerate(grid):
                    for c, val in enumerate(rowvals):
                        cells.append({"r": r, "c": c, "text": val[:max_text],
                                      "locator": "table:T%dR%dC%d" % (ti, r, c)})
                out["tables"].append(
                    _rec("docx", index=ti, rows=len(grid), cols=ncols,
                         extracted=True, confidence=1.0, cells=cells,
                         locator="table:T%d" % ti)
                )
            # inline images (drawings) -> figures
            try:
                rels = d.part.rels
                fig_i = 0
                for rid, rel in rels.items():
                    if "image" in rel.reltype:
                        fig_i += 1
                        out["figures"].append(
                            _rec("docx", id="img%d" % fig_i, kind="image",
                                 ref=str(rel.target_ref), alt="",
                                 locator="figure:img%d" % fig_i)
                        )
            except Exception:
                pass
            # footnotes (footnotes.xml part is not exposed by the API)
            out["footnotes"].extend(_docx_footnotes(path, max_text))
            out["adapter"] = {"docx_read": binding, "fallback": False}
            return out
        except Exception as e:
            out["notes"].append("python-docx failed (%s); falling back" % e.__class__.__name__)
    # --- fallback 1: markitdown ----------------------------------------------
    md = _markitdown(path)
    if md is not None:
        parsed = read_md_text(md, "docx", max_text)
        parsed["adapter"] = {"docx_read": "markitdown", "fallback": True}
        parsed["notes"] = out["notes"] + parsed.get("notes", []) + [
            "structure fidelity reduced via markitdown fallback"]
        parsed["status"] = "ok-with-warnings"
        return parsed
    # --- fallback 2: raw unzip / XML text strip ------------------------------
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        text = re.sub(r"<[^>]+>", " ", xml)
        text = re.sub(r"\s+", " ", text).strip()
        out["paragraphs"].append(
            _rec("docx", index=1, text=text[:max_text], truncated=len(text) > max_text,
                 heading_path="", locator="para:1")
        )
        out["adapter"] = {"docx_read": "raw-xml-strip", "fallback": True}
        out["status"] = "ok-with-warnings"
        out["notes"].append("raw XML strip fallback — headings/tables/figures lost")
        return out
    except Exception as e:
        out["status"] = "failed"
        out["notes"].append("docx unreadable by every adapter: %s" % e)
        return out


def _docx_footnotes(path, max_text):
    fns = []
    try:
        with zipfile.ZipFile(path) as z:
            if "word/footnotes.xml" not in z.namelist():
                return fns
            import xml.etree.ElementTree as ET

            root = ET.fromstring(z.read("word/footnotes.xml"))
            for fn in root.findall("%sfootnote" % W_NS):
                fid = fn.get("%sid" % W_NS)
                ftype = fn.get("%stype" % W_NS)
                if ftype in ("separator", "continuationSeparator"):
                    continue
                txt = "".join(t.text or "" for t in fn.iter("%st" % W_NS)).strip()
                if txt:
                    fns.append(_rec("docx", id=fid, text=txt[:max_text],
                                    locator="footnote:%s" % fid))
    except Exception:
        pass
    return fns


# ----------------------------------------------------------------------------
# MD adapter — markdown parser (regex) → plain text
# ----------------------------------------------------------------------------


def read_md_file(path, max_text):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return read_md_text(fh.read(), "md", max_text)


def read_md_text(text, fmt, max_text):
    out = {"sections": [], "paragraphs": [], "tables": [], "figures": [],
           "footnotes": [], "notes": [], "status": "ok"}
    lines = text.splitlines()
    heading_stack = []
    p_index = 0
    i = 0
    n = len(lines)
    fence = False
    while i < n:
        ln = lines[i]
        if ln.strip().startswith("```"):
            fence = not fence
            i += 1
            continue
        if fence:
            i += 1
            continue
        # heading
        hm = re.match(r"^(#{1,6})\s+(.*\S)\s*$", ln)
        if hm:
            lvl = len(hm.group(1))
            txt = hm.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= lvl:
                heading_stack.pop()
            heading_stack.append((lvl, txt))
            hpath = ">".join(t for _, t in heading_stack)
            out["sections"].append(_rec(fmt, level=lvl, text=txt, heading_path=hpath,
                                        locator="heading:'%s'" % hpath))
            i += 1
            continue
        # pipe table (header row followed by --- separator)
        if "|" in ln and i + 1 < n and re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i + 1]) \
                and "-" in lines[i + 1]:
            block = [ln, lines[i + 1]]
            j = i + 2
            while j < n and "|" in lines[j] and lines[j].strip():
                block.append(lines[j])
                j += 1
            tbl = _md_table(block, len(out["tables"]) + 1, fmt, max_text)
            out["tables"].append(tbl)
            i = j
            continue
        # footnote definition  [^id]: text
        fnm = re.match(r"^\[\^([^\]]+)\]:\s*(.*\S)\s*$", ln)
        if fnm:
            out["footnotes"].append(_rec(fmt, id=fnm.group(1), text=fnm.group(2)[:max_text],
                                         locator="footnote:%s" % fnm.group(1)))
            i += 1
            continue
        # image  ![alt](src)
        for imm in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", ln):
            fid = "img%d" % (len(out["figures"]) + 1)
            out["figures"].append(_rec(fmt, id=fid, kind="image", alt=imm.group(1),
                                       ref=imm.group(2), locator="figure:%s" % fid))
        # paragraph (non-blank, non-list-marker handled as text)
        if ln.strip():
            p_index += 1
            cur = ">".join(t for _, t in heading_stack)
            out["paragraphs"].append(_rec(fmt, index=p_index, text=ln.strip()[:max_text],
                                          truncated=len(ln.strip()) > max_text,
                                          heading_path=cur, locator="para:%d" % p_index))
        i += 1
    if fmt == "md":
        out["adapter"] = {"md_read": "builtin-parser", "fallback": False}
    return out


def _md_table(block, ti, fmt, max_text):
    def split_row(s):
        s = s.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    rows = [split_row(block[0])] + [split_row(r) for r in block[2:]]
    ncols = max((len(r) for r in rows), default=0)
    cells = []
    for r, rowvals in enumerate(rows):
        for c, val in enumerate(rowvals):
            cells.append({"r": r, "c": c, "text": val[:max_text],
                          "locator": "table:T%dR%dC%d" % (ti, r, c)})
    return _rec(fmt, index=ti, rows=len(rows), cols=ncols, extracted=True,
                confidence=1.0, cells=cells, locator="table:T%d" % ti)


# ----------------------------------------------------------------------------
# HTML adapter — semantic-DOM / visible-text / raw  (+ hidden / agent-only)
# ----------------------------------------------------------------------------

_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)


def _parse_hidden_selectors(soup):
    """Scan <style> blocks for rules that hide content, returning the sets of
    class names / ids / tag names that resolve to display:none / visibility:hidden.
    Real documents hide text via a CSS class far more often than inline style, so
    a hidden-content scanner that only checked inline `style=` would miss the
    most common case (and let hidden tokens leak into the visible claim set)."""
    classes, ids, tags = set(), set(), set()
    for st in soup.find_all("style"):
        css = st.get_text() or ""
        # crude rule splitter: `selector-list { declarations }`
        for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if not _HIDDEN_STYLE.search(decl):
                continue
            for one in sel.split(","):
                one = one.strip()
                m = re.match(r"^\.([A-Za-z0-9_-]+)$", one)
                if m:
                    classes.add(m.group(1))
                    continue
                m = re.match(r"^#([A-Za-z0-9_-]+)$", one)
                if m:
                    ids.add(m.group(1))
                    continue
                m = re.match(r"^([A-Za-z][A-Za-z0-9]*)$", one)
                if m:
                    tags.add(m.group(1).lower())
    return {"classes": classes, "ids": ids, "tags": tags}


def _is_hidden(tag, css=None):
    attrs = getattr(tag, "attrs", None)
    if not attrs:
        return False
    if "hidden" in attrs:
        return True
    if str(attrs.get("aria-hidden") or "").lower() == "true":
        return True
    if _HIDDEN_STYLE.search(attrs.get("style") or ""):
        return True
    if css:
        if (tag.name or "").lower() in css["tags"]:
            return True
        if attrs.get("id") in css["ids"]:
            return True
        for c in (attrs.get("class") or []):
            if c in css["classes"]:
                return True
    return False


def _is_agent_only(tag):
    attrs = getattr(tag, "attrs", None)
    if not attrs:
        return False
    cls = " ".join(attrs.get("class") or [])
    if re.search(r"\bagent(-spec|-only)?\b", cls, re.I):
        return True
    for a in attrs:
        if a.lower().startswith("data-agent"):
            return True
    return False


def read_html_file(path, max_text):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    return read_html_text(raw, max_text)


def read_html_text(raw, max_text):
    out = {"sections": [], "paragraphs": [], "tables": [], "figures": [],
           "footnotes": [], "notes": [], "status": "ok",
           "html_modes": {}, "hidden_blocks": [], "agent_only_blocks": [],
           "nav_blocks": []}
    try:
        from bs4 import BeautifulSoup
    except Exception:
        # fallback: markitdown then raw strip
        md = _markitdown_from_text(raw, ".html")
        if md is not None:
            parsed = read_md_text(md, "html", max_text)
            parsed["adapter"] = {"html_read": "markitdown", "fallback": True}
            parsed["html_modes"] = {"ran": "markitdown-text"}
            parsed["notes"] = ["bs4 absent — markitdown fallback, DOM scoping lost"]
            parsed["status"] = "ok-with-warnings"
            return parsed
        text = re.sub(r"<[^>]+>", " ", raw)
        out["paragraphs"].append(_rec("html", index=1, text=re.sub(r"\s+", " ", text).strip()[:max_text],
                                      heading_path="", locator="para:1"))
        out["adapter"] = {"html_read": "raw-strip", "fallback": True}
        out["status"] = "ok-with-warnings"
        out["notes"].append("bs4 + markitdown absent — raw tag strip only")
        return out

    soup = BeautifulSoup(raw, "html.parser")
    parser_used = "html.parser"
    try:
        import lxml  # noqa: F401
        soup = BeautifulSoup(raw, "lxml")
        parser_used = "lxml"
    except Exception:
        pass

    # CSS-class / id / tag hidden selectors from <style> blocks (not just inline)
    css_hidden = _parse_hidden_selectors(soup)

    # ----- collect hidden / agent-only / nav / details BEFORE pruning -------
    def block_text(tag):
        return re.sub(r"\s+", " ", tag.get_text(" ", strip=True))[:max_text]

    hidden_token_pool = []
    for tag in soup.find_all(True):
        agent = _is_agent_only(tag)
        hidden = _is_hidden(tag, css_hidden)
        is_details = tag.name == "details"
        if agent:
            t = block_text(tag)
            if t:
                out["agent_only_blocks"].append(
                    _rec("html", kind="agent-only", tag=tag.name,
                         cls=" ".join(tag.get("class") or []), in_scope=False,
                         text=t, locator="figure:agent-%d" % (len(out["agent_only_blocks"]) + 1)))
                hidden_token_pool.append(t)
        elif hidden or is_details:
            t = block_text(tag)
            if t:
                out["hidden_blocks"].append(
                    _rec("html", kind=("collapsed-details" if is_details else "hidden"),
                         tag=tag.name, in_scope=False, text=t,
                         locator="figure:hidden-%d" % (len(out["hidden_blocks"]) + 1)))
                hidden_token_pool.append(t)
        if tag.name == "nav":
            t = block_text(tag)
            out["nav_blocks"].append(
                _rec("html", kind="nav", in_scope=False, text=t,
                     locator="figure:nav-%d" % (len(out["nav_blocks"]) + 1)))

    # raw-mode signature
    out["html_modes"]["raw"] = {"bytes": len(raw), "sha16": _sha16(raw)}

    # ----- semantic-DOM mode: structure from the VISIBLE tree ----------------
    # Work on a copy with hidden/agent/script/style removed so the visible
    # claim set never silently absorbs hidden or agent-only content. Collect the
    # removal targets FIRST, then decompose — decomposing during find_all()
    # iteration would leave already-listed descendants stale (attrs=None).
    vis = BeautifulSoup(raw, parser_used)
    vis_css_hidden = _parse_hidden_selectors(vis)
    to_remove = [t for t in vis.find_all(True)
                 if t.name in ("script", "style") or t.name == "details"
                 or _is_agent_only(t) or _is_hidden(t, vis_css_hidden)]
    for t in to_remove:
        try:
            if getattr(t, "decomposed", False):
                continue
            t.decompose()
        except Exception:
            pass

    heading_stack = []
    p_index = 0
    body = vis.body or vis
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li",
                             "table", "img", "svg", "figure", "blockquote"]):
        nm = el.name
        if nm in ("h1", "h2", "h3", "h4", "h5", "h6"):
            lvl = int(nm[1])
            txt = el.get_text(" ", strip=True)
            if not txt:
                continue
            while heading_stack and heading_stack[-1][0] >= lvl:
                heading_stack.pop()
            heading_stack.append((lvl, txt))
            hpath = ">".join(t for _, t in heading_stack)
            out["sections"].append(_rec("html", level=lvl, text=txt, heading_path=hpath,
                                        locator="heading:'%s'" % hpath))
        elif nm in ("p", "li", "blockquote"):
            txt = el.get_text(" ", strip=True)
            if not txt:
                continue
            p_index += 1
            cur = ">".join(t for _, t in heading_stack)
            out["paragraphs"].append(_rec("html", index=p_index, text=txt[:max_text],
                                          truncated=len(txt) > max_text, heading_path=cur,
                                          tag=nm, locator="para:%d" % p_index))
        elif nm == "table":
            ti = len(out["tables"]) + 1
            grid = []
            for tr in el.find_all("tr"):
                grid.append([c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])])
            grid = [r for r in grid if r]
            ncols = max((len(r) for r in grid), default=0)
            cells = []
            for r, rowvals in enumerate(grid):
                for c, val in enumerate(rowvals):
                    cells.append({"r": r, "c": c, "text": val[:max_text],
                                  "locator": "table:T%dR%dC%d" % (ti, r, c)})
            out["tables"].append(_rec("html", index=ti, rows=len(grid), cols=ncols,
                                      extracted=True, confidence=1.0, cells=cells,
                                      locator="table:T%d" % ti))
        elif nm == "img":
            fid = "img%d" % (len(out["figures"]) + 1)
            out["figures"].append(_rec("html", id=fid, kind="image",
                                       alt=el.get("alt", ""), ref=el.get("src", ""),
                                       locator="figure:%s" % fid))
        elif nm == "svg":
            fid = "svg%d" % (len([f for f in out["figures"] if f.get("kind") == "svg"]) + 1)
            title = el.find("title")
            out["figures"].append(_rec("html", id=fid, kind="svg",
                                       alt=(title.get_text(strip=True) if title else ""),
                                       locator="figure:%s" % fid))

    # footnotes: role=doc-footnote / id^=fn
    for el in vis.find_all(attrs={"role": "doc-footnote"}):
        out["footnotes"].append(_rec("html", id=el.get("id", "fn%d" % (len(out["footnotes"]) + 1)),
                                     text=el.get_text(" ", strip=True)[:max_text],
                                     locator="footnote:%s" % el.get("id", "")))

    # ----- visible-text mode -------------------------------------------------
    visible_text = body.get_text(" ", strip=True)
    out["html_modes"]["visible_text"] = {"chars": len(visible_text), "sha16": _sha16(visible_text)}
    out["html_modes"]["semantic_dom"] = {
        "parser": parser_used,
        "sections": len(out["sections"]), "paragraphs": len(out["paragraphs"]),
        "tables": len(out["tables"]), "figures": len(out["figures"]),
    }
    out["html_modes"]["ran"] = ["semantic_dom", "visible_text", "raw"]

    # ----- rendered-vs-source divergence (finding #11) -----------------------
    def toks(s):
        return set(w for w in re.findall(r"[A-Za-z0-9%€$£.,/-]{3,}", s.lower()) if not w.isdigit() or True)

    vis_tokens = toks(visible_text)
    hidden_tokens = set()
    for t in hidden_token_pool:
        hidden_tokens |= toks(t)
    only_in_source = sorted(hidden_tokens - vis_tokens)
    out["rendered_vs_source"] = {
        "visible_tokens": len(vis_tokens),
        "hidden_or_agent_tokens": len(hidden_tokens),
        "tokens_in_source_not_rendered": len(only_in_source),
        "sample": only_in_source[:25],
        "divergent": bool(only_in_source),
        "note": ("content present in source markup is NOT in the visible render "
                 "(hidden / collapsed / agent-only / script) — scoped separately, "
                 "never merged into the visible-text claim set"),
    }
    out["adapter"] = {"html_read": "bs4-%s" % parser_used, "fallback": False}
    if out["hidden_blocks"] or out["agent_only_blocks"]:
        out["status"] = "ok-with-warnings"
    return out


# ----------------------------------------------------------------------------
# PDF adapter (DD-07) — pdf-text (pypdf) → markitdown → OCR(if available)
# ----------------------------------------------------------------------------


def _ocr_available(disable=False):
    # An explicit override (flag/env) lets a caller certify the "no OCR" branch
    # deterministically even on a host where tesseract happens to be installed
    # (the DD-07 block rule must be testable everywhere).
    if disable or os.environ.get("DD_DISABLE_OCR"):
        return None
    import shutil
    if shutil.which("tesseract"):
        return "tesseract-bin"
    if _tool_version("pytesseract"):
        return "pytesseract"
    return None


def _pdf_tables(path, out, pages, max_text):
    """Extract tables with pdfplumber (if present) and return (confidence, engine).

    Confidence = share of detected tables that are rectangular and non-trivial
    (>=2 rows, >=2 cols, mostly-populated). A table on a scanned/image-only page
    is invisible to pdfplumber, so the confidence honestly stays low — never
    fabricated. Each extracted table is appended to out["tables"] with a
    page-level locator + a cell grid."""
    ver = _tool_version("pdfplumber")
    if not ver:
        return 0.0, None
    good = 0
    seen = 0
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            for pi, page in enumerate(pdf.pages, 1):
                try:
                    tbls = page.extract_tables() or []
                except Exception:
                    tbls = []
                for tb in tbls:
                    seen += 1
                    grid = [[("" if c is None else str(c)).strip() for c in row]
                            for row in tb if row is not None]
                    grid = [r for r in grid if any(v for v in r)]
                    nrows = len(grid)
                    ncols = max((len(r) for r in grid), default=0)
                    populated = sum(1 for r in grid for v in r if v)
                    cells_total = max(nrows * ncols, 1)
                    fill = populated / cells_total
                    rectangular = all(len(r) == ncols for r in grid) and ncols >= 2 and nrows >= 2
                    is_good = rectangular and fill >= 0.5
                    if is_good:
                        good += 1
                    ti = len(out["tables"]) + 1
                    cells = []
                    for r, rowvals in enumerate(grid):
                        for c, val in enumerate(rowvals):
                            cells.append({"r": r, "c": c, "text": val[:max_text],
                                          "locator": "table:T%dR%dC%d" % (ti, r, c)})
                    out["tables"].append(_rec(
                        "pdf", index=ti, page=pi, rows=nrows, cols=ncols,
                        extracted=is_good, confidence=round(fill, 3), cells=cells,
                        locator="page:%d" % pi))
    except Exception as e:
        out["notes"].append("pdfplumber table pass failed (%s)" % e.__class__.__name__)
        return 0.0, "pdfplumber@%s(failed)" % ver
    conf = (good / seen) if seen else 0.0
    return conf, "pdfplumber@%s" % ver


def _pdf_resources_have_image(res, _depth=0, _seen=None):
    """True if a page/XObject resource tree contains an image XObject.

    LibreOffice (and many real-world PDFs) wrap a page image inside a Form
    XObject, so a single-level /XObject walk misses it and undercounts figures
    (DR-10 sharp edge: a born-digital PDF with one embedded figure reported
    figures=0). Recurse into nested Form XObjects — depth-bounded and
    cycle-guarded — so an embedded figure is detected the way pdfplumber's
    .images sees it. Additive: this can only flip has_image False->True, never
    the reverse, so coverage / image-only / blocking logic is unchanged."""
    if res is None or _depth > 6:
        return False
    if _seen is None:
        _seen = set()
    try:
        res = res.get_object()
        xo = res.get("/XObject")
        if not xo:
            return False
        xo = xo.get_object()
        for k in xo:
            try:
                obj = xo[k].get_object()
            except Exception:
                continue
            oid = id(obj)
            if oid in _seen:
                continue
            _seen.add(oid)
            sub = obj.get("/Subtype")
            if sub == "/Image":
                return True
            if sub == "/Form" and _pdf_resources_have_image(
                    obj.get("/Resources"), _depth + 1, _seen):
                return True
    except Exception:
        return False
    return False


def _pdf_pdfplumber_image_pages(path):
    """Per-page count of PLACED raster images via pdfplumber (`page.images`).

    This is the faithful figure signal: pdfplumber resolves nested/Form XObjects
    and reports images that are actually drawn, so a single embedded figure
    counts once on its page — unlike a raw pypdf resource walk, which also
    matches a shared logo/header Form XObject referenced on every page and
    over-counts. Returns {page_index(1-based): n_images} or None if pdfplumber
    is unavailable (caller then falls back to the resource-tree walk)."""
    try:
        import pdfplumber
    except Exception:
        return None
    counts = {}
    try:
        with pdfplumber.open(path) as pdf:
            for pi, page in enumerate(pdf.pages, 1):
                counts[pi] = len(page.images or [])
    except Exception:
        return None
    return counts


def read_pdf(path, max_text, coverage_threshold, disable_ocr=False):
    out = {"sections": [], "paragraphs": [], "tables": [], "figures": [],
           "footnotes": [], "notes": [], "status": "ok"}
    ocr = _ocr_available(disable=disable_ocr)
    ver = _tool_version("pypdf")
    pages = []  # per-page: {page, chars, has_text, has_image}
    primary = None
    if ver:
        try:
            from pypdf import PdfReader

            reader = PdfReader(path)
            primary = "pdf_read=pypdf@%s" % ver
            # Faithful per-page placed-image counts (pdfplumber); fall back to the
            # resource-tree walk only when pdfplumber is unavailable. DR-10: this
            # detects a born-digital embedded figure WITHOUT over-counting a shared
            # header/footer Form XObject that a raw pypdf walk matches on every page.
            plumb_imgs = _pdf_pdfplumber_image_pages(path)
            p_index = 0
            for pi, page in enumerate(reader.pages, 1):
                try:
                    txt = page.extract_text() or ""
                except Exception:
                    txt = ""
                txt = txt.strip()
                # placed raster images on the page → figure / candidate scan
                if plumb_imgs is not None:
                    n_img = plumb_imgs.get(pi, 0)
                    has_image = n_img > 0
                else:
                    has_image = _pdf_resources_have_image(page.get("/Resources"))
                    n_img = 1 if has_image else 0
                chars = len(re.sub(r"\s+", "", txt))
                has_text = chars >= 12  # a near-empty page is "no text"
                pages.append({"page": pi, "chars": chars,
                              "has_text": has_text, "has_image": has_image,
                              "image_only": (has_image and not has_text)})
                # emit paragraphs (page-level locator fidelity for pdf)
                for para in re.split(r"\n\s*\n", txt):
                    para = re.sub(r"\s+", " ", para).strip()
                    if para:
                        p_index += 1
                        out["paragraphs"].append(
                            _rec("pdf", index=p_index, page=pi, text=para[:max_text],
                                 truncated=len(para) > max_text, heading_path="",
                                 locator="page:%d" % pi))
                for ii in range(n_img):
                    out["figures"].append(_rec("pdf", id="p%d-img%d" % (pi, ii + 1),
                                               kind="image", page=pi, alt="",
                                               locator="figure:p%d-img%d" % (pi, ii + 1)))
        except Exception as e:
            out["notes"].append("pypdf failed (%s); trying markitdown" % e.__class__.__name__)
            primary = None

    # fallback: markitdown (text only, no page metrics) — only if pypdf produced nothing
    if not pages:
        md = _markitdown(path)
        if md is not None:
            parsed = read_md_text(md, "pdf", max_text)
            parsed["adapter"] = {"pdf_read": "markitdown", "fallback": True}
            parsed["notes"] = out["notes"] + ["pypdf unavailable — markitdown text, no page-level metrics"]
            # without page metrics we cannot certify coverage → degrade, do not crash
            parsed["pdf_quality"] = {
                "pages": None, "text_coverage": None, "image_only_pages": None,
                "table_extraction_confidence": 0.0, "ocr_available": ocr,
                "locator_fidelity": "document", "verdict": "unverified-coverage",
                "note": "page-level coverage could not be computed via the fallback adapter",
            }
            parsed["status"] = "ok-with-warnings"
            return parsed

    total = len(pages)
    image_only_pages = sum(1 for p in pages if p["image_only"])
    pages_with_text = sum(1 for p in pages if p["has_text"])
    text_coverage = (pages_with_text / total) if total else 0.0

    # table extraction + confidence — pdfplumber when present, else 0.0 (unknown).
    # Confidence is the share of detected tables whose grid is rectangular and
    # non-trivial; a vector table on a text page extracts cleanly (high), a table
    # baked into a scanned image is invisible to pdfplumber (stays 0.0).
    table_conf, table_engine = _pdf_tables(path, out, pages, max_text)

    quality = {
        "pages": total,
        "pages_with_text": pages_with_text,
        "image_only_pages": image_only_pages,
        "text_coverage": round(text_coverage, 4),
        "coverage_threshold": coverage_threshold,
        "tables_found": len(out["tables"]),
        "table_extraction_confidence": round(table_conf, 4),
        "table_engine": table_engine,
        "ocr_available": ocr,
        "locator_fidelity": "page",   # pdf anchors are page-level, not paragraph
        "per_page": pages,
    }
    # DD-07 block rule (finding #5): below-threshold coverage AND no OCR ⇒ block
    # zero readable pages (every adapter failed) cannot be certified, even with OCR installed
    blocked = (text_coverage < coverage_threshold) and (not ocr or total == 0)
    if blocked:
        quality["verdict"] = "blocked-low-coverage-no-ocr"
        out["status"] = "blocked"
        out["readiness"] = "not-ready"
        out.setdefault("findings", []).append(_rec(
            "pdf", id="DOC-PBW-01", type="extraction-loss", severity="must-fix",
            review_status="needs-review", issue_lifecycle="open",
            evidence=("text_coverage=%.2f < %.2f over %d page(s), %d image-only, "
                      "%s" % (text_coverage, coverage_threshold, total, image_only_pages,
                      "OCR unavailable" if not ocr else "no readable pages")),
            locator="page:1",
            human_review=("Document appears scanned/image-only and no OCR engine is "
                          "available. A human must supply an OCR'd copy or confirm the "
                          "content before this document can travel."),
        ))
        out.setdefault("human_review", []).append(
            "Scanned/image-only PDF with no OCR — extraction below threshold; "
            "cannot certify content. Provide an OCR'd copy or review manually.")
    elif ocr and image_only_pages:
        # OCR present ⇒ record engine / confidence / page provenance
        quality["verdict"] = "ocr-recommended"
        quality["ocr"] = {
            "engine": ocr,
            "confidence": None,  # filled when OCR is actually run by a downstream pass
            "pages_to_ocr": [p["page"] for p in pages if p["image_only"]],
            "provenance": "image-only pages flagged for OCR; engine recorded for the manifest",
        }
        out["status"] = "ok-with-warnings"
    else:
        quality["verdict"] = "ok"

    out["pdf_quality"] = quality
    out["adapter"] = {"pdf_read": primary or "pypdf", "fallback": primary is None,
                      "ocr": ocr}
    return out


# ----------------------------------------------------------------------------
# markitdown fallbacks
# ----------------------------------------------------------------------------


def _markitdown(path):
    try:
        from markitdown import MarkItDown

        return MarkItDown().convert(path).text_content
    except Exception:
        import shutil
        import subprocess

        if shutil.which("markitdown"):
            try:
                return subprocess.run(["markitdown", path], capture_output=True,
                                      text=True, timeout=120).stdout
            except Exception:
                return None
        return None


def _markitdown_from_text(text, suffix):
    import tempfile

    try:
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                         encoding="utf-8") as fh:
            fh.write(text)
            tmp = fh.name
        return _markitdown(tmp)
    except Exception:
        return None


# ----------------------------------------------------------------------------
# orchestration + output (per §3 I/O contract)
# ----------------------------------------------------------------------------


def build_map(path, fmt, max_text, coverage_threshold, source_packet=None,
              disable_ocr=False):
    if fmt == "docx":
        m = read_docx(path, max_text)
    elif fmt == "md":
        m = read_md_file(path, max_text)
    elif fmt == "html":
        m = read_html_file(path, max_text)
    elif fmt == "pdf":
        m = read_pdf(path, max_text, coverage_threshold, disable_ocr=disable_ocr)
    else:
        raise ValueError("unsupported format %r" % fmt)

    counts = {k: len(m.get(k, [])) for k in
              ("sections", "paragraphs", "tables", "figures", "footnotes")}
    # per-format limit reporting (finding #6): never fold a per-format limit into
    # one combined "doc works" verdict.
    fmt_report = {
        "format": fmt,
        "status": m.get("status", "ok"),
        "counts": counts,
        "limits": _format_limits(fmt, m),
    }
    doc = {
        "schema": SCHEMA_VERSION,
        "surface": SURFACE,
        "format": fmt,
        "generated": _now_iso(),
        "input": {"path": os.path.abspath(path),
                  "bytes": os.path.getsize(path),
                  "sha16": _sha16(open(path, "rb").read())},
        "status": m.get("status", "ok"),
        "adapter": m.get("adapter"),
        "counts": counts,
        "format_reports": {fmt: fmt_report},   # one format here; structure is per-format
        "sections": m.get("sections", []),
        "paragraphs": m.get("paragraphs", []),
        "tables": m.get("tables", []),
        "figures": m.get("figures", []),
        "footnotes": m.get("footnotes", []),
        "notes": m.get("notes", []),
    }
    if "readiness" in m:
        doc["readiness"] = m["readiness"]
    if "findings" in m:
        doc["findings"] = m["findings"]
    if "human_review" in m:
        doc["human_review"] = m["human_review"]
    if fmt == "pdf":
        doc["pdf_quality"] = m.get("pdf_quality")
    if fmt == "html":
        for k in ("html_modes", "hidden_blocks", "agent_only_blocks",
                  "nav_blocks", "rendered_vs_source"):
            doc[k] = m.get(k)
    if source_packet:
        doc["source_packet"] = source_packet
    return doc


def _format_limits(fmt, m):
    lim = []
    if fmt == "pdf":
        q = m.get("pdf_quality") or {}
        lim.append("locator fidelity is page-level (no paragraph anchors)")
        lim.append("table extraction confidence %.2f (no pdfplumber/camelot)"
                   % (q.get("table_extraction_confidence") or 0.0))
        if not q.get("ocr_available"):
            lim.append("no OCR engine available")
    if fmt == "html":
        lim.append("hidden / collapsed / agent-only DOM scoped separately (not in visible claim set)")
    if m.get("adapter") and m["adapter"].get("fallback"):
        lim.append("a fallback adapter ran — structure fidelity reduced")
    return lim


def write_md(doc, out_dir):
    fmt = doc["format"]
    L = []
    L.append("# Document map — `%s` (format: **%s**, surface: doc)" %
             (os.path.basename(doc["input"]["path"]), fmt))
    L.append("")
    L.append("- **status:** `%s`%s" % (doc["status"],
             ("  ·  **readiness:** `%s`" % doc["readiness"]) if doc.get("readiness") else ""))
    L.append("- **adapter:** `%s`" % json.dumps(doc.get("adapter")))
    c = doc["counts"]
    L.append("- **counts:** %d sections · %d paragraphs · %d tables · %d figures · %d footnotes"
             % (c["sections"], c["paragraphs"], c["tables"], c["figures"], c["footnotes"]))
    L.append("")
    if doc.get("findings"):
        L.append("## Findings")
        for f in doc["findings"]:
            L.append("- **%s** `%s` (%s) — %s _[%s]_" %
                     (f.get("id"), f.get("severity"), f.get("type"),
                      f.get("evidence"), f.get("locator")))
        L.append("")
    if doc.get("human_review"):
        L.append("## Human review required")
        for h in doc["human_review"]:
            L.append("- %s" % h)
        L.append("")
    if fmt == "pdf" and doc.get("pdf_quality"):
        q = doc["pdf_quality"]
        L.append("## PDF extraction quality (DD-07)")
        L.append("- pages: %s · with text: %s · image-only: %s" %
                 (q.get("pages"), q.get("pages_with_text"), q.get("image_only_pages")))
        L.append("- text coverage: %s (threshold %s)" %
                 (q.get("text_coverage"), q.get("coverage_threshold")))
        L.append("- table-extraction confidence: %s · OCR available: %s · locator fidelity: %s" %
                 (q.get("table_extraction_confidence"), q.get("ocr_available"),
                  q.get("locator_fidelity")))
        L.append("- **verdict:** `%s`" % q.get("verdict"))
        L.append("")
    if fmt == "html":
        rv = doc.get("rendered_vs_source") or {}
        L.append("## HTML modes + rendered-vs-source (finding #11)")
        L.append("- modes run: %s" % (doc.get("html_modes", {}).get("ran")))
        L.append("- hidden blocks: %d · agent-only blocks: %d · nav blocks: %d" %
                 (len(doc.get("hidden_blocks") or []), len(doc.get("agent_only_blocks") or []),
                  len(doc.get("nav_blocks") or [])))
        L.append("- tokens in source but NOT rendered: %s (divergent=%s)" %
                 (rv.get("tokens_in_source_not_rendered"), rv.get("divergent")))
        if rv.get("sample"):
            L.append("  - sample: %s" % ", ".join(rv["sample"][:12]))
        L.append("")
    L.append("## Sections (heading path)")
    for s in doc["sections"][:200]:
        L.append("- %s`%s`  _[%s]_" % ("  " * (int(s.get("level", 1)) - 1 if s.get("level") else 0),
                                        s.get("heading_path"), s.get("locator")))
    L.append("")
    L.append("## Tables")
    for t in doc["tables"]:
        L.append("- table T%s — %s×%s, extracted=%s, confidence=%s _[%s]_" %
                 (t.get("index"), t.get("rows"), t.get("cols"), t.get("extracted"),
                  t.get("confidence"), t.get("locator")))
    L.append("")
    if doc.get("notes"):
        L.append("## Notes")
        for n in doc["notes"]:
            L.append("- %s" % n)
    L.append("")
    with open(os.path.join(out_dir, "document-map.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Document Doctor — DD-01 map + DD-07 PDF quality")
    ap.add_argument("file", help="path to the document (.docx/.pdf/.md/.html)")
    ap.add_argument("--out", help=".trust/<run_id> output dir (default: next to the file)")
    ap.add_argument("--format", default="auto", help="auto|docx|pdf|md|html")
    ap.add_argument("--source-packet", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--coverage-threshold", type=float, default=0.10)
    ap.add_argument("--max-text", type=int, default=4000)
    ap.add_argument("--assume-no-ocr", action="store_true",
                    help="treat OCR as unavailable (certify the DD-07 block branch)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.file):
        print(json.dumps({"status": "failed", "error": "no such file: %s" % args.file}))
        return 2
    try:
        fmt = detect_format(args.file, args.format)
    except ValueError as e:
        print(json.dumps({"status": "failed", "error": str(e)}))
        return 2

    run_id = args.run_id or (_dt.datetime.now().strftime("%Y-%m-%dT%H%M") + "_docmap")
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.file)),
                                       ".trust", run_id)
    os.makedirs(out_dir, exist_ok=True)

    doc = build_map(args.file, fmt, args.max_text, args.coverage_threshold,
                    source_packet=args.source_packet, disable_ocr=args.assume_no_ocr)
    doc["run_id"] = run_id
    with open(os.path.join(out_dir, "document-map.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    write_md(doc, out_dir)

    if not args.quiet:
        print(json.dumps({"status": doc["status"], "format": fmt,
                          "readiness": doc.get("readiness"),
                          "counts": doc["counts"], "out": out_dir,
                          "findings": [f.get("id") for f in doc.get("findings", [])]}))
    # exit 3 on blocked (mirror manifest_gate convention), 0 otherwise
    return 3 if doc["status"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
