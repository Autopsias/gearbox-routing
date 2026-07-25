---
description: "Generates structured test plans from requirements: epics, stories, features, or custom functionality. Use when you say 'create test plan', 'plan tests for epic N', 'test strategy for feature X', or need test scenarios with validation criteria."
argument-hint: "[epic-3] [story-2.1] [feature-login] [custom-functionality] [--overwrite]"
allowed-tools: ["Read", "Write", "Grep", "Glob", "LS", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
---

# 📋 Test Plan Creator - High Context Analysis

## Checklist

1. [ ] Step 0 — Detect project structure (docs dir, output dir)
2. [ ] Step 1 — Check for existing plan (respect `--overwrite`)
3. [ ] Step 2 — Requirements Analysis
4. [ ] Step 3 — Test Scenarios
5. [ ] Step 4 — Validation Criteria
6. [ ] Step 5-6 — Agent Execution Prompts + Test Plan File Generation

## Argument Processing

**Target functionality**: "$ARGUMENTS"

Parse functionality identifier:
```javascript
const arguments = "$ARGUMENTS";
const functionalityPattern = /(?:epic-[\d]+(?:\.[\d]+)?|story-[\d]+(?:\.[\d]+)?|feature-[\w-]+|[\w-]+)/g;
const functionalityMatch = arguments.match(functionalityPattern)?.[0] || "custom-functionality";
const overwrite = arguments.includes("--overwrite");
```

Target: `${functionalityMatch}`
Overwrite existing: `${overwrite ? "Yes" : "No"}`

## Test Plan Creation Process

### Step 0: Detect Project Structure

```bash
# ============================================
# DYNAMIC DIRECTORY DETECTION (Project-Agnostic)
# ============================================

# Detect documentation directories
DOCS_DIRS=""
for dir in "docs" "documentation" "wiki" "spec" "specifications"; do
  if [[ -d "$dir" ]]; then
    DOCS_DIRS="$DOCS_DIRS $dir"
  fi
done
if [[ -z "$DOCS_DIRS" ]]; then
  echo "⚠️ No documentation directory found (docs/, documentation/, etc.)"
  echo "   Will search current directory for documentation files"
  DOCS_DIRS="."
fi
echo "📁 Documentation directories: $DOCS_DIRS"

# Detect output directory (allow env override)
if [[ -n "$CREATE_TEST_PLAN_OUTPUT_DIR" ]]; then
  PLANS_DIR="$CREATE_TEST_PLAN_OUTPUT_DIR"
  echo "📁 Using override output dir: $PLANS_DIR"
else
  PLANS_DIR=""
  for dir in "workspace/testing/plans" "test-plans" "testing/plans" "tests/plans"; do
    if [[ -d "$dir" ]]; then
      PLANS_DIR="$dir"
      break
    fi
  done

  # Create in first available parent
  if [[ -z "$PLANS_DIR" ]]; then
    for dir in "workspace/testing/plans" "test-plans" "testing/plans"; do
      PARENT_DIR=$(dirname "$dir")
      if [[ -d "$PARENT_DIR" ]] || mkdir -p "$PARENT_DIR" 2>/dev/null; then
        mkdir -p "$dir" 2>/dev/null && PLANS_DIR="$dir" && break
      fi
    done

    # Ultimate fallback
    if [[ -z "$PLANS_DIR" ]]; then
      PLANS_DIR="./test-plans"
      mkdir -p "$PLANS_DIR"
    fi
  fi
  echo "📁 Test plans directory: $PLANS_DIR"
fi
```

### Step 1: Check for Existing Plan

Claude MUST NOT overwrite an existing plan file unless `--overwrite` was explicitly passed. Check if test plan already exists:
```bash
planFile="$PLANS_DIR/${functionalityMatch}-test-plan.md"
if [[ -f "$planFile" && "$overwrite" != true ]]; then
  echo "⚠️  Test plan already exists: $planFile"
  echo "Use --overwrite to replace existing plan"
  exit 1
fi
```

**Instructions:** Read ~/.claude/commands/references/create-test-plan/output-templates.md for Requirements Analysis, Test Scenarios, and Validation Criteria templates (Steps 2-4).

**Instructions:** Read ~/.claude/commands/references/create-test-plan/agent-prompts.md for Agent Execution Prompts and Test Plan File Generation (Steps 5-6).

**Instructions:** Read ~/.claude/commands/references/create-test-plan/execution-notes.md for Execution Notes and Completion output.

---

**Instructions:** Read ~/.claude/commands/references/create-test-plan/tasklist-integration.md for TaskList integration patterns.