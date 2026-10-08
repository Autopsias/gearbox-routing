# Phase 1: Create Story (opus)

**Execute when:** `story.status == "backlog"`

```
Output: "=== [Phase 1/8] Creating story: {story_key} (opus) ==="

Update session:
  - phase: "create_story"
  - last_updated: {timestamp}

Write sprint-status.yaml

Task(
  subagent_type="epic-story-creator",
  model="opus",
  description="Create story {story_key}",
  prompt="Create story for {story_key}.

Context:
- Epic file: {sprint_artifacts}/epic-{epic_num}.md
- Story key: {story_key}
- Sprint artifacts: {sprint_artifacts}

Execute the BMAD create-story workflow.
Return ONLY JSON: {story_path, ac_count, task_count, status}"
)

Verify:
- Story file exists at {sprint_artifacts}/stories/{story_key}.md
- Story status updated in sprint-status.yaml

Update session:
  - phase: "create_complete"

PROCEED TO PHASE 2
```

---

## STORY TYPE DETECTION (after Phase 1, before Phase 2)

**Execute once per story** — persists `story_type` in session for all subsequent phases.

```
# Read the story file (just created or pre-existing)
story_content = Read("{sprint_artifacts}/stories/{story_key}.md")
story_title = Extract first line starting with "# " from story_content (strip "# " prefix)

story_type = "coding"  # default

IF story_title contains "UAT Validation" OR "UAT Gate" OR story_title matches "\bUAT\b":
  story_type = "uat"
ELIF story_title contains any of "AWS", "IAM"
     OR story_title OR story_content contains any of
        "deployment-briefings", "MANUAL setup", "No coding required", "/aws-deploy", "AWS_setup":
  story_type = "infrastructure"
ELIF story_title contains any of "Documentation", "Guide", "Reference", "Runbook", "README"
     AND story_content does NOT contain any of "pytest", "test_", ".test.ts", "uv run pytest":
  story_type = "documentation"

Output: "Story type detected: {story_type} ({story_key})"

# Persist to session
Update session: story_type = {story_type}
Write sprint-status.yaml

IF story_type == "uat":
  Output: "════════════════════════════════════════════════════════"
  Output: "UAT STORY DETECTED: {story_key}"
  Output: "════════════════════════════════════════════════════════"
  Output: "This story is a UAT validation gate — not a coding story."
  Output: "Run: /epic-dev-uat {epic_num}"
  Output: "════════════════════════════════════════════════════════"
  HALT  # Cannot auto-process; user must run UAT command first

ELIF story_type == "infrastructure":
  # ─────────────────────────────────────────────────────────────
  # INFRASTRUCTURE WORKFLOW (replaces Phases 2-8)
  # ─────────────────────────────────────────────────────────────
  briefing_ref = Extract first match of "deployment-briefings/\S+" from story_content
  IF briefing_ref is empty: briefing_ref = "(see story file for briefing path)"
  story_acs = Extract all lines matching /^\*?\*?AC-/ from story_content

  Output: "════════════════════════════════════════════════════════"
  Output: "INFRASTRUCTURE STORY: {story_key}"
  Output: "════════════════════════════════════════════════════════"
  Output: "This story requires manual AWS provisioning."
  Output: "No automated implementation will run."
  Output: ""
  Output: "REQUIRED ACTIONS:"
  Output: ""
  Output: "Step 1 — Run /aws-deploy in this project"
  Output: "  Creates/updates the deployment briefing at:"
  Output: "  {briefing_ref}"
  Output: ""
  Output: "Step 2 — Switch to the AWS_setup project and run: /example-project-deploy"
  Output: ""
  Output: "Step 3 — Verify Acceptance Criteria:"
  FOR each ac in story_acs: Output: "  {ac}"
  Output: ""
  Output: "Step 4 — Return here and confirm"
  Output: "════════════════════════════════════════════════════════"

  # ALWAYS prompt user (even in --yolo)
  infra_decision = AskUserQuestion(
    question: "Have you completed the AWS provisioning steps for {story_key}?",
    header: "Infrastructure",
    options: [
      {label: "Yes, all ACs verified — mark done", description: "All acceptance criteria verified"},
      {label: "Not yet — pause here", description: "Will complete provisioning and return"},
      {label: "Partially done — continue later", description: "Incomplete, will resume later"}
    ]
  )

  IF infra_decision == "Yes, all ACs verified — mark done":
    max_retries = 3; retry_count = 0
    WHILE retry_count < max_retries:
      content = Read("{sprint_artifacts}/sprint-status.yaml")
      SEARCH for "  {story_key}: " and extract current_status
      IF current_status == "done": Output: "already done"; BREAK
      Edit(file_path="{sprint_artifacts}/sprint-status.yaml",
           old_string="  {story_key}: {current_status}",
           new_string="  {story_key}: done")
      updated = Read("{sprint_artifacts}/sprint-status.yaml")
      IF updated contains "  {story_key}: done": Output: "sprint-status.yaml updated"; BREAK
      ELSE: retry_count += 1
    END WHILE
    retry_count = 0
    WHILE retry_count < max_retries:
      content = Read("{sprint_artifacts}/stories/{story_key}.md")
      SEARCH for "Status: " and extract current_status
      IF current_status == "done": Output: "story already done"; BREAK
      Edit(file_path="{sprint_artifacts}/stories/{story_key}.md",
           old_string="Status: {current_status}",
           new_string="Status: done")
      updated = Read("{sprint_artifacts}/stories/{story_key}.md")
      IF updated contains "Status: done": Output: "Story file updated"; BREAK
      ELSE: retry_count += 1
    END WHILE
    Output: "Story {story_key} COMPLETE! Infrastructure provisioning confirmed."
    SKIP remaining phases (2-8) and proceed to next story
  ELSE:
    Output: "Infrastructure provisioning not complete. Pausing."
    HALT

# For documentation and coding stories, story_type is set. Continue to Phase 2.
# Phases 2 (validate) runs for both. Phase 3 (ATDD) is skipped for documentation.
```
