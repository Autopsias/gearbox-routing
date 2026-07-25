# Plan review loop (Vista ① — lavish in-browser annotation)

Opt-in, attended-only branch. Read this when the user asks to review the plan in the
browser, or you're at an `AWAITS_REVIEW` checkpoint and want to offer it before
falling back to the normal chat-based `--resume` review. Never runs under `--auto` or
in a cloud Routine (lavish needs a human at a browser) — the default `--resume` review
path never needs this file.

`PLAN.html` is a standalone, portable HTML artifact, and `/plan-builder`
gives every session/item card a stable `data-session-id` / `data-item-id`. That
makes it a first-class [lavish-axi](https://github.com/kunchenguid/lavish-axi)
artifact: instead of the human reading the dashboard and then re-typing "S05 should
depend on S03" into chat, they open it in the browser, click the **exact card**,
and annotate — and you receive that feedback structured and element-anchored.

This is **attended-only and opt-in** — it never runs under `--auto` or in a cloud
Routine (lavish needs a human at a browser). Use it at two moments:

1. **At an `AWAITS_REVIEW` checkpoint** (or when the user says "let me review this
   in the browser"). Before you STOP for `--resume`, offer the loop:
   ```bash
   npx -y lavish-axi <plan-dir>/PLAN.html      # opens/resumes a review session in the browser
   npx -y lavish-axi poll <plan-dir>/PLAN.html # long-poll; returns the user's annotations + queued prompts
   ```
   Leave the poll running (background it if your turn-time is bounded; re-run it if
   it's killed — queued feedback is never lost). Each returned prompt carries the
   `data-session-id` / `data-item-id` of the card it was attached to, so you know
   precisely which session/item the note targets.
2. **Fold the feedback in, then resume.** Annotations are *review intent*, not plan
   writes — you still own every mutation. Apply them the normal way: a scope/dep/
   wording change → edit `spec.json` and `/plan-builder --rebuild --preserve-state`;
   an "approve, continue" → `/plan-execute <dir> --resume`; a "redo S05 with
   this change" → re-dispatch that session with the annotation appended. Reply into
   the browser with `npx -y lavish-axi poll <…> --agent-reply "<what you changed>"`
   so the conversation stays in the artifact, then `npx -y lavish-axi end <…>` when
   the review is done.

If lavish reports `layout_warnings` on `PLAN.html`, that is the same render-time
breakage the baked-in **Vista ②** self-audit catches — fix the dashboard (or rebuild)
before involving the human. Never block the loop on lavish: if it's unavailable, fall
back to the normal chat-based `--resume` review unchanged.
