#!/usr/bin/env python3
"""One canonical copy of the trust adapter scripts — proven, not asserted.

trust-run and trust-review each carried the SAME ten script files: ~5,700
duplicated lines, the secret-redaction path (`egress_sanitizer.py`) among them.
Every fix had to be made twice and divergence between the copies was silent —
nothing in the repo compared them. The scripts now live only under
``skills/trust-review/references/scripts/``; trust-run ships none and its
SKILL.md points here.

Three checks, because the dedup is only real if all three hold:

1. ``test_no_byte_identical_python_source`` — no two authored ``.py`` files in
   the repo are byte-identical. This is the GENERAL guard: a second copy of any
   script fails it, not just these ten.

   ``.py`` only, deliberately. The six ``references/*.md|json`` files ARE still
   duplicated between the two skills, and that is the correct answer for them:
   trust-run's SKILL.md tells the agent to read ``references/conventions.md``
   at its own skill-relative path ("read those values and use them exactly"),
   so trust-run needs its own copy in the deployed tree. It says the opposite
   about the scripts — "you never invoke those scripts from this skill's
   directory" — which is why the scripts, and only the scripts, collapse to one.
2. ``test_adapters_sibling_import_from_a_cold_interpreter`` — the ten files
   stay in ONE directory and import each other as siblings. They rely on
   ``sys.path.insert(HERE)`` (verification.py, external_web_adapter.py,
   internal_corpus_adapter.py, verification_wiring.py, compat_harness.py), so
   splitting the directory breaks them at runtime, not at review time.
3. ``test_runners_reach_the_canonical_copy`` — the two trust-review runners
   actually find it. Both computed a ``references/scripts`` path that did not
   exist (off by a directory level) and swallowed the resulting ImportError, so
   ``loop_controller._HAVE_GATE`` was False and ``vault_writeback.AR`` was None:
   the S02 precondition gate never ran and the vault adapter never resolved. A
   dedup that leaves the one surviving copy unreachable has fixed nothing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
CANONICAL = SKILL / "references" / "scripts"
RUNNERS = SKILL / "scripts"


def _find_repo():
    """The repo root: the nearest folder above the skill whose pyproject.toml has a
    [tool.claude-quality] section. None in the public harness layout, which has none."""
    for parent in SKILL.parents:
        toml = parent / "pyproject.toml"
        if toml.is_file() and "[tool.claude-quality]" in toml.read_text(encoding="utf-8"):
            return parent
    return None


REPO = _find_repo()
CHECK_FILE_SIZES = REPO / "scripts" / "quality" / "check_file_sizes.py" if REPO else None
needs_repo = pytest.mark.skipif(
    REPO is None,
    reason="no pyproject.toml with [tool.claude-quality] above the skill (public harness "
           "layout): the repo-wide source scan cannot run here; it still runs in the source repo",
)

# The ten files that used to exist twice. All must stay in ONE directory.
ADAPTERS = (
    "adapter_registry.py",
    "claim_identity.py",
    "compat_harness.py",
    "egress_sanitizer.py",
    "external_web_adapter.py",
    "internal_corpus_adapter.py",
    "local_corpus.py",
    "manifest_gate.py",
    "pack_hooks.py",
    "pack_loader.py",
    "verification.py",
    "verification_wiring.py",
)

# The inventory above is the PUBLIC adapter set and names no private file. A user's own
# provider pack (provider_pack_<name>.py) and its regression tests (test_provider_pack_
# <name>.py plus data) sit beside it. They are matched by file-name prefix, never listed.
PACK_PREFIXES = ("provider_pack_", "test_provider_pack_")
MODULES = tuple(name[:-3] for name in ADAPTERS)


def _check_file_sizes():
    """Load scripts/quality/check_file_sizes.py by file path.

    Loaded this way, not imported normally, so its directory never lands on
    ``sys.path`` for any later test in the run — the same reasoning ``_probe``
    below applies to the canonical adapters themselves.
    """
    spec = importlib.util.spec_from_file_location(
        "check_file_sizes", CHECK_FILE_SIZES
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _authored_python() -> list[Path]:
    """Every tracked, authored ``.py`` in the repo — the SAME set the file-size
    and complexity ratchets police, decided by their own ``is_excluded()``
    over pyproject's ``[tool.claude-quality].exclude`` (a directory-prefix
    match), not a re-parse of the exclude list here.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    quality = _check_file_sizes()
    exclude = quality.load_config(REPO)["exclude"]
    assert exclude, "pyproject [tool.claude-quality].exclude is empty"
    return [
        REPO / rel
        for rel in listing.split("\0")
        if rel and not quality.is_excluded(Path(rel), exclude)
    ]


def _probe(source: str) -> str:
    """Run a snippet in a COLD interpreter and return its stdout.

    Cold matters: importing these modules in-process would leave the canonical
    directory on ``sys.path`` for every later test, which is exactly what would
    stop check 3 from being able to fail.
    """
    done = subprocess.run(
        [sys.executable, "-c", source], cwd=REPO or SKILL,
        capture_output=True, text=True,
    )
    assert done.returncode == 0, (
        f"probe exited {done.returncode}\nstdout:\n{done.stdout}\n"
        f"stderr:\n{done.stderr}"
    )
    return done.stdout


def test_inventory_lists_no_pack_file() -> None:
    """A pack file or its test is matched by prefix, so the required set never names one."""
    assert not [n for n in ADAPTERS if n.startswith(PACK_PREFIXES)]


@needs_repo
def test_no_byte_identical_python_source() -> None:
    """A second copy of any script in AUTHORED SOURCE — these ten or any future
    pair. NOT "anywhere in the repo": `_authored_python()` scans exactly the
    files the file-size and complexity ratchets scan, via check_file_sizes'
    own `is_excluded()` — a directory-prefix match, so `evals/routing/tasks`
    drops everything under it, not just a literal path component. That drops
    `_plans/**` too, and byte-identical snapshots of all ten adapters do live
    there as research prior art (verified in the 2026-08-20 run). Nothing imports them
    and they are not drift. Stating the wider claim made the test a
    documented promise it does not keep (review of 2026-08-20)."""
    files = _authored_python()

    # Known positive: a check that reports "clean" because its input was empty
    # is worse than no check. The ten canonical adapters must be IN this list.
    missing = [n for n in ADAPTERS if CANONICAL / n not in files]
    assert not missing, (
        f"the file list this check scans does not contain {missing} — it is "
        f"scanning the wrong set of files, so a 'clean' result means nothing"
    )

    by_digest: dict[str, list[str]] = defaultdict(list)
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        by_digest[digest].append(path.relative_to(REPO).as_posix())

    dupes = sorted(sorted(group) for group in by_digest.values() if len(group) > 1)
    assert not dupes, (
        "byte-identical Python source — each group is one file copied. Keep ONE "
        "canonical copy and point the other caller at it:\n"
        + "\n".join("  " + "\n  == ".join(group) for group in dupes)
    )


def test_adapters_sibling_import_from_a_cold_interpreter() -> None:
    """All ten in one directory, importing each other as siblings."""
    present = [name for name in ADAPTERS if (CANONICAL / name).is_file()]
    assert present == list(ADAPTERS), (
        f"the canonical adapter set is incomplete at {CANONICAL}: "
        f"missing {sorted(set(ADAPTERS) - set(present))}"
    )

    # By NAME, not by content — a re-copied directory whose files were then
    # edited would slip past the byte-identity check above.
    elsewhere = sorted(
        path.relative_to(REPO).as_posix()
        for path in (_authored_python() if REPO else [])
        if path.name in ADAPTERS and path.parent != CANONICAL
    )
    assert not elsewhere, (
        "these filenames are the canonical adapter set and may live in exactly "
        f"one directory ({CANONICAL.relative_to(REPO)}) — found also at: {elsewhere}"
    )

    # The canonical directory is deliberately NEVER put on sys.path here. Each
    # module is loaded by file path, exactly as a direct `python3 <file>` run
    # would, and sys.path/sys.modules are reset between loads — so every sibling
    # import has to be satisfied by that module's own `sys.path.insert(HERE)`.
    # Pre-adding the directory would make this check unable to fail.
    out = _probe(
        "import importlib.util, sys\n"
        f"names = {list(MODULES)!r}\n"
        f"here = {str(CANONICAL)!r}\n"
        "pristine = list(sys.path)\n"
        "for name in names:\n"
        "    sys.path[:] = pristine\n"
        "    for other in names:\n"
        "        sys.modules.pop(other, None)\n"
        "    spec = importlib.util.spec_from_file_location(\n"
        "        name, here + '/' + name + '.py')\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    sys.modules[name] = module\n"
        "    spec.loader.exec_module(module)\n"
        "print('imported', len(names))\n"
    )
    assert out.strip() == f"imported {len(MODULES)}"


def test_runners_reach_the_canonical_copy() -> None:
    """loop_controller and vault_writeback resolve the ONE copy at import.

    Deliberately does NOT pre-add the canonical directory to ``sys.path`` — if
    it did, a runner computing a wrong path would still import successfully and
    this check could never fail. Only the runners' own path logic is under test.
    """
    out = _probe(
        f"import sys; sys.path.insert(0, {str(RUNNERS)!r})\n"
        "import loop_controller, vault_writeback\n"
        "print('HAVE_GATE', loop_controller._HAVE_GATE)\n"
        "print('GATE_FILE', getattr(loop_controller, 'manifest_gate', None)"
        " and loop_controller.manifest_gate.__file__)\n"
        "print('AR_FILE', getattr(vault_writeback.AR, '__file__', None))\n"
    )
    reported = dict(line.split(" ", 1) for line in out.strip().splitlines())

    assert reported["HAVE_GATE"] == "True", (
        "loop_controller could not import manifest_gate, so the S02 precondition "
        "gate is skipped entirely (it is guarded by `if _HAVE_GATE:`). Its "
        "sys.path entry does not point at " + str(CANONICAL)
    )
    assert reported["GATE_FILE"] == str(CANONICAL / "manifest_gate.py")
    assert reported["AR_FILE"] == str(CANONICAL / "adapter_registry.py"), (
        "vault_writeback resolved adapter_registry from "
        f"{reported['AR_FILE']}, not the canonical copy"
    )
