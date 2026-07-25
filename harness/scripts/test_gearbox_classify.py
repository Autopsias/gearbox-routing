#!/usr/bin/env python3
"""test_gearbox_classify.py — the one runnable check behind the deploy classifier.

Covers the logic that decides whether a deploy aborts: the glob semantics of the
[live-state] section and the settings.json key-path split (machine churn vs harness
source). If either breaks, a `/model` switch starts aborting deploys again, or a
hook rewrite starts sliding through as routine.

    python3 scripts/test_gearbox_classify.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("gc", os.path.join(HERE, "gearbox-classify.py"))
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)

SPEC = gc.load_pathspec(os.path.join(HERE, "deploy.pathspec"))
LIVE = [gc.glob_to_re(g) for g in SPEC["live-state"]]
CHURN = set(SPEC["settings-churn-keys"])


def is_live(p):
    return any(r.match(p) for r in LIVE)


def main():
    # --- [live-state] globs: * stays in a segment, ** crosses them -------------
    assert is_live("projects/-Users-x-example-project/memory/note.md")
    assert is_live("projects/-Users-x-example-project/memory/sub/deep.md")
    assert is_live("_plans/some-plan-2026-07-25/PLAN.html")
    assert is_live("evals/routing/MISROUTES.md")
    assert is_live("evals/routing/results/2026-07-25/run.json")
    # …and harness source is NOT live-state (the fail-closed default)
    assert not is_live("projects/-Users-x-example-project/transcript.jsonl")  # `*` cannot cross /
    assert not is_live("skills/plan-execute/SKILL.md")
    assert not is_live("scripts/gearbox")
    assert not is_live("evals/routing/AUDIT.md")
    assert not is_live("CLAUDE.md")

    # --- settings.json: the split that must NOT abort a deploy ----------------
    assert gc.is_churn_key("model", CHURN)                  # /model  (the S03B finding)
    assert gc.is_churn_key("effortLevel", CHURN)            # /effort
    assert gc.is_churn_key("permissions.allow", CHURN)      # allow-always growth
    assert gc.is_churn_key("enabledPlugins.foo@bar", CHURN)  # via its declared ancestor
    assert gc.is_churn_key("statusLine.command", CHURN)

    # --- …and the split that MUST abort a deploy ------------------------------
    assert not gc.is_churn_key("hooks", CHURN)
    assert not gc.is_churn_key("hooks.SessionStart", CHURN)
    assert not gc.is_churn_key("env.PYTHONPYCACHEPREFIX", CHURN)
    assert not gc.is_churn_key("permissions.defaultMode", CHURN)
    assert not gc.is_churn_key("permissions.deny", CHURN)
    assert not gc.is_churn_key("someNewKeyTheBinaryAdded", CHURN)  # unknown => loud

    # --- changed_key_paths ----------------------------------------------------
    a = {"model": "opus", "hooks": {"SessionStart": [1]}, "permissions": {"allow": ["a"]}}
    assert set(gc.changed_key_paths(a, dict(a, model="fable"))) == {"model"}
    assert set(gc.changed_key_paths(a, {**a, "permissions": {"allow": ["a", "b"]}})) == {"permissions.allow"}
    assert set(gc.changed_key_paths(a, {**a, "hooks": {"SessionStart": [2]}})) == {"hooks.SessionStart"}
    assert set(gc.changed_key_paths(a, {**a, "newKey": 1})) == {"newKey"}
    assert set(gc.changed_key_paths(a, a)) == set()

    # --- end-to-end on a throwaway repo: churn vs harness vs live vs triage ----
    with tempfile.TemporaryDirectory() as d:
        run = lambda *c: subprocess.run(["git", "-C", d, *c], check=True, capture_output=True)
        run("init", "-q", "-b", "main")
        run("config", "user.email", "t@t"); run("config", "user.name", "t")
        settings = {"model": "opus[1m]", "hooks": {"SessionStart": []}, "permissions": {"allow": []}}
        os.makedirs(os.path.join(d, "projects/p/memory"))
        os.makedirs(os.path.join(d, "skills"))
        for path, body in [
            ("settings.json", json.dumps(settings, indent=2)),
            ("projects/p/memory/m.md", "one\n"),
            ("skills/s.md", "one\n"),
        ]:
            open(os.path.join(d, path), "w").write(body)
        run("add", "-A"); run("commit", "-qm", "base")

        def classify():
            out = subprocess.run(
                [sys.executable, os.path.join(HERE, "gearbox-classify.py"),
                 "--claude-dir", d, "--pathspec", os.path.join(HERE, "deploy.pathspec")],
                capture_output=True, check=True).stdout
            return json.loads(out)

        assert classify()["harness"] == []

        # a /model switch — machine churn, must NOT be harness
        open(os.path.join(d, "settings.json"), "w").write(
            json.dumps({**settings, "model": "claude-fable-5[1m]"}, indent=2))
        r = classify()
        assert r["churn"] == ["settings.json"], r
        assert r["harness"] == [], r
        assert r["detail"]["settings.json"]["churn"] == ["model"], r

        # a hook rewrite in the SAME file — harness source, must abort a deploy
        open(os.path.join(d, "settings.json"), "w").write(
            json.dumps({**settings, "model": "claude-fable-5[1m]",
                        "hooks": {"SessionStart": [{"command": "evil.sh"}]}}, indent=2))
        r = classify()
        assert r["harness"] == ["settings.json"], r
        assert r["churn"] == [], r
        assert r["detail"]["settings.json"]["harness"] == ["hooks.SessionStart"], r

        # live-state edit, harness edit, untracked triage, untracked live-state
        open(os.path.join(d, "settings.json"), "w").write(json.dumps(settings, indent=2))
        open(os.path.join(d, "projects/p/memory/m.md"), "a").write("two\n")
        open(os.path.join(d, "skills/s.md"), "a").write("two\n")
        open(os.path.join(d, "projects/p/memory/new.md"), "w").write("new\n")
        open(os.path.join(d, "stray.md"), "w").write("stray\n")
        r = classify()
        assert r["live_state"] == ["projects/p/memory/m.md"], r
        assert r["harness"] == ["skills/s.md"], r
        assert r["live_untracked"] == ["projects/p/memory/new.md"], r
        assert r["triage_untracked"] == ["stray.md"], r

        # unparseable settings.json => harness + an error, never "routine"
        open(os.path.join(d, "settings.json"), "w").write("{ not json")
        r = classify()
        assert "settings.json" in r["harness"], r
        assert r["errors"], r

    print("test_gearbox_classify: all assertions passed")


if __name__ == "__main__":
    main()
