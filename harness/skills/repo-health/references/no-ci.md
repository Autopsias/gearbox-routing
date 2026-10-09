# When a repo has no CI

`ci.server-side-gate` warns when a repo has a remote but no workflow. Propose CI
only when the answer to **"is this branch consumed by anything?"** is yes — a
deploy pulls from it, a package publishes from it, or a second machine or agent
pushes to it. A repo whose remote is only a backup needs nothing, and the check
is `na` when there is no remote at all.

The reason is not that hooks are bad. A client-side hook is advisory by
construction: `--no-verify` skips it, a fresh clone never installs it, a second
machine may lack the tools it shells out to, and — the one no hook can catch — a
suite can pass locally because of a file that was never committed. Only a clean
checkout proves the pushed tree is green.

When you do propose one, keep it to a single workflow and obey the checks this
skill already enforces, or it will fail its own review:

1. **Call the same contract, do not restate it.** CI runs `make test` / `make
   check`, never its own copy of the commands. Two definitions drift, and the
   repo then has two answers to "is it green".
2. `permissions:` at the **top level**, least-privilege (`contents: read` for a
   test job). A job added later must not inherit a write token by default.
3. **Pin every action to a full commit SHA**, first-party included, with the
   version in a trailing comment. Tags are mutable.
4. `timeout-minutes` on every job — the default is 360.
5. `concurrency` with `cancel-in-progress`, keyed on the ref.
6. **Path filters, measured not guessed.** Count which commits actually touch code
   (`git log --since=30.days -- <paths> | wc -l`) before choosing them, and say
   what the filter excludes. On a busy repo this is the difference between fitting
   the free tier and not.
7. Add a `schedule:` backstop so a docs-only stretch still gets one clean run.
8. Never interpolate `${{ github.event.* }}` into a `run:` block — pass it through
   `env:` instead. That is the expression-injection class
   `vendor/gha-security-review/` exists to catch.

Pin the runner's language version to the development machine's. A version skew
produces red builds that say nothing about the commit under test.
