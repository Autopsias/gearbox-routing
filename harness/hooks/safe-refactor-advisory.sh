#!/bin/bash
# Safe Refactor Advisory Hook (Interactive)
# Detects potential file splitting/refactoring and lets user choose to use /safe-refactor
#
# Returns:
# - exit 0: Continue with original operation
# - exit 2: Block operation (user can then invoke /safe-refactor)

set -euo pipefail

# Hook receives tool info via stdin as JSON
INPUT=$(cat)

# Extract file path from Write tool input
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

if [[ -z "$FILE_PATH" ]]; then
    # Not a Write tool call or no file path, continue
    exit 0
fi

# Get directory and filename
DIR=$(dirname "$FILE_PATH")
FILENAME=$(basename "$FILE_PATH")

# Check if this looks like a module refactoring scenario
is_refactor_scenario() {
    # Indicators of file splitting:
    case "$FILENAME" in
        __init__.py|index.ts|index.js|mod.rs)
            # Creating a facade file - likely splitting a module
            # Check if parent directory name matches an existing file
            local parent_name=$(basename "$DIR")
            local grandparent=$(dirname "$DIR")

            # Check for existing file that would be split
            # Only treat this as a split when an actual sibling source file
            # of the same name exists (i.e. <dirname>.py is being turned into
            # <dirname>/__init__.py). The previous broad `grep "from.*<dir>"`
            # heuristic false-positived on every NEW package whose directory
            # name appeared as a substring of any unrelated import.
            if [[ -f "${grandparent}/${parent_name}.py" ]] || \
               [[ -f "${grandparent}/${parent_name}.ts" ]] || \
               [[ -f "${grandparent}/${parent_name}.go" ]] || \
               [[ -f "${grandparent}/${parent_name}.rs" ]]; then
                return 0
            fi
            ;;
        _legacy.py|_legacy.ts|_legacy.js|internal.py|internal.go|internal.rs)
            # Creating legacy/internal file - definitely a refactor
            return 0
            ;;
    esac

    # Check if creating files in a new directory that mirrors an existing file
    if [[ ! -d "$DIR" ]]; then
        local parent_name=$(basename "$DIR")
        local grandparent=$(dirname "$DIR")
        if [[ -f "${grandparent}/${parent_name}.py" ]] || \
           [[ -f "${grandparent}/${parent_name}.ts" ]]; then
            return 0
        fi
    fi

    return 1
}

# Only intervene for refactoring scenarios
if is_refactor_scenario; then
    # Output message - will be shown to user when operation is blocked
    cat << EOF

╔══════════════════════════════════════════════════════════════════════╗
║  ⚠️  FILE REFACTORING DETECTED - May Break Tests                     ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  Creating: $FILENAME                                                 ║
║  In: $DIR                                                            ║
║                                                                      ║
║  This looks like a file split/modularization operation.              ║
║  Proceeding without test gates may break existing tests.             ║
║                                                                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  OPTIONS:                                                            ║
║                                                                      ║
║  [REJECT] → Then run: /safe-refactor <original_file>                 ║
║             Uses test-safe workflow with:                            ║
║             • Test baseline verification                             ║
║             • Facade pattern for backward compatibility              ║
║             • Git checkpoints for instant rollback                   ║
║             • Incremental migration with test gates                  ║
║                                                                      ║
║  [APPROVE] → Continue with current operation (risky)                 ║
║              Tests may break due to import path changes              ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝

EOF
    exit 2  # Block - requires user approval to proceed
fi

# Not a refactor scenario, continue normally
exit 0
