# Sensitivity & Model-Routing Policy + Artifact Manifest

**This file governs what sensitive material may be seen by which model, what stays on the local machine, what gets redacted, how long anything is kept, and — through the per-run manifest — what each model actually saw on each round.** It is the security half of the Trust Layer's front door; the [travel-class router](../commands/travel-class-router.md) is the other half. Together they decide *how hard to review* a file and *who is allowed to read it*.

It reads its shared vocabulary, the command I/O contract, and the manifest's reserved keys from [`conventions.md`](conventions.md) (§3.2 reserves the manifest; this file defines it in full). The machine-readable manifest schema is [`manifest.schema.json`](manifest.schema.json); the runnable gate that enforces it is [`scripts/manifest_gate.py`](scripts/manifest_gate.py).

> **Acceptance gate (the reason this session blocks all later model-run loops).** No model-run loop — the two-context review, any reviewer subagent, any escalation that sends content to a model — may start until **sensitivity is classified AND model-routing is decided AND both are recorded in the run's `manifest.json`.** A loop that starts without them returns `blocked` (not `failed`) per the conventions status enum. The gate is mechanical (`manifest_gate.py`), not a matter of remembering.

---

## 1. Design stance: local-first, least-exposure

Two principles drive every rule below.

1. **Local-first.** The target artifacts are exactly the sensitive ones — board packs, investor models, regulatory filings. Wherever the toolchain can do the work *without sending content to a model*, it must: file inspection, formula recalculation, content-signal scanning, hashing, and redaction all run as **local processing** (LibreOffice recalc, `openpyxl`/`python-pptx`/`markitdown`, regex scanners). A model is invoked only for the judgement steps that genuinely need one (the hostile accuracy review, the language pass).
2. **Least-exposure.** When a model *is* invoked, it sees the **minimum** required: redacted excerpts over whole files where possible, and never more than the travel class and sensitivity class jointly permit. Every exposure is hashed and logged in the manifest, so "what did the model see" is answerable after the fact, not a matter of trust.

This policy is deliberately **generic** — it names sensitivity classes and routing rules in tool-agnostic terms so it survives changes in the underlying models and skills. Concrete model names live only in the manifest of an actual run, never hard-coded here.

---

## 2. Source sensitivity classification

Every source in the run's source packet (each `S<n>`, see `conventions.md` §1.1) is assigned **exactly one sensitivity class** at intake, independent of its `source_status`. Sensitivity is about *who may see it*; source status is about *whether you may build on it*. They are orthogonal and both recorded.

| Class | `sensitivity` value | What it covers | Default handling |
|---|---|---|---|
| **Public** | `public` | Already published or cleared for public release. | No restriction. May be sent to any routed model in full. |
| **Internal** | `internal` | Ordinary internal working material; no third-party or market-moving content. | May be sent to the routed model, default as excerpts. |
| **Confidential** | `confidential` | Commercially sensitive: unreleased financials, customer/contract data, internal forecasts, personal data (PII). | Redact-by-default; excerpts only; no-upload mode preferred where the work can be done locally. |
| **Restricted** | `restricted` | Board, investor, M&A, or regulatory material; legally privileged; price-sensitive / MNPI. | Local-only by default. A model sees only redacted, minimal excerpts, and only if routing for the travel class explicitly allows it; otherwise the step runs locally or is gated. |

Rules:

- **Default-up on doubt.** If a source's class is ambiguous, assign the **higher** class and ask the human to confirm a downgrade. Never silently assume `internal`.
- **A run inherits the maximum.** The run's effective sensitivity is the **highest** class among its sources. Routing and redaction for the run are decided against that maximum.
- **Recorded once, in the manifest.** The per-source classes and the run maximum are written to `manifest.json → sensitivity` before any model step. This is half of what the acceptance gate checks.

### 2.1 Retrieved internal evidence is sensitive by default (v2, finding #9)

A v2 verification may pull a note from the user's own corpus (`internal-vault`) or a fact from project memory (`internal-memory`, `conventions.md` §2.2). **Even though that retrieval never touches the web, the retrieved content still enters the model's context, the logs, and the manifest — so it is governed like any other source, not waved through because it is "local":**

- It is assigned a **sensitivity class at intake** (default **`internal`**, default up on doubt) and counts toward the run's `sensitivity.run_max` exactly like an uploaded source.
- It is stored **minimally**: the note **path** + an **excerpt hash** + the **minimum snippet** the step needs — never the whole note copied into the run directory.
- **Only the minimum excerpt reaches the reviewer**, under the same redaction + model-routing rules (§3, §5) as any other `internal`+ source. An Obsidian note is "local" for *egress* purposes but is **not** exempt from least-exposure once it enters a prompt.

---

## 3. Redaction (optional, local)

Redaction is **optional** but is the default for `confidential` and `restricted` sources whenever a model step is unavoidable. It runs **locally** — no content leaves the machine to be redacted.

Redaction modes (declared per run, recorded in the manifest):

| Mode | Effect |
|---|---|
| `none` | Source passed through as-is (only valid for `public`/`internal`, or under a logged waiver). |
| `excerpt` | Only the specific cells/ranges/paragraphs a step needs are extracted; the rest of the file is never presented. The default for `internal`+. |
| `mask` | Named entities, account numbers, person names, and absolute monetary magnitudes are replaced with stable placeholders (`<ORG_1>`, `<PERSON_2>`, `<AMOUNT_3>`) **while preserving structure and relationships** so the reviewer can still check internal consistency (e.g. "does the total equal the sum of parts") without seeing the real values. |
| `local-only` | The step does **not** call a model at all — it runs entirely on local tools (recalc, structural scan, signal scan). No exposure to record beyond "ran locally". |

Masking is reversible only locally, via a map kept in the run directory and **never sent to a model**. The manifest records *that* masking was applied and the mask map's hash, not the map itself.

---

## 4. No-upload / local-only modes

Where the toolchain can complete a step without a model, the policy requires it to. These steps are **always** local and never expose content:

- **File inspection & conversion** — `markitdown`, `openpyxl`, `python-pptx`, the `pdf` skill.
- **Formula recalculation** — `xlsx/scripts/recalc.py` via LibreOffice (the "checks tab is a smoke alarm" path).
- **Content-signal scanning** — the router's local scanner (`commands/scripts/content_signals.py`): formulas, financial/legal/investor terms, external-recipient hints are all detected by local regex/parse, never by asking a model.
- **Hashing & manifest assembly** — SHA-256 over the artifact, source packet, excerpts, and prompts.

A run may also be put in **whole-run local-only mode** (e.g. air-gapped handling of `restricted` material). In that mode the two-context model review cannot run; the harness returns `blocked` with the reason `local-only-mode: model review not permitted`, and the human either grants a routing waiver (§6) or accepts a local-only verdict with the model-review limitation recorded.

---

## 5. Model-routing rules by travel class

Routing answers **which model may see what**, as a function of the **travel class** (from F-03) and the **run's maximum sensitivity** (§2). Models are referred to by *role*, not name; the concrete name+version is recorded in the manifest per round.

| Travel class | Model steps that run | Routing rule (vs. sensitivity) |
|---|---|---|
| **Internal** | Light lint only — primarily local; a model is used only if a signal escalates the class. | `public`/`internal`: excerpts allowed. `confidential`+: prefer local-only; redact if a model step is needed. |
| **Travels** | Standard review + evidence notes (single hostile-review context). | `public`/`internal`: excerpts. `confidential`: `mask` excerpts. `restricted`: local-only unless a logged routing waiver permits masked excerpts. |
| **Board / Regulatory** | Full workflow + **two-context** hostile review + human gate. | `restricted` is expected here. The **builder context** and the **independent reviewer context** each see only **masked, minimal excerpts**; whole-file upload is never the default. A different model family for the reviewer (the two-context split) is preferred for blind-spot mitigation; if unavailable, reduced independence is recorded in the manifest (per the capability inventory's Agent fallback). |

Cross-cutting routing rules:

- **The two-context split is a routing decision, recorded.** Builder model and reviewer model (name+version each) go in the manifest. If they end up the same model/family because an override was rejected, that reduced independence is recorded, not hidden.
- **Excerpts over wholes.** A model never receives a whole sensitive file when the step can be done on the excerpts it needs. The router/skill declares the excerpt set; the manifest records its hash.
- **No content in tool logs.** Skills log status, hashes, and finding *references*, never the sensitive cell values themselves.

### 5.1 Verification routing & the web-egress guard (v2, finding #8)

v2 adds a verification step (`conventions.md` §2.2) that can reach **outside** the uploaded packet. Routing it is a sensitivity decision, so it lives here.

**Verification by travel class** (the router applies this; `conventions.md` §2.3 sets the caps):

| Travel class | External (`external-web`) | Internal (`internal-vault` / `internal-memory`) |
|---|---|---|
| **Internal** | opt-in (off by default) | opt-in |
| **Travels** | auto, bounded by the §2.3 cap | auto |
| **Board / Regulatory** | auto, higher cap — **subject to the egress guard below** | auto |

**The web-egress guard.** Sending a claim to an `external-web` provider is an **egress event**. The guard is deterministic and local:

- **Confidential / restricted claim text is never egressed.** Only a **sanitised / abstracted query** may leave: named entities masked (per the §3 `mask` placeholders), monetary magnitudes coarsened, source labels removed, and a banned-token check applied. The manifest records the **raw-claim → egressed-query diff** so "what left the machine" is auditable after the fact.
- **Restricted Board/Regulatory defaults to `skipped-egress`** — the external check does **not** run (the eligible claims are recorded `not-checked-budget`, `conventions.md` §2.2) **unless a named-owner routing waiver with signoff** (§6) explicitly permits a sanitised query. Internal verification (no egress) still runs regardless.
- **The gate enforces it.** `scripts/manifest_gate.py` blocks an egressing verification step whose manifest lacks the required routing record / waiver — `blocked`, not `failed`, the same mechanical precondition that gates a model-run loop. Egress happens on a **recorded decision**, never on trust.

### 5.2 Run outputs are themselves sensitive (v2, finding #15)

The `.trust/<run_id>/` outputs are not neutral metadata. An **egressed query**, an **internal note path**, or a **retrieved snippet** can reveal project context if a `.trust/` folder is forwarded alongside the deliverable. So the **run manifest and the evidence sidecars are classified sensitive outputs**:

- **Redact path / query display by default.** A shared or rendered view of a manifest / sidecar shows masked entities, note-path basenames (not full vault paths), and the *sanitised* query — never the raw values.
- **Full detail is local-only.** The unredacted egress diff, full note paths, and the mask map stay in the run directory under the §7 retention rules (purged on the restricted schedule) and are never the default display.
- **Never embed raw restricted text in a sidecar.** A sidecar carries references, hashes, and a minimum excerpt (the §2.1 rule) — never raw restricted claim text, so the proof can travel without the secret.

---

## 6. Routing & sensitivity waivers

A human may override a default — send `restricted` material to a model, skip redaction, or downgrade a sensitivity class — **only via a logged waiver**, mirroring the readiness-waiver discipline in `conventions.md` §4.

A routing/sensitivity waiver requires, persisted in the manifest: **named owner · the specific override granted · scope (which sources, which step) · expiry · written rationale.** For **Board/Regulatory** travel class, a sensitivity downgrade or a "send restricted to model" waiver **additionally requires named-owner signoff** before the model step may run — without it, the step stays `blocked`. Waivers never widen exposure beyond their stated scope.

---

## 7. Retention

The run directory `.trust/<run_id>/` is the system of record for the trail; retention applies to **what was exposed**, not to the verdict.

- **Manifest, findings, verdict, evidence memo** — retained with the artifact as the durable audit trail (these contain references and hashes, not raw sensitive values).
- **Excerpts, mask maps, any extracted sensitive text** — `retention.purge_after` (default **30 days**, or per the artifact owner's policy). After expiry the harness deletes the raw excerpts/mask map from the run directory, leaving their hashes in the manifest so the trail still proves *what* was seen without *retaining* it.
- **Restricted runs** — default `purge_after` is shortened (default **7 days**) and `retain_excerpts: false` is the default, so sensitive excerpts are purged as soon as the verdict is signed off.
- **No external retention.** Nothing in this policy authorises sending content anywhere it would be retained outside the run directory and the routed model's own (ephemeral) inference context.

Retention parameters are recorded in `manifest.json → retention`.

---

## 8. The per-run artifact manifest

Each run writes **one** `manifest.json` into `.trust/<run_id>/`. It is the single record of what happened to sensitive material in the run. `conventions.md` §3.2 reserves its keys; the full schema is below and machine-checkable in [`manifest.schema.json`](manifest.schema.json).

```json
{
  "manifest_version": "1.0",
  "run_id": "2026-05-29T1430_boardpack",
  "created": "2026-05-29T14:30:05Z",
  "artifact": {
    "path": "BoardPack_Q2.xlsx",
    "sha256": "…",
    "kind": "workbook"
  },
  "travel_class": {
    "value": "board-regulatory",
    "declared": "travels",
    "effective": "board-regulatory",
    "source": "escalated",
    "escalation_signals": ["financial-metric", "board-term", "external-recipient"],
    "waiver": null
  },
  "sensitivity": {
    "run_max": "restricted",
    "sources": [
      { "source_id": "S1", "sensitivity": "restricted", "redaction": "mask" },
      { "source_id": "S2", "sensitivity": "internal",   "redaction": "excerpt" }
    ],
    "redaction_default": "mask",
    "mask_map_sha256": "…"
  },
  "routing": {
    "policy": "board-regulatory/restricted",
    "local_only": false,
    "two_context": true,
    "builder_model":  { "name": "<model>", "version": "<ver>", "context": "builder" },
    "reviewer_model": { "name": "<model>", "version": "<ver>", "context": "reviewer", "independent": true },
    "independence_note": null
  },
  "rounds": [
    {
      "round": 1,
      "role": "reviewer",
      "model": { "name": "<model>", "version": "<ver>" },
      "timestamp": "2026-05-29T14:31:10Z",
      "inputs_seen": [
        { "source_id": "S1", "exposure": "masked-excerpt", "locator": "Outputs!B14:B40", "sha256": "…" }
      ],
      "prompt_hashes": {
        "reviewer_prompt_sha256": "…",
        "source_packet_sha256": "…",
        "input_artifact_sha256": "…",
        "provenance_record_sha256": "…"
      }
    }
  ],
  "retention": {
    "purge_after": "2026-06-05",
    "retain_excerpts": false
  },
  "gate": {
    "sensitivity_recorded": true,
    "routing_recorded": true,
    "status": "ok",
    "checked_at": "2026-05-29T14:30:06Z"
  }
}
```

### 8.1 Required keys (what the gate enforces)

A manifest **passes the acceptance gate** only when all of the following are present and non-empty *before the first model round runs*:

- `run_id`, `created`, `artifact.path`, `artifact.sha256`
- `travel_class.effective` (one of the F-03 classes)
- `sensitivity.run_max` **and** `sensitivity.sources` (≥1 source with a `sensitivity` value) — *sensitivity recorded*
- `routing.policy` **and** a model decision: either `routing.local_only: true`, **or** `routing.builder_model`/`routing.reviewer_model` populated — *routing recorded*

Each completed model round must additionally carry `model.name`, `model.version`, `timestamp`, `inputs_seen` (with a `sha256` per exposure), and `prompt_hashes`. A round missing any of these makes the manifest **non-compliant** for release even if the gate let the loop start.

`gate.status` is `ok` when sensitivity and routing are recorded, else `blocked` with the missing key named. The gate is checked by `scripts/manifest_gate.py` and is the precondition every model-run skill calls before its first round.

---

## 9. How components use this policy

- **The travel-class router (F-03)** classifies the travel class, runs the local content-signal scan, writes the `travel_class` and `sensitivity` blocks of the manifest, and decides `routing` from the table in §5. It will not hand off to a model-run loop until `manifest_gate.py` returns `ok`.
- **Every model-run skill (trust-review and any reviewer subagent, M1/S06–S07; M2 review)** calls `manifest_gate.py` first; on `blocked` it returns `blocked` with the gate's reason and does not invoke a model. On each round it appends a `rounds[]` entry with the model name+version, inputs-seen hashes, prompt hashes, and timestamp.
- **The human gate (`tr-06`, M1/S07)** reads the manifest's waiver and signoff records; a Board/Regulatory waived must-fix or routing waiver needs the named-owner signoff recorded here (per `conventions.md` §4 and §6 above).
- **The verification step (v2, S05–S06)** resolves its providers through the adapter registry (`references/scripts/adapter_registry.py`, F2-04) — never a hard-coded tool — classifies every retrieved internal source per §2.1, applies the §5.1 egress guard before any external call, and writes the **adapter binding**, the **egress diff**, and the verification **verdicts** into the manifest; its `.trust/` outputs are sensitive per §5.2.

If a definition here needs to change, it changes **here** and in `manifest.schema.json` together; no skill redefines a sensitivity class, a redaction mode, or a manifest key locally.
