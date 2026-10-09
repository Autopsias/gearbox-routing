#!/usr/bin/env python3
"""The scorecard file: read it, write it, and decide what may be carried across runs.

Split out of health.py so that file stays under the house 500-LOC limit — it sat
at exactly 500, so even a one-line repair tripped the ratchet. The grouping is
real: everything here is about the STORED card rather than about any check.
health.py re-exports every name, so health_render.py and the tests keep resolving
`health.load_card`, `health.save_card`, `health.tree_key` and
`health.recorded_probes` unchanged.

Imports common + probes; health.py imports this, never the other way round.
"""
import json
import re
from datetime import date

from common import (CHECK_FIELDS, MEASURED, certified,  # noqa: F401  (re-exported)
                    parse_json, run, typed, typed_items)
from probes import WORLD_PROBE_DAYS, WORLD_PROBES

HEALTH_REL = ".claude/health/"


def health_dir(repo):
    return repo / ".claude" / "health"   # a PATH; save_card is the one writer


# CHECK_FIELDS moved down into common.py (and is re-exported above, so
# `health.CHECK_FIELDS` still resolves): the same record shape is now filled in
# at BOTH places a check enters the program — common.check() when the collector
# emits one, and load_card below when one is read back — and two hand-written
# copies of a vocabulary is how they drift apart.


def load_card(repo):
    """The one reader of scorecard.json — and therefore the one type guard on it.

    A card that parses but is not an object (`[]`), or whose `checks` is a string
    or holds a bare string, used to reach every consumer as an AttributeError or
    a KeyError from deep inside render/fix-routes. Refused here with a sentence
    that says what to do; ValueError because recorded_probes() already treats a
    card it cannot use as "no recorded probes" and catches exactly that.

    Each check then goes through common.certified(), NOT a `{**CHECK_FIELDS,
    **c}` merge: the merge fills a MISSING key but leaves an explicit `null`
    alone, so a null status read as a measured result. See certified().
    """
    p = health_dir(repo) / "scorecard.json"
    card = parse_json(p.read_text())
    if card is None:
        raise ValueError(f"{p} is not a JSON object — re-run: health.py collect")
    card["checks"] = [certified(c) for c in typed_items(card.get("checks"))]
    return card


def save_card(repo, card):
    d = health_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    (d / "scorecard.json").write_text(json.dumps(card, indent=1) + "\n")


def tree_key(repo):
    """Identity of the code a probe result describes, or "" for "unknowable".

    NOT HEAD. Two reasons: `git rev-parse --short HEAD` fails on a fresh or
    broken repo and save_card stored "?" for it — and "?" == "?" would have
    preserved stale probe results on every broken repo forever. And HEAD moves
    on amend/rebase even when the code is byte-identical, which throws away
    results that are still true.

    The tree hash answers the real question, and an empty `git status
    --porcelain` on BOTH sides is what makes it honest: a probe run against a
    dirty tree describes code that is not in any tree, so it never carries over.
    "" is returned on any doubt and never compares equal to anything.

    Known ceiling: where the scorecard is itself tracked (this repo), COMMITTING
    it changes the tree hash, so the next collect re-runs probes that were still
    true. That is the conservative direction — a probe re-run costs time, a
    wrongly-kept probe result costs trust — and fixing it would mean hashing a
    filtered tree instead of asking git for HEAD's. Do that only if the re-runs
    actually bite.
    """
    rc, tree = run(["git", "rev-parse", "HEAD^{tree}"], repo)
    if rc != 0 or not re.fullmatch(r"[0-9a-f]{40}", tree.strip()):
        return ""
    # -uall, not the default: git collapses untracked files into a bare
    # "?? dir/" line, which would hide new files under an already-untracked
    # directory. The one exclusion is the collector's OWN output — scorecard.json
    # is tracked in this repo, so counting it would make every tree dirty by the
    # act of measuring it, and preservation could never fire anywhere. The
    # exclusion is an :(exclude) pathspec and not string-slicing of the porcelain
    # columns, because run() strips its output and the leading status column of
    # the first line goes with it.
    rc, out = run(["git", "status", "--porcelain", "-uall", "--",
                   ".", f":(exclude){HEALTH_REL}"], repo)
    if rc != 0 or out.strip():
        return ""
    return tree.strip()


def staleness(repo, card):
    """Say so ON THE PAGE when the page is not describing the reader's tree.

    Reads the TREE hash, never the commit sha. The sha answers the wrong
    question three ways, all of them found by review:

      * a dirty working tree keeps HEAD exactly where it was, so a sha check
        stays silent on the one case that most needs saying — and the scorecard
        that shipped with this very change had `tree: ""`, meaning it WAS
        collected dirty and said nothing;
      * `git commit --amend` moves the sha while the tree stays byte-identical,
        so a sha check cries stale over a reworded message;
      * `run()` glues stderr onto stdout, so a git warning lands inside the
        banner unless the value is shape-checked.

    card.tree_key() already answers the real question and already guards all
    three: it returns "" for "unknowable OR dirty" and otherwise a validated
    40-hex tree hash. This is the same field the collector stores, so the two
    sides of the comparison mean the same thing by construction.

    "" means the page IS describing this tree. Anything else is a sentence the
    reader has to see, so it goes in the masthead and not into a side file no
    template renders — which is how the caveat stayed invisible.
    """
    # SHAPE-CHECKED BEFORE IT REACHES git's ARGV. `tree` is read out of a JSON
    # file on disk, and this value is passed as a git argument POSITIONALLY,
    # before the `--`. tree_key() re.fullmatch-validates its own output; this
    # side had no check at all, and review reproduced both halves of that live:
    # tree "HEAD" compared clean and reported FRESH — the exact false all-clear
    # this function exists to prevent — and tree "--output=/tmp/PWNED" was
    # parsed by git as an OPTION, returned "fresh", AND created the file.
    # Anything that is not a 40-hex object name is not a tree hash, so it is
    # treated as no identity at all (review).
    raw = typed(card.get("tree"), str, "").strip()
    was = raw if re.fullmatch(r"[0-9a-f]{40}", raw) else ""
    now = tree_key(repo)                       # "" == dirty, or not a git repo
    if not was:
        malformed = " (the recorded value is not a tree hash)" if raw else ""
        return (f"collected against a DIRTY or unidentifiable tree{malformed} — these "
                f"numbers describe code that is in no commit. Re-run `health.py collect` "
                f"on a clean tree before relying on them.")
    if not now:
        # NOT "then re-render": fix-routes stamps this sentence verbatim onto
        # every dispatch row, and re-rendering a page fixes nothing there. The
        # real next action is the same on both surfaces — collect again, clean.
        return ("this tree is DIRTY or unidentifiable, so it is not the clean tree "
                "these numbers were collected against. Commit or stash, then re-run "
                "`health.py collect`.")
    if now == was:
        return ""
    # The trees differ — but this repo TRACKS .claude/health/, so committing the
    # dashboard is itself a tree change, and a banner that fires every time you
    # commit the thing it describes is a false alarm that teaches the reader to
    # ignore it. tree_key() already excludes this directory from its dirtiness
    # check for the same reason; it cannot exclude it from the hash. So ask git
    # the question that actually matters: did anything OUTSIDE our own output
    # change? An unknown `was` (a tree from another clone) is not proof of
    # sameness and stays stale.
    rc, _ = run(["git", "diff", "--quiet", was, "HEAD", "--",
                 ".", f":(exclude){HEALTH_REL}"], repo)
    if rc == 0:
        return ""
    sha = typed(card.get("commit"), str, "").strip() or "?"
    # `git diff --quiet` says 0 = same, 1 = differs, 128 = it could not read the
    # object at all ("fatal: bad object" — a tree from a clone this one has never
    # fetched). Both stay stale, which is the safe direction, but they are not
    # the same sentence: telling the reader "this tree has changed since" when
    # the truth is "I cannot find that tree here" sends them to re-run collect
    # over a difference that does not exist (review).
    if rc != 1:
        return (f"collected against a tree this clone DOES NOT HAVE (at commit {sha}); "
                f"nothing here can be compared against it. Re-run `health.py collect`.")
    return (f"collected against a DIFFERENT tree (at commit {sha}); this tree has changed "
            f"since. Re-run `health.py collect` before relying on these numbers.")


def still_fresh(c, today=None):
    """Is this recorded result still allowed to stand on an unchanged tree?

    Always yes for a tree-dependent probe — that is what the tree hash proves.
    A WORLD_PROBES result also has to be recent, because nothing in this repo
    changes when a CVE is published or an upstream pin moves. A result with no
    `recorded` stamp (a card written before the stamp existed, or a hand-edited
    one) cannot prove it is recent, so it expires.
    """
    if c["id"] not in WORLD_PROBES:
        return True
    today = today or date.today()
    try:
        age = (today - date.fromisoformat(c.get("recorded") or "")).days
    except (TypeError, ValueError):
        return False
    return 0 <= age <= WORLD_PROBE_DAYS


def recorded_probes(repo, key):
    """Probe results from the previous card that still describe `key`'s tree.

    `na` counts as a recorded result: SKILL.md tells the operator to record one
    when a tool is not installed and not worth installing now. The narrow cost is
    that if the COLLECTOR itself changes so an id flips na -> pending on an
    unchanged tree, the stale na is carried; --fresh is the answer to that.

    The tree hash bounds staleness only for probes that read the tree; see
    still_fresh() for the ones that read the world instead.
    """
    if not key:
        return {}
    try:
        old = load_card(repo)
    except (OSError, ValueError):
        return {}
    if old.get("tree") != key:
        return {}
    # `status in MEASURED`, never `!= "pending"`: stated the negative way, every
    # value that is not the one spelling of "unmeasured" counts as a result —
    # which is how a null status became a recorded probe. certified() has already
    # turned a null into `pending`; this says the same thing the other way round
    # so neither guard alone is load-bearing.
    return {c["id"]: c for c in old["checks"]
            if c["id"] and c["status"] in MEASURED and still_fresh(c)}


