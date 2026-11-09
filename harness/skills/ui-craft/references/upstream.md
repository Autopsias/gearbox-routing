# Taking a new Stageshow release

The vendored copy is prompt text and code that agents follow and run, so a new release is
read before it is pinned. Nothing applies an update by itself. The weekly drift job
(`scripts/gearbox-weekly-drift.sh`, Mondays 09:00) sends "ui-craft: upstream update to review"
when upstream content changes; `vendor.py` below says the same on demand.

Run every step from `~/your-private-harness` (the source repo), never in `~/.claude`.
`VP` is `python3 skills/repo-health/scripts/vendor.py --skill-dir skills/ui-craft`.

```
- [ ] 1. Confirm: $VP check  → UPDATE stageshow
- [ ] 2. List the commits: $VP diff stageshow
- [ ] 3. Read the real diff of every changed file
- [ ] 4. Replace the whole tree from the new commit; hash must match upstream
- [ ] 5. Move pin, tree and reviewed in vendor.json
- [ ] 6. check_refs.py, then re-read real-pages.md against the new craft/verify/review
- [ ] 7. Smoke test: one REVIEW on a known page
- [ ] 8. One commit: tree + vendor.json (+ real-pages.md if step 6 changed it)
```

3. **Read the diff, not the commit list.**
   `gh api repos/benjitaylor/skills/compare/<pin>...<new> --jq '.files[] | select(.filename|startswith("stageshow/")) | .filename + " " + .status'`,
   then read the patch of each file that matters (`.patch` of that entry). Three questions:
   did `SKILL.md` or a reference change what an agent will do; did a script gain network
   access, file writes outside its `--report`/`--out`, or `eval`; did `package.json` add a
   dependency (vet it: maintained in the last weeks, widely downloaded). Any "yes" is a
   finding to put to the owner before you go on.
4. **Replace the whole subtree.** Download the tarball of the new commit into a new empty
   scratch folder, then `git rm -r -q skills/ui-craft/vendor/stageshow` and copy the new
   `stageshow/` in its place (never `rm -rf` in the same command). Upstream's tree hash is the
   `sha` of the `stageshow` entry in `gh api "repos/benjitaylor/skills/contents?ref=<new>"`;
   `$VP verify` must then print `ok`. Never edit a file inside the tree to make it fit.
5. Update `pin`, `tree` and `reviewed`; update `audit` or `caveat` if step 3 changed them.
   The next `scripts/stage.sh` builds a fresh work copy, because its folder name carries the
   tree hash. Old work copies under `~/.cache/ui-craft/` can go.
6. `python3 skills/ui-craft/scripts/check_refs.py` must exit 0. Then read the diff of
   `references/craft.md`, `verify.md` and `review.md` once more against
   `references/real-pages.md`: a new rule may need a real-page reading, and a dropped rule
   may leave a reading with nothing to adapt.
7. Run REVIEW steps 1 to 3 on one page you reviewed before and compare the frames and the
   `layout.mjs` lines with the last run.
