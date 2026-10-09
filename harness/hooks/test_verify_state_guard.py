"""Self-check for hooks/verify-state-guard.py.

KNOWN POSITIVES: a dispatched agent forging the review ledger (a security
review found one forged surface row was a PASS with no review).
KNOWN NEGATIVES: reads stay open, and the main session (the orchestrator, which
writes `<sid>.accepted.json` for the owner) is never touched.

Run: python3 -m pytest hooks/test_verify_state_guard.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent / "verify-state-guard.py"
LED = "_plans/x/_verify_state/land.low.codex.findings.ndjson"


def run(tool, tool_input, agent=True):
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": "/tmp"}
    if agent:
        payload |= {"agent_id": "agent-abc", "agent_type": "tier-sonnet-medium"}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True)


@pytest.mark.parametrize("tool,ti", [
    ("Write", {"file_path": f"/repo/{LED}", "content": "{}"}),
    ("Edit", {"file_path": f"/repo/{LED}", "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": "/repo/_plans/x/_verify_state/s01.accepted.json", "content": "{}"}),
    ("NotebookEdit", {"notebook_path": "/repo/_plans/x/_verify_state/s01.json"}),
])
def test_an_agent_file_write_into_verify_state_is_refused(tool, ti):
    r = run(tool, ti)
    assert r.returncode == 2 and "_verify_state" in r.stderr


@pytest.mark.parametrize("cmd", [
    f"echo '{{}}' >> {LED}",
    f"printf x > '{LED}'",
    f"cp /tmp/forged {LED}",
    f"mv /tmp/forged {LED}",
    f"rm {LED}",
    f"sed -i '' 's/open/fixed/' {LED}",
    f"cat /tmp/f | tee -a {LED}",
    f"git checkout HEAD~3 -- {LED}",
    f"python3 -c \"open('{LED}','a').write('x')\"",
    f"python3 - <<'EOF'\nopen('{LED}', 'a').write('x')\nEOF",
    f"cd /repo && echo x >> {LED}",
    "find _plans/x/_verify_state -name '*.ndjson' -delete",
    "d=_plans/x/_verify_state; echo x >> $d/s.findings.ndjson",
    # A security review of the guard itself: a READ command with a write
    # flag, and command/process substitution hidden inside a read.
    f"sort -o {LED} /tmp/forged",
    f"uniq /tmp/forged {LED}",
    f"sed -n 'w {LED}' /tmp/forged",
    f"find /tmp -fls {LED}",
    f"find /tmp -ok cp {{}} {LED} ;",
    f"git diff --output={LED}",
    f"git log --output {LED}",
    f"rg --pre /tmp/w.sh x {LED}",
    f"head -c 0 {LED} $(cp /tmp/f {LED})",
    f"cat `cp /tmp/f {LED}`",
    f"cat <(cp /tmp/f {LED})",
])
def test_an_agent_shell_write_into_verify_state_is_refused(cmd):
    assert run("Bash", {"command": cmd}).returncode == 2, cmd


@pytest.mark.parametrize("cmd", [
    "cat _plans/x/_verify_state/s01.feedback.md",
    f"tail -5 {LED} 2>/dev/null | cut -c1-200",
    f"grep -c high {LED} > /tmp/count.txt",
    f"jq -c . {LED} 2>&1 | head",
    "ls _plans/x/_verify_state/",
    f"git log --oneline -- {LED}",
    f"head -5 {LED}",
    "git commit -q -F - <<'EOF'\nfix: the gate now reads _verify_state/x.findings.ndjson\nEOF",
    "python3 run.py verify-run _plans/x s01",            # no _verify_state in the text
])
def test_an_agent_can_still_read_verify_state(cmd):
    r = run("Bash", {"command": cmd})
    assert r.returncode == 0, (cmd, r.stderr)


def test_the_main_session_is_never_blocked():
    """No agent_id = the orchestrator. It writes `accepted.json` for the owner."""
    assert run("Write", {"file_path": "/r/_plans/x/_verify_state/s01.accepted.json"},
               agent=False).returncode == 0
    assert run("Bash", {"command": f"echo x >> {LED}"}, agent=False).returncode == 0


def test_an_agent_write_elsewhere_passes():
    assert run("Write", {"file_path": "/repo/src/verify_state.py"}).returncode == 0
    assert run("Bash", {"command": "echo x >> notes.md"}).returncode == 0


def test_a_malformed_payload_never_blocks():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True)
    assert r.returncode == 0
