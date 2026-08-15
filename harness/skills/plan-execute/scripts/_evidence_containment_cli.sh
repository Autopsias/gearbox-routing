#!/usr/bin/env bash
# S04B evidence — the containment gate exercised through the REAL CLI against a
# COPY of the live plan-framework-upgrade plan (not a toy fixture).
# Usage: _evidence_containment_cli.sh <output-transcript>
set -u
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$SCRIPTS/../../.." && pwd)/_plans/plan-framework-upgrade-2026-08-12"
TMP="$(mktemp -d)"
WORK="$TMP/proj/_plans"
mkdir -p "$WORK"
cp -R "$SRC" "$WORK/live"
PLAN="$WORK/live"
REAL="$(cd "$PLAN" && pwd -P)"
rm -f "$PLAN/.lock"

scrub() { sed -e "s|$REAL|<plan>|g" -e "s|$PLAN|<plan>|g"; }

run() {
  echo ""
  echo "\$ run.py $*" | scrub
  python3 "$SCRIPTS/run.py" "$@" 2>&1 | scrub
  echo "  [exit ${PIPESTATUS[0]}]"
}

# `status` prints one big JSON blob; only the containment block is the point here.
containment() {
  echo ""
  echo "\$ run.py status <plan>   # the 'containment' block of the status JSON"
  python3 - "$SCRIPTS" "$PLAN" <<'PY' | scrub
import json, subprocess, sys
out = subprocess.run([sys.executable, f"{sys.argv[1]}/run.py", "status", sys.argv[2]],
                     capture_output=True, text=True).stdout
print(json.dumps(json.loads(out)["containment"], indent=2, ensure_ascii=False))
PY
}

{
echo "### CLI transcript — copy of the LIVE plan (16 sessions, 21 items, s01–s04 DONE)"
echo ""
echo '```'

echo "--- BASELINE: what this plan already breaks before anything is touched"
containment

echo ""
echo "--- PLANT: hand-delete a nav chip from the real page, then ask the gate"
python3 - "$PLAN" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]) / "PLAN.html"
t = p.read_text()
s = t.index('<a href="#s14" class="strip-chip"')
p.write_text(t[:s] + t[t.index("</a>", s) + 4:])
print('removed the s14 chip from <nav class="session-strip">')
PY
containment
cp "$SRC/PLAN.html" "$PLAN/PLAN.html"

echo ""
echo "--- PLANT: join a parallel_group with a DIFFERENT depends_on set"
echo "    (dispatch batches group peers that are ready together; asymmetric deps"
echo "     dissolve the batch into sequential dispatch with no error)"
run add-session "$PLAN" --id s90 --title Probe --new-item 'rp-90|replan|Probe' \
    --parallel-group pg-probes --depends-on s14 --allow-builder-drift

echo ""
echo "--- ALLOW: the same session without the asymmetry"
echo "    (--allow-builder-drift only because this COPY sits at a different path"
echo "     than the original — a separate refusal this engine already makes)"
run add-session "$PLAN" --id s90 --title Probe --new-item 'rp-90|replan|Probe' \
    --depends-on s14 --model Sonnet --allow-builder-drift

echo ""
echo "--- the settled verify-state records rode forward onto the NEW digest"
python3 - "$PLAN" <<'PY'
import hashlib, json, pathlib, sys
plan = pathlib.Path(sys.argv[1])
digest = hashlib.sha256((plan / "manifest.json").read_bytes()).hexdigest()
print(f"manifest digest now {digest[:12]}…")
for p in sorted((plan / "_verify_state").glob("*.json")):
    s = json.loads(p.read_text())
    print(f"  {p.name:10s} digest={s['manifest_digest'][:12]}… "
          f"bound={s['manifest_digest'] == digest} "
          f"rework_count={s['rework_count']}/{s['max_rework']} outcome={s['outcome']}")
PY

echo ""
echo "--- PLANT: a MIXED GENERATION — put the pre-mutation page back over the"
echo "    post-mutation manifest. This is what a hand edit produces, and it is"
echo "    what passed the old gate. Five surfaces are now named at once."
cp "$SRC/PLAN.html" "$PLAN/PLAN.html"
containment
echo '```'
} > "$1"
rm -rf "$TMP"
echo "wrote $1"
