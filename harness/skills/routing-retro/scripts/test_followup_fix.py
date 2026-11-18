import os
import subprocess

import followup_fix as ff

DAY = 86400
T0 = 1_700_000_000


def _commit(repo, msg, when):
    env = {**os.environ, "GIT_AUTHOR_DATE": f"{when} +0000", "GIT_COMMITTER_DATE": f"{when} +0000",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True, env=env)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def test_only_fix_on_session_lines_within_a_week_pairs(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q", "-b", "main"], check=True)
    f = tmp_path / "a.py"
    f.write_text("old1\nold2\n")
    _commit(tmp_path, "chore: base", T0)
    f.write_text("old1\nold2\nnew1\nnew2\n")
    session = _commit(tmp_path, "feat(my-plan): s01 session work", T0 + DAY)
    f.write_text("old1\nold2\nFIXED\nnew2\n")
    fix_same = _commit(tmp_path, "fix: correct new1", T0 + 2 * DAY)
    f.write_text("OLD\nold2\nFIXED\nnew2\n")
    _commit(tmp_path, "fix: unrelated line", T0 + 3 * DAY)
    f.write_text("OLD\nold2\nFIXED\nLATE\n")
    _commit(tmp_path, "fix: too late", T0 + 10 * DAY)
    pairs, n_sessions = ff.find_pairs(str(tmp_path), ref="HEAD", days=3000)
    assert n_sessions == 1
    assert [(p["session"], p["fix"]) for p in pairs] == [(session, fix_same)]
    assert pairs[0]["lines"] == 1 and pairs[0]["files"] == ["a.py"] and pairs[0]["hours"] == 24.0
