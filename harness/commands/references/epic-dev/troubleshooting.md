# Troubleshooting & Error Handling

Error handling, escalation patterns, and task management integration for `/epic-dev`.

## Contents

- [Error Handling](#error-handling)
- [Gate Escalation Patterns](#gate-escalation-patterns)
- [Confirm Next Story (unless --yolo)](#confirm-next-story-unless-yolo)
- [TASKLIST INTEGRATION (MANDATORY)](#tasklist-integration-mandatory)


---

## Error Handling

On workflow failure:
1. Display error with context
2. Ask: "Retry / Skip story / Stop"
3. Handle accordingly

---

## Gate Escalation Patterns

### Gate 2.5 Failure (Post-Implementation)

When tests fail after 3 verification iterations:

```
gate_escalation = AskUserQuestion(
  question: "Gate 2.5 failed after 3 iterations. How to proceed?",
  header: "Gate Failed",
  options: [
    {label: "Continue anyway", description: "Proceed to code review with failing tests"},
    {label: "Manual fix", description: "Pause for manual intervention"},
    {label: "Skip story", description: "Mark story as blocked"},
    {label: "Stop", description: "Save state and exit"}
  ]
)
```

### Gate 3.5 Failure (Post-Review)

When tests fail after 3 verification iterations:

```
gate_escalation = AskUserQuestion(
  question: "Gate 3.5 failed after 3 iterations. How to proceed?",
  header: "Gate Failed",
  options: [
    {label: "Continue anyway", description: "Mark story done with failing tests (risky)"},
    {label: "Revert review", description: "Revert code review fixes"},
    {label: "Manual fix", description: "Pause for manual intervention"},
    {label: "Stop", description: "Save state and exit"}
  ]
)
```

---

## Confirm Next Story (unless --yolo)

```
IF NOT --yolo AND more_stories_remaining:
  decision = AskUserQuestion(
    question="Continue to next story: {next_story_key}?",
    options=[
      {label: "Continue", description: "Process next story"},
      {label: "Stop", description: "Exit (resume later with /epic-dev {epic_num})"}
    ]
  )

  IF decision == "Stop":
    HALT
```

---

## TASKLIST INTEGRATION (MANDATORY)

### Phase Task Creation Pattern

At workflow start, create tasks for all phases with dependencies:

```
# Create phase tasks for the story workflow
TaskCreate(subject="Phase 1: Create Story", description="Create story {story_key} using PM agent (opus model)", activeForm="Creating story")
TaskCreate(subject="Phase 2: Develop Story", description="Implement story {story_key} using Dev agent (sonnet model)", activeForm="Developing story")
TaskCreate(subject="Phase 3: Review Story", description="Code review for {story_key} using Code Reviewer agent (opus model)", activeForm="Reviewing story")

# Set up dependencies
TaskUpdate(taskId="Phase 2 task ID", addBlockedBy=["Phase 1 task ID"])
TaskUpdate(taskId="Phase 3 task ID", addBlockedBy=["Phase 2 task ID"])
```

### Phase Execution Pattern

```
# When entering a phase
TaskUpdate(taskId=phase_task_id, status="in_progress")

# When phase completes successfully
TaskUpdate(taskId=phase_task_id, status="completed")

# When phase fails
TaskUpdate(taskId=phase_task_id, status="pending", metadata={"error": "reason", "iteration": N})
```

### Verification Gate Sub-Tasks

For verification gates (2.5 and 3.5), create sub-tasks:

```
# Gate 2.5: Post-implementation verification
TaskCreate(
  subject="Gate 2.5: Post-Implementation Verification",
  description="Verify all tests pass after implementation",
  activeForm="Verifying tests after implementation"
)

# Gate 3.5: Post-review verification
TaskCreate(
  subject="Gate 3.5: Post-Review Verification",
  description="Verify all tests pass after code review",
  activeForm="Verifying tests after review"
)
```

### Progress Summary Pattern

After each phase or at command end:

```
╔══════════════════════════════════════════════════════════════╗
║                    EPIC DEVELOPMENT PROGRESS                  ║
╠══════════════════════════════════════════════════════════════╣
║  Epic: {epic_num}                                             ║
║  Story: {story_key}                                           ║
╠══════════════════════════════════════════════════════════════╣
║  Phase 1: Create Story     ✅ Completed                       ║
║  Phase 2: Develop Story    🔄 In Progress                     ║
║  Phase 3: Review Story     ⏸️  Pending                         ║
╠══════════════════════════════════════════════════════════════╣
║  Verification Gates:                                          ║
║    Gate 2.5: ⏸️  Pending                                       ║
║    Gate 3.5: ⏸️  Pending                                       ║
╚══════════════════════════════════════════════════════════════╝
```
