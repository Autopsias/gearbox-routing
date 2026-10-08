# Applying approved changes — per-finding-type recipes

Phase 6 of `/improve` groups the approved findings by file and edits them. Most
findings are a plain edit to an existing file and need nothing from this page. The
finding types below each need a specific sequence, because each one touches a second
file (an index, a settings file, the source it was moved out of) that is easy to
leave inconsistent.

Follow the recipe for each approved finding's type. Everything here is Phase 6
mechanics only — the gates that decided a finding was presentable at all live in
`evidence-ledger.md`.

Audit mode (`/improve audit`) runs the same Phase 6 and the same recipes — no separate
apply path. `relevance-audit.md` Step 6 maps each audit proposal to the recipe that
applies it; that table is the authority on which recipe an audit proposal uses, so add
a recipe there when you add one here.

## Hook conversions

- Read the target settings.json file (project or global, per the user's scope choice).
- Add the hook configuration under the appropriate event key (PreToolUse, PostToolUse, etc.).
- If the `hooks` key doesn't exist yet, create it.
- **Preserve all existing hooks — append, never replace.**

## Rule extractions

- Create the `.claude/rules/` directory if it doesn't exist.
- Write the extracted rule to a new `.md` file with a path-scoping glob in frontmatter.
- Remove the extracted section from CLAUDE.md.
- If the removed text cited a rationale anchor — `(why: docs/…#anchor)` — carry that
  citation into the new rule file. The rationale section stays where it is; only the
  rule that points at it moves. A rule file that drops the citation orphans the section
  exactly as a deletion would.
- Leave nothing behind — no pointer line, no stub. (Skill extractions below DO leave a
  pointer; a demotion does not.)

## Rule deletions

- Delete the bullet (and any sub-bullets under it) from CLAUDE.md.
- If the bullet cited a rationale anchor — `(why: docs/…#anchor)` — delete the section
  it pointed at too, in the same change. An orphaned rationale section is the same
  defect as a dangling index entry, pointing the other way.
- Nothing else moves. A deletion that needs the text kept somewhere is a demotion, not
  a deletion.

## Rule demotions into a skill

- Add the trigger phrasing to the target skill's `description` frontmatter (the
  **Skill Description rewrites** recipe below), and the rule text to that skill's body
  in its most relevant section.
- Remove the bullet from CLAUDE.md. The rule text now lives in the skill, so leaving a
  copy in CLAUDE.md is the drift this recipe exists to avoid.
- Keep the rationale section and carry its `(why: docs/…#anchor)` citation into the
  skill, exactly as in **Rule extractions**. The rule survives, so its rationale has an
  owner; deleting it here would destroy the reasoning and dangle the copied citation.
  Only **Rule deletions** removes a rationale section, because only there does the rule
  itself stop existing.

## Memory deletions

- Delete the memory `.md` file.
- **Remove its line from the MEMORY.md index in the same directory** — the index is a
  list of `- [Title](file.md) — summary` entries and a deleted file leaves a dangling
  pointer that nothing else will catch. If that directory has no MEMORY.md, there is
  nothing to update; say so rather than assuming.
- Both halves land together, or neither does.

## Memory file merges

- Combine the content of the overlapping memory files into one.
- Update the frontmatter (name, description) to reflect the merged scope.
- Delete the duplicate file.
- Update the MEMORY.md index to remove the deleted entry and update the surviving one.

## Skill extractions

- Create the new skill `.md` file with proper frontmatter (name, description).
- Move the procedural content from CLAUDE.md into the skill.
- Replace the CLAUDE.md section with a one-line reference: "See /skill-name for details".

## Feedback-type findings

A feedback finding is applied to its target file **and** saved as a memory file:

- File: `feedback_[topic].md` in the project's memory directory.
- Frontmatter: name, description, `type: feedback`.
- Content: the rule + **Why:** + **How to apply:**.

Update the MEMORY.md index whenever a new memory file is created, by any recipe here.

## Content Misplacement findings

- Remove the content from the source file.
- Add it to the destination file in the appropriate section.
- Moving TO a skill file: place it in the most relevant section and adjust formatting
  to match that skill's style.
- Moving FROM a skill to CLAUDE.md: place it in the most relevant existing section.
- **Preserve meaning** — adjust formatting and context references, nothing else.

## Skill Description rewrites

- Edit the `description` field in the skill's frontmatter.
- Preserve the original intent; improve clarity and activation keywords.
