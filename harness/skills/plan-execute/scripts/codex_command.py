#!/usr/bin/env python3
"""Building the `codex exec` command a dispatched session actually runs.

Split out of run.py, which had grown 634 lines past its size baseline. This is
the INVOCATION layer: given a model and an effort already chosen, produce the
shell command, the worktree file set and the wrapper prompt.

Choosing the model and effort is a separate layer that still lives in run.py.
The executor-policy bar (_session_barred) and the refusal exception come from
ssot_policy, so nothing here has to import the orchestrator back.
"""
import json
import re
import shlex
from pathlib import Path

import egress
import findings_digest as fd
import parallel_contract as pc
import plan_scope as ps
import worktree as wt
from ssot_policy import UnroutableCodexSession, _session_barred
from verify_paths import _feedback_path




class UngatedFullAccessSession(UnroutableCodexSession):
    """`codex_shell.sandbox: danger-full-access` on a session no human gates.
    Raised at DISPATCH time as well as build time on purpose: a manifest is a
    file, and the one invariant protecting an unsandboxed agent must not be
    enforceable only by the tool that wrote it. Subclasses
    UnroutableCodexSession so every existing handler treats it as what it is —
    a loud BLOCKED + halt, never a silent fall-through to Claude."""


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CODEX_CORE_ENV = ("PATH", "HOME", "TMPDIR")


def _codex_shell_grant(session):
    """Normalise a session's declared `dispatch.codex_shell` into the capability
    grant its `codex exec` dispatch needs — or `None` for today's default.

    WHY THIS EXISTS. `codex exec --sandbox workspace-write` (the only shape this
    runner used to emit) denies three things a real plan session routinely needs,
    all MEASURED on codex-cli 0.145.0, 2026-07-29:

      * a write outside the repo workspace -> `operation not permitted`
      * any network access -> `CODEX_SANDBOX_NETWORK_DISABLED=1`
      * a nested `codex exec` / vendor CLI -> `failed to initialize in-process
        app-server client: Operation not permitted` (contract probe P2)

    A plan whose sessions need those could be launched under `--harness codex`
    and then failed session by session, which is the "portable in name only"
    outcome. So a session DECLARES what it needs and the dispatch grants it:

      writable_roots  ->  -c sandbox_workspace_write.writable_roots=[...]   (measured: grants the write)
      network         ->  -c sandbox_workspace_write.network_access=true    (measured: grants the network)
      sandbox         ->  --sandbox danger-full-access                      (the ONLY thing that grants nested dispatch)

    The first two are measured to survive `--ignore-user-config`, so the
    determinism that flag buys is preserved: the dispatch is still fully
    described by this one command line.

    Absent, empty, or all-default -> returns None and the emitted command stays
    identical to the current no-grant form (asserted by
    test_codex_shell_default_is_byte_identical)."""
    shell = ((session.get("dispatch") or {}).get("codex_shell")) or {}
    if not isinstance(shell, dict) or not shell:
        return None
    mode = (shell.get("sandbox") or "workspace-write").strip()
    roots = [
        str(Path(r).expanduser().resolve())
        for r in (shell.get("writable_roots") or [])
        if str(r).strip()
    ]
    network = bool(shell.get("network"))
    raw_env = shell.get("env_include") or []
    if not isinstance(raw_env, list) or any(
        not isinstance(name, str) or not _ENV_NAME_RE.fullmatch(name)
        for name in raw_env
    ):
        raise UnroutableCodexSession(
            "dispatch.codex_shell.env_include must be a list of environment variable names"
        )
    env_include = list(dict.fromkeys(name for name in raw_env if name not in _CODEX_CORE_ENV))
    if mode == "danger-full-access":
        # Full access already grants both, and carrying the narrower keys beside
        # it would leave two readings of one command. build_plan.py refuses the
        # combination; here we simply drop them and say so in the receipt.
        return {"sandbox": mode, "writable_roots": [], "network": True,
                "env_include": env_include, "supersedes": bool(roots) or network}
    if mode != "workspace-write":
        raise UnroutableCodexSession(
            f"dispatch.codex_shell.sandbox {mode!r} is not a codex sandbox mode this "
            "runner emits — use 'workspace-write' (the default) or 'danger-full-access'"
        )
    if not roots and not network and not env_include:
        return None
    return {"sandbox": mode, "writable_roots": roots, "network": network,
            "env_include": env_include, "supersedes": False}


def _assert_full_access_gated(sid, session, grant):
    """An UNSANDBOXED dispatched agent never happens without a human in the loop.

    `danger-full-access` disables the sandbox for every command the dispatched
    session runs. The only sessions allowed to ask for it are ones a human
    already has to clear before dispatch: barred work (`_session_barred` —
    linchpin / `irreversible_change` / `guards_irreversible`), or an explicit
    `requires_human_checkpoint`. Enforced at build time by build_plan.py AND
    here, because a gate enforced in one place is a gate that can be edited
    around (project memory: "gates that cannot fail")."""
    if not grant or grant.get("sandbox") != "danger-full-access":
        return
    if _session_barred(session):
        return
    if (session.get("dispatch") or {}).get("requires_human_checkpoint"):
        return
    raise UngatedFullAccessSession(
        f"{sid} asks for dispatch.codex_shell.sandbox 'danger-full-access' (an "
        "UNSANDBOXED dispatched agent) but no human gates it. Add "
        "dispatch.guards_irreversible: true (or requires_human_checkpoint with its "
        "checkpoint brief), or drop to 'workspace-write' plus the narrower "
        "writable_roots/network grants."
    )


# Codex silently cuts a project AGENTS.md at 32,768 bytes by default, and
# --ignore-user-config also skips the trust entry that would load a repo's own
# .codex/config.toml, so a dispatched session saw only its first 32 KiB
# (profile-a-brain ADR 0012: 127,734 bytes, sections 5-9 lost). A -c override
# survives --ignore-user-config, like the sandbox grants below.
_CODEX_PROJECT_DOC_MAX_BYTES = 262144


def _codex_cmd(model, effort, prompt_file, last_message_file, workdir=None, grant=None):
    """The exact foreground command the wrapper agent must run. Contract verified
    against codex-cli 0.144.1 (s04 hardening r1):
      * `cd <workdir> &&` prefix (s06, adversarial-review consensus) — pins codex
        exec's working tree to the EXACT root the egress guard scanned, instead of
        inheriting the wrapper agent's incidental cwd; scanned tree == shipped tree
        by construction.
      * `--sandbox workspace-write` EXPLICIT — executor dispatches must edit files;
        `codex exec` defaults to a READ-ONLY sandbox, so omitting the flag makes
        the run silently produce no edits while reporting success.
      * `--ignore-user-config --ignore-rules` — the dispatched model/sandbox is
        fully determined by THIS command, never by a stray $CODEX_HOME/config.toml
        or .rules file.
      * `-c project_doc_max_bytes=262144` — the whole project AGENTS.md reaches
        the session, not Codex's default first 32 KiB (see the constant).
      * `-o <file>` (--output-last-message) — the LAST agent message lands in a
        file. We deliberately avoid `--json --output-schema`: open codex bug
        #19816 makes it emit schema-valid INTERMEDIATE messages, so first-match
        stdout parsing intermittently returns a wrong early message; `-o` is
        last-message by definition.
      * `--json` with stdout redirected to `<file>.events.jsonl` — the event
        stream carries the `turn.completed` token usage. Nothing parses it for the
        message: `-o` stays the only source (probed on codex-cli 0.158.0, 2026-09-30:
        the `-o` file is byte-identical with and without `--json`).
      * `- < prompt_file` — the session prompt arrives on stdin, verbatim.
      * `grant` (optional, from `_codex_shell_grant`) — the session's DECLARED
        capability grant. `None` uses the default workspace-write sandbox;
        a grant widens the sandbox exactly as far
        as the session said it needs and no further."""
    eff = f" -c model_reasoning_effort={shlex.quote(effort)}" if effort else ""
    # Archive stale receipts before launch: never relay a prior attempt's result.
    # 2026-09-05: the desktop shell rejects rm -f. A unique recovery directory
    # preserves evidence (including symlinks) without deleting or overwriting it.
    receipt = shlex.quote(last_message_file)
    archive_template = shlex.quote(last_message_file + ".stale.XXXXXX")
    cd = f"cd {shlex.quote(str(workdir))} && " if workdir else ""
    sandbox = (grant or {}).get("sandbox") or "workspace-write"
    caps = ""
    if (grant or {}).get("writable_roots"):
        # json.dumps, never f'"{r}"': a `"` inside a declared root closed the TOML
        # string and smuggled extra roots past the receipt (one declared, three
        # granted). A JSON string array is a valid TOML array.
        toml_arr = json.dumps(grant["writable_roots"], separators=(",", ":"), ensure_ascii=False)
        caps += " -c " + shlex.quote(f"sandbox_workspace_write.writable_roots={toml_arr}")
    if sandbox == "workspace-write" and (grant or {}).get("network"):
        caps += " -c " + shlex.quote("sandbox_workspace_write.network_access=true")
    if (grant or {}).get("env_include"):
        env_names = [*_CODEX_CORE_ENV, *grant["env_include"]]
        toml_arr = "[" + ",".join(f'"{name}"' for name in env_names) + "]"
        caps += " -c " + shlex.quote("shell_environment_policy.inherit=all")
        caps += " -c " + shlex.quote("shell_environment_policy.ignore_default_excludes=true")
        caps += " -c " + shlex.quote(f"shell_environment_policy.include_only={toml_arr}")
    return (
        f"{cd}if [ -e {receipt} ] || [ -L {receipt} ]; then "
        f"codex_receipt_archive=$(mktemp -d {archive_template}) && "
        f'mv {receipt} "$codex_receipt_archive/last-message.txt"; fi && '
        f"codex exec --json --ignore-user-config --ignore-rules --sandbox {sandbox}{caps} "
        f"-c project_doc_max_bytes={_CODEX_PROJECT_DOC_MAX_BYTES} "
        f"-m {shlex.quote(model)}{eff} "
        f"-o {shlex.quote(last_message_file)} - < {shlex.quote(prompt_file)} "
        f"> {shlex.quote(last_message_file + '.events.jsonl')}"
    )


def _refuse_restricted_text(texts, opted_in):
    """Refuse the dispatch when prompt text from OUTSIDE the scanned tree holds a
    secret. `texts` maps a source name to its text; empty texts are skipped. The
    per-repo egress_opt_ins entry clears it, exactly as it clears the tree scan."""
    if opted_in:
        return
    for name, text in texts.items():
        hit = egress._text_hit(text, name) if text else None
        if hit:
            raise UnroutableCodexSession(
                f"data_sensitivity_guard egress: restricted content in the {name} this "
                f"Codex prompt would carry ({hit}) — DO-NOT-SEND to Codex. Remove it "
                f"from that file under the plan directory, or add an unexpired per-repo "
                f"egress_opt_ins entry in model-routing.yaml"
            )


def _codex_worktree_files(plan_dir, session, prompt_file, worktree_path, stamp,
                          opted_in=False):
    """Return the prompt path, runtime directory and worktree metadata.

    A Codex dispatch runs with ``workspace-write`` rooted at the checkout it is
    isolated into — its member worktree (PL-01) or, for a plain session under an
    isolated PLAN, the plan worktree (ISO-02). Its prompt and ``-o`` receipt
    therefore have to live under that checkout too; putting either under the
    shared plan directory would be an invisible sandbox escape that fails only
    after the model starts.

    The Codex lane is the ONE place where containment is mechanical rather than
    advisory: ``_codex_cmd`` emits a real ``cd <workdir> &&`` prefix, so the
    dispatched process genuinely starts in the worktree.
    """
    sid = session["id"]
    source_prompt = Path(prompt_file).read_text()
    digest = fd.digest(plan_dir, sid)
    feedback = _feedback_path(plan_dir, sid)
    feedback_text = feedback.read_text() if feedback.is_file() else ""
    _refuse_restricted_text({"findings digest": digest,
                             f"rework feedback file {feedback.name}": feedback_text},
                            opted_in)
    if feedback_text:
        source_prompt += (
            "\n\n--- BEGIN CURRENT VERIFICATION FEEDBACK ---\n"
            + feedback_text
            + "\n--- END CURRENT VERIFICATION FEEDBACK ---\n"
        )

    if not worktree_path:
        runtime_dir = Path(plan_dir) / "_codex"
        runtime_dir.mkdir(exist_ok=True)
        if not feedback_text and not digest:
            return str(prompt_file), runtime_dir, {}
        prompt_copy = runtime_dir / f"{sid}.{stamp}.prompt.md"
        prompt_copy.write_text(digest + source_prompt)
        return str(prompt_copy), runtime_dir, {}

    group = wt.group_of(session)
    member = wt.member_path(plan_dir, sid)
    if member and not (wt.load_state(plan_dir, group) or {}).get("base_ref"):
        raise wt.WorktreeError(f"no pinned worktree state for isolated Codex session {sid}")
    preamble = ps.dispatch_preamble(plan_dir, session, sid, member)
    if not preamble:
        raise wt.WorktreeError(f"no pinned worktree state for isolated Codex session {sid}")
    worktree = Path(worktree_path)
    runtime_dir = worktree / wt.WORKTREE_DIRNAME / "codex"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    prompt_copy = runtime_dir / f"{sid}.{stamp}.prompt.md"
    prompt_copy.write_text(digest + preamble + source_prompt)
    meta = {"worktree": str(worktree), "isolation": pc.ISOLATION_WORKTREE,
            "worktree_branch": ps.member_branch(plan_dir, group, sid) if member
            else ps.plan_branch(plan_dir)}
    if member:
        meta["worktree_group"] = group
    return str(prompt_copy), runtime_dir, meta


def _codex_no_worktree_prompt(plan_dir, sid, prompt_file, stamp, opted_in=False):
    """The prompt path for the SECOND Codex funnel's own no-worktree branch
    (`_resolve_codex_session_spec` in run.py, when a session has no worktree) —
    the one path that never calls `_codex_worktree_files` at all. Mirrors that
    function's no-worktree case: the original file, byte for byte, unless
    earlier sessions raised findings worth surfacing, in which case a
    digest-prefixed copy. Deliberately does NOT add feedback-text handling —
    this funnel never did, and that gap is out of scope here."""
    digest = fd.digest(plan_dir, sid)
    _refuse_restricted_text({"findings digest": digest}, opted_in)
    if not digest:
        return str(prompt_file)
    runtime_dir = Path(plan_dir) / "_codex"
    runtime_dir.mkdir(exist_ok=True)
    prompt_copy = runtime_dir / f"{sid}.{stamp}.prompt.md"
    prompt_copy.write_text(digest + Path(prompt_file).read_text())
    return str(prompt_copy)


def _codex_wrapper_prompt(sid, cmd, last_message_file):
    """The full prompt for the Claude wrapper agent (this IS the Task prompt for a
    Codex-backed member). Foreground/fresh contract — deliberately NOT codex:rescue,
    which calls `task`, may go background, and returns nothing on failure."""
    return f"""You are the Codex dispatch wrapper for plan-execute session {sid}. Do NOT do the session's work yourself and do NOT edit any file — your ONLY job is to run the Codex CLI once, in the FOREGROUND, and relay its final message.

1. Run EXACTLY this command via the Bash tool (foreground — never run_in_background; set timeout 600000):

   {cmd}

   Do not alter the -m model, the --sandbox mode, or any other flag. Never use `codex exec resume`.

2. If the command fails BEFORE doing any work — non-zero exit with a model-access error (entitlement/paywall, model not found/unavailable, CLI version rejects the model, quota exhausted) — do NOT retry and do NOT produce a closeout. Your entire final message must be one line:
   CODEX-DISPATCH-FAILED: <signal> — <first error line>
   where <signal> is one of: entitlement | unavailable | cli_version_rejected | quota_exhausted.

3. If the command times out, relay whatever {last_message_file} contains (if anything) and append the single line: CODEX-TIMEOUT.

4. Otherwise read {last_message_file} (Codex's LAST agent message, written via -o — always this file, never an earlier intermediate stdout message) and reply with its full contents VERBATIM as your final message. It should end with the <plan-execute-closeout> block the session prompt requires. NEVER write, repair, or synthesize a closeout yourself: if the file is missing or carries no closeout block, relay what exists and append the single line: CODEX-NO-CLOSEOUT."""


def _codex_wt_path(plan_dir, worktrees, sid):
    """The worktree a Codex session runs in, or None when the plan is not
    isolated — REFUSING first when it claims one that is gone. `ps.plan_worktree`
    alone returns None for BOTH "never isolated" and "claimed but vanished", so
    without `require_live` this lane fell through to `workdir=<shared checkout>`
    for a workspace-write process — exactly the collision the Claude lane's
    `dispatch_preamble` refuses. Raised as UnroutableCodexSession so the batch
    records it per-session instead of dying."""
    member = worktrees.get(sid)
    if member:
        return member
    try:
        ps.require_live(plan_dir)
    except wt.WorktreeError as e:
        raise UnroutableCodexSession(str(e)) from e
    return ps.plan_worktree(plan_dir)
