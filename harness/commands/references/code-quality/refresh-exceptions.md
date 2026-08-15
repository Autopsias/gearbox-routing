# --refresh-exceptions branch

Read this ONLY when `$ARGUMENTS` contains `--refresh-exceptions`. This flag runs a
single self-contained action (regenerate exception baselines) and exits immediately —
it does not participate in the rest of the command's analysis/fix/report flow.

If `--refresh-exceptions` flag provided, ONLY refresh exception files and exit:

Both baselines are refreshed (same behavior as the post-`--fix` refresh in
`analysis-rules.md` — the two paths must not diverge):

```bash
echo "=== REFRESHING EXCEPTION BASELINES ==="

# Count before
BEFORE_FILE=$(cat .file-size-exceptions 2>/dev/null | grep -c '"file":' || echo "0")
BEFORE_FUNC=$(cat .function-length-exceptions 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('exceptions',{})))" 2>/dev/null || echo "0")

# Regenerate file size exceptions
if [ -f ~/.claude/scripts/quality/check_file_sizes.py ]; then
    if command -v uv &> /dev/null; then
        uv run python ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" --generate-baseline
    else
        python3 ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" --generate-baseline
    fi
fi

# Regenerate function length exceptions
if [ -f ~/.claude/scripts/quality/check_function_lengths.py ]; then
    if command -v uv &> /dev/null; then
        uv run python ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" --generate-baseline
    else
        python3 ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" --generate-baseline
    fi
fi

# Count after
AFTER_FILE=$(cat .file-size-exceptions 2>/dev/null | grep -c '"file":' || echo "0")
AFTER_FUNC=$(cat .function-length-exceptions 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('exceptions',{})))" 2>/dev/null || echo "0")

echo ""
echo "Exception file updates:"
echo "  File size:       $BEFORE_FILE -> $AFTER_FILE ($(($BEFORE_FILE - $AFTER_FILE)) stale entries removed)"
echo "  Function length: $BEFORE_FUNC -> $AFTER_FUNC ($(($BEFORE_FUNC - $AFTER_FUNC)) stale entries removed)"
echo ""
echo "Done. Exception files updated."
```

Exit after refresh (no other steps executed).
