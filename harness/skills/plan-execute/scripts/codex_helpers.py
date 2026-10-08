"""Shared fixtures and helpers for the provider-aware dispatch suites.

Extracted when test_codex_dispatch.py (2394 lines) was split into the five
suites it had grown into: routing, the egress guard, the --harness codex driver,
the dispatch receipt and the ESC-03 effort climb. They all need the same fixture
SSOT, the same plant helpers and the same `begin` wrappers, and a second copy
would drift from the first.

The fixture SSOT mirrors the live providers block shape and is bound through the
PLAN_EXECUTE_ROUTING_SSOT env override, so every suite stays hermetic w.r.t. the
real ~/.claude/model-routing.yaml.
"""
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import egress  # noqa: E402,F401
import run  # noqa: E402,F401
import run_state_io as rsi  # noqa: E402,F401
from test_shipping import make_plan  # noqa: E402,F401

# Mirrors the live SSOT's providers block shape (models ascending + effort maps);
# only what _Profile parses eagerly plus active_provider.
SSOT_TEMPLATE = """version: 99
active_provider: {provider}

task_classes:
  mechanical:     {{ tier: cheap_fast,        effort: light    }}
  standard_build: {{ tier: workhorse,         effort: standard }}
  agentic_build:  {{ tier: workhorse,         effort: thorough }}
  deep_reasoning: {{ tier: frontier_reasoner, effort: standard }}
  linchpin:       {{ tier: frontier_reasoner, effort: thorough }}

executor_policy:
  executor_for: [{executor_for}]

codex_peer:
  data_sensitivity_guard:
    content_scan_allowlist:
{allowlist}
    egress_opt_ins:
{opt_ins}

providers:
  anthropic:
    calibration:
      status: researched
    models:
      cheap_fast:        haiku
      workhorse:         sonnet
      frontier_reasoner: opus
      apex_reasoner:     fable
    effort:
      control: effort
      map:
        cheap_fast:        {{ light: null, standard: null,   thorough: null }}
        workhorse:         {{ light: low,  standard: medium, thorough: high }}
        frontier_reasoner: {{ light: low,  standard: medium, thorough: high }}
        apex_reasoner:     {{ light: low,  standard: medium, thorough: high }}
    # Mirrors the live providers.anthropic ladder, so a session that FAILS CLOSED
    # off the codex lane climbs the ladder it will really be dispatched on.
    escalation:
      trigger: "2 failures at the same root cause"
      effort_ladder:
        workhorse:         [low, medium, high]
        frontier_reasoner: [low, medium, high]
        apex_reasoner:     [low, medium, high, xhigh]
      model_ladder: [cheap_fast, workhorse, frontier_reasoner, apex_reasoner]
      model_ladder_entry: {{ frontier_reasoner: high, apex_reasoner: medium }}
    degrade:
      signals: [entitlement, unavailable]
      ladder: {{ apex_reasoner: frontier_reasoner, frontier_reasoner: workhorse }}
      floor: workhorse
      effort_on_degrade: {{ frontier_reasoner: high, workhorse: high }}

  openai:
    calibration:
      status: {openai_status}
    # RE-POINTED TO THE REAL THREE-TIER SHAPE (s03, 2026-08-13), as the v1.15 note
    # here said it would be. The `workhorse` tier is GONE with gpt-5.5, which is
    # precisely why dial-driven translation no longer matches Claude tier NAMES
    # against OpenAI tier names: `sonnet` is workhorse under providers.anthropic,
    # and there is no workhorse here to land on. Translation now resolves
    # (task_class, openai) through resolve_route — hence the `task_classes:`
    # blocks above and below, which this fixture previously had no need for.
    models:
      cheap_fast:        gpt-5.6-luna
      frontier_reasoner: gpt-5.6-terra
      apex_reasoner:     gpt-5.6-sol
    task_classes:
      mechanical:     {{ tier: cheap_fast,    effort: light    }}
      standard_build: {{ tier: cheap_fast,    effort: standard }}
      agentic_build:  {{ tier: apex_reasoner, effort: thorough }}
      deep_reasoning: {{ tier: apex_reasoner, effort: thorough }}
      linchpin:       {{ tier: apex_reasoner, effort: maximal  }}
    effort:
      control: reasoning_effort
      map:
        cheap_fast:        {{ light: max,    standard: max,  thorough: max,   exhaustive: max,   maximal: max   }}
        frontier_reasoner: {{ light: max,    standard: max,  thorough: max,   exhaustive: max,   maximal: max   }}
        apex_reasoner:     {{ light: medium, standard: high, thorough: xhigh, exhaustive: xhigh, maximal: max   }}
    # THE CODEX CLIMB (ESC-03, s04) — mirrors the live providers.openai block. luna
    # has NO effort_ladder at all (its curve collapses below max, so `max` is the
    # only cell it ever emits and the climb leaves the tier immediately); terra's
    # ladder is exactly [max] for the same reason; sol enters at xhigh, never at
    # its first rung (`high` would be BELOW the terra@max just left).
    escalation:
      trigger: "2 failures at the same root cause"
      effort_ladder:
        frontier_reasoner: [max]
        apex_reasoner:     [high, xhigh, max]
      model_ladder: [cheap_fast, frontier_reasoner, apex_reasoner]
      model_ladder_entry: {{ apex_reasoner: xhigh }}
    degrade:
      signals: [entitlement, unavailable]
      ladder: {{ apex_reasoner: frontier_reasoner, cheap_fast: frontier_reasoner }}
      floor: frontier_reasoner
      effort_on_degrade: {{ frontier_reasoner: max }}
"""


# The `egress_root` fixture (a clean per-test working tree) lives in conftest.py —
# every suite in this directory needs it now that the guard shells out to gitleaks.

# A SYNTHETIC AWS key: the canonical gitleaks/AWS documentation example, assembled
# at runtime so this source file never itself carries the literal (the repo's own
# githooks/pre-commit gitleaks gate would refuse the commit, correctly). NEVER use
# a real credential here.
_FAKE_AWS_KEY = "AKIA" + "IMNOJVGFDXXXE4OA"


def _plant_secret(path):
    """Write a file whose CONTENT trips gitleaks. The filename is deliberately
    innocent — the whole point of the content scan is that a live key hides in a
    file called `config.py`, not in one called `secrets`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"AWS_ACCESS_KEY_ID = '{_FAKE_AWS_KEY}'\n")
    return path


def _opt_in_line(repo_path, expiry="2099-01-01"):
    return (
        f'      - {{ repo_path: "{repo_path}", approved_by: test-operator, '
        f"date: 2026-07-10, expiry: {expiry} }}"
    )


def _allow_line(path, expiry="2099-01-01"):
    return (
        f'      - {{ path: "{path}", approved_by: test-operator, '
        f"date: 2026-07-28, expiry: {expiry} }}"
    )


def _begin(plan_dir, sessions, capsys, **kw):
    run.cmd_begin(plan_dir, sessions, **kw)
    out = json.loads(capsys.readouterr().out)
    return {m["id"]: m for m in out["batch"]}, out


def _begin_codex(plan_dir, sessions, capsys):
    """begin under the CODEX harness — returns (members, payload, stderr)."""
    run.cmd_begin(plan_dir, sessions, harness="codex")
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    return {m["id"]: m for m in out.get("batch", [])}, out, cap.err


def _codex_session():
    return [{"id": "s01", "title": "S1", "items": ["i1"], "model": "gpt-5.6-sol"}]


# The canonical DONE closeout payload. Shared: the receipt suite feeds it
# back through `apply`, and test_codex_worktree.py replays it per member.
_CLOSEOUT = (
    '<plan-execute-closeout>\n'
    '{"session":"s01","result":"DONE","items_completed":["i1"],'
    '"items_blocked":[],"notes":{},"dispatch_next":false,'
    '"human_checkpoint_reason":null}\n'
    '</plan-execute-closeout>'
)
