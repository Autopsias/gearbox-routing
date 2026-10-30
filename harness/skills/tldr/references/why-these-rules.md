# Why the tldr rules are what they are

Sourced 2026-10-17 while building the skill, not invented. Each rule in
`SKILL.md` and the two references traces to one of these.

## The brief

- Decision first, one page, three findings with their implication, a made
  recommendation, an ask with a date — the shared shape across practitioner
  guides and the open skills that encode them:
  [skills-for-humanity executive summary](https://github.com/human-avatar/skills-for-humanity/blob/main/skills/s4h-writing-executive-summary/SKILL.md)
  (four failures: summarising process, findings without implications, false
  balance, passive recommendation),
  [FinchContext BLUF one-pager](https://finchcontext.com/ai-artifacts/industry-solutions/skills/build-executive-summary.html)
  (bottom line, 3–5 findings with so-whats, owners, dated asks, risks),
  [AskSmarter framework](https://www.asksmarter.ai/resources/analysis-research/executive-summary-framework)
  (250–300 words; "if it is longer than one page it is not an executive
  summary").
- "What if nothing is done" and at most three options with one trade-off each
  come from the harness's briefing format in `~/.claude/CLAUDE.md`
  (Communication style — every decision gets the briefing format).

## Extraction before writing

- A one-shot summary "keeps the topic and drops the point", because the
  figures, exceptions and conditions appear once while the topic appears on
  every page — so extraction must be a separate step from compression
  ([Multigrid, Summarising a long report](https://multigrid.ai/learn/summarise-long-documents)).
- Select the supporting segments first, then generate conditioned on them, so
  the segments become the output's attributions — "Attribute First, then
  Generate" (Slobodkin et al., ACL 2024,
  [doi:10.18653/v1/2024.acl-long.182](https://doi.org/10.18653/v1/2024.acl-long.182)).
- Claim-anchored provenance for multi-document summaries raises attribution
  accuracy and cuts human verification time per claim about threefold
  ([CAMS, arXiv 2606.23989](https://arxiv.org/html/2606.23989v4)).

## Many documents

- Hallucination in multi-document summarization grows with the number of
  input documents; most errors are off-topic or redundant padding and overly
  generic claims (Belém et al., "From Single to Multi",
  [arXiv 2410.13961](https://arxiv.org/html/2410.13961)).
- Chunk, summarize each chunk, then write a meta-summary — Anthropic's
  summarization guide, which notes the pattern also "captures additional
  important details" a single pass misses
  ([Claude Platform Docs, Legal summarization](https://platform.claude.com/docs/en/about-claude/use-case-guides/legal-summarization#improve-performance)).
- Recursive merging of intermediate summaries can amplify hallucination;
  keeping source context or citations in the merge mitigates it
  ([Context-Aware Hierarchical Merging, arXiv 2502.00977](https://arxiv.org/html/2502.00977)).
  The ledger merge reads rows with locators for this reason.

## Spine, budget, and the gate that fails

- Write three spines over the same facts and choose one; enforce a word
  budget as a build error, not advice — version 1 of that skill shipped
  "2,086 words and one chart" with every paragraph individually defensible
  ([remybroun/infographic](https://github.com/remybroun/infographic)).
- A planner → generator → evaluator loop with a human checkpoint for visual
  feel is the common shape of the better community explainer skills
  ([greeun/html-explainer-harness](https://github.com/greeun/html-explainer-harness)).
- All visual rules — audience levers, figure grammar, SVG label floors, the
  Figure QA gate, the two-pass review — are owned by `eli5` and sourced in its
  own `references/why-these-rules.md`. tldr does not restate them.

## Video route

- Judge a video tool by its correction loop: "the best AI explainer video
  generator is not the one that produces the fastest first render. It is the
  one that gets you to an accurate, editable final video with the least
  hidden repair work" ([diyai.io, 2026-08](https://diyai.io/ai-tools/video-generation/ai-explainer-video-generator/)).
  video-creator exposes the script, the cues and a lint + QA loop; that is why
  it is the route.
- NotebookLM (renamed Gemini Notebook, 2026-07) Video Overviews: fixed style,
  no branding, script by prompt only, three a day free, generation can exceed
  30 minutes
  ([Scrimba comparison, 2026-08](https://scrimba.com/articles/ai-explainer-video-makers-compared-2026/),
  [DistilBook alternatives, 2026-09](https://distilbook.com/blog/notebooklm-video-overviews-alternatives)).
