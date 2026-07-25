---
name: write-a-skill
description: Author new USER-LEVEL agent skills in ~/.claude/skills with proper structure, progressive disclosure, and bundled resources. Use when user wants to create, write, or build a new skill, or asks "how do I make a Claude Code skill for X". If the request is about benchmarking/eval-ing an existing skill's performance or optimizing trigger accuracy with evals, that is skill-creator (plugin); this skill is for authoring new user-level skills.
---

# Writing Skills

New or edited skills under `~/.claude/skills/` are harness source — author and commit them
in the source worktree `~/your-private-harness`, then deploy (`gearbox-deploy` or
`git -C ~/.claude pull --ff-only`); never hand-edit skill files directly in `~/.claude`.

## Process

1. **Gather requirements** - ask user about:
   - What task/domain does the skill cover?
   - What specific use cases should it handle?
   - Does it need executable scripts or just instructions?
   - Any reference materials to include?

2. **Draft the skill** - create:
   - SKILL.md with concise instructions
   - Additional reference files if content exceeds 500 lines
   - Utility scripts if deterministic operations needed

3. **Score against the quality rubric** - before presenting, walk the 16 criteria in
   [references/skill-quality-rubric.md](references/skill-quality-rubric.md) (TRIGGER /
   STRUCTURE / STEERING / PRUNING). Nothing ships with a criterion at 0. The same rubric
   applies when UPDATING an existing skill, not just creating one.

4. **Review with user** - present draft and ask:
   - Does this cover your use cases?
   - Anything missing or unclear?
   - Should any section be more/less detailed?

## Skill Structure

```
skill-name/
├── SKILL.md           # Main instructions (required)
├── REFERENCE.md       # Detailed docs (if needed)
├── EXAMPLES.md        # Usage examples (if needed)
└── scripts/           # Utility scripts (if needed)
    └── helper.js
```

## SKILL.md Template

Copyable starting point: [TEMPLATE.md](TEMPLATE.md).

## Description Requirements

The description is **the only thing your agent sees** when deciding which skill to load. It's surfaced in the system prompt alongside all other installed skills. Your agent reads these descriptions and picks the relevant skill based on the user's request.

**Goal**: Give your agent just enough info to know:

1. What capability this skill provides
2. When/why to trigger it (specific keywords, contexts, file types)

**Format**:

- Max 1024 chars
- Write in third person
- First sentence: what it does
- Second sentence: "Use when [specific triggers]"

**Good example**:

```
Extract text and tables from PDF files, fill forms, merge documents. Use when working with PDF files or when user mentions PDFs, forms, or document extraction.
```

**Bad example**:

```
Helps with documents.
```

The bad example gives your agent no way to distinguish this from other document skills.

## When to Add Scripts

Add utility scripts when:

- Operation is deterministic (validation, formatting)
- Same code would be generated repeatedly
- Errors need explicit handling

Scripts save tokens and improve reliability vs generated code.

## When to Split Files

Split into separate files when:

- SKILL.md approaches Anthropic's 500-line ceiling — split branch-only content into reference files well before that if a section only applies to a subset of invocations
- Content has distinct domains (finance vs sales schemas)
- Advanced features are rarely needed
- A reference file itself grows past 100 lines — give it a `## Contents` TOC

## Review Checklist

After drafting, verify:

**MUST (breaks the skill if missing)**

- [ ] Description includes triggers ("Use when...")
- [ ] SKILL.md under 500 lines (Anthropic's official ceiling); reference files over 100 lines have a TOC

**Should (guidelines)**

- [ ] SKILL.md under 100 lines
- [ ] No time-sensitive info
- [ ] Consistent terminology
- [ ] Concrete examples included
- [ ] References one level deep
- [ ] Full 16-criterion pass: [references/skill-quality-rubric.md](references/skill-quality-rubric.md) — also mandatory when updating existing skills
