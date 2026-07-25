---
name: 'code-review'
description: '[DEMOTED s08 — typed front door routes to /adversarial-review] BMAD adversarial Senior-Developer code review that finds 3-10 specific problems per story (code quality, tests, architecture, security, performance). NEVER accepts `looks good`. Kept as the BMAD-specific workflow invoked by Lane-A workers; for a standalone deep review prefer /adversarial-review.'
disable-model-invocation: true
---

<!-- ROUTING (skill-unification s08, CP-01): The canonical DEEP code reviewer is now
     /adversarial-review (dual-model Claude+Codex). This BMAD wrapper is DEMOTED as a
     typeable front door but PRESERVED as a capability: Lane-A workers
     (agents/epic-code-reviewer.md, references/epic-dev/full/phase-5-code-review.md)
     invoke it via Skill(skill='bmad-bmm-code-review') for the BMAD story-review flow.
     Do NOT type this directly for ad-hoc review — use /adversarial-review.
     Routing table: ~/.claude/SKILL-UNIFICATION-ROUTING.md. Removal decided in s09. -->

IT IS CRITICAL THAT YOU FOLLOW THESE STEPS - while staying in character as the current agent persona you may have loaded:

<steps CRITICAL="TRUE">
1. Always LOAD the FULL @{project-root}/_bmad/core/tasks/workflow.xml
2. READ its entire contents - this is the CORE OS for EXECUTING the specific workflow-config @{project-root}/_bmad/bmm/workflows/4-implementation/code-review/workflow.yaml
3. Pass the yaml path @{project-root}/_bmad/bmm/workflows/4-implementation/code-review/workflow.yaml as 'workflow-config' parameter to the workflow.xml instructions
4. Follow workflow.xml instructions EXACTLY as written to process and follow the specific workflow config and its instructions
5. Save outputs after EACH section when generating any documents from templates
</steps>
