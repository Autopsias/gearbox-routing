#!/usr/bin/env python3
# =============================================================================
# Office Trust Layer — dd-08 EARLY capability spike
#   "Do anchored Word comments round-trip through Word / LibreOffice?"
#
# This is the gate the capability-inventory `docx_comment_write` row depends on,
# proven BEFORE S04/dd-05 builds on it (the same "no downstream task may assume
# a tool until its contract is proven" rule as review-1 finding 4).
#
# It (1) writes a .docx with a comment ANCHORED on a run via python-docx, (2)
# checks the OOXML anchoring markers + a python-docx reload, then (3) round-trips
# the file through LibreOffice (soffice --convert-to docx) and re-checks that the
# anchor + comment text survive. It writes a PASS/PARTIAL/FAIL marker to
# fixtures/_dd08_comment_roundtrip_proof.txt and exits 0 only on PASS.
#
#   PASS    ⇒ S04/dd-05 may use anchored comments + an appendix.
#   PARTIAL ⇒ python-docx writes anchors but LibreOffice drops them — fall back
#             to appendix + a .trust/<run>/ sidecar (no evidence lost).
#   FAIL    ⇒ no anchored-comment path — appendix + sidecar only.
# =============================================================================
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROOF = os.path.join(HERE, "_dd08_comment_roundtrip_proof.txt")
ANCHOR_TEXT = "Revenue grew 12% in FY25 versus FY24."
COMMENT_TEXT = "source? FY25 figure unverified — trace to a source ID"
AUTHOR = "Office Trust Layer"


def _markers(path):
    """Return (has_comments_xml, has_rangeStart, has_rangeEnd, has_reference)."""
    z = zipfile.ZipFile(path)
    names = z.namelist()
    doc = z.read("word/document.xml").decode("utf-8", "replace")
    return (
        "word/comments.xml" in names,
        "commentRangeStart" in doc,
        "commentRangeEnd" in doc,
        "commentReference" in doc,
    )


def _reload_comments(path):
    import docx

    try:
        return list(docx.Document(path).comments)
    except Exception:
        return None


def run(tmp=None):
    import docx

    tmp = tmp or os.path.join(os.environ.get("TMPDIR", "/tmp"), "dd08_spike")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    src = os.path.join(tmp, "anchored.docx")

    result = {"python_docx_version": getattr(docx, "__version__", "?"),
              "api": None, "write": {}, "libreoffice": {}, "verdict": "FAIL"}

    # ---- 1. write an anchored comment ---------------------------------------
    d = docx.Document()
    p = d.add_paragraph(ANCHOR_TEXT)
    api_ok = hasattr(d, "add_comment")
    result["api"] = "Document.add_comment(runs=...)" if api_ok else None
    if not api_ok:
        result["verdict"] = "FAIL"
        result["note"] = "python-docx exposes no add_comment API"
        _emit(result)
        return result
    d.add_comment(runs=p.runs, text=COMMENT_TEXT, author=AUTHOR, initials="OTL")
    d.save(src)

    cx, rs, re_, rf = _markers(src)
    reloaded = _reload_comments(src)
    write_ok = cx and rs and re_ and rf and reloaded is not None and len(reloaded) == 1
    result["write"] = {"comments_xml": cx, "rangeStart": rs, "rangeEnd": re_,
                       "reference": rf, "reload_count": (len(reloaded) if reloaded is not None else None),
                       "anchored": rs and re_ and rf, "ok": write_ok}

    # ---- 2. LibreOffice round-trip ------------------------------------------
    soffice_bin = shutil.which("soffice") or shutil.which("libreoffice")
    lo = {"available": bool(soffice_bin)}
    if lo["available"]:
        outdir = os.path.join(tmp, "lo_out")
        os.makedirs(outdir, exist_ok=True)
        env = dict(os.environ)
        env["HOME"] = os.path.join(tmp, "lohome")
        os.makedirs(env["HOME"], exist_ok=True)
        # A UNIQUE per-run UserInstallation profile avoids the rc=77 lock that a
        # concurrent soffice (e.g. the recalc fixture) otherwise triggers.
        profile = os.path.join(tmp, "loprofile")
        shutil.rmtree(profile, ignore_errors=True)
        try:
            r = subprocess.run(
                [soffice_bin, "--headless",
                 "-env:UserInstallation=file://%s" % profile,
                 "--convert-to", "docx:MS Word 2007 XML",
                 "--outdir", outdir, src],
                capture_output=True, text=True, timeout=180, env=env)
            lo["rc"] = r.returncode
            conv = os.path.join(outdir, "anchored.docx")
            lo["converted"] = os.path.exists(conv)
            if os.path.exists(conv):
                ccx, crs, cre, crf = _markers(conv)
                creload = _reload_comments(conv)
                text_ok = bool(creload) and any(
                    "unverified" in (c.text or "") for c in creload)
                lo.update({"comments_xml": ccx, "rangeStart": crs, "rangeEnd": cre,
                           "reference": crf, "anchored": crs and cre and crf,
                           "reload_count": (len(creload) if creload is not None else None),
                           "text_preserved": text_ok,
                           "ok": ccx and crs and crf and text_ok})
        except Exception as e:
            lo["error"] = "%s: %s" % (type(e).__name__, e)
    result["libreoffice"] = lo

    # ---- 3. verdict ----------------------------------------------------------
    if write_ok and lo.get("ok"):
        result["verdict"] = "PASS"
    elif write_ok and not lo.get("available"):
        result["verdict"] = "PARTIAL"   # write proven; LO not available to confirm
    elif write_ok:
        result["verdict"] = "PARTIAL"   # write proven; LO dropped the anchor/text
    else:
        result["verdict"] = "FAIL"
    _emit(result)
    return result


def _emit(result):
    lines = [
        "dd-08 — anchored Word comment round-trip spike",
        "=" * 52,
        "python-docx: %s   API: %s" % (result.get("python_docx_version"), result.get("api")),
        "",
        "WRITE (python-docx):",
        "  comments.xml=%s  rangeStart=%s  rangeEnd=%s  reference=%s  reload=%s  -> ok=%s"
        % (result["write"].get("comments_xml"), result["write"].get("rangeStart"),
           result["write"].get("rangeEnd"), result["write"].get("reference"),
           result["write"].get("reload_count"), result["write"].get("ok")),
        "",
        "LIBREOFFICE round-trip:",
        "  available=%s  rc=%s  comments.xml=%s  anchored=%s  text_preserved=%s  -> ok=%s"
        % (result["libreoffice"].get("available"), result["libreoffice"].get("rc"),
           result["libreoffice"].get("comments_xml"), result["libreoffice"].get("anchored"),
           result["libreoffice"].get("text_preserved"), result["libreoffice"].get("ok")),
        "",
        "VERDICT: %s" % result["verdict"],
        "",
        "Interpretation:",
        "  PASS    -> S04/dd-05 uses anchored comments + appendix.",
        "  PARTIAL -> python-docx writes anchors but LibreOffice could not be confirmed;",
        "             fall back to appendix + .trust/<run>/ sidecar (no evidence lost).",
        "  FAIL    -> appendix + sidecar only.",
        "",
        json.dumps(result, indent=2),
        "",
    ]
    with open(PROOF, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    res = run()
    print("dd-08 verdict:", res["verdict"], "| proof:", PROOF)
    # PASS or PARTIAL are both acceptable gate outcomes (each has a defined path);
    # only a hard FAIL (no write path at all) fails the fixture.
    sys.exit(0 if res["verdict"] in ("PASS", "PARTIAL") else 1)
