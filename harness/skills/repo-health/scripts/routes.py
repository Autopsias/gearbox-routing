#!/usr/bin/env python3
"""Fix routes — for a failing check, the dispatch that actually fixes it.

Every route here is one the ORCHESTRATING AGENT can fire on its own. That is not
a style preference: probed live, two subagents whose definitions
declare `SlashCommand` in their frontmatter (`digdeep`, `linting-fixer`) were
spawned and NEITHER was given the tool. A route that says "/code-quality --fix"
and nothing else is therefore operator homework wearing a command's clothes — it
looks actionable on the dashboard and never runs.

So a command-backed route names the command FILE. A command file is a prompt:
an agent told to read it and follow everything after the frontmatter, with
$ARGUMENTS substituted, behaves as the command (probed the same day against
commands/code-quality.md --check). `dispatch` says which of the three kinds a
row is, and `operator` is non-empty only for the part a human must genuinely do.

`routed` says whether anybody DECIDED. It is True for every entry in ROUTES —
including the seven whose decision was "no automation fits, this is the human's"
— and False only for UNKNOWN, a check id with no entry at all. Those two used to
be indistinguishable (both `dispatch: operator`, both an empty `agent`), so
SKILL.md told the reader to go write a route for `hyg.todo-density`, which
already carries the only answer it will ever have.

Data + pure functions, with exactly one read of the world: a route resolves its
command file against the deploy target and reports what it found there — whether
the file exists, and whether it still declares the flags the route passes. That
read has to happen here, because reporting "not dispatchable on this machine" is
the whole job. Nothing else touches the filesystem; health.py owns the scorecard
I/O and imports this, never the other way round.
"""
import re
from functools import lru_cache
from pathlib import Path

from common import typed, typed_items
# The table itself lives in route_table.py (a pure function of the builders
# defined below) so this file stays under the house 500-line limit; see that
# file's docstring for why it is not the other way round. Safe to import at the
# top: route_table.py takes the builders as ARGUMENTS rather than importing
# them from here, so there is no cycle to order around.
from route_table import build_routes

# The commands live in the user's Claude config directory, not in this skill —
# they are harness surface, and no skill-relative path reaches them. Resolved at
# emit time and reported as found/not-found rather than assumed, so a route that
# cannot be dispatched says so on the row instead of failing in the subagent.
COMMANDS = Path.home() / ".claude" / "commands"

ARG_HINT = re.compile(r"^argument-hint:\s*(.+)$", re.M)
FLAG = re.compile(r"--[a-z][a-z0-9-]*")


@lru_cache(maxsize=None)
def declared_flags(md):
    """The flags a command FILE declares, from its `argument-hint:` frontmatter.

    Empty set = could not tell (absent file, no `argument-hint:`, or a hint with
    no flags in it). Deliberately not distinguished from "declares nothing",
    because both answers mean the same thing to a caller: this file cannot
    confirm any flag, so nothing may be waved through against it.

    `argument-hint:` is the declared contract and the one robustly parseable
    list in these files. A whole-file grep is NOT usable — test-orchestrate.md
    carries the prose line "pytest --fix or similar" in its guard rails, so a
    document-wide scan would accept the exact bug this check exists to catch.
    """
    try:
        hint = ARG_HINT.search(md.read_text())
    except OSError:
        return frozenset()
    return frozenset(FLAG.findall(hint.group(1))) if hint else frozenset()


def flag_check(f, args):
    """Why `args`' flags cannot be trusted against command file `f` — "" if they can.

    This runs against the DEPLOYED file, which is the copy a dispatch actually
    reaches. The suite checks this repo's `commands/<name>.md` because a clean CI
    checkout has no deploy target; that proves the source is right, never that
    the deployed copy still is. A stale deployed file silently demotes `--fix-all`
    to a positional argument, and the subagent budget is spent changing nothing —
    so the row says so instead of pretending to be dispatchable.

    Empty means VERIFIED, not unexamined: every non-clean state, including "could
    not look", returns a sentence. So EXISTENCE IS ESTABLISHED FIRST — "no flags
    passed, nothing to verify" used to return "" ahead of it, which made a
    flagless route to a file that is not on the machine read as verified clean.
    """
    if not f.is_file():
        return f"cannot verify this route: {f} is not on this machine"
    passed = sorted(set(FLAG.findall(args)))
    if not passed:
        return ""                      # file found, nothing claimed against it
    named = ", ".join(passed)
    declared = declared_flags(f)
    if not declared:
        return (f"cannot verify {named}: {f.name} declares no parseable "
                "`argument-hint:` line")
    undeclared = sorted(set(passed) - declared)
    if undeclared:
        return (f"{f.name} does not declare " + ", ".join(undeclared)
                + " (it declares " + ", ".join(sorted(declared))
                + ") — the argument would land on a positional and the run would "
                  "change nothing")
    return ""

# ARGUMENTS ARE PART OF THE ROUTE, and they are checked. Naming a real command
# file is only half of a dispatch: four routes here once passed `--fix` to
# commands that define no such flag, so the argument landed on a positional
# (test-orchestrate reads it as `test_scope`; ci-orchestrate as the issue
# description, staying in non-fix mode) and the run burned a subagent budget
# changing nothing. Every flag below must appear in the target's
# `argument-hint:` frontmatter — test_every_route_passes_flags_the_target_file_defines
# reads that line out of THIS repo's commands/ and fails the suite otherwise,
# and flag_check() above reads it again at emit time out of the DEPLOYED file
# the dispatch will really reach, so a stale deploy is reported on the row
# rather than dispatched into. Where no fix-capable flag exists at all, the row
# is an operator row that says so; it never pretends.

# The translation notes are not decoration: the live probe followed
# commands/code-quality.md end to end and hit all three of them. The command
# files were written for the main session, where `Task`, `SlashCommand` and
# `AskUserQuestion` exist; a spawned agent has none of those names. Without the
# notes the dispatched agent stalls at the first chaining step.
FOLLOW = ("Read {file} in full and follow everything after its YAML frontmatter "
          "as your instructions, with $ARGUMENTS = `{args}`. Translation notes "
          "(probed): where the file says `Task(subagent_type=...)`, "
          "use the Agent tool; you have NO SlashCommand and NO AskUserQuestion "
          "tool, so skip any chaining or resume step that needs one and say so "
          "in your report instead of stalling.")

# EVERY path a prompt names is resolved HERE, against this file, before the
# prompt is emitted. A subagent is spawned with the prompt string and nothing
# else — it has not read SKILL.md, which is the only place `<skill>` is defined —
# so a `<skill>/...` that survives into a prompt sends it to a file that is not
# there, and it skips or stalls on the first instruction it was given. That is
# exactly what sec.dangerous-workflow shipped. Deriving from `__file__` rather
# than the deploy target also means a source checkout points at the copy it is
# actually running. test_no_row_emits_an_unresolved_placeholder_or_a_path_that_
# is_not_there opens every one of them.
SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"

# The three speed checks POINT at SKILL.md's ladder instead of restating it —
# a second copy of five rungs in three prompts is three things to drift.
# test_the_speed_routes_point_at_a_section_that_exists fails if the heading moves.
LADDER = (f"Read the '## Test & CI speed — the ladder' section of {SKILL_MD} in "
          "full and follow it in order — never skip a rung, and stop at the "
          "first one that fixes this: a faster suite that flakes is worse than "
          "a slow one. ")


def cmd(name, args, agent="general-purpose", operator=""):
    """A route backed by a slash-command FILE, dispatched as a subagent prompt."""
    f = COMMANDS / f"{name}.md"
    return {"dispatch": "command-file", "routed": True, "agent": agent,
            "command": f"/{name}",
            "command_file": str(f), "command_file_found": f.is_file(),
            "flag_check": flag_check(f, args),
            "prompt": FOLLOW.format(file=f, args=args), "operator": operator}


def agent(name, prompt, operator=""):
    """A route backed by an installed subagent, dispatched by name."""
    return {"dispatch": "agent", "routed": True, "agent": name, "command": "",
            "command_file": "", "command_file_found": None, "flag_check": "",
            "prompt": prompt, "operator": operator}


def operator(why):
    """The residue: a check whose fix is DECIDED to be a human's, and why.

    `routed` is True here exactly as it is on the other two: routing something
    to the operator is a decision somebody made and can be argued with. Only
    UNKNOWN — nobody has decided yet — is False. Those two used to emit rows of
    the same shape, so SKILL.md told the agent to go write a route for
    `hyg.todo-density`, which already carries the only answer it will ever have.
    """
    return {"dispatch": "operator", "routed": True, "agent": "", "command": "",
            "command_file": "", "command_file_found": None, "flag_check": "",
            "prompt": "", "operator": why}


# `routes.ROUTES` still resolves as an attribute of THIS module — every caller
# and test reaches it that way, unchanged by the table moving to its own file.
ROUTES = build_routes(cmd, agent, operator, SKILL_DIR, LADDER)

# The ONLY row with `routed: False` — nobody has decided who fixes this check.
# Built from operator() so a field added there cannot go missing here, then
# flipped: it is the same SHAPE as a deliberate operator row and the opposite
# answer, which is precisely why the flag has to be read instead of the shape.
UNKNOWN = dict(operator("no fix route is defined for this check id — decide the "
                        "route and add it to scripts/routes.py ROUTES"),
               routed=False)

# A check that was never measured. NOT a fix route — there is nothing to fix
# yet, because nobody looked. It gets a row of its own because the alternative
# is what this file exists to remove: `fix-routes` filtered on status ==
# "fail", a `pending` blocking check printed nothing, and SKILL.md reads no
# output as "nothing blocking is failing". Unmeasured is not passing — and that
# is true of an advisory row under `--advisory` too, so the wording is
# tier-neutral and the row carries its own `tier`.
# The probe to run is already on the row — the collector puts it in `detail`.
PROBE = {"dispatch": "probe", "routed": True, "agent": "", "command": "",
         "command_file": "",
         "command_file_found": None, "flag_check": "", "operator": "",
         "prompt": "This check has never been measured — `pending` is "
                   "not `pass`. Run the probe named in this row's `detail` from "
                   "the repo root (SKILL.md step 2 has the pass/fail rule), "
                   "record the answer with `health.py set <id> "
                   "<pass|warn|fail|na> \"<one-line detail>\" --repo <repo>`, "
                   "then re-run fix-routes: if it comes back `fail` it will "
                   "arrive with its fix route attached."}


def unreadable(files):
    """A `pending` check whose DETAIL is a "could not read X" DIAGNOSIS, not an
    unrun probe command — routed here instead of PROBE, which told the agent to
    run a probe that does not exist for these DETERMINISTIC checks (a tracked
    workflow read through `common.unmeasured`/`pending_if_unread`, never a
    hand-run tool). Reproduced live (review): an unlinked tracked
    workflow left three BLOCKING security checks `pending` with this exact
    diagnosis, `fix_rows` sent all three to PROBE anyway, and the checks' real
    fix route (ci-infrastructure-builder, for the workflow content those checks
    actually judge) never fired.

    NOT `ROUTES[cid]` with the diagnosis appended: that route assumes an ANSWER
    was read — "add a permissions block to every workflow LACKING one" presumes
    the workflow's permissions block was seen and found missing, and this check
    has no such answer, because the one file that would prove it either way
    never opened. Dispatching the check's normal fixer at a file it ALSO cannot
    open either fails outright or has it invent content for bytes nobody read —
    the exact lie this whole skill exists to stop. The honest fix is upstream of
    every check that depends on this file: get the bytes back, then re-measure.
    One row per unreadable-file diagnosis, same as PROBE is one row per unrun
    probe — `fix_rows` still emits one per CHECK id, so several checks blocked
    on the same file arrive as several rows, each pointing at the same fix.
    """
    named = ", ".join(files)
    return {"dispatch": "agent", "routed": True, "agent": "general-purpose",
            "command": "", "command_file": "", "command_file_found": None,
            "flag_check": "",
            "prompt": (f"This check was never measured: {named} could not be "
                       "opened — permissions, a dangling symlink, or a tracked "
                       "file missing from a sparse checkout (`ls -l` it to see "
                       "which). Restore it with `git checkout -- <path>` if it "
                       "is missing from the worktree, or `chmod` it back if the "
                       "permissions are wrong. Then re-run `health.py collect "
                       "--repo <repo>` so this check gets its real measurement "
                       "— do not guess the verdict from the check's title or "
                       "its normal fix."),
            "operator": ""}


# The only two statuses that mean "nothing to do here": measured, and no
# finding. `na` is a recorded result (the check does not apply to this repo),
# not an absence. Every other status — including any added later — is either
# unmeasured or a finding, and both get a row.
CLEAR = ("pass", "na")
UNMEASURED = ("pending", None)

# The two tiers a mode can filter on. Anything else — `None`, or a tier a later
# version invents — is NOT a third tier to filter out: the tier decides WHEN a
# finding is shown, never whether it exists, so dropping it is the same silent
# fallthrough CLEAR refuses one field over. It gets a row in every mode, and the
# row says what is actually wrong, which is the tier and not the fix.
TIERS = ("blocking", "advisory")
UNTIERED = operator("this check's `tier` is neither `blocking` nor `advisory`, so "
                    "no mode can decide when to show it and it was being dropped "
                    "in silence — fix the tier where the check is emitted "
                    "(scripts/health.py or scripts/probes.py), then re-run")


def route_for(cid):
    return ROUTES.get(cid, UNKNOWN)


def fix_rows(card, advisory=False):
    """One row per check that has a FINDING or is UNMEASURED, in card order.

    Three row shapes, because "broken", "nobody looked" and "couldn't look"
    are different answers and collapsing them is how a check reports all clear
    without checking, or tells the agent to run a probe that does not exist. A
    finding carries its fix route; an unrun probe carries PROBE and the command
    that would settle it; a `pending` STAMPED `unread` by `common.unmeasured`/
    `pending_if_unread` (a DETERMINISTIC check that could not read its own
    input) carries `unreadable()` instead — the file to restore, never a probe
    command nobody wrote (found by review: an unlinked tracked
    workflow left three blocking checks `pending` this way, and PROBE told the
    agent to run a command that does not exist for them). `status` and `tier`
    are on every row so neither shape nor neither tier can be mistaken for the
    other downstream.

    Blocking only by DEFAULT: that is the set `--fix-blocking` may touch without
    asking, and advisory findings wait for a go-ahead (SKILL.md step 5).
    `advisory=True` IS that go-ahead and ADDS the advisory rows — it never drops
    a blocking one, so a fix pass cannot narrow itself into missing a blocker.

    Which statuses are FINDINGS is not enumerated, in either tier — CLEAR is,
    and everything else is a finding. Three times a status that means "something
    is wrong here" fell out of an enumerated finding list and printed nothing:
    `pending` in both tiers, then `warn` in advisory, then `warn` in blocking
    (sec.action-pinning warns when only first-party actions are unpinned;
    sec.dangerous-workflow warns on event data interpolated near `run:` — both
    blocking, neither ever `fail`). Stated the other way round the next status
    somebody adds arrives as a routed row instead of as silence.

    The TIER is read the same way round, for the same reason: only a recognised
    tier may be filtered out by a mode, and an unrecognised one (`None`, or a
    tier a later version adds) is shown in every mode with UNTIERED on it.
    """
    tiers = TIERS if advisory else ("blocking",)
    # Type-guarded, not just key-guarded: health.load_card already refuses a card
    # that is not an object and drops non-dict members, and this repeats the guard
    # because fix_rows is a pure function anyone may hand a dict to.
    for c in typed_items(typed(card, dict, {}).get("checks")):
        tier = c.get("tier")
        if tier in TIERS and tier not in tiers:
            continue                   # a real tier this mode does not show yet
        status = c.get("status")
        if status in CLEAR:
            continue
        # None as well as "pending": certified() turns a null into `pending` before
        # a CARD reaches here, and fix_rows takes any dict, so it is refused twice.
        cid = c.get("id")          # .get: the comment above promised a guard here
        # `unread` is set ONLY by common.unmeasured/pending_if_unread, and ONLY
        # on the branch where the check's own conclusion rests on a file that
        # would not open — never on a check that is `pending` because nobody
        # has run its probe yet. That is what tells the two `pending` origins
        # apart; see unreadable()'s docstring for why they need different rows.
        unread = typed_items(c.get("unread"), str) if status in UNMEASURED else []
        route = (UNTIERED if tier not in TIERS
                 else unreadable(unread) if unread
                 else PROBE if status in UNMEASURED else route_for(cid))
        yield {"id": cid, "tier": tier, "status": status,
               "title": c.get("title", ""), "detail": c.get("detail", ""), **route}
