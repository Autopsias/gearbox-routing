"""EXPECT — a gate passes on its OUTPUT, not only its exit code.

Exit 0 is a claim, not proof that the check ran. A pytest invocation that
collected nothing, a suite in which every test skipped, a grep over an empty
input, a runner that short-circuited on "no changes detected": all exit 0, and
all read as green. That is the same silent-green class as the unrunnable
``code-review-gate`` and the empty reviewed surface, in a third costume — and
unlike those two it needs no reviewer to close, only the string the check
prints when it really ran.

An optional ``expect`` on an argv registry entry names that string. Both argv
run sites (``verify._run_gate`` and ``shipping.ship_run_argv``) consult this
module off the one shared resolver, so a gate cannot lose its ``expect`` by
being used as a ``pre_deploy_gate`` instead of a verify gate.

Substring by default; ``/…/flags`` is a regex. A value that begins AND ends
with ``/`` is ALWAYS read as one (the sed/JS convention), so a literal path
needs escaped slashes.
"""

import re

_FLAGS = {"i": re.I, "m": re.M, "s": re.S}


def expect_mismatch(expect, output):
    """Return ``None`` when ``output`` satisfies ``expect``, else the reason.

    A malformed pattern or an unknown flag FAILS the gate instead of being
    ignored: a pattern nobody can compile is a broken gate, and a broken gate
    must be loud rather than green (an ignored ``expect`` is worse than no
    ``expect``, because the registry then documents a guarantee nothing
    enforces)."""
    if not expect:
        return None
    text = output or ""
    m = re.fullmatch(r"/(.+)/([a-zA-Z]*)", expect, re.DOTALL)
    if not m:
        return None if expect in text else f"output did not contain EXPECT {expect!r}"
    pattern, flagstr = m.group(1), m.group(2)
    bad = [f for f in flagstr if f not in _FLAGS]
    if bad:
        return (f"EXPECT {expect!r} carries unsupported regex flag(s) {''.join(bad)!r} "
                "(i, m, s only). A value that begins AND ends with '/' is always read as a "
                "regex — to match a literal path, escape the slashes: \"/\\\\/usr\\\\/bin\\\\/env/\".")
    flags = 0
    for f in flagstr:
        flags |= _FLAGS[f]
    try:
        found = re.search(pattern, text, flags)
    except re.error as exc:
        return f"EXPECT {expect!r} is not a valid regex ({exc})"
    return None if found else f"output did not match EXPECT regex {expect!r}"


# Why the excerpt says this out loud: the gate's own output IS its happy path,
# so a rework attempt handed only that output sees nothing wrong and cannot act.
_HEADER = (
    "{gate} exited 0 but its output did not satisfy the gate's declared EXPECT.\n"
    "  {reason}\n"
    "Exit 0 alone is not proof the check ran. Either make the check emit its EXPECT "
    "line, or fix the EXPECT in the eval-gates registry if it names the wrong string.\n"
    "--- gate output ---\n"
)


def expect_excerpt(gate, reason, output):
    """The failure excerpt, or ``output`` untouched when there was no mismatch."""
    return output if reason is None else _HEADER.format(gate=gate, reason=reason) + output


def gate_miss(gate_def, res, *, skip=False):
    """``expect_mismatch`` over a finished run's combined stdout+stderr.

    Only ever reports on a ZERO exit. A non-zero exit already IS the failure,
    and its excerpt must not be prefixed with "exited 0 but…" — a gate report
    that misstates what happened is worse than one that says less.

    ``skip`` is for the SYNTHETIC ``verify-simulate`` pass only — no fixture,
    hence no real output to match. A ``fixture_fake`` IS matched, so a fixture
    too thin to satisfy its own gate is a failing fixture rather than a green
    one."""
    if skip or res.get("returncode") != 0:
        return None
    return expect_mismatch(gate_def.get("expect"),
                           (res.get("stdout") or "") + "\n" + (res.get("stderr") or ""))
