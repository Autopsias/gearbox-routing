#!/usr/bin/env bash
# flake-rerun.sh — calibrated process-level reruns for flake-detective.
# Each run is a separate $PYTEST_CMD invocation, so fixtures and modules are
# rebuilt fresh — the same semantics CI uses.
#
# Usage:
#   flake-rerun.sh [--cmd "<pytest invocation>"] [--help] -- <test_target> [N=10] [pytest_args...]
#
# Exit codes:
#   0  success (verdict emitted, regardless of which verdict)
#   64 usage error (bad args, N out of range)
#   65 invocation resolution failed (no pytest runner found)
#   70 internal script error
#
# Trigger to revisit: when pytest's stdout format changes the worker prefix
# (e.g. drops "[gw0]"), when --junitxml schema changes, when pytest exit-code
# taxonomy changes (codes 0-5 today), or when xmllint becomes unavailable on
# common CI base images and we need a different XML parser.

set -u  # error on undefined vars; do NOT use -e because we expect non-zero pytest exits

VERSION="1.0.0"

usage() {
    cat <<'EOF'
flake-rerun.sh — calibrated process-level reruns for pytest flake confirmation.

USAGE
  flake-rerun.sh [--cmd "<pytest invocation>"] [--help] -- <test_target> [N=10] [pytest_args...]

ARGUMENTS
  --             REQUIRED separator before <test_target>. Prevents flag-shaped node IDs
                 (e.g. parametrized values starting with -) from being parsed as options.
  <test_target>  pytest node id, file path, or any selector pytest accepts.
                 Quote ids with spaces: 'tests/test_x.py::test_y[param with spaces]'
  N              number of process-level reruns. Integer in [1, 100]. Default: 10.
  pytest_args    extra args passed verbatim to every pytest invocation.

OPTIONS
  --cmd CMD      explicit pytest command; overrides FLAKE_PYTEST_CMD and auto-detection.
  --help         print this help and exit 0.

ENV VARS
  FLAKE_PYTEST_CMD  if set, used as the pytest command (resolution rank 2).

PYTEST COMMAND RESOLUTION (in order)
  1. --cmd flag
  2. FLAKE_PYTEST_CMD env var
  3. lockfile auto-detection: uv.lock → "uv run pytest"; poetry.lock → "poetry run pytest";
     pdm.lock → "pdm run pytest"; hatch.toml → "hatch run test";
     tox.ini → "tox -e py"; noxfile.py → "nox -s tests"
  4. "python -m pytest" (if `python` resolves)
  5. "pytest" (last resort)

VERDICTS
  flaky                  ≥1 PASS and ≥1 FAIL across valid runs
  deterministic-pass     all valid runs PASS
  deterministic-fail     all valid runs FAIL with the same error signature
  no-tests-collected     pytest exit 5 on first run
  collection-error       pytest exit 1 with "errors during collection" marker
  usage-error            pytest exit 4
  infrastructure-error   pytest exit 2 or 3, or invocation resolution failed
  inconclusive           fewer valid runs than max(3, ceil(N/3)) after filtering

Exit codes are NOT collapsed: codes 2, 3, 4, 5 are excluded from flake-rate math
because mixing them with PASS/FAIL corrupts the statistics.

EXAMPLES
  flake-rerun.sh -- tests/test_x.py
  flake-rerun.sh -- tests/test_x.py 22
  flake-rerun.sh -- 'tests/test_x.py::test_y[a b]' 30
  FLAKE_PYTEST_CMD="poetry run pytest" flake-rerun.sh -- tests/test_x.py
  flake-rerun.sh --cmd "uv run pytest" -- tests/test_x.py 5
EOF
}

# ---------- arg parsing (stop at --) ----------
PYTEST_CMD_OVERRIDE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --cmd)
            [[ $# -ge 2 ]] || { echo "ERROR: --cmd requires an argument" >&2; exit 64; }
            PYTEST_CMD_OVERRIDE="$2"; shift 2 ;;
        --) shift; break ;;
        *) echo "ERROR: unexpected argument before '--': $1" >&2; usage >&2; exit 64 ;;
    esac
done

[[ $# -ge 1 ]] || { echo "ERROR: missing <test_target> after '--'" >&2; usage >&2; exit 64; }

TEST_TARGET="$1"; shift
N="${1:-10}"
[[ $# -ge 1 ]] && shift || true
EXTRA_ARGS=("$@")

# Validate N
if ! [[ "$N" =~ ^[0-9]+$ ]] || [[ "$N" -lt 1 ]] || [[ "$N" -gt 100 ]]; then
    echo "ERROR: N must be an integer in [1, 100]; got '$N'" >&2
    exit 64
fi

# ---------- resolve PYTEST_CMD ----------
resolve_pytest_cmd() {
    if [[ -n "$PYTEST_CMD_OVERRIDE" ]]; then
        echo "$PYTEST_CMD_OVERRIDE"; return 0
    fi
    if [[ -n "${FLAKE_PYTEST_CMD:-}" ]]; then
        echo "$FLAKE_PYTEST_CMD"; return 0
    fi
    if [[ -f uv.lock ]]; then echo "uv run pytest"; return 0; fi
    if [[ -f poetry.lock ]]; then echo "poetry run pytest"; return 0; fi
    if [[ -f pdm.lock ]]; then echo "pdm run pytest"; return 0; fi
    if [[ -f hatch.toml ]]; then echo "hatch run test"; return 0; fi
    if [[ -f tox.ini ]]; then echo "tox -e py"; return 0; fi
    if [[ -f noxfile.py ]]; then echo "nox -s tests"; return 0; fi
    if command -v python >/dev/null 2>&1; then echo "python -m pytest"; return 0; fi
    if command -v pytest >/dev/null 2>&1; then echo "pytest"; return 0; fi
    return 1
}

PYTEST_CMD="$(resolve_pytest_cmd)" || {
    echo "ERROR: could not resolve a pytest command (no lockfile, no python, no pytest)" >&2
    exit 65
}

# Split PYTEST_CMD into array for safe expansion (no eval)
read -r -a PYTEST_CMD_ARR <<< "$PYTEST_CMD"

# ---------- prepare temp dir ----------
TMPDIR_ROOT="${TMPDIR:-/tmp}"
WORKDIR="$(mktemp -d "$TMPDIR_ROOT/flake-rerun.XXXXXX")" || { echo "ERROR: mktemp failed" >&2; exit 70; }

echo "flake-rerun.sh v$VERSION"
echo "  pytest_cmd:   ${PYTEST_CMD_ARR[*]}"
echo "  test_target:  $TEST_TARGET"
echo "  N:            $N"
echo "  extra_args:   ${EXTRA_ARGS[*]:-(none)}"
echo "  workdir:      $WORKDIR"
echo

# ---------- run loop ----------
PASS_COUNT=0
FAIL_COUNT=0
INFRA_COUNT=0
USAGE_ERR_COUNT=0
NO_TESTS_COUNT=0
COLLECTION_ERR_COUNT=0
PARSER_CONF="high"  # demoted to "low" if junitxml missing on any run

declare -a RUN_OUTCOMES   # "pass" / "fail" / "infra" / "usage" / "no-tests" / "collection-err"
declare -a RUN_WORKERS    # comma-joined worker ids per run

for i in $(seq 1 "$N"); do
    XML="$WORKDIR/run-$i.xml"
    LOG="$WORKDIR/run-$i.log"

    # We never enabled `set -e` (only `set -u` at top), so non-zero pytest exits
    # do NOT abort the script. Capture exit code into EC and continue.
    EC=0
    "${PYTEST_CMD_ARR[@]}" \
        "$TEST_TARGET" \
        --color=no -vv -rA --tb=short \
        --junitxml="$XML" \
        "${EXTRA_ARGS[@]}" \
        > "$LOG" 2>&1 || EC=$?

    OUTCOME="unknown"
    case "$EC" in
        0) OUTCOME="pass"; PASS_COUNT=$((PASS_COUNT + 1)) ;;
        1)
            # exit 1 = tests failed OR collection error; distinguish via log content
            if grep -q "errors during collection" "$LOG" 2>/dev/null; then
                OUTCOME="collection-err"; COLLECTION_ERR_COUNT=$((COLLECTION_ERR_COUNT + 1))
            else
                OUTCOME="fail"; FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
            ;;
        2|3) OUTCOME="infra"; INFRA_COUNT=$((INFRA_COUNT + 1)) ;;
        4) OUTCOME="usage"; USAGE_ERR_COUNT=$((USAGE_ERR_COUNT + 1)) ;;
        5) OUTCOME="no-tests"; NO_TESTS_COUNT=$((NO_TESTS_COUNT + 1)) ;;
        *) OUTCOME="infra"; INFRA_COUNT=$((INFRA_COUNT + 1)) ;;
    esac
    RUN_OUTCOMES+=("$OUTCOME")

    # Worker extraction (best effort)
    WORKERS=""
    if [[ -f "$XML" ]]; then
        # junitxml: <testcase classname="..." name="..."> — xdist puts gw* in the name when present
        WORKERS="$(grep -oE 'gw[0-9]+' "$XML" 2>/dev/null | sort -u | tr '\n' ',' | sed 's/,$//' || true)"
    else
        PARSER_CONF="low"
        WORKERS="$(grep -oE '\[gw[0-9]+\]' "$LOG" 2>/dev/null | sort -u | tr -d '[]' | tr '\n' ',' | sed 's/,$//' || true)"
    fi
    [[ -z "$WORKERS" ]] && WORKERS="(none)"
    RUN_WORKERS+=("$WORKERS")

    printf "  run %3d: outcome=%-13s exit=%d workers=%s\n" "$i" "$OUTCOME" "$EC" "$WORKERS"
done

echo

# ---------- aggregate ----------
VALID=$((PASS_COUNT + FAIL_COUNT))
TOTAL=${#RUN_OUTCOMES[@]}
INFRA_TOTAL=$((INFRA_COUNT + USAGE_ERR_COUNT + NO_TESTS_COUNT + COLLECTION_ERR_COUNT))
MIN_VALID=$(( N / 3 < 3 ? 3 : N / 3 ))

# Wilson 95% confidence interval for pass-rate (standard formula, z=1.96)
# pass_rate = PASS / VALID
# wilson_lo, wilson_hi computed via awk for portability
if [[ $VALID -gt 0 ]]; then
    PASS_RATE=$(awk -v p="$PASS_COUNT" -v n="$VALID" 'BEGIN{ printf "%.4f", p/n }')
    read WILSON_LO WILSON_HI < <(
        awk -v p="$PASS_COUNT" -v n="$VALID" 'BEGIN {
            z=1.96; phat=p/n
            denom=1+z*z/n
            center=(phat+z*z/(2*n))/denom
            spread=z*sqrt((phat*(1-phat)+z*z/(4*n))/n)/denom
            lo=center-spread; hi=center+spread
            if (lo<0) lo=0; if (hi>1) hi=1
            printf "%.4f %.4f", lo, hi
        }'
    )
else
    PASS_RATE="n/a"; WILSON_LO="n/a"; WILSON_HI="n/a"
fi

# Minimum detectable flake rate at this N (probability of seeing at least one fail
# given true rate r is 1-(1-r)^N; solve for r at confidence 0.95)
MDR=$(awk -v n="$N" 'BEGIN { printf "%.4f", 1 - exp(log(0.05)/n) }')

# ---------- verdict ----------
VERDICT="inconclusive"
if [[ "${RUN_OUTCOMES[0]}" == "no-tests" ]]; then
    VERDICT="no-tests-collected"
elif [[ "${RUN_OUTCOMES[0]}" == "collection-err" ]]; then
    VERDICT="collection-error"
elif [[ "${RUN_OUTCOMES[0]}" == "usage" ]]; then
    VERDICT="usage-error"
elif [[ $VALID -lt $MIN_VALID ]]; then
    VERDICT="inconclusive"
elif [[ $PASS_COUNT -gt 0 && $FAIL_COUNT -gt 0 ]]; then
    VERDICT="flaky"
elif [[ $FAIL_COUNT -eq 0 && $PASS_COUNT -gt 0 ]]; then
    VERDICT="deterministic-pass"
elif [[ $PASS_COUNT -eq 0 && $FAIL_COUNT -gt 0 ]]; then
    VERDICT="deterministic-fail"
elif [[ $INFRA_TOTAL -gt 0 && $VALID -eq 0 ]]; then
    VERDICT="infrastructure-error"
fi

# ---------- summary ----------
cat <<EOF
========== SUMMARY ==========
verdict:                   $VERDICT
pytest_cmd:                ${PYTEST_CMD_ARR[*]}
parser_confidence:         $PARSER_CONF

runs:                      $TOTAL (target N=$N)
  pass:                    $PASS_COUNT
  fail:                    $FAIL_COUNT
  collection-error:        $COLLECTION_ERR_COUNT
  no-tests-collected:      $NO_TESTS_COUNT
  usage-error:             $USAGE_ERR_COUNT
  infra-error (exit 2/3/?): $INFRA_COUNT
  valid (pass+fail):       $VALID
  min_valid_for_verdict:   $MIN_VALID

pass_rate (valid runs):    $PASS_RATE
wilson_95_ci:              [$WILSON_LO, $WILSON_HI]
min_detectable_rate@N:     $MDR (95% confidence to see ≥1 fail)

per-run worker assignments saved in $WORKDIR
=============================
EOF

exit 0
