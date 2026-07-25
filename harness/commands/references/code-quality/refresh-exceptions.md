# --refresh-exceptions branch

Read this ONLY when `$ARGUMENTS` contains `--refresh-exceptions`. This flag runs a
single self-contained action (regenerate exception baselines) and exits immediately —
it does not participate in the rest of the command's analysis/fix/report flow.

If `--refresh-exceptions` flag provided, ONLY refresh exception files and exit:

```bash
echo "=== REFRESHING EXCEPTION BASELINES ==="

# Count before
BEFORE_FUNC=$(cat .function-length-exceptions 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('exceptions',{})))" 2>/dev/null || echo "0")

# Regenerate function length exceptions
if [ -f ~/.claude/scripts/quality/check_function_lengths.py ]; then
    if command -v uv &> /dev/null; then
        uv run python ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" --generate-baseline
    else
        python3 ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" --generate-baseline
    fi
fi

# Count after
AFTER_FUNC=$(cat .function-length-exceptions 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('exceptions',{})))" 2>/dev/null || echo "0")

echo ""
echo "Function length exceptions: $BEFORE_FUNC -> $AFTER_FUNC ($(($BEFORE_FUNC - $AFTER_FUNC)) stale entries removed)"
echo ""
echo "Done. Exception files updated."
```

Exit after refresh (no other steps executed).
