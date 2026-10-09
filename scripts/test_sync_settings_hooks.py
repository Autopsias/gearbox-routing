"""REP-03: settings.json ships only hook entries whose files ship."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sync_from_claude", Path(__file__).with_name("sync-from-claude.py")
)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def _cmd(command, matcher=None):
    group = {"hooks": [{"type": "command", "command": command}]}
    if matcher:
        group["matcher"] = matcher
    return group


def _harness(tmp_path, hooks, files=("hooks/shipped.sh",)):
    harness = tmp_path / "harness"
    for rel in files:
        (harness / rel).parent.mkdir(parents=True, exist_ok=True)
        (harness / rel).write_text("#!/bin/sh\n")
    (harness / "settings.json").write_text(json.dumps({"hooks": hooks}))
    return harness


def _load(harness):
    return json.loads((harness / "settings.json").read_text())["hooks"]


def test_hook_on_missing_file_is_dropped_with_its_empty_group_and_event(tmp_path):
    harness = _harness(tmp_path, {
        "Stop": [_cmd('p="$HOME/.claude/hooks/private.py"; exec python3 "$p"')],
        "PreToolUse": [_cmd("~/.claude/scripts/private.sh", "Bash")],
    })
    dropped = sync.prune_settings_hooks(harness)
    assert len(dropped) == 2
    assert _load(harness) == {}


def test_hook_on_exported_file_is_kept(tmp_path):
    keep = _cmd('p="$HOME/.claude/hooks/shipped.sh"; [ -f "$p" ] || exit 0; exec bash "$p"')
    harness = _harness(tmp_path, {"Stop": [keep, _cmd("~/.claude/hooks/gone.sh")]})
    dropped = sync.prune_settings_hooks(harness)
    assert dropped == ["Stop: ~/.claude/hooks/gone.sh (names a file not in the export: hooks/gone.sh)"]
    assert _load(harness) == {"Stop": [keep]}
    assert sync.check_settings_hooks(tmp_path) == []


def test_unparseable_reference_is_dropped(tmp_path):
    harness = _harness(tmp_path, {"Stop": [
        _cmd('"$CLAUDE_PROJECT_DIR"/.claude/hooks/shipped.sh'),
        _cmd("~/.claude/hooks/$NAME.sh"),
        _cmd("~/.claude/hooks/../hooks/shipped.sh"),
    ]})
    dropped = sync.prune_settings_hooks(harness)
    assert len(dropped) == 3 and all("unparseable" in d for d in dropped)
    assert _load(harness) == {}


def test_manifest_exclude_unwires_a_shipped_hook(tmp_path):
    harness = _harness(tmp_path, {"Stop": [_cmd("~/.claude/hooks/shipped.sh")]})
    exclude = sync.settings_hook_exclude({"settings_hook_exclude": ["hooks/shipped.sh"]})
    dropped = sync.prune_settings_hooks(harness, exclude)
    assert "settings_hook_exclude" in dropped[0]
    assert _load(harness) == {}
    assert sync.settings_hook_exclude({}) == frozenset()


def test_scan_goes_red_on_a_planted_dangling_hook(tmp_path):
    _harness(tmp_path, {"Stop": [_cmd("~/.claude/hooks/shipped.sh")]})
    assert sync.check_settings_hooks(tmp_path) == []
    _harness(tmp_path, {"Stop": [_cmd("~/.claude/hooks/shipped.sh"), _cmd("~/.claude/hooks/planted.py")]})
    hits = sync.check_settings_hooks(tmp_path)
    assert len(hits) == 1 and "planted.py" in hits[0]
    # ... and the hit reaches the scan layer's verdict, not just the helper.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    green, output, count = sync.run_blocked_pattern_scan(tmp_path, ["never-matches-anything-xyz"])
    assert not green and "planted.py" in output


def test_public_policy_is_mirrored_with_task_tokens(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    harness = tmp_path / "harness"
    assert sync.mirror_public_files(repo, harness) == ["model-routing.yaml"]
    text = (harness / "model-routing.yaml").read_text()
    for line in ("      cheap_fast:        haiku ", "      workhorse:         sonnet ",
                 "      frontier_reasoner: opus ", "      apex_model: fable ",
                 "      frontier_reasoner: glm-5.3".replace("glm-5.3", "opus")):
        assert line in text, line
    assert "gpt-6.1-sol" in text                     # other profiles keep vendor ids
    assert "  claude-haiku-5-5:" in text             # so does the price table


def test_the_exported_runner_can_climb_the_mirrored_policy(tmp_path):
    """plan-execute walks the ladder from Task tokens; with vendor ids it found nothing."""
    repo = Path(__file__).resolve().parents[1]
    sync.mirror_public_files(repo, tmp_path)
    spec = importlib.util.spec_from_file_location("rr", repo / "harness" / "scripts" / "resolve_route.py")
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)
    nxt = rr.escalate("agentic_build", "anthropic", current={"model_id": "sonnet", "native_effort": "high"},
                      ssot_path=str(tmp_path / "model-routing.yaml"))
    assert nxt != rr.EXHAUSTED and nxt["model_id"] in ("sonnet", "opus"), nxt


def test_task_token_rewrite_fails_closed_on_an_unknown_id():
    import pytest

    bad = "providers:\n  anthropic:\n    models:\n      cheap_fast: mystery-model\n"
    with pytest.raises(sync.SyncError):
        sync.to_task_tokens(bad)


def test_mirror_fails_closed_when_the_public_policy_is_missing(tmp_path):
    import pytest

    with pytest.raises(sync.SyncError):
        sync.mirror_public_files(tmp_path, tmp_path / "harness")
