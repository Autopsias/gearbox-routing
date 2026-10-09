# External evidence and production measurements

The repo and TypeSafe's own docs cannot answer three questions that change verdicts: is the SDK
safe to depend on, does anyone outside TypeSafe confirm the accuracy claims, and what does the
incumbent cost. Production data answers a fourth: how large each site really is. A test scan
that skipped all four matched a manual analysis on every site and missed every number around
them.

## Tools, in this order

Use the research servers, not the built-in web search. Tried with the same
question: one Exa search returned six independent evaluations with baselines, and `gh` found
nine benchmark repos. Load the MCP tools through ToolSearch when they are deferred.

| Need | Tool |
|---|---|
| Write-ups, reviews, independent tests | `mcp__exa__web_search_exa`. Describe the page you want ("independent benchmark comparing <model> against another model, with a baseline"), not keywords. Do not add Exa's `github` category: it returned mostly unrelated pages. |
| Benchmark and integration repos | `gh search repos '<model> bench' --created '>=<launch date>'`, then `gh api repos/<owner>/<repo>` and the README |
| Code that calls the vendor | `mcp__grep__searchGitHub` with a literal pattern, for example the API host name |
| Read a page | `mcp__exa__web_fetch_exa` (several URLs in one call); `mcp__ref__ref_read_url` for docs pages |
| Library and SDK docs | `mcp__ref__ref_search_documentation` |
| Registry and API numbers | `curl` on the JSON endpoints in section 1: these are API reads, not searches |
| Built-in WebSearch and WebFetch | only when the Exa and Ref tools fail in this run. Say so in the report. |

## Rules for every web request

- Search with public terms only: the vendor, model, package and incumbent product names. Never
  put the repo's code, data, file names, customer names or internal terms in a query or a URL.
- Cite the URL and the date read for every fact. A page by TypeSafe is a vendor claim, even
  when it reports a benchmark.
- When a source fails (rate limit, 404), try once more after about a minute, then write "not
  measured" with the URL. Never fill the gap from memory.

## 1. SDK health (fit test 11)

| What | Where (tried 2026-09-19) |
|---|---|
| Versions, release dates | `https://pypi.org/pypi/<package>/json` (`info.version`, `releases`) and `https://registry.npmjs.org/<scope>%2F<name>` (`dist-tags`, `time`) |
| Downloads last month | `https://api.npmjs.org/downloads/point/last-month/<package>`; for PyPI, `https://pypistats.org/api/packages/<package>/recent` (it returned 429 once that day: retry, then report "not measured") |
| Breaking changes | the SDK changelog pages that `jev_docs.py` saves |
| Stars, age, last push | `https://api.github.com/repos/<owner>/<repo>` (`stargazers_count`, `created_at`, `pushed_at`) |

Take the package names from the docs' SDK pages. Report the numbers with the date, and
compare them with the user's dependency rule when one exists (for example "actively maintained
and widely used").

## 2. How the vendor measures accuracy (fit test 9)

Find the page or the launch post that carries the vendor's benchmark. Record what the answers
were compared against: human labels, an answer key, or the answers of other models. Agreement
with other models is not accuracy. Record whether a calibration curve is published.

## 3. Independent results (fit test 9)

Search for tests of Jev by people outside TypeSafe, per task class that the fit table needs
(entity matching, moderation, answer checking, classification, reranking, and for a
development-loop site: code review, security triage, lint-style checks). A post that reports a
saving with no method is a lead to follow, not a result. Record who ran the
test, the baseline, the numbers, and the URL. Do not reproduce a result unless the user asks.
"None found for <task class>" is a finding.

## 4. Incumbent prices and capabilities (fit test 6)

For every incumbent in a TEST, LATER or NO row that is a named product (a reranker, a
moderation endpoint, a hosted model), read its pricing page: the price per million tokens or
per call, any free allowance, the batch shape (one request for all candidates?) and the
languages. For a model reached through a gateway, the gateway's price list wins over the
provider's. Compute the cost per verdict on both sides with the live Jev price.

## 5. Network floor

Time one unauthenticated request to the Jev models endpoint from where the scan runs. It
returns 403 without a key, which is enough to time the connection:

```bash
curl -s -o /dev/null -w 'connect=%{time_connect} tls=%{time_appconnect} total=%{time_total}\n' \
  https://api.typesafe.ai/v1/models
```

Label the location ("from the developer machine"). The production server's floor is the one
that matters for a live site; measure it only under `--prod-read`.

## 6. Production measurements (only under `--prod-read`)

The user grants these by passing `--prod-read`, or by saying so in the request. They are
read-only log and config reads. Never write, restart, or read key stores or environment dumps.
Without the grant, put each command in the report under "Measure next", ready to run.

| Measure | Why | How |
|---|---|---|
| Spend share per caller, current build | fit tests 6 and 7 | aggregate the repo's token or cost log by caller over a stated window |
| Input size per site against the live limit | fit test 2 | the same log: the share of calls over the limit, not only the mean |
| Size of each unruled band | TEST FIRST sizing | count the band's log event over a window |
| The production value of each cap and flag | fit test 2, live check | read the deployed config, not the code default |
| Step latency | a speed claim | the step's timing log, else note that none exists |
| Language mix | fit test 4 | a small sample of the real inputs |
| Network floor from production | a live site | the curl above, run on the server |

A meter can read less than the bill. When the repo has a wallet or billing read, report shares
from the meter and label them as shares.
