"""Tier-agent effort enforcement (2026-07-26).

The defect this guards: a plan's per-session `reasoning` tier used to be applied ONLY
by prepending "Think hard…"-style prose to the subagent prompt. Measured against the
Claude Code 2.1.220 docs, that is inert — those phrases are not recognized keywords,
and a `subagent_type: null` dispatch is a fresh general-purpose agent with no
definition file, so it inherits the SESSION effort. The tier is now resolved to an
agent definition carrying real `effort:` frontmatter.

Every assertion here is paired: a PLANT (the wrong behavior must be detected) and an
ALLOW CONTROL (the right behavior must not trip). A check that only ever sees the
passing case cannot fail, which is the defect class this project keeps re-finding.

Run: python test_tier_agent.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run  # noqa: E402


def _agent_dir(tmp, names):
    d = Path(tmp) / "agents"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / f"{n}.md").write_text(
            f"---\nname: {n}\ndescription: test\nmodel: opus\neffort: high\n---\nbody\n"
        )
    return d


def _parse_frontmatter(text):
    """Parse an agent definition's YAML frontmatter. Raises on invalid YAML — that is
    the point. Uses PyYAML when available, else a deliberately strict minimal parser
    that still rejects the failure mode that bit us (an unquoted scalar containing
    ': '), so this check never degrades to a no-op on a box without PyYAML."""
    if not text.startswith("---"):
        raise ValueError("no frontmatter block")
    body = text.split("---", 2)[1]
    try:
        import yaml
    except ImportError:
        out = {}
        for raw in body.strip().splitlines():
            line = raw.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            if ":" not in line:
                raise ValueError(f"frontmatter line is not a mapping: {line!r}")
            key, _, val = line.partition(":")
            val = val.strip()
            quoted = len(val) >= 2 and val[0] == val[-1] and val[0] in "'\""
            if not quoted and ": " in val:
                raise ValueError(
                    f"unquoted value for {key.strip()!r} contains ': ' — invalid YAML: {val!r}"
                )
            out[key.strip()] = (val[1:-1] if quoted else val) or None
        return out
    d = yaml.safe_load(body)
    if not isinstance(d, dict):
        raise ValueError(f"frontmatter did not parse to a mapping, got {type(d).__name__}")
    return d


def _assert_frontmatter_parses(path, text, name, model, effort):
    try:
        fm = _parse_frontmatter(text)
    except Exception as e:  # noqa: BLE001 — any parse failure is the finding
        raise AssertionError(
            f"{name}: frontmatter is not valid YAML ({e}) — Claude Code will not load "
            f"this agent at all. {path}"
        ) from None
    assert fm.get("name") == name, f"{name}: frontmatter name is {fm.get('name')!r}"
    assert fm.get("model") == model, f"{name}: frontmatter model is {fm.get('model')!r}"
    assert fm.get("description"), f"{name}: description is required and must be non-empty"
    if model == "haiku":
        # HAIKU INVARIANT (model-routing.yaml `agents:`, guard check (b)): haiku rejects
        # the reasoning dial, so a haiku tier agent must carry NO effort key.
        assert "effort" not in fm, f"{name}: haiku must carry no effort: key (HAIKU INVARIANT)"
    else:
        assert fm.get("effort") == effort, (
            f"{name}: frontmatter effort is {fm.get('effort')!r}, table says {effort!r}"
        )


def test_frontmatter_parse_check_catches_the_unquoted_colon(tmp_path):
    """PLANT + ALLOW CONTROL for the parser itself. The bug that shipped was a
    description containing ': ', which invalidates the YAML while leaving every
    grepped line intact — so the check that matters is that PARSING rejects it."""
    broken = (
        "---\nname: tier-opus-high\n"
        "description: plan-execute dispatch tier: opus at high effort\n"
        "model: opus\neffort: high\n---\nbody\n"
    )
    try:
        _assert_frontmatter_parses("x", broken, "tier-opus-high", "opus", "high")
    except AssertionError as e:
        assert "not valid YAML" in str(e), f"wrong failure reason: {e}"
    else:
        raise AssertionError(
            "PLANT FAILED: an unquoted ': ' in description was accepted as valid "
            "frontmatter — this is the exact bug the check exists to catch"
        )
    ok = (
        "---\nname: tier-opus-high\n"
        "description: plan-execute dispatch tier - opus at high effort\n"
        "model: opus\neffort: high\n---\nbody\n"
    )
    _assert_frontmatter_parses("x", ok, "tier-opus-high", "opus", "high")  # must not raise

    # And a quoted colon IS legal YAML — the check must not over-reject.
    quoted = (
        "---\nname: tier-opus-high\n"
        'description: "plan-execute dispatch tier: opus at high effort"\n'
        "model: opus\neffort: high\n---\nbody\n"
    )
    _assert_frontmatter_parses("x", quoted, "tier-opus-high", "opus", "high")


def test_resolves_only_when_the_definition_exists(tmp_path):
    """PLANT: a tier in the table whose FILE is missing must NOT resolve — Task would
    fail the dispatch outright on an unknown subagent_type. ALLOW: present file resolves."""
    tmp = tmp_path
    empty = Path(tmp) / "empty"
    empty.mkdir()
    assert run._tier_agent("opus", "high", agent_dir=empty) is None, (
        "PLANT FAILED: resolved a tier agent whose definition file does not exist"
    )
    present = _agent_dir(tmp, ["tier-opus-high"])
    assert run._tier_agent("opus", "high", agent_dir=present) == "tier-opus-high", (
        "ALLOW CONTROL FAILED: an existing tier definition did not resolve"
    )


def test_unmapped_tiers_do_not_resolve(tmp_path):
    """PLANT: opus xhigh/max are deliberately unmapped (escalation rungs the SSOT calls
    dead/operator-elected). They must return None so the caller reports the gap,
    rather than silently reusing the `high` agent. ALLOW: high still resolves."""
    d = _agent_dir(tmp_path, ["tier-opus-high"])
    for tier in ("xhigh", "max"):
        assert run._tier_agent("opus", tier, agent_dir=d) is None, (
            f"PLANT FAILED: {tier} resolved to a tier agent; it must report the gap instead"
        )
    assert run._tier_agent("opus", "high", agent_dir=d) == "tier-opus-high", (
        "ALLOW CONTROL FAILED: high stopped resolving"
    )
    # An unrecognized model must not resolve either.
    assert run._tier_agent(None, "high", agent_dir=d) is None
    assert run._tier_agent("gpt-5.6-sol", "high", agent_dir=d) is None


def test_fable_escalation_apex_pairs_resolve(tmp_path):
    """ESC-01: fable is the named escalation-apex exception — unlike opus/sonnet's
    xhigh (dead/operator-elected, deliberately unmapped, see test above), fable gets
    real tier agents through xhigh because it's reached from opus-high on the SSOT's
    standing ladder. PLANT: fable.max still has no tier agent (same advisory
    fallback as any other unmapped cell) and must not resolve. ALLOW: medium/high/
    xhigh each resolve to their own file when present, and report the gap (None)
    when the file is missing — same existence discipline as every other tier."""
    empty = Path(tmp_path) / "empty"
    empty.mkdir()
    for tier in ("medium", "high", "xhigh"):
        assert run._tier_agent("fable", tier, agent_dir=empty) is None, (
            f"PLANT FAILED: fable {tier} resolved with no definition file on disk"
        )
    present = _agent_dir(
        tmp_path, ["tier-fable-medium", "tier-fable-high", "tier-fable-xhigh"]
    )
    for tier, name in (
        ("medium", "tier-fable-medium"),
        ("high", "tier-fable-high"),
        ("xhigh", "tier-fable-xhigh"),
    ):
        assert run._tier_agent("fable", tier, agent_dir=present) == name, (
            f"ALLOW CONTROL FAILED: fable {tier} did not resolve to {name}"
        )
    assert run._tier_agent("fable", "max", agent_dir=present) is None, (
        "PLANT FAILED: fable max resolved to a tier agent; it must stay unmapped "
        "(advisory fallback), same as opus/sonnet max"
    )


def test_every_mapped_tier_has_a_real_definition_shipped():
    """The table is only useful if the files it names are actually deployed. This is
    the check that fails when someone adds a table row and forgets the agent file
    (or renames an agent file and forgets the table)."""
    src = Path(__file__).resolve().parents[3] / "agents"
    if not src.is_dir():  # not running inside the source clone; skip quietly
        print("  (skipped shipped-definition check — source agents/ dir not found)")
        return
    for (model, effort), name in sorted(run._TIER_AGENTS.items()):
        f = src / f"{name}.md"
        assert f.is_file(), f"_TIER_AGENTS names {name} but {f} does not exist"
        text = f.read_text()
        # PARSE the frontmatter, do not grep it. The first draft of these agents had
        # `description: plan-execute dispatch tier: haiku …` — an unquoted YAML scalar
        # containing ": ", which makes the whole frontmatter INVALID, so Claude Code
        # would not load the agent at all. Every regex-based check (including
        # verify-routing.sh's) stayed green on that file because the `model:`/`effort:`
        # lines it greps for were still present. Parsing is the only check that catches it.
        _assert_frontmatter_parses(f, text, name, model, effort)
        # The frontmatter must actually carry the pair the table claims — a tier
        # agent whose `effort:` says something else is worse than none at all,
        # because the member payload would report effort_enforced: true.


def test_reasoning_directive_still_covers_every_tier():
    """The prepend path remains the documented fallback for unmapped tiers, so the
    directive table must still cover every reasoning tier (verify-routing.sh check
    (d) asserts the same thing against the SSOT; this keeps the local invariant)."""
    for tier in ("low", "medium", "high", "xhigh", "max"):
        assert tier in run._REASONING_DIRECTIVE, f"{tier} lost its directive"
    assert run._reasoning_directive("low") == "", "low must prepend nothing"
    assert run._reasoning_directive("high"), "high must still have advisory prose"
    assert run._reasoning_directive("nonsense") == "", "unknown tier must prepend nothing"


def main():
    import tempfile

    failures = 0
    for fn in (
        test_resolves_only_when_the_definition_exists,
        test_unmapped_tiers_do_not_resolve,
        test_fable_escalation_apex_pairs_resolve,
        test_frontmatter_parse_check_catches_the_unquoted_colon,
        test_every_mapped_tier_has_a_real_definition_shipped,
        test_reasoning_directive_still_covers_every_tier,
    ):
        try:
            if fn.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as tmp:
                    fn(tmp)
            else:
                fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
