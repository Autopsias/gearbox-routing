# Story & Epic Completion

## STEP 6: Story Completion - MANDATORY STATUS UPDATES

**CRITICAL: Execute these steps DIRECTLY using Edit tool (this is the exception to "no Edit" rule).**

After quality gate passes (PASS or WAIVED):

### 6.1 Update sprint-status.yaml

```
max_retries = 3
retry_count = 0

WHILE retry_count < max_retries:

  # 1. Read current file to get ACTUAL content
  content = Read("{sprint_artifacts}/sprint-status.yaml")

  # 2. Find current status - look for "  {story_key}: <status>"
  SEARCH for line matching "  {story_key}: " and extract current_status

  IF current_status == "done":
    Output: "sprint-status.yaml already shows 'done'"
    BREAK

  # 3. Edit with EXACT strings (preserve 2-space indent)
  Edit(
    file_path="{sprint_artifacts}/sprint-status.yaml",
    old_string="  {story_key}: {current_status}",
    new_string="  {story_key}: done"
  )

  # 4. Verify by re-reading
  updated = Read("{sprint_artifacts}/sprint-status.yaml")
  IF updated contains "  {story_key}: done":
    Output: "sprint-status.yaml updated successfully"
    BREAK
  ELSE:
    retry_count += 1
    Output: "Verification failed, retry {retry_count}/{max_retries}"

END WHILE

IF retry_count >= max_retries:
  Output: "FAILED to update sprint-status.yaml after 3 retries"
  HALT with "Manual intervention required for status update"
```

### 6.2 Update story file Status

```
max_retries = 3
retry_count = 0

WHILE retry_count < max_retries:

  # 1. Read story file
  content = Read("{sprint_artifacts}/stories/{story_key}.md")

  # 2. Find current Status line (e.g., "Status: in_progress")
  SEARCH for line starting with "Status: " and extract current_status

  IF current_status == "done":
    Output: "Story file already shows 'done'"
    BREAK

  # 3. Edit with EXACT strings
  Edit(
    file_path="{sprint_artifacts}/stories/{story_key}.md",
    old_string="Status: {current_status}",
    new_string="Status: done"
  )

  # 4. Verify by re-reading
  updated = Read("{sprint_artifacts}/stories/{story_key}.md")
  IF updated contains "Status: done":
    Output: "Story file status updated successfully"
    BREAK
  ELSE:
    retry_count += 1
    Output: "Verification failed, retry {retry_count}/{max_retries}"

END WHILE

IF retry_count >= max_retries:
  Output: "FAILED to update story file status after 3 retries"
  HALT with "Manual intervention required for status update"
```

### 6.3 Clear session state

Clear epic_dev_session (or update for next story)

```
Output:
═══════════════════════════════════════════════════════════════════════════
STORY COMPLETE: {story_key}
═══════════════════════════════════════════════════════════════════════════
Phases completed: 8/8
Quality Gate: {gate_decision}
Coverage: P0={p0_coverage}%, P1={p1_coverage}%, Overall={overall_coverage}%
Status updated: sprint-status.yaml and story file -> done
═══════════════════════════════════════════════════════════════════════════

IF NOT --yolo AND more_stories_remaining:
  next_decision = AskUserQuestion(
    question: "Continue to next story: {next_story_key}?",
    header: "Next Story",
    options: [
      {label: "Continue", description: "Process next story with full 8-phase workflow"},
      {label: "Stop", description: "Exit (resume later with /epic-dev-full {epic_num} --resume)"}
    ]
  )

  IF next_decision == "Stop":
    HALT
```

---

## STEP 7: Epic Completion - MANDATORY EPIC STATUS UPDATE

When all stories in the epic are done (no more pending stories):

**CRITICAL: Execute these steps DIRECTLY using Edit tool.**

### 7.0 UAT Gate Check (MANDATORY)

```
# UAT must pass before epic can be marked done
content = Read("{sprint_artifacts}/sprint-status.yaml")

IF content contains "epic_uat_session:" with epic == {epic_num}:
  # Extract gate_decision from epic_uat_session block
  IF gate_decision in ["PASS", "WAIVED"]:
    Output: "UAT gate: {gate_decision}"
    # Proceed to epic status update below
  ELSE:
    Output: "HALT: UAT not passed (current: {gate_decision}). Run: /epic-dev-uat {epic_num}"
    HALT
ELSE:
  Output: "HALT: UAT required before epic completion. Run: /epic-dev-uat {epic_num}"
  HALT
```

### 7.1 Update epic status in sprint-status.yaml

```
max_retries = 3
retry_count = 0

WHILE retry_count < max_retries:

  # 1. Read current file
  content = Read("{sprint_artifacts}/sprint-status.yaml")

  # 2. Find current epic status - look for "  epic-{epic_num}: <status>"
  SEARCH for line matching "  epic-{epic_num}: " and extract current_status

  IF current_status == "done":
    Output: "Epic status already shows 'done'"
    BREAK

  # 3. Edit with EXACT strings
  Edit(
    file_path="{sprint_artifacts}/sprint-status.yaml",
    old_string="  epic-{epic_num}: {current_status}",
    new_string="  epic-{epic_num}: done"
  )

  # 4. Verify
  updated = Read("{sprint_artifacts}/sprint-status.yaml")
  IF updated contains "  epic-{epic_num}: done":
    Output: "Epic status updated successfully"
    BREAK
  ELSE:
    retry_count += 1
    Output: "Verification failed, retry {retry_count}/{max_retries}"

END WHILE
```

### 7.2 Update epic retrospective status (if exists)

```
# Look for retrospective entry
content = Read("{sprint_artifacts}/sprint-status.yaml")

IF content contains "epic-{epic_num}-retrospective:":
  SEARCH for "  epic-{epic_num}-retrospective: " and extract current_status

  IF current_status in ["optional", "backlog"]:
    Edit(
      file_path="{sprint_artifacts}/sprint-status.yaml",
      old_string="  epic-{epic_num}-retrospective: {current_status}",
      new_string="  epic-{epic_num}-retrospective: pending"
    )
    Output: "Retrospective status set to 'pending'"
```

### 7.3 Verify all story statuses

```
content = Read("{sprint_artifacts}/sprint-status.yaml")

# Count stories for this epic that are NOT done
SEARCH for all lines matching "  {epic_num}-*: "
FOR each match:
  IF status != "done":
    Output: "Story {key} is still '{status}' - epic cannot be complete"
    HALT

Output: "All {count} stories verified as 'done'"
```

```
Output:
════════════════════════════════════════════════════════════════════════════════
EPIC {epic_num} COMPLETE!
════════════════════════════════════════════════════════════════════════════════
Stories completed: {count}
Total phases executed: {count * 8}
All quality gates: {summary}
Epic status: done
Retrospective status: pending

Next steps:
- Retrospective: /bmad-bmm-retrospective
- Next epic: /epic-dev-full {next_epic_num}
════════════════════════════════════════════════════════════════════════════════
```
