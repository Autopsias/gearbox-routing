# Bootstrap calibration exemplar

The shipped known-bad / known-good pair, used when the calibration archive has no run of its own yet
(`exemplar_source: "bootstrap"`). It is deliberately generic — a vendor-renewal decision, no domain
knowledge required.

**What this is for:** calibrating *how strictly* a critic reads, not *what* it reads for. The verdicts
below are against this exemplar's own illustrative items. **The run's own compiled rubric is the only
rubric being scored.** Never import an item from here, and never let the exemplar's subject matter
influence a finding about the memo under review.

A run on the bootstrap pair is fully valid and carries no cap. Once a `SATISFIED` run has been archived,
selection prefers the archived pair (`intake-and-rubric.md` Step 7).

---

## Known-bad excerpt

> **Vendor Platform Review**
>
> Our current analytics vendor contract is up for renewal soon. The platform has served us reasonably
> well, though there have been some issues with reliability and the team has raised various concerns
> over the past year. Pricing has increased significantly. Several alternatives exist in the market and
> some of them look promising. There are risks either way, and switching would obviously involve
> effort. On balance we think it makes sense to consider our options carefully before committing to a
> multi-year renewal.

**Verdicts — 0 of 5.**

| Item (illustrative) | Verdict | Why |
|---|---|---|
| States its recommendation in the first paragraph, before the reasoning | **FAIL** | "Consider our options carefully" is not a recommendation; no action, no timeframe, and it arrives last. |
| Names a specific quantity or date tied to the recommendation | **FAIL** | "Soon", "significantly", "several", "some" — every quantity is a hedge. |
| Every load-bearing claim carries a stated ground | **FAIL** | Reliability issues, concerns, price increase: all asserted, none grounded. |
| Names a specific objection and answers it | **FAIL** | "Risks either way" names no objection and answers none. |
| States why the decision matters now | **FAIL** | The renewal date — the only forcing event — is never given. |

**The trap this excerpt exists to teach:** it is fluent, well-mannered, correctly structured prose, and
it contains almost no information. A lenient critic scores it 3 or 4 of 5 on the strength of its tone.
Fluency is not a substance marker.

## Known-good excerpt

> **Renew the analytics contract for 12 months, not 36.**
>
> Recommend a 12-month renewal at $148k, signed before the 30 September expiry, instead of the 36-month
> term the vendor is offering at $121k/year. The 36-month term saves $81k over three years, but our
> event volume has grown 140% in 18 months (source: billing exports, Jan 2025–Jun 2026) and the
> contract prices overage at $0.004/event above 2bn — at the current growth rate we cross that ceiling
> in month 14, which turns the discount into a $60k–$95k overage exposure we cannot cap.
>
> The strongest argument against this is that the vendor has signalled it will withdraw the $121k rate
> after this cycle, so a 12-month renewal likely costs more in 2027. That is probably true; we accept
> a worse 2027 rate in exchange for the option to leave, because two competitors now support our
> event schema natively (verified against their public API docs, 2 August) and a 36-month term removes
> that option entirely. **We would take the 36-month term if the vendor caps overage at 2.5bn events
> in writing** — that single change removes the exposure the recommendation rests on.

**Verdicts — 5 of 5.**

| Item (illustrative) | Verdict | Why |
|---|---|---|
| States its recommendation in the first paragraph, before the reasoning | **PASS** | Sentence one: the action, the number, the term, and the deadline. |
| Names a specific quantity or date tied to the recommendation | **PASS** | $148k and 30 September, both recurring in the body. |
| Every load-bearing claim carries a stated ground | **PASS** | Growth figure → billing exports with a date range; competitor claim → public API docs with a check date. |
| Names a specific objection and answers it | **PASS** | The withdrawn rate is named, conceded as probably true, and answered with the reason the trade is still worth it. |
| States why the decision matters now | **PASS** | The 30 September expiry, and the month-14 overage ceiling. |

**What separates the two is not length or polish — it is that every sentence in the second excerpt
would be falsified by a specific fact.** That is the standard to calibrate against.

**One further thing the good excerpt does that a critic should notice and credit:** it states the
condition under which its own recommendation flips (a written 2.5bn overage cap). A memo that names
what would change its mind is doing the reader's work for them.
