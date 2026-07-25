---
description: "Creates a self-contained continuation prompt capturing current context, progress, and next steps for seamless handoff to a new Claude Code session. Use only when you explicitly ask to save/hand off the session: 'save session', 'next session prompt', 'continuation prompt', 'handoff prompt'."
argument-hint: "[optional: focus_area]"
---

# Generate Session Continuation Prompt

You are creating a comprehensive prompt that can be used to continue work in a new Claude Code session. Focus on what was being worked on, what was accomplished, and what needs to be done next.

## Context Capture Instructions

Create a detailed continuation prompt that includes:

### 1. Session Summary
- **Main Task/Goal**: What was the primary objective of this session?
- **Work Completed**: List the key accomplishments and changes made
- **Current Status**: Where things stand right now

### 2. Next Steps
- **Next Steps (Priority Order)**: What should be tackled first in the next session?
- **Pending Tasks**: Any unfinished items that need attention
- **Blockers/Issues**: Any problems encountered that need resolution

### 3. Important Context
- **Key Files Modified**: List the most important files that were changed
- **Critical Information**: Any warnings, gotchas, or important discoveries
- **Dependencies**: Any tools, commands, or setup requirements

### 4. Validation Commands
- **Test Commands**: Specific commands to verify the current state
- **Quality Checks**: Commands to ensure everything is working properly

## Format the Output as a Ready-to-Use Prompt

Generate the continuation prompt in this format:

```
## Continuing Work on: [Project/Task Name]

### Previous Session Summary
[Brief overview of what was being worked on and why]

### Progress Achieved
- ✅ [Completed item 1]
- ✅ [Completed item 2]
- 🔄 [In-progress item]
- ⏳ [Pending item]

### Current State
[Description of where things stand, any important context]

### Next Steps (Priority Order)
1. [Most important next task with specific details]
2. [Second priority with context]
3. [Additional tasks as needed]

### Important Files/Areas
- `path/to/important/file.py` - [Why it's important]
- `another/critical/file.md` - [What needs attention]

### Commands to Run
```bash
# Verify current state
[specific command]

# Continue work
[specific command]
```

### Notes/Warnings
- ⚠️ [Any critical warnings or gotchas]
- 💡 [Helpful tips or discoveries]

### Request
Please continue working on [specific task/goal]. Next Steps (Priority Order) above lists what to tackle first.
```

## Process the Arguments

If "$ARGUMENTS" is provided (e.g., "testing", "epic-4", "coverage"), tailor the continuation prompt to focus on that specific area.

## Make it Actionable

The generated prompt should be:
- **Self-contained**: the prompt MUST let someone understand the full context without access to this conversation
- **Specific**: Include exact file paths, command names, and clear objectives
- **Actionable**: Clear next steps that can be immediately executed
- **Focused**: Prioritize what's most important for the next session

Generate this continuation prompt now based on the current session's context and work.

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Empty continuation prompt | No recent git activity or task context | Provide a focus area: `/nextsession "working on story 16-1"` |
| Missing file references | Files changed but not staged | Stage changes first: `git add -A` |
| Context too long | Many files changed in session | The prompt auto-summarizes; trust the output |

## Examples

### Basic usage
```
/nextsession
```
Generates a continuation prompt from recent git history and task context.

### With focus area
```
/nextsession "implementing entity resolution scoring"
```
Focuses the prompt on entity resolution work specifically.