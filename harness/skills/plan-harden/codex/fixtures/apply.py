#!/usr/bin/env python3
"""Phase 4 apply + backport, reused verbatim as the fixture smoke test's stand-in
for what an interactive Codex orchestrator does by hand (Edit spec.json, rebuild).
Implements SKILL.md §4.3-4.5:

  1. Parse the real adversarial-review Findings output (§2.2's seam: `blocking |
     material | minor` -> plan-harden's 🔴 | 🟡 | 🟣) plus the model-lint flags.
  2. Backport the hardening into spec.json's session `prompt` field (the rebuild
     source of truth) — never hand-edit the generated `sessions/*.prompt.md`
     directly; that's how a `[HARDENED:...]` tag would get silently wiped by a
     later `--rebuild` (the exact bug this discipline exists to prevent).
  3. Rebuild the LIVE plan dir for real (`build_plan.py --rebuild --preserve-state`)
     so `sessions/*.prompt.md` + `PLAN.html` + `manifest.json` all reflect it.
  4. Verify the round-trip: rebuild AGAIN to a temp dir (same basename — the
     generated text embeds the plan dir's name) and diff every prompt body
     byte-for-byte. A rebuild is idempotent by construction once spec.json is
     the source of truth; this proves it, rather than assuming it.

Usage: apply.py <plan-dir> <findings-file> <lint-json-file> <build_plan.py-path>
Prints a JSON summary: {"blocking": n, "material": n, "minor": n, "session_hardened": "sNN",
                          "roundtrip_ok": bool}
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    plan_dir, findings_file, lint_file, build_plan_py = sys.argv[1:5]
    plan_dir = Path(plan_dir)

    findings_text = Path(findings_file).read_text(errors="replace")
    counts = {label: len(re.findall(rf"### \[{label}\]", findings_text)) for label in
              ("blocking", "material", "minor")}
    lint = json.loads(Path(lint_file).read_text())

    # § 2.2 seam: blocking->🔴, material->🟡, minor->🟣. Applied hardening target:
    # the fixture's model-lint flag names s02 directly, so that is where the
    # combined review+lint hardening lands — a real, non-synthetic edit target.
    target_session = lint["flags"][0]["session"] if lint["flags"] else "s02"

    hardening_lines = [
        f"[HARDENED:codex-adversarial-r1] Reviewed by adversarial-review "
        f"(gpt-5.6-sol · reasoning_effort=xhigh recommended; this fixture ran on "
        f"a fallback rung — see harden-run.txt) — "
        f"{counts['blocking']} blocking / {counts['material']} material / "
        f"{counts['minor']} minor finding(s) in the Findings section.",
    ]
    for flag in lint["flags"]:
        if flag["session"] != target_session:
            continue
        hardening_lines.append(
            f"[HARDENED:codex-model-lint] {flag['recommendation']} — "
            f"declare the resolved model/effort explicitly, or record why "
            f"{flag['declared']} was the deliberate choice."
        )

    # §4.4 — backport FIRST: spec.json is the rebuild source of truth. Editing
    # the generated sessions/*.prompt.md directly and only "later" syncing
    # spec.json is exactly the ordering that has bitten this repo before (a
    # rebuild silently wiped hardenings applied only to generated files) — so
    # this port never lets the live prompt and spec.json disagree, even
    # momentarily.
    spec_path = plan_dir / "spec.json"
    spec = json.loads(spec_path.read_text())
    for s in spec["sessions"]:
        if s["id"] == target_session:
            s["prompt"] = s["prompt"].rstrip("\n") + "\n\n" + "\n".join(hardening_lines)
            break
    else:
        raise SystemExit(f"session {target_session} not found in spec.json")
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")

    # §4.3 — apply for real: rebuild the LIVE plan dir so sessions/*.prompt.md +
    # PLAN.html + manifest.json all reflect the backported spec.json.
    r_live = subprocess.run(
        [sys.executable, build_plan_py, str(spec_path), str(plan_dir),
         "--rebuild", "--preserve-state"],
        capture_output=True, text=True,
    )
    if r_live.returncode != 0:
        print(json.dumps({**counts, "session_hardened": target_session,
                           "roundtrip_ok": False, "roundtrip_mismatches": ["LIVE REBUILD FAILED"],
                           "rebuild_stderr_tail": r_live.stderr[-500:]}, indent=2))
        return

    # §4.5 — round-trip verification: rebuild AGAIN, to a TEMP dir (same
    # basename, since the generated text embeds the plan dir's name), and
    # diff every prompt body byte-for-byte against what's now live.
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / plan_dir.name
        r = subprocess.run(
            [sys.executable, build_plan_py, str(spec_path), str(out)],
            capture_output=True, text=True,
        )
        roundtrip_ok = r.returncode == 0
        mismatches = []
        if roundtrip_ok:
            for p in sorted((plan_dir / "sessions").glob("*.prompt.md")):
                rebuilt_p = out / "sessions" / p.name
                if not rebuilt_p.exists() or rebuilt_p.read_text() != p.read_text():
                    mismatches.append(p.name)
            roundtrip_ok = not mismatches

    print(json.dumps({
        **counts,
        "session_hardened": target_session,
        "roundtrip_ok": roundtrip_ok,
        "roundtrip_mismatches": mismatches if not roundtrip_ok else [],
        "rebuild_stderr_tail": r.stderr[-500:] if not roundtrip_ok else "",
    }, indent=2))


if __name__ == "__main__":
    main()
