"""TH-01 — the orchestrator's own context size, recorded at every dispatch.

Covers the two paths the item names (a real transcript is read -> an int; the
env var or the file is missing -> null), the never-raise contract, the
`parents[3]` tree-root claim, and the cwd encoding the transcript path is built
from. Each check runs against a REAL on-disk transcript in a temp HOME rather
than a mocked `context_tokens`, because the defect this guards against is
exactly a path that resolves to nothing and returns a plausible null.

Run: pytest skills/plan-execute/scripts/test_orchestrator_ctx.py -q
"""

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import orchestrator_ctx as octx  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"


def _transcript(home: Path, cwd: Path, session=SESSION, usage=None):
    """A minimal but REAL transcript: one assistant record carrying usage, in
    the directory Claude Code would have written it to for `cwd`."""
    usage = usage or {"input_tokens": 1200, "cache_read_input_tokens": 40000,
                      "cache_creation_input_tokens": 800, "output_tokens": 300}
    directory = home / ".claude" / "projects" / octx.encode_cwd(cwd)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (session + ".jsonl")
    path.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "model": "claude-opus-5", "usage": usage},
    }) + "\n")
    return path


WORKER_MARKERS = octx.load_by_path("t_store", octx.COMPACT_STORE_PATH).WORKER_MARKERS


def _env(monkeypatch, home: Path, cwd: Path, session=SESSION, worker=False):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(cwd)
    # This suite is itself often run from a dispatched agent, whose environment
    # ALREADY carries worker markers. Clear them, or every check below measures
    # the guard instead of the thing it names.
    for marker in WORKER_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    if worker:
        monkeypatch.setenv(WORKER_MARKERS[0], "1")
    if session is None:
        monkeypatch.delenv(octx.SESSION_ENV, raising=False)
    else:
        monkeypatch.setenv(octx.SESSION_ENV, session)


# --------------------------------------------------------------------------
# The anchor claim: parents[3] is the tree root in BOTH trees this ships to.
# --------------------------------------------------------------------------
def test_tree_root_anchor_locates_the_hooks_directory():
    """The claim is that `parents[3]` RESOLVES, so that is what is asserted.

    This used to require TREE_ROOT.name to be "your-private-harness" or ".claude",
    which is a proxy for the anchor rather than the anchor: the same repo
    checked out as a git worktree (`gearbox-iso`, where plan-level isolation
    runs) has a different directory name and failed here while the anchor was
    perfectly correct. A name is not a structure (2026-08-22)."""
    assert (octx.TREE_ROOT / "skills" / "plan-execute" / "scripts").is_dir(), octx.TREE_ROOT
    assert octx.CONTEXT_TOKENS_PATH.is_file(), octx.CONTEXT_TOKENS_PATH
    # A KNOWN NEGATIVE: the anchor must be the tree root, not its parent.
    assert not (octx.TREE_ROOT.parent / "hooks" / "context_tokens.py").is_file()


# --------------------------------------------------------------------------
# The cwd encoding, against the shapes MEASURED under ~/.claude/projects.
# --------------------------------------------------------------------------
def test_encode_cwd_matches_claude_codes_own_directory_names():
    assert octx.encode_cwd("/Users/x/your-private-harness") == "-Users-x-your-private-harness"
    assert octx.encode_cwd("/Users/x/.claude") == "-Users-x--claude"
    assert (octx.encode_cwd("/Users/x/DeveloperFolder/AWS_setup")
            == "-Users-x-DeveloperFolder-AWS-setup")


# --------------------------------------------------------------------------
# Found path / not-found path.
# --------------------------------------------------------------------------
def test_reads_an_int_from_a_real_transcript(tmp_path, monkeypatch):
    home, cwd = tmp_path / "home", tmp_path / "repo"
    cwd.mkdir()
    _transcript(home, cwd)
    _env(monkeypatch, home, cwd)
    tokens = octx.orchestrator_ctx_tokens()
    assert isinstance(tokens, int) and tokens >= 42000, tokens


def test_null_when_the_session_env_var_is_unset(tmp_path, monkeypatch):
    home, cwd = tmp_path / "home", tmp_path / "repo"
    cwd.mkdir()
    _transcript(home, cwd)
    _env(monkeypatch, home, cwd, session=None)
    assert octx.orchestrator_ctx_tokens() is None


def test_null_when_the_transcript_file_is_missing(tmp_path, monkeypatch):
    home, cwd = tmp_path / "home", tmp_path / "repo"
    cwd.mkdir()
    (home / ".claude" / "projects").mkdir(parents=True)
    _env(monkeypatch, home, cwd)
    assert octx.orchestrator_ctx_tokens() is None


def test_finds_the_transcript_when_the_process_cwd_moved(tmp_path, monkeypatch):
    """The transcript directory is keyed on the SESSION's cwd; `begin` may run
    from anywhere. The glob fallback is what stops that from reading as null."""
    home, session_cwd, elsewhere = tmp_path / "home", tmp_path / "repo", tmp_path / "other"
    session_cwd.mkdir()
    elsewhere.mkdir()
    _transcript(home, session_cwd)
    _env(monkeypatch, home, elsewhere)
    assert isinstance(octx.orchestrator_ctx_tokens(), int)


# --------------------------------------------------------------------------
# The `source` predicate. A live transcript on this host returns
# `read:no_model_window`, so an equality test against "read" would null out
# every real dispatch while still passing a naive found/not-found test.
# --------------------------------------------------------------------------
def test_a_transcript_with_no_model_window_still_records_the_int(tmp_path, monkeypatch):
    home, cwd = tmp_path / "home", tmp_path / "repo"
    cwd.mkdir()
    path = _transcript(home, cwd)
    _env(monkeypatch, home, cwd)

    import importlib.util
    spec = importlib.util.spec_from_file_location("ct_probe", octx.CONTEXT_TOKENS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tokens, window, source = module.context_tokens(str(path))
    assert window is None and source == "read:no_model_window"   # the known positive
    assert octx.orchestrator_ctx_tokens() == tokens


# --------------------------------------------------------------------------
# Never raises — an unreadable transcript, a hostile session id, a vanished
# context_tokens.py all degrade to None rather than taking the dispatch down.
# --------------------------------------------------------------------------
def test_never_raises_on_a_corrupt_transcript(tmp_path, monkeypatch):
    home, cwd = tmp_path / "home", tmp_path / "repo"
    cwd.mkdir()
    path = _transcript(home, cwd)
    path.write_bytes(b"\x00not json at all\n")
    _env(monkeypatch, home, cwd)
    assert octx.orchestrator_ctx_tokens() is None


def test_never_raises_when_context_tokens_is_missing(tmp_path, monkeypatch):
    home, cwd = tmp_path / "home", tmp_path / "repo"
    cwd.mkdir()
    _transcript(home, cwd)
    _env(monkeypatch, home, cwd)
    monkeypatch.setattr(octx, "CONTEXT_TOKENS_PATH", tmp_path / "gone" / "context_tokens.py")
    assert octx.orchestrator_ctx_tokens() is None


def test_a_path_traversal_session_id_is_refused(tmp_path, monkeypatch):
    home, cwd = tmp_path / "home", tmp_path / "repo"
    cwd.mkdir()
    _transcript(home, cwd)
    _env(monkeypatch, home, cwd, session="../../etc/passwd")
    assert octx.transcript_path() is None


def test_the_worker_markers_do_not_suppress_the_measurement(tmp_path, monkeypatch):
    """THE REGRESSION THIS FILE EXISTS FOR NOW.

    This module used to route the session id through
    ``compact_store.env_session()``, which refuses whenever
    ``CLAUDE_CODE_CHILD_SESSION`` or ``CLAUDE_CODE_FORK_SUBAGENT`` is set.
    MEASURED 2026-08-23 on this host, both markers read ``'1'`` inside the
    ORCHESTRATOR's OWN tool subprocess, so that refusal fired on every single
    dispatch and the field landed ``null`` on 33 of 33 records while the reader
    underneath it worked perfectly.

    A previous version of this test asserted the opposite, on the reading that a
    dispatched agent returning 132,718 tokens had reported the WORKER's context.
    A direct probe on 2026-08-23 settled it: a dispatched subagent's subprocess
    reports the SAME ``CLAUDE_CODE_SESSION_ID`` as the orchestrator and resolves
    to the SAME transcript file. That 132,718 was the orchestrator's own context
    at that moment, not a worker's — the two were never distinguishable because
    they were never different."""
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    cwd.mkdir()
    _transcript(home, cwd)
    _env(monkeypatch, home, cwd)
    clean = octx.orchestrator_ctx_tokens()
    assert isinstance(clean, int)
    _env(monkeypatch, home, cwd, worker=True)
    assert octx.env_session_id()          # the markers no longer veto the id
    assert octx.orchestrator_ctx_tokens() == clean


def test_no_session_id_is_still_no_measurement(tmp_path, monkeypatch):
    """Reading the env var directly must not become reading it blindly: with no
    id there is nothing to resolve, and a missing transcript stays None rather
    than becoming some other session's number."""
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    cwd.mkdir()
    _transcript(home, cwd)
    _env(monkeypatch, home, cwd)
    assert isinstance(octx.orchestrator_ctx_tokens(), int)

    monkeypatch.delenv(octx.SESSION_ENV, raising=False)
    assert octx.env_session_id() is None
    assert octx.orchestrator_ctx_tokens() is None

    monkeypatch.setenv(octx.SESSION_ENV, "no-transcript-anywhere-0000")
    assert octx.transcript_path() is None
    assert octx.orchestrator_ctx_tokens() is None
