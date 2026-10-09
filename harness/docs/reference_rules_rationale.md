# Why these rules exist

The rules themselves live in `~/.claude/CLAUDE.md`. Each rule there carries a
`(why: docs/reference_rules_rationale.md#anchor)` pointer to its section here.
This file is NOT loaded into base context — read it when you want the evidence
behind a rule, when you are tempted to relax one, or when you are writing a new
one and need the house style for justification.

Each heading quotes the first words of the rule it explains.

## Keep any project-level PostToolUse hook CHEAP

It blocks the loop on every edit, and the cost is invisible except as the session
feeling slow. Trader's ruff-autoformat hook measures ~184 ms median and earns its
place; full lint passes, test collection and network calls do not.

## Vet every NEW dependency before you add it

Supply-chain attacks ride on abandoned or obscure packages — reuse and stdlib come
first, but when a package is the right rung, only a maintained, popular one is safe
to install. The ponytail ladder decides WHETHER to add a dependency; this rule
decides WHICH one qualifies.

## Communication style — the whole section

These rules govern how you talk to the operator in every project. They are based on the
Federal Plain Language Guidelines and the BLUF ("bottom line up front") briefing
format.

Responses in technical shorthand force rereading and guessing; decisions can only be
made properly when context and reasons are stated simply.

## Every decision gets the briefing format

On 2026-08-22 "commit the work and close the session without its gate" was offered
and chosen, and only then found to have no implementation (gate waivers exist for two
rubber-stamp gate types, and a review gate is neither); the operator chose an outcome
that could not be delivered. Hence: never offer an option you have not verified is
executable.

The who-executes half was added. A `gearbox deploy` was blocked by the
permission classifier, so the card offered three options, the second being "add a
permission rule for `gearbox deploy`". The operator picked it — and the next turn was
not the deploy but "I need you to add it", because a standing rule bars the assistant
from editing its own permission files. The steps then arrived in a third turn, by
which point the operator had taken a different route entirely ("do it yourself I took
out auto mode"). The option was executable; nothing said by whom, or how. An option
the user must perform carries its steps in the card, so choosing it ends the exchange
instead of starting one.

## ALWAYS verify how a tool / MCP / framework / substrate actually behaves

Capabilities, causes, and costs asserted from memory instead of measured have
repeatedly turned out wrong, and the remediation a wrong diagnosis justifies is often
expensive or destructive to undo.

The refusal half was added: three refusals of a plan-state write were read
as "the classifier blocks writes", escalated to the operator four times across two
manual command runs, and the actual cause was the `cd <dir> && python <script>` shape
— the identical command as one absolute-path invocation ran instantly. Two refusals is
a correlation; varying one thing is the experiment.

## When a user reports that something you produced is wrong, missing, or not showing

Claiming something works or is fixed from indirect signals — file greps, cache
reasoning, a health/status proxy — instead of running the real operation has repeatedly
produced false confidence that later had to be walked back.

The proactive half was added after three failures in one session: a browser
diagnosis given without checking which of several Chrome instances answered ("chrome is
both logged in and the javascript shit is active, I've just checked"), a plan dashboard
declared correct from 47 balanced anchor comments while the user could see only 10 of 20
sessions rendered, and — in the retrospective for those two — a verb-extraction check
that returned zero verbs and so emitted 68 false "missing verb" findings.

## RE-MEASURE every number you are about to state in a final report

Wrong numbers inside a substantially CORRECT report are the most reproducible failure of
a long session, and the hardest to catch — the surrounding claims are right, so nothing
looks wrong. Measured externally (Leonxlnx/unlazy): every skill-run report in
a controlled six-run test carried 1-3 wrong numbers while its substance held. Measured
here: a plan dashboard declared correct from "47 balanced anchor comments" while only 10
of 20 sessions rendered.

The subagent half: a subagent's "149 passed" was seven touched test files,
not the suite; the suite was 766. A subset count and a suite count are textually
identical, so the command is the only thing that distinguishes them — asked for it on the
next dispatch, the number came back correct and named `python3 -m pytest . -q` and its
directory.

## ALWAYS end a substantive status report or handoff with a plain-language two-liner

Four times across sessions the user had to ask "in plain language, what do I do next /
what do you need from me that you can't do yourself" — and a task Claude could do (a
one-line script fix) sat on the operator's handoff list across two sessions until the
user pushed back ("bullshit analysis"); it took 30 minutes once simply done.

## NEVER end an eval cycle with narration alone

A wall of narration or a sprawling multi-page dump pushes the synthesis work onto the
user and buries the actual decision they need to make.

## NEVER probe credential stores or enumerate API keys / secrets

Credential probing is never the right tool for a legitimate diagnostic need — service
health, container status, disk, API liveness — which `~/.claude/scripts/prod-status.sh`
already serves read-only.

## ALWAYS apply the 17-criterion skill-quality rubric

Unrubric'd skill authoring reliably re-accumulates the same debt — vague descriptions,
oversized SKILL.md, terminology drift, duplication. The /skill-creator plugin can't embed
the rubric itself, so this rule is the binding.

## NEVER edit your own permission or settings files to widen your access

Self-widening permissions to route around a correct denial defeats the point of the gate
— expanding the command surface must stay a human-gated change, never self-serve.

## When a repeated step's cost visibly compounds

2026-08-23, the `example-isolation-plan` run. Sessions s09 and s09b went through roughly
six verify cycles; a measured pass took a median of 597s and a maximum of 1,381s. The operator
asked why the review gate was slow, then had to ask a second time — that time with
profanity about wasted time and tokens. Both asks were reasonable: I had watched every one
of those cycles and never brought him the cost.

When I finally measured it, the answer took two commands: the gate process had been alive
35m30s while its current `claude` child had been alive 5m29s, which means attempt 1 had
silently consumed its full 1800s timeout and been killed without a verdict. The payload
also included 32,993 bytes of the session's own captured probe logs, handed to the reviewer
with an instruction to read each file in full as ADDED code.

Two lessons. The cost was knowable at any point for the price of one `ps` call, so the wait
was never the problem — the silence was. And estimating instead of measuring made it worse:
I told him "roughly two minutes is the deterministic gate", extrapolated from a 41-test
subset, when the real figure reached 520s.

## When another session or agent is waiting on you

Several Claude sessions run against this repo at once, and they block each other for
real reasons — `gearbox deploy` refuses while another session is mid-write in
`~/.claude`. A peer that is blocked on you has no way to learn you finished except
from you.

On 2026-08-23 a peer session sent: "I have subscribed to your idle signal and will
deploy then. If you would rather I hold off longer, say so." The session it wrote to
then finished every write, ran two deploys of its own, and reported to the operator
that "`~/.claude` is quiet again, so the other session can take its turn" — without
ever messaging the peer. The peer waited and ended without deploying. Telling the
operator felt like telling everyone; it told no one who was waiting.

The same complaint had already been made three times in one earlier session, in
plainer words: "proceed but I don't see no update to the other session in the other
session", "we should commit and deploy yes, but again the other live session in this
repo got no update, fix that", and "the other session still got shit".

The mechanism exists and is cheap: `ListAgents` names every addressable peer,
`SendMessage` reaches one. Neither needs the operator's involvement.
