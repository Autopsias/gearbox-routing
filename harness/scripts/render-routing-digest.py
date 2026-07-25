#!/usr/bin/env python3
"""render-routing-digest.py — deterministic renderer for the CLAUDE.md routing digest.

Reads the SOURCE TEMPLATE (~/.claude/model-routing.digest.md), extracts the requested
VARIANT block (v0 = minimal, installed by s02; full = advisory, published by s04), stamps
it with the live SSOT version (~/.claude/model-routing.yaml `version:`), and installs it
into the TARGET file (~/.claude/CLAUDE.md) between generated
'<!-- BEGIN ROUTING (model-routing.yaml vN) -->' / '<!-- END ROUTING -->' markers.

CLAUDE.md is an ALWAYS-LOADED surface for every project — corruption there is expensive.
Safety invariants (never relaxed):
  1. Exactly one BEGIN/END marker PAIR must exist in the target before an in-place update
     is allowed. Missing, duplicate, or reordered markers => refuse, exit non-zero, NO WRITE.
     (First-time bootstrap onto a target with ZERO markers requires the explicit --install
     flag; this is the one deliberate exception, and it still refuses on partial/duplicate
     corruption.)
  2. The rendered block must fit the budget (<=40 lines, <=1800 bytes). Overflow refuses to
     write ANYTHING — a silently truncated write would still diff-clean against a stale
     re-render and ship lossy-green.
  3. Writes are temp-file + os.replace (atomic rename) — never an in-place partial write.

Usage:
  render-routing-digest.py --variant v0 [--claude-dir DIR] [--install]
  render-routing-digest.py --variant v0 --check          # dry-run: diff installed vs re-rendered, no write

Exit codes: 0 = clean/written. 1 = content drift (--check only). 2 = structural/tooling
failure (missing files, marker corruption, overflow, parse error) — ALWAYS fails closed.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_route import RouteResolverError, resolve  # noqa: E402

MAX_LINES = 40
MAX_BYTES = 1800  # raised 1638->1800 (2026-07-03): the 1.6 KiB budget was fully saturated
                  # and a post-delivery engagement probe found the tight cap forced out a
                  # live-tripped escalation clause. 1800 B (~+40 tokens/session, negligible for
                  # an always-loaded digest) restores headroom for the correctness fixes.

# Target-file (CLAUDE.md) marker scan — ANCHORED to line-start/line-end (s02 re-harden
# fix #10). A bare substring scan previously counted a prose/backtick MENTION of the
# marker text (e.g. documenting the marker syntax in a sentence) as a real marker,
# causing a false "duplicate marker corruption" refusal on an otherwise-intact file. A
# real marker must be the ENTIRE content of its line (surrounding whitespace only);
# anything with other text before/after on the same line (prose, backticks, a bullet
# prefix) cannot match. Group 1 captures the literal marker text for slicing, so
# offsets behave exactly as before when the marker legitimately starts at column 0.
BEGIN_RE = re.compile(r"^[ \t]*(<!-- BEGIN ROUTING \(model-routing\.yaml v\d+\) -->)[ \t]*$", re.M)
END_MARKER = "<!-- END ROUTING -->"
END_MARKER_RE = re.compile(r"^[ \t]*(" + re.escape(END_MARKER) + r")[ \t]*$", re.M)

SRC_BEGIN_RE = re.compile(
    r"<!-- BEGIN ROUTING \(rendered from [^)]*\) -->"
)
SRC_END_RE = re.compile(r"<!-- END ROUTING -->")


class RenderError(Exception):
    """Structural failure — always fail closed (exit 2), never write."""


def fatal(msg: str) -> "RenderError":
    return RenderError(msg)


def read(path: str) -> str:
    if not os.path.isfile(path):
        raise fatal(f"missing required file: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_ssot_version(yaml_text: str) -> int:
    m = re.search(r"^version:\s*(\d+)\s*$", yaml_text, re.M)
    if not m:
        raise fatal("could not find top-level `version:` in SSOT yaml")
    return int(m.group(1))


def extract_variant_block(source_text: str, variant: str) -> str:
    """Pull the inner content (between, NOT including, the source template's own
    BEGIN/END ROUTING markers) for the named variant section of model-routing.digest.md."""
    start_tag = f"<!-- ===== VARIANT: {variant} "
    end_tag_prefix = f"<!-- ===== END VARIANT {variant} "
    start_idx = source_text.find(start_tag)
    if start_idx == -1:
        raise fatal(f"variant '{variant}' not found in digest source template (missing {start_tag!r})")
    end_idx = source_text.find(end_tag_prefix, start_idx)
    if end_idx == -1:
        raise fatal(f"variant '{variant}' section never closed (missing {end_tag_prefix!r})")
    section = source_text[start_idx:end_idx]

    begins = SRC_BEGIN_RE.findall(section)
    ends = SRC_END_RE.findall(section)
    if len(begins) != 1 or len(ends) != 1:
        raise fatal(
            f"variant '{variant}' section must contain exactly one BEGIN/END ROUTING pair "
            f"in the source template (found {len(begins)} BEGIN, {len(ends)} END)"
        )
    b = SRC_BEGIN_RE.search(section)
    e = SRC_END_RE.search(section)
    if b.end() > e.start():
        raise fatal(f"variant '{variant}' source markers are reordered (END before BEGIN)")
    inner = section[b.end():e.start()]
    return inner.strip("\n")


PROVIDER_PLACEHOLDER = "{{ACTIVE_PROVIDER}}"


def render_block(yaml_path: str, source_path: str, variant: str) -> str:
    yaml_text = read(yaml_path)
    version = parse_ssot_version(yaml_text)
    active_provider = parse_active_provider(yaml_text)
    inner = extract_variant_block(read(source_path), variant)
    inner = inner.replace(PROVIDER_PLACEHOLDER, active_provider)
    begin_line = f"<!-- BEGIN ROUTING (model-routing.yaml v{version}) -->"
    block = f"{begin_line}\n{inner}\n{END_MARKER}\n"

    lines = block.count("\n")
    nbytes = len(block.encode("utf-8"))
    if lines > MAX_LINES or nbytes > MAX_BYTES:
        raise fatal(
            f"rendered digest EXCEEDS budget ({lines} lines / {nbytes} bytes; "
            f"cap is {MAX_LINES} lines / {MAX_BYTES} bytes) — refusing to write a lossy/"
            f"truncated block. Trim the '{variant}' variant in the source template."
        )
    return block


def validate_single_pair(text: str, path: str):
    """Return (begin_match, end_pos) for the sole BEGIN/END pair, or raise RenderError.
    Both BEGIN_RE and END_MARKER_RE are line-anchored (see comment at their definition)
    so a prose/backtick MENTION of the marker text is never counted as a real marker."""
    begins = list(BEGIN_RE.finditer(text))
    ends = [m.start(1) for m in END_MARKER_RE.finditer(text)]
    if len(begins) == 0 and len(ends) == 0:
        raise fatal(f"no ROUTING markers found in {path} (missing marker pair)")
    if len(begins) != 1 or len(ends) != 1:
        raise fatal(
            f"{path} must contain EXACTLY one BEGIN/END ROUTING marker pair "
            f"(found {len(begins)} BEGIN, {len(ends)} END) — refusing to write "
            f"(missing/duplicate marker corruption)"
        )
    b = begins[0]
    e = ends[0]
    if b.end(1) > e:
        raise fatal(f"{path} ROUTING markers are reordered (END before BEGIN) — refusing to write")
    return b, e


def atomic_write(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".render-routing-digest.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # mkstemp always creates the temp file at mode 0600 regardless of umask. Since
        # os.replace() keeps the TEMP FILE's inode (and thus ITS mode bits), a naive
        # replace narrows an always-loaded CLAUDE.md from 0644 -> 0600 on every render
        # (s02 re-harden fix #9). Preserve the pre-existing target's mode; for a
        # brand-new target, honor the process umask the way a normal file create would
        # (undo mkstemp's forced 0600 narrowing) instead of silently keeping 0600.
        if os.path.exists(path):
            try:
                os.chmod(tmp_path, os.stat(path).st_mode)
            except OSError:
                pass
        else:
            _prev_umask = os.umask(0)
            os.umask(_prev_umask)
            try:
                os.chmod(tmp_path, 0o666 & ~_prev_umask)
            except OSError:
                pass
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def do_install_or_update(target_path: str, new_block: str, allow_install: bool) -> None:
    if not os.path.isfile(target_path):
        if not allow_install:
            raise fatal(f"target {target_path} does not exist (pass --install to create it)")
        atomic_write(target_path, new_block)
        return

    text = read(target_path)
    begins = list(BEGIN_RE.finditer(text))
    ends = [m.start(1) for m in END_MARKER_RE.finditer(text)]

    if len(begins) == 0 and len(ends) == 0:
        if not allow_install:
            raise fatal(f"no ROUTING markers found in {target_path} (missing marker pair; pass --install to bootstrap)")
        sep = "" if text.endswith("\n") or text == "" else "\n"
        new_text = text + sep + ("\n" if text.strip() else "") + new_block
        atomic_write(target_path, new_text)
        return

    b, e = validate_single_pair(text, target_path)
    end_full = e + len(END_MARKER)
    new_text = text[: b.start(1)] + new_block.rstrip("\n") + text[end_full:]
    atomic_write(target_path, new_text)


# ---- s02 re-harden fix #1: tie the digest to SSOT `task_classes:` SEMANTICS, not just
# the version stamp. The rendered digest table is authored prose (model-routing.digest.md
# is a static template) — a `task_classes` model/effort change WITHOUT touching the
# template or bumping `version` previously re-rendered byte-identical and shipped
# stale routing under a green guard. This cross-checks each SSOT task_classes row that
# IS surfaced in the rendered table (some, e.g. `linchpin`, are deliberately not shown)
# against the tier text actually present in that row.
#
# s03 UPDATE: `task_classes:` now stores {tier, effort-INTENT} (light/standard/
# thorough), not the concrete (model, effort) pair the rendered table shows
# ("sonnet · medium") — comparing the raw tuple directly would false-fail on
# every commit once the block was renamed/retired from its legacy shape. Each
# row is resolved through scripts/resolve_route.py's `resolve()` (the same
# single normative resolver the runtime/tests use) against `active_provider`
# to get the concrete `model_id`/`native_effort` before comparing to the table.
TASK_CLASS_ROW_TMPL = r"^\|\s*{name}\s*\|.*\|\s*(\w+)\s*[·/]\s*(\w+)\b"


def parse_active_provider(yaml_text: str) -> str:
    m = re.search(r"^active_provider:\s*(\S+)\s*$", yaml_text, re.M)
    if not m:
        raise fatal("could not find top-level `active_provider:` in SSOT yaml")
    return m.group(1)


def find_next_top_level_key(text: str, start: int) -> int:
    """Index of the next top-level (column-0, non-comment) `key:` line after `start`,
    or len(text) if none. Robust to comment/blank-line growth inside the preceding
    block — a fixed-offset window or a literal-next-key anchor silently mis-slices
    when the block grows or a row is renamed."""
    for m in re.finditer(r"^(\S)", text[start:], re.M):
        idx = start + m.start()
        nl = text.find("\n", idx)
        line = text[idx: nl if nl != -1 else len(text)]
        if line.startswith("#"):
            continue
        return idx
    return len(text)


def parse_task_classes(yaml_text: str) -> dict:
    """{tier, effort-INTENT} rows straight off the SSOT (raw, un-resolved) — used
    only to know which task-class NAMES exist; resolve_task_classes() below does
    the tier/intent -> concrete model_id/native_effort translation."""
    tc_start = yaml_text.find("\ntask_classes:\n")
    if tc_start == -1:
        raise fatal("could not find `task_classes:` block in SSOT yaml")
    tc_end = find_next_top_level_key(yaml_text, tc_start + len("\ntask_classes:\n"))
    block = yaml_text[tc_start:tc_end]
    rx = re.compile(r"^\s*([\w-]+):\s*\{\s*tier:\s*[\"']?([\w-]+)[\"']?\s*,\s*effort:\s*(\w+)\s*\}", re.M)
    parsed = {mo.group(1): (mo.group(2), mo.group(3)) for mo in rx.finditer(block)}
    if not parsed:
        raise fatal("parsed zero rows from SSOT `task_classes:` block — parser or SSOT is broken")
    return parsed


def resolve_task_classes(yaml_path: str) -> dict:
    """name -> (model_id, native_effort), each resolved via scripts/resolve_route.py's
    resolve() against `active_provider` — the same normative path the runtime/tests
    use, so the digest cross-check never drifts from a second hand-rolled resolution."""
    yaml_text = read(yaml_path)
    names = parse_task_classes(yaml_text)
    active_provider = parse_active_provider(yaml_text)
    out = {}
    for name in names:
        try:
            got = resolve(name, active_provider, ssot_path=yaml_path)
        except RouteResolverError as e:
            raise fatal(f"resolve_route.resolve({name!r}, {active_provider!r}) failed: {e}")
        if got == "exhausted" or not isinstance(got, dict):
            raise fatal(f"resolve_route.resolve({name!r}, {active_provider!r}) returned {got!r}, expected a baseline dict")
        out[name] = (got["model_id"], got["native_effort"])
    return out


def check_task_class_semantics(yaml_path: str, block: str) -> list:
    """Return a list of human-readable mismatch descriptions (empty = clean). Only
    checks task_classes rows that ARE surfaced in the rendered digest table — a class
    the digest deliberately omits (e.g. `linchpin`) is not a failure."""
    task_classes = resolve_task_classes(yaml_path)
    issues = []
    for name, (model, effort) in sorted(task_classes.items()):
        row_rx = re.compile(TASK_CLASS_ROW_TMPL.format(name=re.escape(name)), re.M)
        mo = row_rx.search(block)
        if not mo:
            continue
        found_model, found_effort = mo.group(1), mo.group(2)
        # NO-DIAL TIERS (effort is None — e.g. haiku): resolve_route.py correctly
        # returns no native effort (haiku rejects the reasoning dial), but the
        # digest's prose table still shows a friendly tier-label token (e.g.
        # "haiku · low") for readability. Only the model needs to match here;
        # requiring found_effort == None would false-flag every no-dial row.
        mismatch = (found_model != model) or (effort is not None and found_effort != effort)
        if mismatch:
            issues.append(
                f"digest row '{name}' shows tier {found_model}·{found_effort} but SSOT "
                f"task_classes.{name} resolves (via resolve_route.py) to "
                f"{model}·{effort if effort is not None else '(no dial)'} — "
                f"bump `version:` and refresh model-routing.digest.md's table text"
            )
    return issues


def do_check(target_path: str, new_block: str, yaml_path: str) -> int:
    text = read(target_path)
    b, e = validate_single_pair(text, target_path)
    end_full = e + len(END_MARKER)
    installed = text[b.start(1): end_full] + "\n"
    semantic_issues = check_task_class_semantics(yaml_path, new_block)
    byte_clean = installed.rstrip("\n") == new_block.rstrip("\n")
    if byte_clean and not semantic_issues:
        print(f"CLEAN: {target_path} matches the current SSOT re-render.")
        return 0
    if semantic_issues:
        print("DRIFT: digest task-class table diverges from SSOT task_classes semantics:", file=sys.stderr)
        for issue in semantic_issues:
            print(f"  - {issue}", file=sys.stderr)
    if byte_clean:
        return 1
    print(f"DRIFT: {target_path} does NOT match the current SSOT re-render.", file=sys.stderr)
    print("--- installed ---", file=sys.stderr)
    print(installed, file=sys.stderr)
    print("--- would-render ---", file=sys.stderr)
    print(new_block, file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=["v0", "full"], default="v0")
    ap.add_argument("--claude-dir", default=os.environ.get("CLAUDE_DIR", os.path.expanduser("~/.claude")))
    ap.add_argument("--yaml", default=None, help="override SSOT path (default: <claude-dir>/model-routing.yaml)")
    ap.add_argument("--source", default=None, help="override digest source template (default: <claude-dir>/model-routing.digest.md)")
    ap.add_argument("--target", default=None, help="override install target (default: <claude-dir>/CLAUDE.md)")
    ap.add_argument("--install", action="store_true", help="allow bootstrap onto a target with zero existing markers")
    ap.add_argument("--check", action="store_true", help="dry-run: report drift, write nothing")
    args = ap.parse_args(argv)

    claude_dir = args.claude_dir
    yaml_path = args.yaml or os.path.join(claude_dir, "model-routing.yaml")
    source_path = args.source or os.path.join(claude_dir, "model-routing.digest.md")
    target_path = args.target or os.path.join(claude_dir, "CLAUDE.md")

    try:
        block = render_block(yaml_path, source_path, args.variant)
        if args.check:
            return do_check(target_path, block, yaml_path)
        do_install_or_update(target_path, block, args.install)
        print(f"OK: wrote variant '{args.variant}' digest into {target_path}")
        return 0
    except RenderError as e:
        print(f"FATAL (render-routing-digest.py): {e}", file=sys.stderr)
        return 2
    except Exception as e:  # tooling crash — fail closed, never a silent bypass
        print(f"FATAL (render-routing-digest.py, unexpected {type(e).__name__}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
