# Deep mode: the parallel workflow

## When and what it costs

Deep mode switches on automatically above 90 ledger rows. It costs roughly an order of magnitude
more tokens than a single-agent scan, and in exchange reads several times as many sites in depth.

The workflow runs these agents, and the script assembles the report from their structured output:

| Agent | Model | Job |
|---|---|---|
| facts card | Sonnet | Jev facts from the saved docs, and the repo's rules, data and languages |
| external evidence | Sonnet | sections 1 to 3 and 5 of `external-evidence.md`, from the web, with no repo path in its prompt |
| one reader per shard | Sonnet | a site record for every ledger row, deep reads first |
| sweep | Sonnet | the rows that the readers left unread, up to 20 |
| incumbent prices | Sonnet | section 4 of `external-evidence.md`, from the web, given only the public product names the readers recorded |
| judge | Fable, or Opus | the fit rules, the ranking, the cost per verdict, and "Measure next"; no web tools, and every web-derived input arrives fenced as untrusted data |
| check | Sonnet | reopens the `file:line` behind every TEST row and tries to refute it |

## The judge model

The judge is the one step where the model changed the result: in a side-by-side run, a Fable
judge cross-checked more and avoided a wrong claim that an Opus judge made, at a higher price per
token. Fable is the default judge; pass `judge_model: opus` for the cheaper one. The readers stay on Sonnet: they record facts and do not rank.

## Steps

1. Run step 1 of SKILL.md with `--out <scratch>/jev-docs`. The script also saves its diff as
   `diff.txt` there.
2. Under `--prod-read` only: run section 6 of `external-evidence.md` yourself, before the
   workflow, and write the results with their commands and windows to
   `<scratch>/measurements.md`. Workflow agents never touch production.
3. Run step 3 of SKILL.md until the wrapper is confirmed. Then split the ledger, with the
   library roots:
   ```bash
   python3 <skill-dir>/scripts/scan_sites.py <repo> --exclude DIR --wrapper 'NAMES' \
     --library <installed library dir> --shards 6 --shard-out <scratch>/shards.json
   ```
   Use 6 shards up to 90 rows and 8 above that. The workflow runs shards + 6 agents at most.
4. Copy `<skill-dir>/scripts/deep_scan.workflow.js` into the scratch directory. The Workflow tool
   runs only scripts in the working directory or a directory added to the session, and it
   refused the installed skill path. Call the Workflow tool with `scriptPath` =
   `<scratch>/deep_scan.workflow.js` and
   `args` = `{repo, skill_dir, scratch, docs_dir, shards_file, n_shards, n_rows, shard_sizes,
   measurements, judge_model, extra_rules}`. `judge_model` is `fable` unless the user passed
   `--opus-judge`. The script's `shards:` line gives `n_rows`, and its list of rows per shard
   gives `shard_sizes` (its length is `n_shards`); the workflow uses it to catch a reader that
   skips rows. `measurements` is
   the path from step 2, else `""`. `extra_rules` carries any read limit that the user set,
   else `""`.
5. Save the returned `report` in the scratch directory. Open two of its `file:line` claims
   yourself before you deliver it. Add nothing that the workflow did not find. When a spot
   check fails, say so above the report.
6. When a site is TEST FIRST, run the "Measure next" command that decides it if that command
   is local and read-only, and put the result above the report. Compare the verdicts with the
   repo's earlier `# Jev fit:` report too. A verdict that changed with no changed fact and no
   new count is the judge's reading, and the check above the report says so.
