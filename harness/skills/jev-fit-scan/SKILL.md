---
name: jev-fit-scan
description: "Scans the current repo for every place where software makes a verdict (model calls with closed-set output, generated text where a selection would do, heuristic score bands that nothing rules on, work that a cost cap cut short, model or agent steps in CI, hooks and review gates) and judges where Jev, TypeSafe's verdict-only model, would raise quality or cut cost with no loss of function. Re-reads the live Jev docs on every run, so new models, limits, prices and patterns change the verdicts. Use when the user says jev fit scan, where can I use Jev here, check this repo for Jev, TypeSafe System One fit, or wants Jev re-checked after a new release."
disable-model-invocation: true
argument-hint: "[--deep | --no-deep] [--prod-read] [--opus-judge] [path or focus area] [previous report path]"
effort: medium  # two scripts do the sweep; the work is reading sites and judging them
---

# /jev-fit-scan — where does Jev fit in this repo?

Jev answers typed questions about a block of text: it picks, grades, or gives a yes/no
probability. It is cheap per token, which makes the usual error a confident "replace X with
Jev" that rests on a spend share nobody measured, an output field nobody checked, or a vendor
benchmark taken as proof. This scan is built to avoid those three errors. It produces a
report. It changes no code.

**Words used here.** A *site* is a place in the repo where software makes, or should make, a
verdict. A *verdict* is a pick, a grade, or a yes/no. The *incumbent* is what the site uses
today. The *development loop* is the steps that build and check the software: CI, git hooks,
review gates, agent skills. The *facts card* is what you read about Jev in this run. The
*snapshot* is the dated copy of those facts bundled with this skill. A *shadow test* runs Jev
beside the incumbent, logs both answers, and acts on the incumbent only.

## Hard rules

- **Read-only.** Change no code and send nothing from the repo to TypeSafe or to a search
  engine. The scan reads the repo, TypeSafe's public docs and public web pages, searched with
  public terms only. The owner decides later whether any data may leave; a scan that already
  sent some took that choice away.
- **Production only on request.** Read production logs and config only when the user passes
  `--prod-read` or says so, and then only read. Without it, list the commands under "Measure next".
- **Never look for API keys.** Do not read `.env` files, key stores or environment dumps, and do
  not check whether a Jev key exists.
- **A number is measured in this run, or it is labelled.** Write "not measured" plus the command
  that would measure it. Label vendor numbers as vendor claims.

## Checklist

Copy this into your working notes and tick it as you go.

```
- [ ] 1. Refresh the Jev facts from the live docs -> facts card + what changed
- [ ]    ... then the external evidence: SDK health, vendor test method, independent results
- [ ] 2. Learn the repo's rules and words
- [ ] 3. Inventory the sites (script twice: find the model wrapper, then run with --wrapper)
- [ ]    ... and give EVERY ledger row a one-line disposition before any deep reading
- [ ]    ... ledger over 90 rows, or --deep given, and no --no-deep? -> section "Deep mode"
- [ ] 4. Read each site end to end (input, incumbent, output fields that code READS, cost of an error)
- [ ] 5. Judge each site with references/fit-rules.md
- [ ] 6. Measure what decides a verdict; incumbent prices; production only under --prod-read
- [ ] 7. Write the report, then re-open every file:line and re-check every number in it
```

## 1. Refresh the Jev facts

```bash
python3 <skill-dir>/scripts/jev_docs.py --out <scratch-dir>/jev-docs
```

The docs index is the only fixed URL. The script finds every other page through it, because
page URLs carry model versions and move.

- **Exit 0.** Read every page under `READ FIRST`. Build the facts card: model ids and aliases,
  question types and what each returns, input limits, price, rate limits, input kinds,
  languages, customization, data terms, the weak points of the newest model, and the SDK
  state. Write the source page beside each fact.
- **The script lists what changed since the snapshot.** Read every `NEW page`. A new cookbook or
  pattern is a new shape of site: derive two to five search words from it and search the repo
  for them in step 3. For each `CORE PAGE CHANGED`, compare the page with
  `references/jev-snapshot.md` and list the facts that differ. Then open the table "When a
  Jev fact changes" in `references/fit-rules.md`: it names the fit tests that each fact
  moves. Apply those tests with the new fact. A removed weak point re-opens every earlier NO
  that cited it.
- **Exit 2 (docs unreachable).** Try a web fetch or web search tool for the docs index. If that
  fails too, use the snapshot, and mark every Jev fact in the report "snapshot <date>, not
  re-checked".
- **Do not rewrite this skill's files during a scan.** When the snapshot is more than about
  three months old, or the diff is long, say so in the report. The refresh is one command in
  the skill's source: `jev_docs.py --write-snapshot <date>`, plus an edit of `jev-snapshot.md`.

**External evidence.** TypeSafe's docs cannot say whether the SDK is safe to depend on, whether
anyone outside TypeSafe confirms the accuracy claims, or what the incumbents cost. Collect
sections 1 to 3 and 5 of `references/external-evidence.md` now; section 4 (incumbent prices)
waits for step 6, when the incumbents are known.

## 2. Learn the repo's rules and words

Read the agent and contributor files (`CLAUDE.md`, `AGENTS.md`, `README`, `CONTRIBUTING`), the
rule files they point to (`.claude/rules/`, `.cursor/rules/`, plan or roadmap notes), the glossary
(`CONTEXT.md` or similar), the index of design records, eval results, and the agent's memory index
(`~/.claude/projects/<repo path, "/" as "-">/memory/MEMORY.md`). You need five things from them:

- **Standing rules that block a class of change**, such as a frozen eval judge, a stop on new
  retrieval experiments, or a "no new vendors" rule. A blocked site gets NOT NOW, with the rule quoted.
- **The repo's own words** for its parts. Use them in the report.
- **What data flows through the models**: customer documents, personal data, source code, and
  in which languages.
- **The eval suite and its noise band**, because the shadow test gate in step 7 uses them.
- **Failures on record**: a wrong pick, a miss, an incident. A gate or a host choice needs one.

## 3. Inventory the sites

```bash
python3 <skill-dir>/scripts/scan_sites.py <repo> --out <scratch-dir>/hits.json
```

1. Look at the top-level layout. Drop plan, archive, evidence and generated directories with
   `--exclude DIR`, or they bury the code hits.
2. Read the list `probable model wrappers`. Most repos call models through their own function.
   Open the top candidates, confirm the real wrappers, then run the script again with
   `--wrapper 'name1|name2'`. Without this step you find the wrapper and miss every site behind it.
3. Cover what a regex cannot see:
   - **Libraries that call a model for you** (agent, graph, RAG and extraction frameworks). Run the
     script once more with the library's installed directory as `<repo>` (for example
     `.venv/lib/python3.x/site-packages/<library>`), then list its model calls and their output
     schemas. They are often the largest share of the spend, and the repo's own code hides them.
   - Prompts kept in a database, a config service or a CMS.
   - Verdicts that people make by hand, or that nobody makes: a mostly empty category column, a
     `tagged_by` or `approved_by` field, a status that a person sets from a short list, a table or
     inbox that users judge row by row, a notebook that analysed a sample once. Look at the
     schema, the UI components and `notebooks/`.
   - The new shapes from step 1.

4. **Work through the ledger before any deep read.** The script ends with a ledger of likely
   sites in thirteen groups, and it prints what each group means. Each row prints the two lines of
   wording that put it there, so most rows need no file open.
   - Write one line for every row: the kind of site, or "not a site" and why (provider plumbing,
     a test helper, a one-off maintenance script). Add one row for each model call inside a library.
   - Then spend the deep reads in this order: bands with a backlog on record, cost cuts, rewrites,
     then screens, verdict calls and development-loop steps, largest likely spend first.
   - A `dev_loop` row is the runner: follow it to the prompt or skill file that it runs. A
     `host_choices` row is a folder that the host agent picks from: look for wrong picks on record.
   - When time runs out, the rows you did not open go in the report as unread. Never drop them.
     A finished ledger with shallow notes beats three deep reads and thirty rows nobody opened.

Six kinds of site exist. The last four are easy to miss, and one of them is often the best find.

| Kind | What it is |
|---|---|
| **Verdict call** | A model call whose output is a pick, a grade, a yes/no, or a list of indexes. A model router is one too, whether a model, a rule or a keyword list picks the tier: a Choice over "no model, small model, large model". The ledger group `routers` lists them: find what picks the tier and count requests per tier. |
| **Generate where a selection works** | A model writes text, but the code needs only a filter or a choice over text that exists already: drop unsupported sentences, pick a span, choose a function and closed arguments. |
| **Unruled band** | Code computes a score. The top band acts, the bottom band drops, and the middle band is only logged, queued or defaulted. A catch-all bucket (`return "other"`) is the same thing for a keyword classifier: count its share. So is code that calls itself a "proxy" for a judgment (regexes that score tone or plain language). No LLM is there today, so Jev adds a capability instead of replacing one. The ledger group `bands` lists them. A guard in code that allows or denies an agent's tool calls (a shell command, a file write) from a pattern list is the same thing: the list rules only on the spellings that someone wrote down. The ledger group `tool_guards` lists them. Look for a miss or a false denial on record. Fit tests 3, 6 and 10 decide such a site: the input can be adversarial, the incumbent is plain code, and every command would leave the machine. |
| **Work that cost cut short** | The owner wanted a ruling on every item, and cost or time forced a cut that the code still shows: a `random.sample` before a model call, a cap on the list, a comment "too expensive", a one-off analysis script that ran once. The cut is the evidence on record, and the gain is coverage: the share of items that get no ruling today. Without such a cut, "Jev could label this table" is a wish. The ledger group `cost_cuts` lists them. |
| **Missing gate, with evidence** | A failure class on record (an issue, an incident note, a failing eval, a TODO) that a cheap verdict would catch. Without such evidence, do not list it: a wish list is not a finding. |
| **Development-loop verdict** | A model or a coding agent rules while the software is built, not while it runs: an AI review step in CI, a gate in a hook, a triage of test or log output, a router that picks a skill or a tool, a model judge or audit in a test or eval folder. The incumbent is often a frontier model that reads a whole diff, so look at the reshape "screen, then escalate". The same bar applies: a measured cost or duration of the step, or a failure on record. "Jev could lint this repo" with no incumbent and no failure is a wish. The data that leaves is the source code: fit test 10. |

## Deep mode (`--deep`)

Run it when the user passes `--deep`, or by itself when the scan's `ledger:` line counts more
than 90 unique rows (repo and libraries) and the user did not pass `--no-deep`. When it starts
by itself, say so in one line first, with its cost and the `--no-deep` way out. Between 40 and
90 rows, a normal scan ends its report with one line saying that deep mode exists. Deep mode
replaces steps 2 and 4 to 7 with a workflow of parallel agents: follow
`references/deep-mode.md`, which holds the cost, the steps and the Workflow call.

Deep mode reads untrusted web pages, and its agents can still reach the repo, `~/.claude`
memory and the network. The research agent keeps its shell because it needs `gh` and `curl`,
and a Workflow agent has no option to drop single tools. Keep permission prompts on: never run
it in bypass mode.

## 4. Read each site end to end

For each site, write down:

- **Input**: what goes in, the language, the data class, and every cap on the way to the model
  (list length, characters, tokens) with its value in the production config. A cap set to 0,
  or missing, means no cap. An uncapped list sent to a model is a side finding whatever Jev's
  fit: a test scan missed a candidate cap set to 0 in production.
- **Incumbent**: which model or which code, and its price class. A frontier model, a small fast
  model, a purpose-built model (reranker, moderation endpoint), or plain code.
- **Output**: every field of the schema, and for each field **whether any code reads it**.
  Search for the field name and follow it to its consumers. A generated text field that
  nothing reads does not block Jev. A field that code reads does.
- **What a wrong verdict does**: annotates only, or destroys something (merge, delete, retire a
  fact, block a user, send a message).
- **How often it runs**: per request, per item, per batch; a development-loop step per commit,
  per pull request or per session, with its duration. Note timeouts, fallbacks and feature flags.

On a large repo, split this reading across subagents by directory when you have them. Ask for
`file:line` on every claim. Open every schema that a subagent reports before it goes into the
report: a subagent's summary is a lead, not a reading.

## 5. Judge each site

Read `references/fit-rules.md` now. It holds the eleven fit tests, the ways to reshape a site
so that it fits, the verdict labels, and the table that ties each test to the Jev fact it
depends on. Judge with the facts card from step 1, never with remembered numbers.

## 6. Measure what decides a verdict

Measure only what can change a verdict.

- **Spend share per site.** Use token logs, cost meters or test runs that you can reach locally
  (the `usage_cost` hits show where they are). Spend moves when the code moves, so take a
  window on the current build.
- **Input size against the live limit**, from logs when they exist, else from the caps in code.
- **Latency of the step**, when the claimed gain is speed.
- **Incumbent prices**: section 4 of `references/external-evidence.md`, for every named product
  in a TEST, LATER or NO row.
- **Production**: under `--prod-read`, run section 6 of that file. Without it, put its commands,
  filled in for this repo, under "Measure next". A number the owner can get with one command
  beats a paragraph that says it is unknown.

## 7. Report

Use this template. The answer comes first. Most repos have zero or one good site, and "Jev
fits nowhere here" is a valid and useful answer.

```markdown
# Jev fit: <repo name>
**Answer:** <one or two sentences: where Jev fits, or that it does not, and the main reason>

## Jev facts used (live docs read <date> | snapshot <date>, not re-checked)
| Fact | Value | Source page | Changed since snapshot? |

## What changed in Jev since the snapshot
<new pages, changed facts, and which verdicts they moved. "Nothing" is a valid entry.>

## Fit table (best first)
| # | Site (file:line) | Kind | Today | With Jev (question type + what code does with it) | Gain: quality / cost / latency | Risk or blocker | Verdict | Re-open when |

## Where the spend and the time go
<measured shares per site, with the window and the command. Else "not measured" + the command.>

## External evidence
<SDK health with dates; how the vendor measures accuracy; independent results per task class;
incumbent prices and the cost per verdict on both sides; the network floor and where from. URL each.>

## Data that would leave, and its language
<the data class per TEST site, the languages in the real data, the live data terms. The owner decides.>

## Coverage
<ledger rows: N given a disposition, M read in depth. Name every row left unread.>

## Measure next
<each missing number that could change a verdict, with the exact command, filled in for this repo>

## The call, for site #1
<five lines at most, no SDK code: the state, the question type, the options or levels in words, the criteria and their source, the threshold, what code does on each side>

## Next step: shadow test for site #1
<data source, sample size, how labels are made, the gate, the cost at the live price, the undo path>

## Your call (at most three options)
<each option with its trade-off in one line; a recommendation; what happens if nothing is done>
```

- Keep the report as short as the findings. In "Jev facts used", list only the facts that a
  verdict in this report depends on. When no site reaches TEST, write "nothing to decide" in
  place of the call, the next step and the option card. Do not pad the card with future ideas.
- Give TEST FIRST to one site at most. The recommendation in "Your call" starts with that site.
  When it starts with another site, the card states the reason (for example the data terms).
- Every NO and NOT NOW names the fact or rule it depends on in the column "Re-open when". A
  later run flips it when that fact changes.
- Text generation work goes in one OUT row, with its share of the spend when that is known.
- When the user gives an earlier report, or the repo holds one (search for the heading
  `# Jev fit:`), list the verdicts that changed and why.
- Deliver the report in the reply. Save a file only where the repo keeps such records, or
  when the user asks. When the user has reporting rules of their own, those rules win.

## Gotchas

Each one happened in a real scan.

- **a spend share went stale in six days.** A note said that verdict calls were
  about half of one pipeline's spend. A fresh one-hour window gave about a tenth. The old window covered a
  different ingest path, and a later code change removed many of those calls. Measure on the
  current build and the current path, and state the window.
- **a "blocker" field that nobody read.** A schema had a generated
  `name` field, recorded as the reason Jev could not serve the call. A search showed that no
  code read `name`. Only the two id fields were in use.
- **the best site was not a model call.** A matching hook wrote mid-confidence pairs
  to a log, and nothing ruled on them. A scan of model calls alone never sees this.
- **the vendor's comparison was weaker than the repo's incumbent.** The reranking
  cookbook shows a gain over keyword search. The repo used a cross-encoder already, at about
  the same price per token, with all candidates in one request.
- **the repo's own wrapper hid the model calls.** Three of five known sites had no
  SDK import. They called a wrapper function. The second script run with `--wrapper` found them.
- **a large repo, a time limit, and no ledger.** A test scan of a large repo
  spent its time on three deep reads, never opened four other sites or the library's model
  calls, and did not say so. The ledger step and the Coverage section exist because of this run.
- **a clean mapping outranked a real gap.** A test scan gave TEST FIRST to a
  grading call on the cheapest model tier, because its output mapped neatly onto the question
  types. Its gain was "better calibrated", with no measurement. The same scan pushed an unruled
  score band with a documented backlog down to LATER, because a merge is destructive. A shadow
  test merges nothing. The ranking rules in `references/fit-rules.md` exist because of this run.
- **right sites, missing evidence.** A deep scan matched a manual analysis on every
  site but missed what the manual one found on the web and in production logs, and a cap set to
  0. The external evidence step, `--prod-read` and the cap field exist because of this.
- **outside evidence changed a verdict's reason.** A manual analysis rejected
  Jev for reranking partly because "nobody compared it with a cross-encoder". Five minutes of
  web search found an independent test with Jev level with Cohere Rerank 4 Pro on English data,
  and another where Jev lost to Claude Haiku 4.5 on phishing. Search before you write "no evidence".
- **the scan could not see the development loop.** The scanner dropped every
  dot-directory as "not the product", so no review gate or CI step reached the ledger. The first
  fix listed rows on a test repo that were mostly prose naming a CLI. The `dev_loop` group
  now holds only code and config that run a model, the review gates among them.
- **the scanner saw none of four public build shapes.** A keyword classifier with a
  catch-all bucket, a sampled study, a monitor that answers NONE, an agent loop: 0 ledger rows.
  Three groups came from it. On a real repo several of their rows were noise (FastAPI's
  `response_model=`, `analysis_complete`), and most true rows pointed at one site.
- **a month-old docstring count made a TEST FIRST.** A deep judge ranked a
  classifier first on a catch-all count dated a month earlier. A replay found
  only a handful of real misses. Fit test 7 now asks for the misses, counted in this run.
- **a model router, then a shell guard in a hook, gave 0 ledger rows.** The groups `routers` and `tool_guards` came from them.
