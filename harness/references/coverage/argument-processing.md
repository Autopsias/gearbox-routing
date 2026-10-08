## Command Arguments Processing

Process $ARGUMENTS as mode and target:
- If no arguments: mode="analyze", target=None (analyze all)
- If one argument: check if it's a valid mode, else treat as target with mode="analyze"
- If two arguments: first=mode, second=target
- Validate mode is one of: analyze, improve, generate, validate

```bash
# ============================================
# DYNAMIC DIRECTORY DETECTION (Project-Agnostic)
# ============================================

# Allow environment override
if [[ -n "$COVERAGE_REPORTS_DIR" ]] && [[ -d "$COVERAGE_REPORTS_DIR" || -w "$(dirname "$COVERAGE_REPORTS_DIR")" ]]; then
  REPORTS_DIR="$COVERAGE_REPORTS_DIR"
  echo "Using override reports directory: $REPORTS_DIR"
else
  # Search standard locations
  REPORTS_DIR=""
  for dir in "workspace/reports/coverage" "reports/coverage" "coverage/reports" ".coverage-reports"; do
    if [[ -d "$dir" ]]; then
      REPORTS_DIR="$dir"
      echo "Found reports directory: $REPORTS_DIR"
      break
    fi
  done

  # Create in first available parent
  if [[ -z "$REPORTS_DIR" ]]; then
    for dir in "workspace/reports/coverage" "reports/coverage" "coverage"; do
      PARENT_DIR=$(dirname "$dir")
      if [[ -d "$PARENT_DIR" ]] || mkdir -p "$PARENT_DIR" 2>/dev/null; then
        mkdir -p "$dir" 2>/dev/null && REPORTS_DIR="$dir" && break
      fi
    done

    # Ultimate fallback
    if [[ -z "$REPORTS_DIR" ]]; then
      REPORTS_DIR="./coverage-reports"
      mkdir -p "$REPORTS_DIR"
    fi
    echo "Created reports directory: $REPORTS_DIR"
  fi
fi

# Parse command arguments
MODE="${1:-analyze}"
TARGET="${2:-}"

# Validate mode
case "$MODE" in
    analyze|improve|generate|validate)
        echo "Executing /coverage $MODE $TARGET"
        ;;
    *)
        # If first argument is not a valid mode, treat it as target with default analyze mode
        TARGET="$MODE"
        MODE="analyze"
        echo "Executing /coverage $MODE (analyzing target: $TARGET)"
        ;;
esac
```
