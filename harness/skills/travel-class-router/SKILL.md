---
name: travel-class-router
description: The Office Trust Layer's front door — classifies how far a file will travel (Internal / Travels / Board-or-Regulatory), auto-escalates the class when the content itself looks risky (live formulas, financial or legal or investor terms, external recipients, stale conflicts, unsupported numbers), and routes to the matching review depth (light lint / single-context review / full two-context review + human gate). Downgrades require a logged waiver. Use FIRST on any artifact entering the Trust Layer. Triggers on "route this artifact", "what travel class", "how far does this file travel", "what review depth", "run the front door", "classify this before review", or "should this go to the board".
---

# Travel-Class Router (front door)

When this skill fires you are routing an artifact into the Office Trust Layer. Your job is to decide **how far this file will travel** and therefore **how hard it must be reviewed** — and to do so **defensively**: a file the user calls an "internal draft" gets forwarded, so the *content* can override the *intent* and raise the class. You never lower the class without a logged waiver.

**Read these first** — do not redefine anything they own:
- `../trust-review/references/conventions.md` — enums, the command I/O contract, the `{ok, ok-with-warnings, blocked, failed}` status, the waiver rule.
- `../trust-review/references/sensitivity-routing-policy.md` — source sensitivity classes, redaction, model-routing by travel class, retention, and the manifest. **This router writes the manifest's `travel_class`, `sensitivity`, and `routing` blocks and must pass the gate before any model-run loop.**

(These live in the sibling `trust-review` skill, under `../trust-review/references/`.)

## Inputs (declared up front, per the I/O contract)
- `artifact` — the file path (required).
- `declared` — the class the user claims: `internal` | `travels` | `board-regulatory`. If absent, **ask** before routing.
- `internal_domain` (optional) — the org's email domain, to tell internal vs external recipients apart. If unknown, treat any email address as a possible external recipient and ask.

## The three classes and their review depth

| Travel class | Where the file goes | Review depth it triggers |
|---|---|---|
| **Internal** | Stays put; not forwarded | **Light lint** — mostly local checks; no model review unless a signal escalates. |
| **Travels** | Forwarded / reused internally | **Standard review** + evidence notes — single hostile-review context. |
| **Board / Regulatory** | Board, investors, clients, regulators | **Full workflow** + **two-context** hostile review + **human gate**. |

## Step 1 — Establish the declared class
Take `declared` from the request, or ask the user. Record it; you will compare it against what the content implies.

## Step 2 — Scan the content LOCALLY (never skip this)
Run the local scanner — no upload, no model:
```bash
python scripts/content_signals.py <artifact> --declared <declared> [--internal-domain <domain>]
```
It returns the detected signals and the **content-derived minimum class**. Content signals **raise** the minimum class per this fixed mapping:

| Signal | Raises minimum to |
|---|---|
| `formula` (live formulas in the file) | **Travels** |
| `financial-metric` (EBITDA, margin, %, currency, forecast/guidance…) | **Travels** |
| `unsupported-number` (high numeric density — *candidate*; confirmed vs the source packet at S03) | **Travels** |
| `legal-regulatory` (statutory/contract/privilege/MNPI language) | **Board / Regulatory** |
| `investor-board-client-term` (board, investor, shareholder, due diligence, client deliverable…) | **Board / Regulatory** |
| `external-recipient` (an external email / send-outside hint) | **Board / Regulatory** |
| `stale-source-conflict` | **Board / Regulatory** — *not content-detectable; decided from the source packet (S03). Carry forward as a re-check.* |

If the scanner returns `scanned: false` (it could not read the file), **default UP** to at least **Travels** and tell the user you could not inspect the content — do not under-route on a file you couldn't read.

## Step 3 — Compute the effective class
`effective = max(declared, content_min_class)` on the order `internal < travels < board-regulatory`.

- If `effective > declared` → **escalation**. State plainly which signal(s) caused it (e.g. *"escalated Internal → Board/Regulatory: detected board/investor terms and an external recipient"*) and proceed at the higher class.
- If `effective == declared` → proceed at the declared class.
- **Downgrade (route below `effective`) is only allowed via a logged waiver** — see Step 6. Default to **asking** the user whenever the class is ambiguous rather than guessing low.

## Step 4 — Classify source sensitivity & decide routing
Per `sensitivity-routing-policy.md`:
1. Assign each source `S<n>` a sensitivity class (`public`/`internal`/`confidential`/`restricted`); default up on doubt. The run's `sensitivity.run_max` is the highest.
2. Pick the **routing** for `effective_class × run_max` from policy §5 (local-only / excerpt / mask; single vs two-context; which model role sees what). Prefer **local-only** and **excerpts** over whole-file exposure.
3. Decide redaction mode (default `excerpt` for `internal`+, `mask` for `confidential`/`restricted`).

## Step 5 — Write the manifest blocks and RUN THE GATE
Create the run dir `.trust/<run_id>/` (`<ISO-timestamp>_<short-slug>`) and write `manifest.json` with the `travel_class` (declared, effective, source=`declared`/`escalated`, the `escalation_signals`), `sensitivity` (per-source + run_max + redaction), and `routing` blocks. Then:
```bash
python ../trust-review/references/scripts/manifest_gate.py .trust/<run_id>/manifest.json --write
```
- Gate `ok` → you may proceed to the routed review depth (Step 7).
- Gate `blocked` → **stop**. Return status `blocked` with the gate's missing keys. **No model-run loop may start.** This is the S02 acceptance gate; it is mechanical, not discretionary.

## Step 6 — Waivers (downgrade or routing override)
Any downgrade of the effective class, or any routing override (e.g. send `restricted` material to a model), is recorded as a waiver in the manifest: **named owner · the override granted · scope · expiry · written rationale**. For **Board/Regulatory**, a downgrade or "send restricted to model" waiver **additionally requires named-owner signoff** recorded before the model step runs. Without the waiver, the higher class and stricter routing stand.

## Step 7 — Hand off to the routed depth
- **Internal** → light lint (local structural checks; `workbook-doctor` quick pass). No model unless escalated.
- **Travels** → `workbook-doctor` + a single-context `trust-review` (evidence notes on).
- **Board / Regulatory** → full `workbook-doctor` → `trust-review` two-context loop → human gate (`tr-06`). Every model round appends a `rounds[]` entry to the manifest (model name+version, inputs-seen hashes, prompt hashes, timestamp).

The bullets above are the **workbook / deck** depths. A **`doc`-surface** artifact routes through Step 7a; a **recurring refresh** of a prior run routes through `office-update` first (it produces the change-set, then only the changed/new claims re-enter the routed depth).

## Step 7a — The `doc` surface branch (v2)
A `doc`-surface artifact (`conventions.md` §1.6) routes by **`format`** as well as travel class: the `document-doctor` (S02) reads `docx` / `pdf` / `md` / `html` with **per-format** extraction limits — never one blurred "doc" pass — and the router records `surface: doc` + the `format` in the manifest's `artifact` block (`kind: document`). Review depth scales with the class exactly like the workbook path; **verification** (`conventions.md` §2.2–§2.5) is added on top and is auto-on from `Travels` up:

| Travel class | Doc review depth | Verification (`conventions.md` §2.2–§2.5) |
|---|---|---|
| **Internal** | Light lint — a structure pass only; **no model** unless a signal escalates. | **Opt-in** (0 by default). |
| **Travels** | `document-doctor` + a single-context `trust-review` (evidence notes on). | **Auto**: external (`external_search` / `external_fetch`) **and** internal (the `internal_*` chain), bounded by the §2.3 Travels cap. |
| **Board / Regulatory** | Full `document-doctor` → `trust-review` two-context loop → human gate (`tr-06`). | **Auto**, higher cap; the headline + executive-summary claims are always in scope. |

**Degrade, don't fail.** Verification operations are resolved at runtime by the adapter registry (`../trust-review/references/scripts/adapter_registry.py`, F2-04). When a backend is **absent**, the stated fallback runs (`capability-inventory.md`): external-absent ⇒ internal-only, eligible claims recorded `not-checked-budget`; internal-absent ⇒ packet / session-memory fallback; **both-absent ⇒ the doc still gets the full structural + provenance review**, with verification coverage recorded as *degraded* in the manifest. A missing connector **never** blocks the trust path and **never** fabricates a verdict.

**Egress is gated.** Before any external verification step runs, the **web-egress guard** (`sensitivity-routing-policy.md` §5.1) applies: confidential / restricted **claim text is never sent out**; only a sanitised query may egress; a restricted Board/Regulatory artifact defaults to **`skipped-egress`** unless a named-owner waiver **with signoff** is recorded. `manifest_gate.py` blocks an egressing verification step that lacks the waiver — the same mechanical gate that blocks a model-run loop without routing. Internal verification (`internal-vault` / `internal-memory`, no egress) still runs.

## Outputs (I/O contract)
Write to `.trust/<run_id>/`:
- `travel-class-router.json` — `{ status, declared_class, effective_class, escalated, escalation_signals, routing, gate }`.
- `travel-class-router.md` — a short human memo: the class, why (which signals escalated it), the routing decision, and the gate result.
- Return exactly one status: `ok` (routed, gate passed), `ok-with-warnings` (routed but e.g. content unreadable → defaulted up), `blocked` (gate failed — sensitivity/routing not recorded), or `failed` (could not run).

**Golden rule:** when in doubt, route **up** and ask. The cost of over-reviewing an internal file is minutes; the cost of under-reviewing a board file is the whole reason this plugin exists.

> **Packaging note.** This skill is the skill-installable form of the plugin's `/travel-class-router` command — identical logic, same local scanner (`scripts/content_signals.py`), same mechanical `manifest_gate.py`. Install it via the "Save skill" button to get the front door without the full plugin/marketplace install.
