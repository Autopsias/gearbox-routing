# Security policy

## Reporting a vulnerability

If you find a security issue in Gearbox (the guard scripts, resolver, or
install/uninstall paths — e.g. a way to make `install.sh` write outside its
declared target, or a way to make `verify-routing.sh` report a false PASS),
please report it privately rather than opening a public issue:

- Open a GitHub private security advisory on this repository
  (`Security` tab → `Report a vulnerability`), or
- Email the maintainers listed in `CODEOWNERS`.

Please include: the affected script/version, a reproduction (ideally against
a throwaway `--claude-home`, never against a live install), and the
impact you believe it has.

## Scope notes specific to this project

- **Gearbox is a policy file + shell/Python guard scripts, not a network
  service.** Most realistic risk is local: a crafted `model-routing.yaml` or
  fixture tricking `install.sh` or `verify-routing.sh` into unsafe file
  operations (path traversal, clobbering files outside `--claude-home`,
  silently passing a guard that should fail).
- **The shipped example provider profiles (anthropic/openai/gemini) are not
  a security boundary** — they're dated, illustrative pricing/model data.
  Treat inaccuracies there as a data-quality bug (`docs/PROVIDERS.md`
  staleness cadence), not a vulnerability, unless they cause an unsafe code
  path.
- We do not consider "the shipped example profile is stale" alone a security
  issue — file that as a normal issue instead.

## Response

We aim to acknowledge reports within a few days and to land a fix or
mitigation before any public disclosure. Given this is a small
community-maintained framework, please be patient and feel free to follow up
if you don't hear back.
