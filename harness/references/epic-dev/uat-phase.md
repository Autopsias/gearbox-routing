# Epic UAT - Hybrid Three-Tier User Acceptance Testing

Execute mandatory UAT for epic: "$ARGUMENTS"

## Contents

- [STEP 1: Parse Arguments](#step-1-parse-arguments)
- [STEP 2: Verify BMAD Project & Load Context](#step-2-verify-bmad-project-load-context)
- [STEP 2.5: Deployment Gate (MANDATORY)](#step-25-deployment-gate-mandatory)
- [STEP 3: Handle Flags](#step-3-handle-flags)
- [PHASE 1: Script Generation (Automated)](#phase-1-script-generation-automated)
- [PHASE 2: Three-Tier Execution Engine](#phase-2-three-tier-execution-engine)
- [PHASE 3: Evidence-Rich Report Generation](#phase-3-evidence-rich-report-generation)
- [Summary](#summary)
- [Execution by Tier](#execution-by-tier)
- [Gate Criteria](#gate-criteria)
- [Scenario Results](#scenario-results)
- [Evidence Detail](#evidence-detail)
- [Failed Scenarios (Detail)](#failed-scenarios-detail)
- [Skipped Scenarios](#skipped-scenarios)
- [PHASE 4: Gate Decision (Deterministic + User Choice)](#phase-4-gate-decision-deterministic-user-choice)
- [Summary](#summary)
- [Note](#note)
- [SPRINT-STATUS SESSION TRACKING](#sprint-status-session-tracking)
- [TASKLIST INTEGRATION](#tasklist-integration)
- [ERROR HANDLING](#error-handling)
- [EXECUTE NOW](#execute-now)


---

## STEP 1: Parse Arguments

Parse "$ARGUMENTS":
- **epic_num** (required): First positional argument (e.g., "1")
- **--waiver**: Skip directly to waiver flow (must provide justification)
- **--resume**: Continue an interrupted UAT session
- **--retest-only**: Only re-test previously failed scenarios

Validation:
- If no epic_num: Error "Usage: /epic-dev-uat <epic-number> [--waiver] [--resume] [--retest-only]"

---

## STEP 2: Verify BMAD Project & Load Context

```bash
PROJECT_ROOT=$(pwd)
while [[ ! -d "$PROJECT_ROOT/_bmad" ]] && [[ "$PROJECT_ROOT" != "/" ]]; do
  PROJECT_ROOT=$(dirname "$PROJECT_ROOT")
done

if [[ ! -d "$PROJECT_ROOT/_bmad" ]]; then
  echo "ERROR: Not a BMAD project. Run bmad-method install first."
  exit 1
fi
```

Load sprint artifacts path from `_bmad/bmm/config.yaml` (default: `_bmad-output/implementation-artifacts`)
Set test artifacts path: `_bmad-output/test-artifacts`

Read `{sprint_artifacts}/sprint-status.yaml`:
- Verify epic exists
- Verify all stories for epic are "done" (UAT runs after all stories complete)
- Check for existing `epic_uat_session` entry

---

## STEP 2.5: Deployment Gate (MANDATORY)

```
env_descriptor_path = "{sprint_artifacts}/uat-environment.yaml"

IF NOT exists(env_descriptor_path):
  Output: "ERROR: UAT requires an environment descriptor at {env_descriptor_path}."
  Output: "Create uat-environment.yaml declaring how to reach the deployed system."
  Exit 1

env_descriptor = Read(env_descriptor_path)
health_check_cmd = env_descriptor.deployment.health_check
deploy_cmd = env_descriptor.deployment.deploy_command

Output: "Checking deployment health..."
health_result = Bash(health_check_cmd)

IF health_result.exit_code == 0 AND "ok" IN health_result.stdout.lower() OR "healthy" IN health_result.stdout.lower():
  Output: "  Deployment is healthy. Proceeding."
  deployment_verified = true
ELSE:
  Output: "  Deployment is NOT healthy or unreachable."
  Output: "  Health check output: {health_result.stdout} {health_result.stderr}"

  deploy_decision = AskUserQuestion(
    question: "Deployed system is unhealthy. How do you want to proceed?",
    header: "Deploy Gate",
    options: [
      {label: "Deploy now", description: "Run deploy command, then re-check health"},
      {label: "I'll fix manually", description: "Pause while I fix the deployment, then re-check"},
      {label: "Waiver (code inspection only)", description: "Proceed without deployment — report flagged as UNVERIFIED-DEPLOYMENT"}
    ]
  )

  IF deploy_decision == "Deploy now":
    Output: "Deploying..."
    deploy_result = Bash(deploy_cmd, timeout=300000)
    Output: deploy_result.stdout
    # Re-check health
    health_result = Bash(health_check_cmd)
    IF health_result.exit_code == 0:
      Output: "  Deployment is now healthy."
      deployment_verified = true
    ELSE:
      Output: "  Deployment still unhealthy after deploy. Cannot proceed."
      Exit 1

  ELIF deploy_decision == "I'll fix manually":
    AskUserQuestion(
      question: "Press continue when the deployment is fixed and healthy.",
      header: "Waiting",
      options: [
        {label: "Continue", description: "Re-check health now"},
        {label: "Abort", description: "Cancel UAT"}
      ]
    )
    health_result = Bash(health_check_cmd)
    IF health_result.exit_code == 0:
      deployment_verified = true
    ELSE:
      Output: "  Still unhealthy. Aborting."
      Exit 1

  ELIF deploy_decision contains "Waiver":
    deployment_verified = false
    Output: "  WARNING: UAT will run as UNVERIFIED-DEPLOYMENT (code inspection only)."
    Output: "  Report will be flagged accordingly."
```

---

## STEP 3: Handle Flags

```
IF --waiver:
  GOTO PHASE 4 (Waiver Flow)

IF --resume:
  # Load existing session
  content = Read("{sprint_artifacts}/sprint-status.yaml")

  IF content does NOT contain "epic_uat_session:" with epic == {epic_num}:
    Output: "No UAT session found for Epic {epic_num}. Starting fresh."
    PROCEED to PHASE 1

  # Extract session state
  # Resume from last incomplete scenario
  PROCEED to PHASE 2 (with session state)

IF --retest-only:
  # Check for previous UAT report
  previous_report = "{test_artifacts}/uat-report-epic-{epic_num}.md"
  IF NOT exists(previous_report):
    Output: "No previous UAT report found. Run full UAT first."
    Exit 1
  # Will be used in PHASE 1 to generate retest-only script
```

---

## PHASE 1: Script Generation (Automated)

```
Output: "════════════════════════════════════════════════════════"
Output: "  PHASE 1: UAT Script Generation"
Output: "════════════════════════════════════════════════════════"

# Check for previous report (for re-test marking)
previous_report_path = ""
IF exists("{test_artifacts}/uat-report-epic-{epic_num}.md"):
  previous_report_path = "{test_artifacts}/uat-report-epic-{epic_num}.md"

# Delegate to uat-script-generator agent
Task(
  subagent_type="uat-script-generator",
  model="opus",
  description="Generate UAT script for Epic {epic_num}",
  prompt="Generate a UAT test script for Epic {epic_num}.

Context:
- Sprint artifacts: {sprint_artifacts}
- Test artifacts: {test_artifacts}
- Project root: {PROJECT_ROOT}
- Epic number: {epic_num}
- Previous UAT report: {previous_report_path or 'none'}
- Retest only: {--retest-only flag or false}

Read all completed stories for this epic, extract acceptance criteria,
and generate a complete UAT script with three-tier classification at:
  {test_artifacts}/uat-script-epic-{epic_num}.md

CRITICAL CONTEXT:
- Environment descriptor: {env_descriptor_path} (READ THIS FIRST)
- Deployment verified: {deployment_verified}

MANDATORY RULES:
1. Every auto/auto+verify step MUST execute against the DEPLOYED system via channels in uat-environment.yaml
2. Local source inspection (cat src/, grep src/, python -c 'import ast') is FORBIDDEN — that is acceptance testing, not UAT
3. For every scenario ask: 'If I deleted all source code from my laptop, would this test still work?' If NO → not UAT
4. ACs about file existence or code structure are OUT OF SCOPE for UAT — note them as 'covered by acceptance tests'
5. Do NOT grep source code for function signatures. Instead determine how an OPERATOR would verify behavior on the RUNNING system.

Return JSON: {script_path, total_scenarios, auto_count, verify_count, manual_count, p0_count, p1_count, p2_count, retest_count, filtered_out_of_scope_count}"
)

# Verify script was created
script_path = "{test_artifacts}/uat-script-epic-{epic_num}.md"
content = Read(script_path)
IF content is empty:
  Output: "ERROR: UAT script generation failed"
  Exit 1

# ANTI-PATTERN VALIDATION: Check that auto_steps don't inspect source code
ANTI_PATTERNS = "pytest|test_ac_|tests/|cat src/|cat _bmad|grep .* src/|grep .* _bmad|cd /Users/|cd ~/|import ast|importlib|architecture\\.md|planning-artifacts|sprint-status\\.yaml"
Grep(pattern=ANTI_PATTERNS, path=script_path)
IF matches found in "Auto Steps" sections:
  match_count = count(matches)
  total_auto_steps = count(auto steps in script)
  Output: "WARNING: {match_count} auto steps match anti-patterns (source inspection, not deployed-system tests)."
  IF match_count > total_auto_steps * 0.5:
    Output: "REJECTED: >50% of auto steps are code inspection, not UAT. Regenerating with stronger instructions..."
    # Re-run generator with explicit rejection of source-code inspection
  ELSE:
    Output: "Some auto steps reference local files. These will be skipped at runtime."

# Parse scenario counts from script
Output: "UAT Script generated: {script_path}"
Output: "  Total scenarios: {total}"
Output: "  Tier 1 (auto):        {auto_count}"
Output: "  Tier 2 (auto+verify): {verify_count}"
Output: "  Tier 3 (manual):      {manual_count}"
Output: "  P0 (Critical):        {p0_count}"
Output: "  P1 (Important):       {p1_count}"
Output: "  P2 (Nice-to-have):    {p2_count}"

# Initialize session tracking in sprint-status.yaml
Edit or append epic_uat_session to sprint-status.yaml:

  epic_uat_session:
    epic: {epic_num}
    phase: "executing"
    total_scenarios: {total}
    auto_count: {auto_count}
    verify_count: {verify_count}
    manual_count: {manual_count}
    p0_passed: 0
    p0_total: {p0_count}
    p1_passed: 0
    p1_total: {p1_count}
    p2_passed: 0
    p2_total: {p2_count}
    gate_decision: null
    started: "{timestamp}"
```

---

## PHASE 2: Three-Tier Execution Engine

```
Output: ""
Output: "════════════════════════════════════════════════════════"
Output: "  PHASE 2: Hybrid UAT Execution"
Output: "════════════════════════════════════════════════════════"
Output: ""
Output: "Execution model:"
Output: "  Tier 1 (auto):        Fully automated — you see evidence summary"
Output: "  Tier 2 (auto+verify): Automated execution — you review output quality"
Output: "  Tier 3 (manual):      You execute steps — system handles setup/teardown"
Output: ""
Output: "Scenarios are ordered by priority (P0 first, then P1, then P2)."
Output: ""

# Read the UAT script
script = Read("{test_artifacts}/uat-script-epic-{epic_num}.md")

# Parse all scenarios from the script
# Scenarios are delimited by "### UAT-" headers
# Extract: id, priority, title, execution_tier, setup_commands, auto_steps,
#          evidence_capture, verification_criteria, user_verification_prompt,
#          manual_steps, expected_result, teardown_commands

# Initialize counters
results = {}
evidence_log = {}
p0_passed = 0
p0_failed = 0
p1_passed = 0
p1_failed = 0
p2_passed = 0
p2_failed = 0
skipped = 0
scenario_index = 0

# Process scenarios in priority order: P0 first, then P1, then P2
FOR each scenario IN scenarios (ordered by priority):
  scenario_index += 1

  Output: ""
  Output: "────────────────────────────────────────────────────────"
  Output: "  Scenario {scenario_index}/{total}: {scenario.id}"
  Output: "  Priority: {scenario.priority} | Tier: {scenario.execution_tier}"
  Output: "  {scenario.title}"
  IF scenario has [RE-TEST] tag:
    Output: "  [RE-TEST] This scenario previously failed"
  Output: "────────────────────────────────────────────────────────"
  Output: ""

  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  # TIER 1: Fully Automated (auto)
  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  IF scenario.execution_tier == "auto":
    Output: "  [AUTO] Running automated execution..."
    Output: ""

    # 1. Run setup commands
    IF scenario.setup_commands:
      Output: "  Setting up..."
      FOR each cmd IN scenario.setup_commands:
        Bash(cmd)
      END FOR

    # 2. Run auto_steps via Bash, capture all stdout
    #    RUNTIME GUARD: Skip steps that inspect source code instead of deployed system
    auto_output = ""
    auto_exit_code = 0
    FOR each step IN scenario.auto_steps:
      IF step starts with "cat src/" OR step starts with "cat _bmad" \
         OR step matches "grep .* src/" OR step matches "grep .* _bmad" \
         OR step contains "cd /Users/" OR step contains "cd ~/" \
         OR step contains "import ast" OR step contains "importlib" \
         OR step starts with "python -c" AND NOT step contains "ssh":
        auto_output += "SKIPPED (code inspection, not deployed-system test): {step}\n"
        Output: "    SKIPPED (code inspection): {step truncated to 80 chars}"
        CONTINUE

      result = Bash(step)
      auto_output += result.stdout + "\n"
      IF result.exit_code != 0:
        auto_exit_code = result.exit_code
        auto_output += "STDERR: " + result.stderr + "\n"
    END FOR

    # 3. Run evidence_capture commands
    evidence_output = ""
    IF scenario.evidence_capture:
      FOR each cmd IN scenario.evidence_capture:
        result = Bash(cmd)
        evidence_output += result.stdout + "\n"
      END FOR

    # 4. Evaluate verification_criteria against captured output
    all_checks_pass = true
    check_results = []
    combined_output = auto_output + evidence_output

    FOR each criterion IN scenario.verification_criteria:
      IF criterion.type == "contains":
        passed = criterion.value IN combined_output
        check_results.append({criterion: criterion.description, passed: passed})
        IF NOT passed: all_checks_pass = false

      ELIF criterion.type == "not_contains":
        passed = criterion.value NOT IN combined_output
        check_results.append({criterion: criterion.description, passed: passed})
        IF NOT passed: all_checks_pass = false

      ELIF criterion.type == "exit_code":
        passed = auto_exit_code == criterion.value
        check_results.append({criterion: "Exit code = " + criterion.value, passed: passed})
        IF NOT passed: all_checks_pass = false

      ELIF criterion.type == "regex":
        passed = regex_match(criterion.value, combined_output)
        check_results.append({criterion: criterion.description, passed: passed})
        IF NOT passed: all_checks_pass = false
    END FOR

    # 5. Auto-determine PASS/FAIL
    verdict = "PASS" IF all_checks_pass ELSE "FAIL"

    # 6. Display evidence summary
    Output: "  Verification checks:"
    FOR each check IN check_results:
      status = "✓" IF check.passed ELSE "✗"
      Output: "    {status} {check.criterion}"
    END FOR
    Output: ""
    Output: "  Verdict: {verdict}"

    # Store evidence
    evidence_log[scenario.id] = {
      auto_output: auto_output,
      evidence_output: evidence_output,
      checks: check_results
    }

    # 7. Run teardown
    IF scenario.teardown_commands:
      FOR each cmd IN scenario.teardown_commands:
        Bash(cmd)
      END FOR

    # Record result
    results[scenario.id] = {verdict: verdict, notes: "Auto-verified", evidence: "See evidence log"}

  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  # TIER 2: Auto Execute + User Verifies (auto+verify)
  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ELIF scenario.execution_tier == "auto+verify":
    Output: "  [AUTO+VERIFY] Running automated execution, then you review..."
    Output: ""

    # 1. Run setup commands
    IF scenario.setup_commands:
      Output: "  Setting up..."
      FOR each cmd IN scenario.setup_commands:
        Bash(cmd)
      END FOR

    # 2. Run auto_steps via Bash, capture all stdout
    #    RUNTIME GUARD: Skip steps that inspect source code instead of deployed system
    auto_output = ""
    FOR each step IN scenario.auto_steps:
      IF step starts with "cat src/" OR step starts with "cat _bmad" \
         OR step matches "grep .* src/" OR step matches "grep .* _bmad" \
         OR step contains "cd /Users/" OR step contains "cd ~/" \
         OR step contains "import ast" OR step contains "importlib" \
         OR step starts with "python -c" AND NOT step contains "ssh":
        auto_output += "SKIPPED (code inspection, not deployed-system test): {step}\n"
        Output: "    SKIPPED (code inspection): {step truncated to 80 chars}"
        CONTINUE

      result = Bash(step)
      auto_output += result.stdout + "\n"
      IF result.exit_code != 0:
        auto_output += "STDERR: " + result.stderr + "\n"
    END FOR

    # 3. Run evidence_capture commands
    evidence_output = ""
    IF scenario.evidence_capture:
      FOR each cmd IN scenario.evidence_capture:
        result = Bash(cmd)
        evidence_output += result.stdout + "\n"
      END FOR

    # 4. Display FULL untruncated output to user
    Output: ""
    Output: "  ── Output ──────────────────────────────────────────"
    Output: auto_output
    IF evidence_output:
      Output: ""
      Output: "  ── Evidence ────────────────────────────────────────"
      Output: evidence_output
    Output: "  ────────────────────────────────────────────────────"
    Output: ""

    # 5. Ask user the specific verification question
    verification_question = scenario.user_verification_prompt
    # Default if not specified:
    IF NOT verification_question:
      verification_question = "Does the output above meet the expected behavior for this scenario?"

    verdict = AskUserQuestion(
      question: verification_question,
      header: "Quality",
      options: [
        {label: "PASS", description: "Output meets quality expectations"},
        {label: "PASS with notes", description: "Acceptable but with observations"},
        {label: "FAIL", description: "Output does not meet expectations"}
      ]
    )

    # 6. Handle verdict
    IF verdict == "PASS with notes":
      notes = AskUserQuestion(
        question: "What observations do you want to record?",
        header: "Notes",
        options: [
          {label: "Minor quality issue", description: "Content is acceptable but could be better"},
          {label: "Formatting concern", description: "Content is correct but formatting needs work"},
          {label: "Unexpected tone", description: "Works but tone/personality is off"}
        ]
      )
      verdict = "PASS"
      results[scenario.id] = {verdict: "PASS", notes: notes}
    ELIF verdict == "FAIL":
      notes = AskUserQuestion(
        question: "What was wrong with the output?",
        header: "Failure",
        options: [
          {label: "Wrong content", description: "Output content is incorrect"},
          {label: "Poor quality", description: "Content is low quality or incoherent"},
          {label: "Error in output", description: "Output contains errors or tracebacks"},
          {label: "Missing expected content", description: "Expected content is absent"}
        ]
      )
      results[scenario.id] = {verdict: "FAIL", notes: notes}
    ELSE:
      results[scenario.id] = {verdict: "PASS", notes: ""}

    # Store evidence
    evidence_log[scenario.id] = {
      auto_output: auto_output,
      evidence_output: evidence_output,
      user_judgment: verdict
    }

    # 7. Run teardown
    IF scenario.teardown_commands:
      FOR each cmd IN scenario.teardown_commands:
        Bash(cmd)
      END FOR

  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  # TIER 3: Manual Execution (manual)
  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ELIF scenario.execution_tier == "manual":
    Output: "  [MANUAL] System handles setup/teardown — you execute the steps."
    Output: ""

    # 1. Run setup commands automatically
    IF scenario.setup_commands:
      Output: "  Setting up pre-conditions..."
      FOR each cmd IN scenario.setup_commands:
        Bash(cmd)
      END FOR
      Output: "  Setup complete."
      Output: ""

    # 2. Tell user exactly what to do
    Output: "  Steps for you to execute:"
    FOR each step IN scenario.manual_steps:
      Output: "    {step.number}. {step.text}"
    END FOR
    Output: ""

    Output: "  Expected Result:"
    FOR each expectation IN scenario.expected_result:
      Output: "    - {expectation}"
    Output: ""

    # 3. Wait for user to confirm they executed
    AskUserQuestion(
      question: "Have you completed the manual steps above?",
      header: "Execution",
      options: [
        {label: "Done", description: "I've completed the steps"},
        {label: "Could not execute", description: "I was unable to perform the steps"}
      ]
    )

    # 4. Run evidence_capture commands automatically
    evidence_output = ""
    IF scenario.evidence_capture:
      Output: "  Capturing evidence..."
      FOR each cmd IN scenario.evidence_capture:
        result = Bash(cmd)
        evidence_output += result.stdout + "\n"
      END FOR

      # 5. Show captured evidence
      Output: ""
      Output: "  ── Captured Evidence ───────────────────────────────"
      Output: evidence_output
      Output: "  ────────────────────────────────────────────────────"
      Output: ""

    # 6. Ask user for verdict
    verdict = AskUserQuestion(
      question: "What is the result for {scenario.id}?",
      header: "UAT Verdict",
      options: [
        {label: "PASS", description: "Scenario behaved as expected"},
        {label: "PASS with notes", description: "Passed but with observations to record"},
        {label: "FAIL", description: "Scenario did not meet expected behavior"},
        {label: "SKIP", description: "Cannot test right now (provide reason)"}
      ]
    )

    # Handle verdict (same as original Phase 2 flow)
    IF verdict == "PASS":
      results[scenario.id] = {verdict: "PASS", notes: ""}

    ELIF verdict == "PASS with notes":
      notes = AskUserQuestion(
        question: "What observations do you want to record?",
        header: "Notes",
        options: [
          {label: "Minor UI issue", description: "Works but could look/feel better"},
          {label: "Slow response", description: "Functionally correct but slow"},
          {label: "Unexpected behavior", description: "Passed but something was surprising"}
        ]
      )
      results[scenario.id] = {verdict: "PASS", notes: notes}

    ELIF verdict == "FAIL":
      fail_notes = AskUserQuestion(
        question: "Describe what went wrong:",
        header: "Failure",
        options: [
          {label: "Wrong output", description: "Got different result than expected"},
          {label: "Error/crash", description: "System error or unexpected failure"},
          {label: "Missing feature", description: "Feature not implemented or not working"},
          {label: "Security concern", description: "Security-related failure"}
        ]
      )
      results[scenario.id] = {verdict: "FAIL", notes: fail_notes}

    ELIF verdict == "SKIP":
      skip_reason = AskUserQuestion(
        question: "Why are you skipping this scenario?",
        header: "Skip Reason",
        options: [
          {label: "Environment issue", description: "Cannot set up required conditions"},
          {label: "Blocked by bug", description: "Known issue prevents testing"},
          {label: "Time constraint", description: "Will test later"},
          {label: "Not applicable", description: "Scenario doesn't apply to current state"}
        ]
      )
      results[scenario.id] = {verdict: "SKIP", notes: skip_reason}
      skipped += 1

    # Store evidence
    evidence_log[scenario.id] = {evidence_output: evidence_output}

    # 7. Run teardown automatically
    IF scenario.teardown_commands:
      Output: "  Cleaning up..."
      FOR each cmd IN scenario.teardown_commands:
        Bash(cmd)
      END FOR

  END IF (tier dispatch)

  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  # Common: Update counters and handle P0 failures
  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  current_verdict = results[scenario.id].verdict

  IF current_verdict == "PASS":
    IF scenario.priority == "P0": p0_passed += 1
    ELIF scenario.priority == "P1": p1_passed += 1
    ELIF scenario.priority == "P2": p2_passed += 1

  ELIF current_verdict == "FAIL":
    IF scenario.priority == "P0":
      p0_failed += 1
      Output: ""
      Output: "  *** P0 FAILURE DETECTED ***"
      Output: "  A critical scenario has failed."

      # Offer early exit on P0 failure
      continue_decision = AskUserQuestion(
        question: "A P0 scenario failed. Continue testing or stop to fix?",
        header: "P0 Failure",
        options: [
          {label: "Continue testing", description: "Finish remaining scenarios for full report"},
          {label: "Stop and fix", description: "Exit UAT to address P0 failure first"}
        ]
      )

      IF continue_decision == "Stop and fix":
        Output: ""
        Output: "UAT paused due to P0 failure."
        Output: "Fix the issue and re-run: /epic-dev-uat {epic_num} --resume"
        Update epic_uat_session: phase: "paused", gate_decision: null
        Exit 1

    ELIF scenario.priority == "P1": p1_failed += 1
    ELIF scenario.priority == "P2": p2_failed += 1

  # Show running progress
  completed = scenario_index
  total_passed = p0_passed + p1_passed + p2_passed
  total_failed = p0_failed + p1_failed + p2_failed
  Output: ""
  Output: "  Progress: {completed}/{total} | Passed: {total_passed} | Failed: {total_failed} | Skipped: {skipped}"

END FOR
```

---

## PHASE 3: Evidence-Rich Report Generation

```
Output: ""
Output: "════════════════════════════════════════════════════════"
Output: "  PHASE 3: UAT Report Generation"
Output: "════════════════════════════════════════════════════════"

# Calculate rates
p0_total = p0_passed + p0_failed  # (skipped P0s don't count)
p1_total = p1_passed + p1_failed
p0_rate = (p0_passed / p0_total * 100) IF p0_total > 0 ELSE 100
p1_rate = (p1_passed / p1_total * 100) IF p1_total > 0 ELSE 100
total_tested = p0_total + p1_total + p2_passed + p2_failed
total_pass_rate = ((p0_passed + p1_passed + p2_passed) / total_tested * 100) IF total_tested > 0 ELSE 100

# Determine gate decision
IF p0_rate < 100:
  gate_decision = "FAIL"
ELIF p1_rate < 90:
  gate_decision = "CONCERNS"
ELSE:
  gate_decision = "PASS"

# Count by tier
auto_passed = count(results WHERE tier == "auto" AND verdict == "PASS")
auto_failed = count(results WHERE tier == "auto" AND verdict == "FAIL")
verify_passed = count(results WHERE tier == "auto+verify" AND verdict == "PASS")
verify_failed = count(results WHERE tier == "auto+verify" AND verdict == "FAIL")
manual_passed = count(results WHERE tier == "manual" AND verdict == "PASS")
manual_failed = count(results WHERE tier == "manual" AND verdict == "FAIL")

# Generate report
report_path = "{test_artifacts}/uat-report-epic-{epic_num}.md"

Write(report_path, content="""
# UAT Report: Epic {epic_num} - {Epic Title}

## Summary
| Metric | Value |
|--------|-------|
| Date | {date} |
| Total Scenarios | {total} |
| Passed | {total_passed} ({total_pass_rate}%) |
| Failed | {total_failed} |
| Skipped | {skipped} |
| **Decision** | **{gate_decision}** |

## Execution by Tier
| Tier | Total | Passed | Failed | Description |
|------|-------|--------|--------|-------------|
| Tier 1 (auto) | {auto_total} | {auto_passed} | {auto_failed} | Fully automated with programmatic verification |
| Tier 2 (auto+verify) | {verify_total} | {verify_passed} | {verify_failed} | Automated execution, human quality judgment |
| Tier 3 (manual) | {manual_total} | {manual_passed} | {manual_failed} | User-executed with automated setup/teardown |

## Gate Criteria
| Gate | Required | Actual | Status |
|------|----------|--------|--------|
| P0   | 100%     | {p0_rate}%   | {PASS if p0_rate == 100 else FAIL} |
| P1   | >= 90%   | {p1_rate}%   | {PASS if p1_rate >= 90 else FAIL} |

## Scenario Results

| ID | Priority | Tier | Story | Verdict | Notes |
|----|----------|------|-------|---------|-------|
{FOR each scenario in results:}
| {id} | {priority} | {tier} | {story_key} | {verdict} | {notes} |
{END FOR}

## Evidence Detail

{FOR each scenario in results:}
### {scenario.id}: {scenario.title}
**Tier**: {scenario.tier} | **Verdict**: {scenario.verdict}

{IF scenario.id IN evidence_log:}
**Execution Output:**
```
{evidence_log[scenario.id].auto_output}
```

{IF evidence_log[scenario.id].evidence_output:}
**Evidence Capture:**
```
{evidence_log[scenario.id].evidence_output}
```
{END IF}

{IF evidence_log[scenario.id].checks:}
**Verification Checks:**
{FOR each check IN evidence_log[scenario.id].checks:}
- {✓ if passed else ✗} {check.criterion}
{END FOR}
{END IF}

{IF evidence_log[scenario.id].user_judgment:}
**User Judgment:** {evidence_log[scenario.id].user_judgment}
{END IF}
{END IF}

---
{END FOR}

## Failed Scenarios (Detail)

{FOR each scenario WHERE verdict == "FAIL":}
### {scenario.id}: {scenario.title}
- **Priority**: {scenario.priority}
- **Tier**: {scenario.tier}
- **Story**: {scenario.story_key}
- **Expected**: {scenario.expected_result}
- **Actual**: {scenario.notes}
- **Impact**: {P0=blocking, P1=significant, P2=minor}
{END FOR}

## Skipped Scenarios

{FOR each scenario WHERE verdict == "SKIP":}
- **{scenario.id}**: {scenario.title} — Reason: {scenario.notes}
{END FOR}
""")

Output: "UAT Report generated: {report_path}"
Output: ""
```

---

## PHASE 4: Gate Decision (Deterministic + User Choice)

```
Output: ""
Output: "════════════════════════════════════════════════════════"
Output: "  PHASE 4: UAT Gate Decision"
Output: "════════════════════════════════════════════════════════"
Output: ""
Output: "  P0 Pass Rate: {p0_rate}% (required: 100%)"
Output: "  P1 Pass Rate: {p1_rate}% (required: >= 90%)"
Output: "  Overall:      {total_pass_rate}%"
Output: ""
Output: "  Tier breakdown:"
Output: "    Auto:        {auto_passed}/{auto_total} passed"
Output: "    Auto+Verify: {verify_passed}/{verify_total} passed"
Output: "    Manual:      {manual_passed}/{manual_total} passed"
Output: ""

IF --waiver:
  # Waiver flow
  Output: "Waiver requested for Epic {epic_num} UAT."

  waiver_reason = AskUserQuestion(
    question: "Provide justification for UAT waiver:",
    header: "Waiver",
    options: [
      {label: "Environment unavailable", description: "Cannot set up test environment"},
      {label: "Time-critical release", description: "Urgent deployment needed"},
      {label: "Covered by automation", description: "Automated tests provide sufficient coverage"},
      {label: "Partial validation done", description: "Some manual testing was performed outside UAT"}
    ]
  )

  gate_decision = "WAIVED"
  Output: "UAT WAIVED: {waiver_reason}"
  Output: "Waiver documented in UAT report and sprint-status.yaml"

  # Write minimal waiver report
  Write("{test_artifacts}/uat-report-epic-{epic_num}.md", content="""
# UAT Report: Epic {epic_num} - {Epic Title}

## Summary
| Metric | Value |
|--------|-------|
| Date | {date} |
| **Decision** | **WAIVED** |
| Waiver Reason | {waiver_reason} |

## Note
UAT was waived. No scenarios were executed interactively.
Automated test coverage is assumed sufficient per waiver justification.
""")

ELIF gate_decision == "PASS":
  Output: "UAT PASSED"
  Output: "  All P0 scenarios passed (100%)"
  Output: "  P1 pass rate meets threshold (>= 90%)"
  Output: ""
  Output: "Epic {epic_num} is cleared for completion."

ELIF gate_decision == "FAIL":
  Output: "UAT FAILED"
  Output: "  P0 scenarios have failures — these MUST be fixed."
  Output: ""
  Output: "  Failed P0 scenarios:"
  FOR each failed P0 scenario:
    Output: "    - {scenario.id}: {scenario.title}"
  Output: ""

  fail_decision = AskUserQuestion(
    question: "UAT failed. How do you want to proceed?",
    header: "UAT Failed",
    options: [
      {label: "Fix and re-test", description: "Fix P0 failures, then run: /epic-dev-uat {epic_num} --retest-only"},
      {label: "Request waiver", description: "Document justification and proceed anyway"},
      {label: "Stop", description: "Leave epic incomplete until fixed"}
    ]
  )

  IF fail_decision == "Request waiver":
    waiver_reason = AskUserQuestion(
      question: "Provide waiver justification:",
      header: "Waiver",
      options: [
        {label: "Known limitation", description: "Accepted limitation with tracking ticket"},
        {label: "Workaround exists", description: "Users can work around the issue"},
        {label: "Fix scheduled", description: "Fix planned for next sprint"}
      ]
    )
    gate_decision = "WAIVED"
    Output: "UAT WAIVED (was FAIL): {waiver_reason}"

  ELIF fail_decision == "Fix and re-test":
    Output: "Fix the failing scenarios, then run:"
    Output: "  /epic-dev-uat {epic_num} --retest-only"
    # Keep session in sprint-status with gate_decision = "FAIL"
    Update epic_uat_session: gate_decision: "FAIL"
    Exit 1

  ELIF fail_decision == "Stop":
    Output: "Epic {epic_num} UAT incomplete. Fix issues and re-run."
    Update epic_uat_session: gate_decision: "FAIL"
    Exit 1

ELIF gate_decision == "CONCERNS":
  Output: "UAT: CONCERNS"
  Output: "  All P0 scenarios passed"
  Output: "  P1 pass rate below 90% threshold"
  Output: ""

  concern_decision = AskUserQuestion(
    question: "P1 pass rate is below 90%. How do you want to proceed?",
    header: "Concerns",
    options: [
      {label: "Accept and proceed", description: "Accept current state, document concerns"},
      {label: "Fix and re-test", description: "Fix P1 failures, then run: /epic-dev-uat {epic_num} --retest-only"},
      {label: "Request waiver", description: "Document justification for P1 gaps"}
    ]
  )

  IF concern_decision == "Accept and proceed":
    gate_decision = "PASS"
    Output: "Concerns accepted. Epic cleared for completion."

  ELIF concern_decision == "Fix and re-test":
    Output: "Fix the failing P1 scenarios, then run:"
    Output: "  /epic-dev-uat {epic_num} --retest-only"
    Update epic_uat_session: gate_decision: "CONCERNS"
    Exit 1

  ELIF concern_decision == "Request waiver":
    waiver_reason = AskUserQuestion(
      question: "Provide waiver justification for P1 gaps:",
      header: "Waiver",
      options: [
        {label: "Low impact", description: "P1 failures are low-impact edge cases"},
        {label: "Fix scheduled", description: "Fixes planned for next sprint"},
        {label: "Acceptable risk", description: "Risk accepted by stakeholder"}
      ]
    )
    gate_decision = "WAIVED"
    Output: "UAT WAIVED (was CONCERNS): {waiver_reason}"

END IF

# Update sprint-status.yaml with final decision
Update epic_uat_session:
  phase: "completed"
  gate_decision: "{gate_decision}"
  completed: "{timestamp}"

Output: ""
Output: "════════════════════════════════════════════════════════"
Output: "  UAT COMPLETE: Epic {epic_num}"
Output: "════════════════════════════════════════════════════════"
Output: "  Decision: {gate_decision}"
Output: "  Report: {test_artifacts}/uat-report-epic-{epic_num}.md"
Output: ""
IF gate_decision in ["PASS", "WAIVED"]:
  Output: "  Epic is cleared for completion."
  Output: "  Run: /epic-dev {epic_num}  (to mark epic done)"
Output: "════════════════════════════════════════════════════════"
```

---

## SPRINT-STATUS SESSION TRACKING

The `epic_uat_session` block is managed in `sprint-status.yaml`:

```yaml
epic_uat_session:
  epic: 1
  phase: "executing"          # generating | executing | paused | completed
  total_scenarios: 12
  auto_count: 5               # Tier 1 scenarios
  verify_count: 4             # Tier 2 scenarios
  manual_count: 3             # Tier 3 scenarios
  p0_passed: 5
  p0_total: 5
  p1_passed: 6
  p1_total: 7
  p2_passed: 3
  p2_total: 4
  gate_decision: null          # PASS | CONCERNS | FAIL | WAIVED
  started: "2026-02-14T18:00:00Z"
  completed: null
```

Session lifecycle:
- Created at PHASE 1 start
- Updated after each scenario in PHASE 2
- Finalized at PHASE 4 end
- Cleared when epic is marked "done" by epic-dev

---

## TASKLIST INTEGRATION

### Task Creation at Start

```
TaskCreate(
  subject="Phase 1: Generate UAT Script - Epic {epic_num}",
  description="Generate three-tier UAT test script from story acceptance criteria",
  activeForm="Generating UAT script"
) -> task_id_1

TaskCreate(
  subject="Phase 2: Hybrid UAT Execution - Epic {epic_num}",
  description="Execute {auto_count} auto + {verify_count} auto+verify + {manual_count} manual scenarios",
  activeForm="Executing UAT scenarios"
) -> task_id_2
TaskUpdate(taskId=task_id_2, addBlockedBy=[task_id_1])

TaskCreate(
  subject="Phase 3: Generate Evidence-Rich UAT Report - Epic {epic_num}",
  description="Compile UAT results with execution evidence into report",
  activeForm="Generating UAT report"
) -> task_id_3
TaskUpdate(taskId=task_id_3, addBlockedBy=[task_id_2])

TaskCreate(
  subject="Phase 4: UAT Gate Decision - Epic {epic_num}",
  description="Evaluate UAT results against gate criteria",
  activeForm="Evaluating UAT gate"
) -> task_id_4
TaskUpdate(taskId=task_id_4, addBlockedBy=[task_id_3])
```

### Phase Transitions

```
# When entering each phase:
TaskUpdate(taskId=phase_task_id, status="in_progress")

# When phase completes:
TaskUpdate(taskId=phase_task_id, status="completed")
```

---

## ERROR HANDLING

On any error:
1. Save current session state to sprint-status.yaml
2. Display error with context
3. Offer: "Retry / Resume later / Stop"
4. Resume with: `/epic-dev {epic_num} --uat --resume`

---

## EXECUTE NOW

Parse "$ARGUMENTS" and begin the hybrid UAT sequence immediately.
