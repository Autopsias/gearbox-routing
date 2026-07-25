#!/usr/bin/env python3
"""render-routing-digest.py — deterministic renderer for the CLAUDE.md routing digest.

Ported from the source deployment's ~/.claude/scripts/render-routing-digest.py (S05,
this port), made PROVIDER-AWARE: the rendered task-class table now shows each class's
tier resolved to the ACTIVE PROVIDER's real model id, not just the abstract tier
label — the whole point of GUARD-02 (an "sonnet" reader should see "claude-sonnet-5",
whichever provider is active).

Reads the SOURCE TEMPLATE (model-routing.digest.md), extracts the requested VARIANT
block, resolves each task_classes row's {tier, effort} to {model_id, native_effort}
for active_provider, stamps the block with the live SSOT version (`version:`, now
SEMVER — see docs/VERSIONING.md, NOT the source deployment's bare integer), and
installs it into the TARGET file (CLAUDE.md) between generated
'<!-- BEGIN ROUTING -->' / '<!-- END ROUTING -->' markers.

MARKER CHANGE FROM SOURCE (semver migration): the source deployment stamped the
version INTO the marker itself (`<!-- BEGIN ROUTING (model-routing.yaml v4) -->`),
which meant every policy bump also required a literal marker-text rewrite (the
regex embeds the version). This port DECOUPLES the marker from the config version
entirely — the marker is now the bare, version-agnostic `<!-- BEGIN ROUTING -->` /
`<!-- END ROUTING -->` pair. The version still renders as visible TEXT inside the
block body (so a reader can see which policy version produced it), but the marker
regex itself never needs to change again when the policy bumps.

CLAUDE.md is an ALWAYS-LOADED surface for every project — corruption there is
expensive. Safety invariants (never relaxed):
  1. Exactly one BEGIN/END marker PAIR must exist in the target before an in-place
     update is allowed. Missing, duplicate, or reordered markers => refuse, exit
     non-zero, NO WRITE. First-time bootstrap onto a target with ZERO markers
     requires the explicit --install flag; it still refuses on partial/duplicate
     corruption.
  2. The rendered block must fit the budget (<=40 lines, <=1800 bytes). Overflow
     refuses to write ANYTHING.
  3. Writes are temp-file + os.replace (atomic rename) — never an in-place partial
     write.

Usage:
  render-routing-digest.py [--repo-dir DIR] [--claude-home DIR] [--install]
  render-routing-digest.py --check          # dry-run: diff installed vs re-render

Exit codes: 0 = clean/written. 1 = content drift (--check only). 2 = structural/
tooling failure (missing files, marker corruption, overflow, parse error, resolver
mismatch) — ALWAYS fails closed.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import tempfile

MAX_LINES = 40
MAX_BYTES = 1800

BEGIN_MARKER = "<!-- BEGIN ROUTING -->"
END_MARKER = "<!-- END ROUTING -->"
BEGIN_RE = re.compile(r"^[ \t]*(<!-- BEGIN ROUTING -->)[ \t]*$", re.M)
END_MARKER_RE = re.compile(r"^[ \t]*(" + re.escape(END_MARKER) + r")[ \t]*$", re.M)

# Grep-gates (run at import time, below): NO old integer `vN` stamp and NO
# `model-routing.yaml v4`-style marker may survive anywhere this script touches.
_OLD_MARKER_RE = re.compile(r"<!--\s*BEGIN ROUTING\s*\(model-routing\.yaml v\d+\)\s*-->")
_OLD_STAMP_RE = re.compile(r"<!--\s*routing-ssot:\s*v\d+\s*-->")


class RenderError(Exception):
    """Structural failure — always fail closed (exit 2), never write."""


def fatal(msg: str) -> "RenderError":
    return RenderError(msg)


def read(path: str) -> str:
    if not os.path.isfile(path):
        raise fatal(f"missing required file: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_ssot_version(yaml_text: str) -> str:
    """SEMVER (docs/VERSIONING.md), not the source deployment's bare integer. A
    bare-int `version:` is the OLD pre-migration shape and is a hard parse error
    here, not a silently-accepted alternate format."""
    m = re.search(r'^version:\s*"?(\d+\.\d+\.\d+)"?\s*$', yaml_text, re.M)
    if m:
        return m.group(1)
    if re.search(r"^version:\s*\d+\s*$", yaml_text, re.M):
        raise fatal("SSOT `version:` is a bare integer — this repo migrated to semver "
                    "(docs/VERSIONING.md); fix the SSOT, do not widen the parser back")
    raise fatal("could not find top-level `version:` (expected semver MAJOR.MINOR.PATCH) in SSOT yaml")


def _slice_same_indent_block(block: str, key: str) -> str | None:
    key_start = block.find(key + ":")
    if key_start == -1:
        return None
    line_start = block.rfind("\n", 0, key_start) + 1
    indent = key_start - line_start
    nxt = re.search(rf"^\s{{0,{indent}}}\S", block[key_start + len(key) + 1:], re.M)
    end = key_start + len(key) + 1 + (nxt.start() if nxt else len(block) - key_start - len(key) - 1)
    return block[key_start:end]


def find_next_top_level_key(text: str, start: int) -> int:
    for m in re.finditer(r"^(\S)", text[start:], re.M):
        idx = start + m.start()
        nl = text.find("\n", idx)
        line = text[idx: nl if nl != -1 else len(text)]
        if line.startswith("#"):
            continue
        return idx
    return len(text)


def parse_task_classes(yaml_text: str) -> dict:
    tc_start = yaml_text.find("\ntask_classes:\n")
    if tc_start == -1:
        raise fatal("could not find `task_classes:` block in SSOT yaml")
    tc_end = find_next_top_level_key(yaml_text, tc_start + len("\ntask_classes:\n"))
    block = yaml_text[tc_start:tc_end]
    # GUARD PARSE CONTRACT: `{ tier:, effort: }`, not `{ model:, effort: }`.
    rx = re.compile(r"^\s*([\w-]+):\s*\{\s*tier:\s*([\w-]+),\s*effort:\s*(\w+)\s*\}", re.M)
    parsed = {mo.group(1): (mo.group(2), mo.group(3)) for mo in rx.finditer(block)}
    if not parsed:
        raise fatal("parsed zero rows from SSOT `task_classes:` block — parser or SSOT is broken")
    return parsed


def parse_active_provider(yaml_text: str) -> str:
    m = re.search(r"^active_provider:\s*(\S+)\s*(?:#.*)?$", yaml_text, re.M)
    if not m:
        raise fatal("SSOT has no top-level `active_provider:`")
    return m.group(1)


def parse_provider_profile(yaml_text: str, provider: str) -> dict:
    """{model: {tier: model_id}, effort_map: {tier: {intent: native|None}}}."""
    p_start = yaml_text.find("\nproviders:\n")
    if p_start == -1:
        raise fatal("SSOT has no `providers:` block")
    p_end = yaml_text.find("\nprices:\n", p_start)
    providers_block = yaml_text[p_start: p_end if p_end != -1 else len(yaml_text)]
    m = re.search(rf"^  {re.escape(provider)}:\s*$", providers_block, re.M)
    if not m:
        raise fatal(f"active_provider '{provider}' has no `providers.{provider}:` block")
    nxt = re.search(r"^  [\w-]+:\s*$", providers_block[m.end():], re.M)
    block = providers_block[m.start(): m.end() + (nxt.start() if nxt else len(providers_block) - m.end())]

    models_slice = _slice_same_indent_block(block, "models")
    models = dict(re.findall(r"^\s+([\w-]+):\s*(\S+)", models_slice or "", re.M))

    effort_slice = _slice_same_indent_block(block, "effort") or ""
    map_slice = _slice_same_indent_block(effort_slice, "map") or ""
    effort_map = {}
    for tier in models:
        row = re.search(rf"^\s*{re.escape(tier)}:\s*\{{([^}}]*)\}}", map_slice, re.M)
        if row:
            pairs = dict(re.findall(r"(\w+):\s*(null|\w+)", row.group(1)))
            effort_map[tier] = {k: (None if v == "null" else v) for k, v in pairs.items()}
    return {"models": models, "effort_map": effort_map}


# ---- resolve_route.py import (S09 dependency) ------------------------------
# GUARD-02 hardening requirement: this renderer must use resolve_route.py's resolve()
# as the SINGLE tier->model resolution path — never reimplement it — so the renderer
# and the vendored resolver cannot drift apart. resolve_route.py is authored in a
# LATER session (S09); it does not exist yet at S05 port time. Import it lazily and
# fall back to a local BASELINE-ONLY resolution (mirrors ARCHITECTURE.md §3's
# "Baseline" rule exactly: tier+intent -> native level, falling back to `standard`)
# only when the module is absent, so this script isn't blocked on S09 landing.
# TODO(S07 final integration): once resolve_route.py exists, delete
# `_baseline_resolve_fallback` below and make the import a hard requirement (no
# try/except) — a silent fallback surviving past S07 would let the renderer and the
# resolver drift exactly as the hardening note warns against.
def _load_resolve_route():
    repo_dir = os.environ.get("_RRD_REPO_DIR")
    candidate = os.path.join(repo_dir, "claude", "scripts", "resolve_route.py") if repo_dir else None
    if not candidate or not os.path.isfile(candidate):
        return None
    spec = importlib.util.spec_from_file_location("resolve_route", candidate)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _baseline_resolve_fallback(tier: str, intent: str, profile: dict) -> tuple[str | None, str | None]:
    """ponytail: temporary stand-in for resolve_route.resolve(), baseline case only
    (no escalation/degrade — the digest only ever renders the baseline row). Delete
    once resolve_route.py lands (S09) and the import above becomes non-optional."""
    model_id = profile["models"].get(tier)
    tier_map = profile["effort_map"].get(tier, {})
    if not tier_map or all(v is None for v in tier_map.values()):
        return model_id, None
    return model_id, tier_map.get(intent, tier_map.get("standard"))


def resolve_task_classes_for_active_provider(yaml_text: str) -> dict:
    """{class_name: (model_id, native_effort_or_None)} for every task_classes row,
    resolved against active_provider. Uses resolve_route.resolve() when importable
    (see _load_resolve_route's TODO), else the baseline-only fallback."""
    task_classes = parse_task_classes(yaml_text)
    active_provider = parse_active_provider(yaml_text)
    profile = parse_provider_profile(yaml_text, active_provider)

    resolver_mod = _load_resolve_route()
    resolved = {}
    for name, (tier, intent) in task_classes.items():
        if resolver_mod is not None and hasattr(resolver_mod, "resolve"):
            result = resolver_mod.resolve(
                task_class=name, active_provider=active_provider, current=None, signal="none"
            )
            resolved[name] = (result["model_id"], result.get("native_effort"))
        else:
            resolved[name] = _baseline_resolve_fallback(tier, intent, profile)
    return resolved


def render_task_class_row(name: str, model_id: str | None, native_effort: str | None) -> str:
    if model_id is None:
        return f"unresolved({name})"
    return f"{model_id}" + (f" · {native_effort}" if native_effort else "")


def extract_variant_block(source_text: str, variant: str) -> str:
    start_tag = f"<!-- ===== VARIANT: {variant} "
    end_tag_prefix = f"<!-- ===== END VARIANT {variant} "
    start_idx = source_text.find(start_tag)
    if start_idx == -1:
        raise fatal(f"variant '{variant}' not found in digest source template (missing {start_tag!r})")
    end_idx = source_text.find(end_tag_prefix, start_idx)
    if end_idx == -1:
        raise fatal(f"variant '{variant}' section never closed (missing {end_tag_prefix!r})")
    section = source_text[start_idx:end_idx]

    begins = list(re.finditer(re.escape(BEGIN_MARKER), section))
    ends = list(re.finditer(re.escape(END_MARKER), section))
    if len(begins) != 1 or len(ends) != 1:
        raise fatal(
            f"variant '{variant}' section must contain exactly one BEGIN/END ROUTING pair "
            f"in the source template (found {len(begins)} BEGIN, {len(ends)} END)"
        )
    b, e = begins[0], ends[0]
    if b.end() > e.start():
        raise fatal(f"variant '{variant}' source markers are reordered (END before BEGIN)")
    inner = section[b.end():e.start()]
    return inner.strip("\n")


_TIER_ROW_PLACEHOLDER_RE = re.compile(r"\{\{resolve:(\w+)\}\}")


def render_block(yaml_path: str, source_path: str, variant: str) -> str:
    yaml_text = read(yaml_path)
    version = parse_ssot_version(yaml_text)
    resolved = resolve_task_classes_for_active_provider(yaml_text)
    active_provider = parse_active_provider(yaml_text)

    inner = extract_variant_block(read(source_path), variant)

    def _sub(mo):
        cls = mo.group(1)
        if cls not in resolved:
            raise fatal(f"digest template references unknown task_classes row '{cls}' via {{{{resolve:{cls}}}}}")
        model_id, native_effort = resolved[cls]
        return render_task_class_row(cls, model_id, native_effort)

    inner = _TIER_ROW_PLACEHOLDER_RE.sub(_sub, inner)
    inner = inner.replace("{{version}}", version).replace("{{active_provider}}", active_provider)

    block = f"{BEGIN_MARKER}\n{inner}\n{END_MARKER}\n"

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


def do_check(target_path: str, new_block: str) -> int:
    text = read(target_path)
    b, e = validate_single_pair(text, target_path)
    end_full = e + len(END_MARKER)
    installed = text[b.start(1): end_full] + "\n"
    byte_clean = installed.rstrip("\n") == new_block.rstrip("\n")
    if byte_clean:
        print(f"CLEAN: {target_path} matches the current SSOT re-render.")
        return 0
    print(f"DRIFT: {target_path} does NOT match the current SSOT re-render.", file=sys.stderr)
    print("--- installed ---", file=sys.stderr)
    print(installed, file=sys.stderr)
    print("--- would-render ---", file=sys.stderr)
    print(new_block, file=sys.stderr)
    return 1


def _assert_no_old_markers_anywhere(*texts: str) -> None:
    """Grep-gate (hardening requirement): NO old integer `vN` marker/stamp shape may
    survive in any text this script reads or writes."""
    for t in texts:
        if _OLD_MARKER_RE.search(t):
            raise fatal("found an OLD `<!-- BEGIN ROUTING (model-routing.yaml vN) -->`-style "
                        "marker — this repo migrated to the bare `<!-- BEGIN ROUTING -->` marker; "
                        "fix the source (do not re-widen the parser to accept it)")
        if _OLD_STAMP_RE.search(t):
            raise fatal("found an OLD `<!-- routing-ssot: vN -->` integer stamp — migrate it to "
                        "semver or drop it; the guard/renderer no longer recognize the integer shape")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=["v0", "full"], default="full")
    ap.add_argument("--repo-dir", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     help="Gearbox repo root (default: parent of claude/scripts/..)")
    ap.add_argument("--claude-home", default=os.environ.get("CLAUDE_HOME", os.path.expanduser("~/.claude")),
                     help="unused by default paths below; kept for parity with verify-routing.sh's env override")
    ap.add_argument("--yaml", default=None, help="override SSOT path (default: <repo-dir>/claude/model-routing.yaml)")
    ap.add_argument("--source", default=None, help="override digest source template (default: <repo-dir>/claude/model-routing.digest.md)")
    ap.add_argument("--target", default=None, help="override install target (default: <repo-dir>/CLAUDE.md)")
    ap.add_argument("--install", action="store_true", help="allow bootstrap onto a target with zero existing markers")
    ap.add_argument("--check", action="store_true", help="dry-run: report drift, write nothing")
    args = ap.parse_args(argv)

    repo_dir = args.repo_dir
    os.environ["_RRD_REPO_DIR"] = repo_dir  # threaded through to _load_resolve_route
    yaml_path = args.yaml or os.path.join(repo_dir, "claude", "model-routing.yaml")
    source_path = args.source or os.path.join(repo_dir, "claude", "model-routing.digest.md")
    target_path = args.target or os.path.join(repo_dir, "CLAUDE.md")

    try:
        _assert_no_old_markers_anywhere(read(yaml_path), read(source_path))
        block = render_block(yaml_path, source_path, args.variant)
        if os.path.isfile(target_path):
            _assert_no_old_markers_anywhere(read(target_path))
        if args.check:
            return do_check(target_path, block)
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
