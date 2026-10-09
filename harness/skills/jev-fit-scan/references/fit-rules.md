# Fit rules: does Jev fit this site?

## Contents
- [How to use this file](#how-to-use-this-file)
- [The eleven fit tests](#the-eleven-fit-tests)
- [Ways to reshape a site so that it fits](#ways-to-reshape-a-site-so-that-it-fits)
- [Verdict labels](#verdict-labels)
- [The shadow test and its gate](#the-shadow-test-and-its-gate)
- [When a Jev fact changes](#when-a-jev-fact-changes)

## How to use this file

Every test below reads a fact from the facts card that you built from the live docs in this
run. The tests name the fact, never its value, so that a new Jev release changes the answer
with no edit to this file. When the text gives a value as an example, it is dated, and the
live page wins.

Run all eleven tests on every site that passes test 1. A site needs a clear answer on each
test, with `file:line` or a measured number as evidence, before it can get a TEST label.

**A TEST label needs a gain that is measured or structural.** A structural gain exists by
construction: a band that nothing rules on today gets a ruling, or a checker that can only drop
sentences can no longer change wording. "Jev's probabilities are better calibrated than the
incumbent's" is neither measured nor structural. It is a vendor claim. A working incumbent on
a small fast model, with no failure on record and no measured spend share, gets NOT NOW
("measure the spend share first"), however cleanly its output maps onto the question types.

**Rank the TEST sites in this order:** (1) a gap on record: an unruled band with a backlog, a
failure class in an issue, an incident note or a failing eval, a cost cut in the code with a
counted share of items that get no ruling; (2) a structural gain;
(3) a measured cost or latency gain, largest share first. A development-loop site ranks by the
same order. Its usual measure is the duration and the token count of the step per pull request
or per session, from the CI log or the agent's own receipt.

## The eleven fit tests

**1. Is the output that code READS a closed set?**
List every output field and its consumer. The shapes that fit are the live question types (on
2026-09-19: pick one of N, grade on ordered levels, yes/no probability). A list of indexes
fits as one yes/no per item. A generated field that nothing reads does not count. A
generated field that code reads blocks a full swap: look at "cascade" and "split" below
before you write NO.
Then read the option list that the site would send, which is often an enum in the code today.
Each option must mean one distinct thing. Two options that overlap ("16GB" and "16 GB", or
"billing" beside "payments") split the probability, and neither looks confident. Merge them in
the proposed call, and count the share of a catch-all option such as "other". When the list
might not cover every input, the proposed call adds an `other` or `none of the above` option,
as the live Choice page advises, and a pick of that option goes to the path for doubt (test 5).
Without it, a shadow test counts a forced pick as a Jev miss when the option list is at fault.

**2. Does the input fit the live input limit?**
Use measured prompt sizes when logs exist, else the caps in code, read at their production
value: a cap set to 0 or missing means no cap. When inputs run over the
limit, look for a missing cap or filter first. A cap helps the incumbent too, costs nothing,
and is often the real finding. Report it on its own line, with or without Jev.

**3. Does the verdict need a kind of reasoning on the live weak-point list?**
Read the weak-point page of the newest model. On 2026-09-19 the list was: literal reading,
counting and numbers, date and time comparison, indirection over several steps, a large state
full of irrelevant detail, adversarial content, contradictory instructions, no structural
consistency between question types, and text generation. When the verdict depends on one of
these, either move that part to code (see "split") or write NO and cite the weak point.

**4. Do the language and the input kind match what Jev supports?**
Check the data, not the code: fixtures, eval sets, sample records. A non-primary language
lowers accuracy by an amount nobody published, so it turns a TEST into "test on this
language first".

**5. What does a wrong verdict do?**
When it only annotates, a direct test is fine. When it destroys something (merge, delete,
retire a fact, block a user, send a message), the first step is a shadow test, the undo path
stays in place, and the threshold for the destructive class is set on precision, not on
overall agreement. A shadow test is read-only, so this test never lowers a site's rank: a
destructive site can be TEST FIRST. What it rules out is a live swap as the first step.
Then name where a doubtful verdict goes: a person, a review queue, or the incumbent (see
"cascade"). Give the `file:line` of that path when it exists. When the site has no such path,
the proposal includes one, and the report says that it is new work. A site that acts on every
verdict, with no path for doubt, does not get a TEST label.

**6. What is the incumbent, and what does one verdict cost on each side?**
- A frontier model: a saving is plausible. Compute it.
- A small fast model: the saving per call is small. It matters only with a large spend share.
- A purpose-built model (reranker, moderation endpoint, classifier): usually no gain. Check
  what the vendor's cookbook compared against. A gain over keyword search says nothing about
  a cross-encoder.
- Plain code: Jev adds cost and a network call. Only a quality gain can justify it.
Read the incumbent's price from its pricing page (section 4 of `external-evidence.md`).
Count requests as well as tokens. Jev asks many questions over one state in one request, but
a per-candidate design sends one request per candidate. Compute tokens per verdict on both
sides at the live prices.

**7. Is the gain measured?**
A cost claim needs the site's measured share of spend. A speed claim needs the measured
duration of the step. A coverage claim (an unruled band, a catch-all bucket, a sample in place
of the full set) needs the counted share of items that get no ruling today, and the cost of a
ruling on all of them: item count x tokens per item x the live price. Without the number, the
gain column says "not measured", and the site cannot be TEST FIRST on those grounds.
Count it in this run. A count in a code comment, a docstring or an earlier report is a lead:
re-run it, or the site is NOT NOW with the command under "Measure next". An incident on record
proves the gap exists, not its size. Count the items the incumbent gets wrong, not the whole
bucket: a catch-all bucket that is mostly right leaves little to gain. When only a person can
tell right from wrong, hand-label a sample.

**8. Does a repo rule block this class of change?**
Quote the rule. A judge that produces the repo's evals of record is a NO by default: a new
judge breaks comparison with every earlier result.

**9. What accuracy evidence exists for this class of task?**
Vendor cookbook numbers are vendor claims. Note the baseline they used and whether they
scored against a labelled answer key at all. Look for independent results (sections 2 and 3 of
`external-evidence.md`). When none exist,
the first step is a small labelled test on the repo's own data.

**10. Which data would leave, and on what terms?**
Name the data class. Read the live data terms (training, retention, region). State it as the
owner's call. Never decide it for them, and never send data to find out. For a development-loop
site the data is the source code and its diffs. Say so: the incumbent may run under terms that
the owner accepted already, and Jev's terms are a new decision. A gateway route (see the fact
"Access routes") adds the gateway as a second processor with terms of its own: read both.

**11. How would the repo call Jev?**
Check the SDK's age, release pace, breaking changes and downloads in the package registries
today (section 1 of `external-evidence.md`). When the
SDK is young, plan one plain HTTP call with the HTTP client the repo has already. That adds no
dependency. When the repo calls its models through a gateway already (the `llm_call` hits show
the host), check the live list of access routes: a gateway that serves Jev needs no new vendor
account. Name the route's maturity as the gateway states it, for example "alpha".

## Ways to reshape a site so that it fits

A site that fails test 1 as written can pass after a small redesign. Propose one only when it
keeps the function that downstream code depends on.

- **Select, do not generate.** The model rewrites a text to remove bad parts. Instead, split the
  text in code, ask one yes/no per part, and let code drop the failures. The wording can no
  longer drift, and guards against a rewrite that grows or shrinks become unnecessary.
- **One yes/no per item.** A multi-select output ("which of these facts are duplicates") becomes
  one question per candidate, all in one request over one state.
- **Cascade.** Jev gives the verdict. The incumbent runs only when Jev's answer confidence is
  low, or when generated text is truly needed. Quality holds because the hard cases still
  reach the incumbent.
- **Screen, then escalate.** The reverse of a cascade: the incumbent keeps the ruling, and Jev
  decides what the incumbent reads. Many cheap questions run over every item (each hunk of a
  diff, each log line, each comment, each event of a monitor); only the items above a threshold
  reach the incumbent. It applies at run time as well as in the development loop: the ledger
  group `screens` lists model calls that answer "nothing" for most items. The
  gain is the incumbent's input tokens and its wall time, so measure those first: the share of
  its input that ends with no finding is the ceiling of the gain. The risk is
  silent: an item that the screen drops is never seen. Set the gate on the screen's recall, and
  keep a random sample of the dropped items in front of the incumbent, so that a miss can show.
- **Split judgment from arithmetic.** Jev extracts the parts (the date named in the text, the
  amount, the unit). Code compares, counts and converts.
- **Lift the cut.** A sample or a cap exists because the incumbent costs too much for every item.
  Jev rules on every item, and the incumbent reads only the items that Jev flags. Keep the cap
  when it guards the input limit (test 2) and not the cost.
- **Ask at the checkpoint** (ledger group `checkpoints`). An agent loop asks a frontier model "is the task complete?" at each
  step, and code reads only the yes or no. One yes/no per step fits when the state is the last
  result and a short goal. A judgment over the whole history is indirection over several steps:
  run test 3 before any TEST label.
- **Route by tier** (ledger group `routers`). One Choice per request picks the tier: no model
  (a fixed command or a lookup), a small model, or a large model. Code acts on the pick, and a
  low-confidence pick goes to the larger tier. The gain is the share of requests that move to a
  cheaper tier, and the largest part is the share that needs no model at all: count both on real
  requests. When a frontier model picks the tier today, the saving is also that model's call.
  When a rule picks it, the gain is quality: count the requests that the rule sends to the wrong tier.
- **Rule on the middle band.** A heuristic score keeps its top and bottom bands. Jev grades only
  the middle band, which today goes to a log or a queue.
- **Composite score.** One vague judgment becomes several atomic grades, combined by weights in
  code that the team can tune and test.
- **A grade ranks; it does not pass or fail by itself.** A third-party test put
  headlines in a sensible order with a 1-to-10 grade, but real published headlines got only 5 to
  7. For a pass mark, ask a yes/no on the named condition, or set the cut on labelled items in
  the shadow test. This applies to the two reshapes above.
- **Filter before you send.** When the state is large, select the relevant fields in code first.
  That also addresses the "large state" weak point.

## Verdict labels

| Label | Meaning |
|---|---|
| **TEST FIRST** | The best site. It passes all tests, or fails only on "accuracy unknown". One per report at most. |
| **TEST NEXT** | It passes, but with less value or more risk than the first. |
| **LATER** | It depends on the result of a TEST site, or on a redesign in a vendored library. |
| **NOT NOW** | A repo rule blocks it, or a measurement that decides it is missing. Name the rule or the measurement. |
| **NO** | It fails a test. Name the test and the Jev fact behind it, so that a later run can re-open it. |
| **OUT** | Text generation and other work with no verdict in it. One row for all of it. |

A report with no TEST row is a good report when that is what the evidence says.

## The shadow test and its gate

Plan this for the TEST FIRST site. Do not build it unless the user asks.

1. **Data**: a few hundred real items from logs or from a dry-run endpoint. Pull them read-only.
2. **Labels**: from existing ground truth where it exists. Else label a sample with clear rules,
   and check the ambiguous items with a strong model or a person. The incumbent's logged
   decisions are also the material for the first criteria: write the criteria from one part of
   the labelled set, and run the gate on the other part.
3. **Run**: Jev beside the incumbent. Log both answers. Act on the incumbent only.
4. **Gate**, fixed before the run: precision of the destructive class; for a screen, recall
   against what the incumbent finds with no screen; agreement with the labels
   compared with the incumbent's agreement; the share of items that Jev sends to "uncertain";
   for query paths, the repo's own eval suite, where a move inside its noise band is no change.
   Add one gate on the threshold itself: list the confidence of every miss beside the confidence
   of the hits. A clean gap between them gives the threshold. Misses at high confidence point at
   the option list or the question (two options that overlap, or two questions in one), not at
   the threshold. With no clean gap, the site does not fit as asked: split the question and
   test again, or leave the site with the incumbent.
5. **Cost**: item count x tokens per item x the live price. State it in the report.
6. **Rollout, only after the gate passes**: behind a flag that defaults to off, with the
   incumbent as fallback on any error or timeout, and the versioned model id pinned. An alias
   such as `jev-latest` moves when the vendor ships, and thresholds tuned on one version do
   not carry to the next. Log every verdict with its probabilities and, when it arrives, the
   real outcome. That log is the only input from which a stronger model or a person can revise
   the criteria and the thresholds later, and it is the labelled set for the next version.
   Track the share of items that Jev decides alone, and run the gate again before any criteria
   edit goes live. A higher share does not show that the picks are correct, and criteria that
   grow by one case at a time start to contradict each other (test 3).

## When a Jev fact changes

Step 1 of the skill tells you which facts changed. This table tells you which tests to run again.

| Fact on the facts card | Tests that read it | When it changes |
|---|---|---|
| Question types and what they return | 1, reshape list | A new type can remove a redesign. A native multi-select, for example, ends the one-request-per-item cost. Derive the shapes again from the live list. |
| Text output (none) | 1, the OUT row | When Jev can write text, the OUT row becomes a list of cost candidates. Compare the price per output token with each incumbent. |
| Input limits | 2 | Re-open every NO that cites size. |
| Price, and whether output is free | 6, 7 | Compute the cost per verdict again. |
| Weak-point list of the newest model | 3, 5 | A removed weak point re-opens the NOs that cite it. A new weak point sends the TEST rows through test 3 again. |
| Languages and input kinds | 4 | New languages or image and audio input re-open the sites that were blocked on them. |
| Customization (none) | 9 | Fine-tuning or adapters re-open the sites where domain accuracy was the doubt. |
| Data terms: training, retention, region | 10 | Rewrite the "your call" card. |
| Rate limits | 6 | Check batch and backfill sites against the new limits. |
| SDK state | 11 | A stable SDK can replace the plain HTTP plan. |
| Access routes: the direct API, gateways | 8, 10, 11 | A gateway that the repo uses already can lift a "no new vendors" block. It also adds a second set of data terms. |
| Cookbooks and patterns in the index | skill step 3 | A new entry is a new shape of site. Search the repo for it. |
| Model ids and aliases | shadow test step 6 | Pin the new versioned id. Tune the thresholds again. |
