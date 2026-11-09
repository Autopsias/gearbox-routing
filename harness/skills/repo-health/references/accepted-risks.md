# Accepted risks

`sec.dep-vulns` and `sec.supply-chain` read an optional
`.claude/health/accepted-risks.json` in the target repo: a JSON list of objects
with exactly the keys `id, package, kind (advisory|maintenance), reason, ruling,
accepted_on, review_by, expires, fix_available, accept_despite_fix`. After the
tool runs, ask which findings are still open:

`python3 <skill>/scripts/health.py accepted --repo <repo> advisory:<id> maintenance:<package> …`

A `kind=advisory` row matches an advisory id; `kind=maintenance` matches a package
the supply-chain probe warns on. An unexpired match is reported as
`accepted (<ruling>)` and does not fail the row: when `all_accepted` is true, record
`pass` with the printed `detail`. An expired row is ignored and named. A row with
`fix_available=true` counts only when `accept_despite_fix=true`. A malformed file
or row is named in `detail`, never read as "nothing accepted".

