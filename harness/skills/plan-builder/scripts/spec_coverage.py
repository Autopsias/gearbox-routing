"""covers / spec_ref: tie plan items to the requirements of a SPEC.md.

Contract: skills/spec-builder/references/contract.md section 7. Both keys are
optional; a spec with neither builds exactly as before. Warnings never refuse.
"""
import re
import subprocess
import sys
from pathlib import Path

ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-?\d+$")
_REQ = re.compile(r"^- ([A-Z][A-Z0-9]*-?\d+): (WITHDRAWN\b)?")
_PLAN = re.compile(r"^- (P\d+): .*?— covers: (.*?) — status:")


def validate_shape(spec):
    """Raise ValueError on a malformed item.covers or top-level spec_ref."""
    for it in spec.get("items") or []:
        c = it.get("covers") if isinstance(it, dict) else None
        if c is None:
            continue
        if not isinstance(c, list) or not all(isinstance(x, str) and ID_RE.match(x) for x in c):
            raise ValueError(
                f"Item {it.get('id')!r}: 'covers' must be a list of requirement ids "
                f"matching [A-Z][A-Z0-9]*-?\\d+, got {c!r}")
    ref = spec.get("spec_ref")
    if ref is None:
        return
    if (not isinstance(ref, dict) or set(ref) != {"path", "plan"}
            or not all(isinstance(ref[k], str) and ref[k].strip() for k in ref)):
        raise ValueError("'spec_ref' must be an object {\"path\": <SPEC.md path>, \"plan\": \"P<n>\"}, "
                         f"got {ref!r}")


def check(spec):
    """validate_shape, then print each coverage warning to stderr (never refuses)."""
    validate_shape(spec)
    for w in warnings(spec):
        print(f"WARNING: {w}", file=sys.stderr)


def item_covers(it):
    return {"covers": it["covers"]} if "covers" in it else {}


def top(spec):
    return {"spec_ref": spec["spec_ref"]} if spec.get("spec_ref") else {}


def _resolve(path):
    p = Path(path)
    if p.is_absolute():
        return p
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        root = "."
    return Path(root) / p


def _parse(text):
    """Requirement lines count only under ## Requirements and plan lines only under ## Plans (a Results or Open
    questions line such as '- R2: NEVER CHECKED' is not a definition), and never inside a code fence or an HTML comment."""
    live, withdrawn, plans = set(), set(), {}
    section, fence = "", False
    for ln in re.sub(r"<!--.*?-->", "", text, flags=re.S).splitlines():  # a comment is not a line
        if ln.lstrip().startswith(("```", "~~~")):  # an example in a code fence is not a line either
            fence = not fence
            continue
        if fence:
            continue
        if ln.startswith("## "):
            section = ln[3:].strip().lower()
            continue
        m = _REQ.match(ln) if section == "requirements" else None
        if m:
            (withdrawn if m.group(2) else live).add(m.group(1))
        m = _PLAN.match(ln) if section == "plans" else None
        if m:
            plans[m.group(1)] = [x.strip() for x in m.group(2).split(",") if x.strip()]
    return live, withdrawn, plans


def warnings(spec):
    """Coverage warnings (list of str). Empty without a spec_ref."""
    ref = spec.get("spec_ref")
    if not ref:
        return []
    p = _resolve(ref["path"])
    if not p.is_file():
        return [f"spec_ref.path {ref['path']!r} does not exist; coverage not checked"]
    try:
        live, withdrawn, plans = _parse(p.read_text())
    except (OSError, UnicodeDecodeError) as e:  # gone or unreadable after the check above: a warning, never an abort
        return [f"spec_ref.path {ref['path']!r} could not be read ({type(e).__name__}); coverage not checked"]
    out = []
    if ref["plan"] not in plans:
        out.append(f"spec_ref.plan {ref['plan']!r} is not listed in {ref['path']}; "
                   f"it lists {sorted(plans)}")
    covered = set()
    for it in spec.get("items") or []:
        for i in it.get("covers") or []:
            covered.add(i)
            if i in withdrawn:
                out.append(f"item {it['id']} covers {i}, which {ref['path']} marks WITHDRAWN")
            elif i not in live:
                out.append(f"item {it['id']} covers {i}, which {ref['path']} does not define")
    for i in plans.get(ref["plan"], []):
        if i not in covered:
            out.append(f"{ref['plan']} covers {i} in {ref['path']}, but no item covers {i}")
    return out
