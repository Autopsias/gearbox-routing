# Migration / backward-compatibility gate (v3 / FD-03)

- Checked: 2026-05-31T19:37:12Z
- Trust root: `/sessions/trusting-vigilant-ritchie/mnt/Downloads/office-trust-layer/office-trust-layer/.trust`
- Runs checked: **9** (v1: 7, v2: 2, v3: 0)
- Range covered (≥1 v1 **and** ≥1 v2): **True**
- Prior runs read-only (no mutation): **True**
- All runs load under the v3 reader: **True**
- **VERDICT: PASS**

## Migration notes

v3 reader loaded every committed v1/v2 run; new v3 optional fields (degraded_status, reviewer_independence, human_gate_digest, vault_writeback) defaulted safely (absent ⇒ v1/v2 behaviour); reviewer_independence inferred without overstating; a v3 refresh consumed each prior run READ-ONLY (whole-tree byte-hash unchanged). New outputs stamp schema_version=3.

## Per-run

| run | schema | required-ok | gate ran | v3-view ok | no-mutation | reviewer_independence |
|---|---|---|---|---|---|---|
| `2026-05-29T1820_q3-review` | 1 | True | True | True | True | same-family |
| `2026-05-30T1015_q3-humangate` | 1 | True | True | True | True | same-family |
| `2026-05-30T1200_pmo-dogfood` | 1 | True | True | True | True | same-family |
| `2026-05-31T0724_dr04-t2d-fcst-xlsx` | 1 | True | True | True | True | same-family |
| `2026-05-31T0801_dr10-strategy-pdf` | 1 | True | True | True | True | same-family |
| `2026-05-31T0813_dr11-t2d-reorg-md` | 1 | True | True | True | True | same-family |
| `2026-05-31T0816_dr13-nate-substack-pdf` | 1 | True | True | True | True | same-family |
| `2026-05-31T1530_s05-source-verification` | 2 | True | True | True | True | same-model |
| `2026-05-31T1730_s06-internal-verification` | 2 | True | True | True | True | same-model |
