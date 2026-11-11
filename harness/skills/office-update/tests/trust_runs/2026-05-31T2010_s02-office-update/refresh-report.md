# Refresh change-set — 2026-05-31T2010_s02-office-update

**office-update** · schema_version **3** · status **ok-with-warnings** · prior run `2026-05-29T1615_q3-boardpack` (schema 1) · read-only **True**

> 8 unchanged, 0 updated, 1 new, 1 dropped (tombstoned), 1 superseded (linked).

## Change-set

| class | count |
|---|---|
| unchanged | 8 |
| updated | 0 |
| new | 1 |
| dropped (tombstoned) | 1 |
| superseded (linked) | 1 |

Prior claims: **10** → current claims: **9**. Carried forward: **8** verdict(s), **0** valid waiver(s) — identity-stable *unchanged* claims only.

## Per-claim

| claim_id | class | relation | carried verdict | invalidation | locator |
|---|---|---|---|---|---|
| `C-11345560da0d` | unchanged | unchanged | needs-review | — | `q3_actuals.csv#headcount` |
| `C-3c0bdb5c1485` | unchanged | unchanged | needs-review | — | `q3_forecast_v2.csv#Q4` |
| `C-3dc4e395c021` | unchanged | unchanged | needs-review | — | `strategy_offsite_notes.md#lighthouse` |
| `C-45c7b241bb4d` | unchanged | unchanged | needs-review | — | `strategy_offsite_notes.md#headcount` |
| `C-5d24efff1840` | unchanged | unchanged | needs-review | — | `q3_actuals.csv#ebitda_margin` |
| `C-8cf0e3f92287` | unchanged | unchanged | verified | — | `q3_actuals.csv#cloud_migration_spend` |
| `C-92bfc298353c` | unchanged | moved | needs-review | — | `#moved-to-board-summary` |
| `C-d05483e8c6f4` | unchanged | unchanged | needs-review | — | `q3_actuals.csv#revenue` |
| `C-754523db5ed6` | new | changed-identity | — | — | `cfo_email_thread.md#10:30#restated` |
| `C-7c65eea5c0d2` | dropped | dropped | — | — | `strategy_offsite_notes.md#emea` |
| `C-985ac65c1101` | superseded | superseded | — | — | `cfo_email_thread.md#10:30` |

## Tombstones (dropped — preserved, never deleted)

- `C-7c65eea5c0d2` — Possible reorg of EMEA sales team (last seen `strategy_offsite_notes.md#emea`) → **tombstoned**

## Supersession links (linked, NOT blended)

- `C-985ac65c1101` → superseded by `C-754523db5ed6`. Prior value: _18% (board display)_ · New value: _Present EBITDA margin as 18% flat to the board, footnote the detail (restated post-close)_ — both preserved, never averaged.

## Migration notes

office-update consumed prior run 2026-05-29T1615_q3-boardpack (schema_version 1) READ-ONLY via the fd-03 reader (whole-dir byte-hash unchanged before/after). Diffed by fd-02 claim identity. Carried forward 8 prior verdict(s) + 0 valid waiver(s) on identity-stable UNCHANGED claims only; 0 identity-stable-but-invalidated claim(s) re-enter needs-review; 1 dropped claim(s) tombstoned (never deleted); 1 superseded source(s) linked, not blended. New output stamped schema_version=3.
