---
description: "Discovers the next unrun test gate from epic/story definitions and executes it. Use when you say 'run test gates', 'next test gate', 'check test gates', or need to validate story completion against gate criteria."
argument-hint: "no arguments needed - auto-detects next gate"
allowed-tools: ["Bash", "Read", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
---

# ⚠️ PROJECT-SPECIFIC COMMAND - Requires test gates infrastructure
# This command requires:
# - ~/.claude/lib/testgates_discovery.py (test gate discovery script)
# - docs/epics.md (or similar) with test gate definitions
# - user-testing/scripts/ directory with validation scripts
# - user-testing/reports/ directory for results
#
# The file path checks in Step 3.5 are project-specific examples that should be
# customized for your project's implementation structure.

# Test Gate Finder & Executor

**Your task**: Find the next test gate to run, show the user what's needed, and execute it if they confirm.

## Step 1: Discover Test Gates and Prerequisites

First, check if the required infrastructure exists:

```bash
# ============================================
# PRE-FLIGHT CHECKS (Infrastructure Validation)
# ============================================

TESTGATES_SCRIPT="$HOME/.claude/lib/testgates_discovery.py"

# Check if discovery script exists
if [[ ! -f "$TESTGATES_SCRIPT" ]]; then
  echo "❌ Test gates discovery script not found"
  echo "   Expected: $TESTGATES_SCRIPT"
  echo ""
  echo "   This command requires the testgates_discovery.py library."
  echo "   It is designed for projects with test gate infrastructure."
  exit 1
fi

# Check for epic definition files
EPICS_FILE=""
for file in "docs/epics.md" "docs/EPICS.md" "docs/test-gates.md" "EPICS.md"; do
  if [[ -f "$file" ]]; then
    EPICS_FILE="$file"
    echo "📁 Found epics file: $EPICS_FILE"
    break
  fi
done

if [[ -z "$EPICS_FILE" ]]; then
  echo "⚠️ No epics definition file found"
  echo "   Searched: docs/epics.md, docs/EPICS.md, docs/test-gates.md, EPICS.md"
  echo "   Test gate discovery may fail without this file."
fi

# Check for user-testing directory structure
if [[ ! -d "user-testing" ]]; then
  echo "⚠️ No user-testing/ directory found"
  echo "   This command expects user-testing/scripts/ and user-testing/reports/"
  echo "   Creating minimal structure..."
  mkdir -p user-testing/scripts user-testing/reports
fi
```

Run the discovery script to get test gate configuration:

```bash
python3 "$TESTGATES_SCRIPT" . --format json > /tmp/testgates_config.json 2>/dev/null
```

If this fails or produces empty output, tell the user:
```
❌ Failed to discover test gates from epic definition file
Make sure docs/epics.md (or similar) exists with story and test gate definitions.
```

## Step 2: Check Which Gates Have Already Passed

Parse the config to get list of all test gates in order:

```bash
cat /tmp/testgates_config.json | python3 -c "
import json, sys
config = json.load(sys.stdin)
gates = config.get('test_gates', {})
for gate_id in sorted(gates.keys()):
    print(gate_id)
"
```

For each gate, check if it has passed by looking for a report with "PROCEED":

```bash
gate_id="TG-X.Y"  # Replace with actual gate ID

# Check subdirectory first: user-testing/reports/TG-X.Y/
if [ -d "user-testing/reports/$gate_id" ]; then
    report=$(find "user-testing/reports/$gate_id" -name "*report.md" 2>/dev/null | head -1)
    if [ -n "$report" ] && grep -q "PROCEED" "$report" 2>/dev/null; then
        echo "$gate_id: PASSED"
    fi
fi

# Check main directory: user-testing/reports/TG-X.Y_*_report.md
if [ ! -d "user-testing/reports/$gate_id" ]; then
    report=$(find "user-testing/reports" -maxdepth 1 -name "${gate_id}_*report.md" 2>/dev/null | head -1)
    if [ -n "$report" ] && grep -q "PROCEED" "$report" 2>/dev/null; then
        echo "$gate_id: PASSED"
    fi
fi
```

Build a list of passed gates.

## Step 3: Find Next Test Gate

Walk through all gates in sorted order. For each gate:

1. **Skip if already passed** (from Step 2)
2. **Check if prerequisites are met:**
   - Get the gate's `requires` array from the config
   - Check if all required test gates have passed
3. **First non-passed gate with prerequisites met = next gate**

Get gate info from config:

```bash
gate_id="TG-X.Y"
cat /tmp/testgates_config.json | python3 -c "
import json, sys
config = json.load(sys.stdin)
gate = config['test_gates'].get('$gate_id', {})
print('Name:', gate.get('name', 'Unknown'))
print('Requires:', ','.join(gate.get('requires', [])))
print('Script:', gate.get('script', 'N/A'))
"
```

## Step 3.5: Check Story Implementation Status

Before suggesting a test gate, check if the required story is actually implemented:
for the current gate ID, define the set of implementation files that must exist for
that gate's story to count as implemented, then check for their presence.

This is genuinely project-specific — the gate IDs and file paths differ per project.
`Read ~/.claude/commands/references/usertestgates/step-3.5-example-file-checks.md` for
a worked example (from a historical PPTX-pipeline project) showing the case-statement
pattern; adapt it to this project's actual gates before running.

**Store the story readiness status** to use in Step 4.

**Naming note:** `STORY_NOT_READY` (bash output), "Story [X.Y] NOT IMPLEMENTED" (user-facing
text), and `story_ready` (task metadata field) are three surfaces of the same
story-readiness state. Do not rename these machine tokens — they're read by scripts/task
metadata downstream, not just displayed.

## Step 4: Show Gate Status to User

**Format output like this:**

If some gates already passed:
```
================================================================================
Passed Gates:
  ✅ TG-1.1 - Agent Framework Validation (PASSED)
  ✅ TG-1.2 - Word Parser Validation (PASSED)

🎯 Next Test Gate: TG-1.3 - Excel Parser Validation
================================================================================
```

If story is NOT READY (implementation files missing from Step 3.5):
```
⏳ Story [X.Y] NOT IMPLEMENTED

Required story: Story [X.Y] - [Story Name]

Missing implementation files:
  ❌ src/templates/example-project/title_slide.html.j2
  ❌ src/templates/example-project/big_number.html.j2
  ❌ src/templates/example-project/three_metrics.html.j2
  ❌ src/templates/example-project/bullet_list.html.j2
  ❌ src/templates/example-project/chart_template.html.j2

Please complete Story [X.Y] implementation first.

Once complete, run: /usertestgates
```

If gate is READY (story implemented AND all prerequisite gates passed):
```
✅ This gate is READY to run

Prerequisites: All prerequisite test gates have passed
Story Status: ✅ Story [X.Y] implemented

Script: user-testing/scripts/TG-1.3_excel_parser_validation.py

Run TG-1.3 now? (Y/N)
```

If gate is NOT READY (prerequisite gates not passed):
```
⏳ Complete these test gates first:

  ❌ TG-1.1 - Agent Framework Validation (not passed)

Once complete, run: /usertestgates
```

## Step 5: Execute Gate if User Confirms

If gate is ready and user types Y or Yes:

### Detect if Test Gate is Interactive

Check if the test gate script contains `input()` calls (interactive):

```bash
gate_script="user-testing/scripts/TG-X.Y_*_validation.py"
if grep -q "input(" "$gate_script" 2>/dev/null; then
    echo "INTERACTIVE"
else
    echo "NON_INTERACTIVE"
fi
```

### For NON-INTERACTIVE Gates:

Run directly:

```bash
python3 user-testing/scripts/TG-X.Y_*_validation.py
```

Show the exit code and interpret:
- Exit 0 → ✅ PROCEED
- Exit 1 → ⚠️ REFINE
- Exit 2 → 🚨 ESCALATE
- Exit 130 → ⚠️ Interrupted

Check for report in `user-testing/reports/TG-X.Y/` and mention it

### For INTERACTIVE Gates (Agent-Guided Mode):

**Step 5a: Run Parse Phase**

```bash
python3 user-testing/scripts/TG-X.Y_*_validation.py --phase=parse
```

This outputs parsed data to `/tmp/tg-X.Y-parse-results.json`

**Step 5b: Load Parse Results and Collect User Answers**

Load the parse results:
```bash
cat /tmp/tg-X.Y-parse-results.json
```

For TG-1.3 (Excel Parser), the parse results contain:
- `workbooks`: Array of parsed workbook data
- `total_checks`: Number of validation checks needed (e.g., 30)

For each workbook, you need to ask the user to validate 6 checks. The validation questions are:

1. Sheet Extraction: "All sheets identified and named correctly?"
2. Table Accuracy: "Headers and rows extracted completely?"
3. Metrics Calculation: "Min/max/mean/trend computed accurately?"
4. Chart Suggestions: "Appropriate chart types suggested?"
5. Edge Cases: "Formulas, empty cells, special chars handled?"
6. Data Contract: "Output matches expected JSON schema?"

**For each check:**
1. Show the user the parsed data (from `/tmp/` or parse results)
2. Ask: "Check N/30: [description] - How do you assess this? (PASS/FAIL/PARTIAL/N/A)"
3. Collect: status (PASS/FAIL/PARTIAL/N/A) and optional notes
4. Store in answers array

**The example below shows JSON format only — never nudge the user toward PASS; always
collect their independent judgment for every check, regardless of how prior checks landed.**

**Step 5c: Create Answers JSON**

Create `/tmp/tg-X.Y-answers.json`:

```json
{
  "test_gate": "TG-X.Y",
  "test_date": "2025-10-10T12:00:00",
  "checks": [
    {
      "check_num": 1,
      "status": "PASS",
      "notes": "All sheets extracted correctly"
    },
    {
      "check_num": 2,
      "status": "FAIL",
      "notes": "Header row misaligned on merged cells"
    },
    {
      "check_num": 3,
      "status": "PARTIAL",
      "notes": "Trend computed but edge-case formulas not handled"
    }
  ]
}
```

**Step 5d: Run Report Phase**

```bash
python3 user-testing/scripts/TG-X.Y_*_validation.py --phase=report --answers=/tmp/tg-X.Y-answers.json
```

This generates the final report in `user-testing/reports/TG-X.Y/` with:
- User's validation answers
- Recommendation (PROCEED/REFINE/ESCALATE)
- Exit code (0/1/2)

Show the exit code and interpret:
- Exit 0 → ✅ PROCEED
- Exit 1 → ⚠️ REFINE
- Exit 2 → 🚨 ESCALATE

**Instructions:** Read ~/.claude/commands/references/usertestgates/special-cases.md for special case handling and execution notes.

---

**Instructions:** Read ~/.claude/commands/references/usertestgates/tasklist-integration.md for TaskList integration patterns.
