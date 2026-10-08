"""LND-01 / ISO — the argparse surface for the ship-*, verify-*, worktree-* and
land-* subcommands, lifted out of ``run.py``.

Pure declaration, no behaviour: every one of these builds a subparser and
returns. It lives here because ``run.py`` is grandfathered in
``.file-size-exceptions`` at an exact LOC and this repo's ``pre-commit`` runs
``check_file_sizes.py --staged``, which BLOCKS any commit that grows a file past
its baseline. The ratchet was right — adding the land subcommands pointed at a
better structure, and extracting the parser declarations left ``run.py`` well
under its bound instead of bumping it.

``add_dir`` moved with them because every parser block in ``run.py`` calls it;
``run.py`` imports it back from here so nothing else had to change.
"""

import plan_version_gate as pvg

# Moved here WITH `add_dir`, which is its only consumer: the flag's `choices` and
# the function that declares it cannot live in two files without one importing
# the other, and `run.py` importing this module means this module must not import
# `run.py`. `run.py` reads it back from here.
HARNESSES = ("claude", "codex")


def add_dir(sp, optional=True):
    # QW-02: plan_dir is optional on every subcommand (not just `status
    # --all`) — omit it to default to the CANONICAL/latest plan in
    # _plans_index.md (see `_resolve_bare_invocation`).
    if optional:
        sp.add_argument("plan_dir", nargs="?", help="Plan directory (or PLAN.html path)")
    else:
        sp.add_argument("plan_dir", help="Plan directory (or PLAN.html path)")
    # Global flag: only `begin` acts on it; other subcommands accept-and-ignore.
    sp.add_argument(
        "--unsafe-lock",
        action="store_true",
        help="begin: lock even on a networked/sync FS where the lock is "
        "unreliable. Accepted and ignored by other subcommands.",
    )
    # Which ORCHESTRATOR is driving this run (CP-02). Only `plan` and `begin`
    # act on it; `status`/`checkpoint` (and the ship-*/verify-*/land-*
    # subcommands) accept-and-ignore it so a Codex orchestrator can pass it
    # uniformly. NEVER auto-detected and never persisted — see run.py's
    # HARNESS MODE block.
    sp.add_argument(
        "--harness",
        choices=HARNESSES,
        default="claude",
        help="Which orchestrator is running this loop. `claude` (default) is "
        "unchanged Task-tool dispatch. `codex` emits a runnable `codex exec` "
        "command per session for a Codex orchestrator to run itself.",
    )


def _add_plan_parser(sub):
    """`plan` -- moved out of run.py's `_add_core_parsers` for the size ratchet."""
    s = sub.add_parser("plan")
    add_dir(s)
    s.add_argument("--resume", action="store_true")
    s.add_argument("--session", default=None)
    s.add_argument(
        "--auto",
        action="store_true",
        help="Autonomous mode: the orchestrator self-drives through verify-rework "
        "loops and `dispatch_next:false` hints, halting only on human checkpoints, "
        "hard blockers, and stale-deploy confirms. Echoed in the action so the "
        "orchestrator (and run.ndjson) record the posture; human gates stay sacrosanct.",
    )
    # A file, never an argv string: owner prose with an apostrophe breaks shell
    # quoting and a leading '-' breaks argparse.
    g = s.add_mutually_exclusive_group()
    g.add_argument(
        "--answer-file",
        default=None,
        help="--resume past a human checkpoint: a UTF-8 file holding the owner's "
        "answer verbatim. Recorded with the plan and put at the top of the "
        "resumed session's prompt.",
    )
    g.add_argument(
        "--no-answer",
        action="store_true",
        help="--resume past a human checkpoint: the owner explicitly declined to "
        "answer. NOT agreement with any option in the brief.",
    )


def _add_begin_parser(sub):
    """`begin` — moved out of run.py's `_add_core_parsers` for the size ratchet."""
    s = sub.add_parser("begin")
    add_dir(s)
    s.add_argument("--sessions", nargs="+", required=True)
    s.add_argument(
        "--resume",
        action="store_true",
        help="Dispatch even though the plan is HALTED — the same deliberate override "
        "`plan --resume` is. Without it, `begin` refuses on a halted plan.",
    )
    s.add_argument(
        "--concurrent",
        action="store_true",
        help="REG-02: proceed even though another plan that would SHARE this "
        "plan's working tree is registry-active in this repo. Without it, `begin` "
        "refuses and names them. Two plans at or above the isolation gate share no "
        "tree and need no override. Logs concurrent_begin_override to run.ndjson.",
    )
    s.add_argument(
        "--isolate", action="store_true",
        help="ISO-01: force plan-level git isolation on for this run, whatever the "
        "manifest's plan_schema_version says (the gate is >= "
        f"{pvg.ISOLATION_MIN_SCHEMA}). Recorded in run.ndjson with the actual version.",
    )
    s.add_argument(
        "--no-isolate", action="store_true",
        help="Force plan-level git isolation OFF for this run. WINS over --isolate "
        "and over a manifest stamped at or above the gate.",
    )
    s.add_argument(
        "--no-route-at-dispatch", action="store_true",
        help="Schema v8: turn route-at-dispatch OFF for this run (env "
        "PLAN_EXECUTE_ROUTE_AT_DISPATCH=0 does the same). Only authored cells dispatch; "
        "an unpinned v8 session is refused, never run on the orchestrator's model. "
        "Recorded in plan state and wins over any frozen cell.",
    )


def _add_shipping_parsers(sub):
    """the ship-* subcommands."""
    # Shipping subcommands.
    s = sub.add_parser("ship-begin")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the shipping plan without locking or executing.",
    )
    s.add_argument(
        "--resume",
        action="store_true",
        help="Ship past a human checkpoint / a stale-auth confirm (operator OK).",
    )
    s.add_argument(
        "--confirm-stale",
        action="store_true",
        help="Proceed with a deploy whose authorization digest went stale.",
    )
    s = sub.add_parser("ship-record")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--step", required=True)
    s.add_argument("--status", required=True, choices=["done", "failed"])
    s.add_argument("--result-file", default=None)
    s.add_argument(
        "--lease-token", default=None,
        help="The lease_token ship-begin returned with this directive. Optional; when "
             "given it must match the lease this plan holds, or the record refuses.",
    )
    s = sub.add_parser("ship-run")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--step", required=True)
    s = sub.add_parser("ship-finalize")
    add_dir(s)
    s.add_argument("--session", required=True)
    s = sub.add_parser("ship-release")
    add_dir(s)
    s = sub.add_parser("ship-status")
    add_dir(s)
    s.add_argument("--session", required=True)
    s = sub.add_parser("ship-simulate")
    add_dir(s)
    s.add_argument("--session", required=True)


def _add_verify_parsers(sub):
    """the verify-* subcommands (session verification gates)."""
    # Verify subcommands (session verification gates).
    s = sub.add_parser("verify-begin")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--dry-run", action="store_true",
                   help="Compute the gate plan without running real gates.")
    s.add_argument("--resume", action="store_true",
                   help="Resume a verify sub-loop after a crash (DOING + pending state).")
    s = sub.add_parser("verify-record")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--gate", required=True)
    s.add_argument("--status", required=True, choices=["done", "failed"])
    s.add_argument("--result-file", default=None)
    s = sub.add_parser("verify-run")
    add_dir(s)
    s.add_argument("--session", required=True)
    s.add_argument("--gate", required=True)
    s = sub.add_parser("verify-finalize")
    add_dir(s)
    s.add_argument("--session", required=True)
    s = sub.add_parser("verify-status")
    add_dir(s)
    s.add_argument("--session", required=True)
    s = sub.add_parser("verify-simulate")
    add_dir(s)
    s.add_argument("--session", required=True)
    # PL-01 — worktree isolation. `worktree-status` is read-only; cleanup is the
    # EXPLICIT, LAST step of a group's lifecycle (contract §4) and has no
    # automatic caller anywhere in this file, on purpose.
    s = sub.add_parser("worktree-status")
    add_dir(s)
    s.add_argument("--group", required=True)
    s = sub.add_parser("worktree-cleanup")
    add_dir(s)
    s.add_argument("--group", required=True)
    s.add_argument(
        "--force", action="store_true",
        help="remove a member worktree even though it still holds uncommitted, untracked "
             "or locally-created ignored content. The branch is still deleted only with "
             "`git branch -d`, never -D, so unmerged commits always survive.",
    )

def _add_land_parsers(sub):
    """LND-01 — the land stage (contract §4/§5).

    `land` is idempotent and resumable, so `land-resume` — the name §4.6's
    conflict brief prints — is deliberately the SAME entry point rather than a
    second path that could drift from it.
    """
    for name in ("land", "land-resume", "land-status"):
        add_dir(sub.add_parser(
            name, help="the land stage: sync, re-gate, human ack, serial merge"))
    s = sub.add_parser("finish", help="the last step of every plan: commit its leftover "
                       "record, report leftovers, fast-forward the checkout when safe, "
                       "report CI (references/finish-contract.md)")
    add_dir(s)
    s.add_argument("--no-wait", action="store_true",
                   help="look at CI once instead of polling for up to 540 s")
    s.add_argument("--apply", action="store_true",
                   help="run a plan that finished in report-only mode for real")
    s = sub.add_parser("land-ack", help="record the human land decision (contract §5.1)")
    add_dir(s)
    s.add_argument("--note", default=None, help="why you approved THIS candidate")
    # The disposition vocabulary is IMPORTED, never re-typed: `--decision` here and
    # `ack-checkpoint --decision` one scope over are the same two words, and this
    # module is already downstream of `land_gate` through `run.py`'s own imports.
    import land_gate as lgt  # noqa: PLC0415 — one call, and this file stays declaration-only

    s = sub.add_parser("land-verify",
                       help="record the HUMAN's on-box disposition for a land re-gate "
                            "that NO MODEL may run")
    add_dir(s)
    s.add_argument("--decision", required=True, choices=list(lgt.DECISIONS),
                   help="what you found in the merged tree. `verified-on-box`: you "
                        "reviewed it and it is sound — recorded as its own gate outcome, "
                        "never as a pass. `blocked`: it is not, and nothing ships.")
    s.add_argument("--note", default=None,
                   help="what you checked, or why it is blocked")
    s = sub.add_parser("land-record", help="record a SKILL-kind land re-gate result")
    add_dir(s)
    s.add_argument("--gate", required=True)
    s.add_argument("--status", required=True, choices=["passed", "failed"])
    s.add_argument("--findings-count", type=int, default=None)
    s.add_argument("--result-file", default=None,
                   help="an artifact a PASS can show. Checked to exist and be non-empty — "
                        "the same refusal the evidence gate applies.")
