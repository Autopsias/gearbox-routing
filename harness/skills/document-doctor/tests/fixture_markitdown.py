#!/usr/bin/env python3
"""
Fixture: markitdown — PROVES that an Office source can be converted to markdown
for inspection/ingestion (Stage 0 source prep reads heterogeneous uploads).

Contract proven:  markitdown <file.xlsx> -> markdown on stdout containing the
                  source's cell content.
Failure mode:     markitdown missing -> cannot auto-ingest that file type.
Fallback:         fall back to openpyxl/python-pptx direct read per file type.

Exit: 0 = PASS, 1 = FAIL.
"""
import os
import shutil
import subprocess
import sys
import tempfile

import openpyxl


def main():
    if not shutil.which("markitdown"):
        print("FAIL markitdown: binary not on PATH")
        return 1

    scratch = tempfile.mkdtemp(prefix="otl_mid_", dir=os.environ.get("TMPDIR", "/tmp"))
    xlsx_path = os.path.join(scratch, "src.xlsx")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Region"
    ws["B1"] = "Revenue"
    ws["A2"] = "Iberia"
    ws["B2"] = 42
    wb.save(xlsx_path)
    wb.close()

    try:
        proc = subprocess.run(
            ["markitdown", xlsx_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("FAIL markitdown: timed out")
        return 1

    md = proc.stdout
    if proc.returncode == 0 and "Iberia" in md and "Revenue" in md:
        print(f"PASS markitdown: converted xlsx -> markdown ({len(md)} chars), cell content present")
        return 0
    print(f"FAIL markitdown: rc={proc.returncode}, expected 'Iberia' and 'Revenue' in output")
    print("stdout:", md[:300])
    print("stderr:", proc.stderr[:300])
    return 1


if __name__ == "__main__":
    sys.exit(main())
