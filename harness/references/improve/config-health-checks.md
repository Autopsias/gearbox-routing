# Config health & consolidation checks — Phase 4c

`/improve` Phase 4c runs the CLAUDE.md **budget** measurement (and its zero-sum rule)
inline in the main command, because that one gates what other findings may propose.
Every remaining config-health sub-check lives here. Run **all** of them, against BOTH
project-level and global-level configs from the Discovery Agent's config map.

Each check produces ordinary findings: they go through Phase 5's evidence gates like
any other, and gate 1 accepts the config text itself (`path:line`) as their excerpt
because these are file-derived, not conversation-derived.

## Contents
- [Memory Consolidation](#memory-consolidation)
- [Rule Extraction](#rule-extraction)
- [Skill Extraction](#skill-extraction)
- [Skill Consolidation](#skill-consolidation)
- [Cross-Skill Consistency](#cross-skill-consistency)
- [Content Placement Audit](#content-placement-audit)
- [Skill Budget Monitoring](#skill-budget-monitoring)
- [Skill Description Quality Audit](#skill-description-quality-audit)
- [CLAUDE.md Structural Validation](#claudemd-structural-validation)
- [Cross-Level Analysis](#cross-level-analysis)

## Memory Consolidation
- Read all memory files and group by topic similarity
- Flag duplicates or heavily overlapping files (e.g., two feedback files covering the same rule) — recommend merging
- Flag memory files with stale references: files, functions, or features mentioned in the memory that no longer exist in the codebase
- Flag memory files with relative dates that were never converted to absolute

## Rule Extraction
- Scan CLAUDE.md for file-type-specific or path-specific instructions (patterns like "for *.test.ts files", "in API routes", "when editing components/", etc.)
- Suggest migrating these to `.claude/rules/` with path-scoping globs in frontmatter
- Rules only load when Claude touches matching files, reducing always-on context cost

## Skill Extraction
- Flag CLAUDE.md sections longer than ~20 lines that read like procedures or multi-step workflows
- Suggest converting to skills (on-demand loading: ~100 tokens metadata cost vs full content always in context)
- Good candidates: step-by-step processes, detailed how-to instructions, decision trees

## Skill Consolidation
- Check ALL skills at both project (`<project>/.claude/commands/`, `<project>/.claude/skills/`) and global (`~/.claude/commands/`) levels
- Flag overlapping skills: two skills that cover similar functionality or could be merged
- Flag oversized skills: skills that have grown beyond their original purpose
- Flag stale skills: skills referencing files, APIs, or patterns that no longer exist in the codebase
- Flag shadowed skills: a project skill with the same name as a global skill (intentional override or accidental?)

## Cross-Skill Consistency

After reading all skill files from the Discovery Agent, review them holistically for contradictions. Check these 5 patterns:

- **Conflicting directives:** One skill says "ALWAYS do X" while another says "NEVER do X" or "avoid X"
- **Overlapping trigger conditions:** Two skills with descriptions claiming the same activation context (e.g., both say "Use when debugging")
- **Inconsistent terminology:** Skills using different terms for the same concept (e.g., "sub-agent" vs "task" vs "background agent")
- **Process conflicts:** Skills prescribing different procedures for the same scenario (e.g., one says "ask before acting" while another says "act then verify")
- **Skills vs CLAUDE.md:** CLAUDE.md establishes a rule but a skill contradicts or overrides it without acknowledgment

Present contradictions as Critical-tier findings with both sources cited (file paths + relevant lines).

## Content Placement Audit

Check 5 directions for misplaced content:

**Direction 1: CLAUDE.md → Skill Files**
- Scan CLAUDE.md for sections that reference specific skills by name
- If a section only applies when a specific skill is active, flag it: "This guidance only matters during [skill] — consider moving it into the skill file itself"
- **Secondary detection:** also flag sections describing procedures only relevant during a specific workflow type (brainstorming, reviewing, debugging, planning) even without a skill name mention — these are implicitly skill-specific

**Direction 2: Memory → Skills**
- Scan memory files for entries with type `feedback` or `project` that contain multi-step procedures, decision trees, or workflow descriptions
- If a memory file reads more like a how-to than a fact, flag it: "This memory contains procedural knowledge — consider converting to a skill"

**Direction 3: Skill Files → CLAUDE.md**
- Scan each skill for universal behavioral rules — rules about general Claude behavior across sessions/tasks
- **Only flag rules that apply universally**, NOT rules about what to do within the skill's own procedure. Example: "ALWAYS present findings one at a time" is skill-internal (don't flag), while "ALWAYS use AskUserQuestion for decisions" is universal (flag)
- If found: "This rule in [skill] applies universally — consider promoting to CLAUDE.md"

**Direction 4: CLAUDE.md → Memory**
- Scan CLAUDE.md for factual/reference content that isn't a behavioral instruction (project facts, external system pointers, user preferences that don't change behavior)
- These are better as memory entries — they persist across sessions but don't consume always-on instruction budget

**Direction 5: Between Skills**
- If two skills share identical or near-identical sections (copy-pasted patterns), flag for extraction into a shared reference or CLAUDE.md rule

## Skill Budget Monitoring

Calculate total character count across ALL skill `description` fields (from frontmatter of all skill/command files at both project and global levels).

Community research suggests a ~16K character budget for skill metadata — skills beyond this may be silently invisible (cannot be discovered or invoked). **Note: this figure is community-discovered, not officially documented by Anthropic, and may change.**

- **Warning:** >12K chars (~75% of estimated budget)
- **Elevated:** >15K chars (~94% of estimated budget)
- If over warning: list all skills sorted by description length, suggest compression targets (ideal: 130 chars per description)
- If over elevated: identify which skills are likely invisible and suggest investigation
- **Always present as Maintenance-tier** regardless of threshold — this is informational monitoring based on unofficial data. Only escalate to Critical if the user reports actually experiencing invisible skills.

## Skill Description Quality Audit

For each skill, check its description against activation best practices:

- **Third person?** ("Processes files" not "I process files" or "You should use this to...")
- **Trigger conditions?** ("Use when..." or "Triggers when...")
- **Appropriate length?** (130-263 chars ideal range)
- **Specific enough?** (has concrete keywords, not vague "helps with things")

Research showed activation rates range from 20% (bad description) to 90% (optimized). Present as Maintenance-tier findings with suggested rewrites.

## CLAUDE.md Structural Validation

Check if CLAUDE.md sections follow the WHAT/WHY/HOW framework:
- **WHAT**: Project context, tech stack, repo structure
- **WHY**: Principles, conventions, anti-patterns
- **HOW**: Workflows, commands, operational procedures

Flag sections that mix categories (a HOW section buried in WHY context). Light-touch — suggest reorganization only if structure is genuinely unclear, not for stylistic preference. Present as Maintenance-tier findings.

## Cross-Level Analysis
- Check for duplicated rules between project and global CLAUDE.md
- Flag contradictory instructions across levels (project rule says X, global rule says Y)
- Flag memory files that belong at the other level (e.g., project-specific feedback stored in global memory, or cross-project feedback stored in project memory)
- Flag skills that exist at both levels with different content
