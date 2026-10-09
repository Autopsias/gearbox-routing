# Jev facts — snapshot of 2026-09-21

This file is a baseline for comparison and a fallback for when the docs are unreachable.
It is not the source of truth. The live docs win. When you quote a fact from this file in a
report, write "snapshot 2026-09-21, not re-checked" beside it.

Docs index (the only fixed URL): `https://docs.typesafe.ai/llms.txt`
Page list and page hashes of the same date: `docs-index-snapshot.tsv` (read by `scripts/jev_docs.py`).

## Facts from the vendor docs

| Fact | Value on 2026-09-21 | Source page |
|---|---|---|
| Vendor, product | TypeSafe AI. "System One" models. Jev is the first. | `/introduction` |
| Models | `jev-1.13.0`. Aliases `jev-latest` and `jev-preview` both point to it. An alias moves when a release ships. | `/models` |
| Endpoint | `POST https://api.typesafe.ai/v1/systemone`. `GET /v1/models` lists the aliases. | `/api`, `/models` |
| Question types | **Choice**: one of up to 255 options; returns the pick, a probability per option, and `confidence`. **Score**: 2 to 10 ordered levels; returns a probability-weighted score that can land between levels, a probability per level, and `confidence`. **Noul**: a yes/no question; returns the probability of yes, with no `confidence`. | `/primitives`, `/primitives/*`, `/confidence` |
| Questions per request | Many. Jev reads the `state` once and answers every question in parallel and independently. | `/models`, `/patterns/fan-out` |
| Input limits | 64k tokens for one request (state plus all questions). 32k tokens for the state plus the longest question. | `/models` |
| Input kinds | Text only: a string, a JSON object, or an array of text values. No image, audio or video. | `/models` |
| Text output | None. Jev does not write text. | `/model-jaggedness/jev-1.13` |
| Price | $0.042 per million input tokens ($42 per billion). Output is free. | `/models` |
| Rate limits | 250,000 tokens per second and 1,200 requests per minute. The page warns that they change with no notice. | `/models` |
| Languages | English is the primary training language. Other languages, CJK scripts included, are "handled but not equally well". The page says to test on your own content. | `/models` |
| Customization | No fine-tuning and no adapters. All accounts share the same weights. Domain rules go in `instructions`, `criteria` and the `state`. | `/models` |
| Data terms | Customer requests are not used for training. A Data Processing Agreement exists. Zero data retention is for enterprise customers only. | `/models`, `/legal` |
| Weak points of jev-1.13 | Literal reading. Counting and numbers. Date and time comparison. Indirection. A large state full of irrelevant detail. Adversarial content. Contradictory instructions and criteria. No structural consistency between question types (a threshold tuned on a Noul does not carry to a Choice). Text generation. | `/model-jaggedness/jev-1.13` |
| Python SDK | `typesafe-sdk` 0.7.1 of 2026-09-21: a bug fix, and examples for AI gateways. 0.7.0 of 2026-09-18 and 0.6.0 of 2026-09-15 each broke compatibility. | `/sdk/python/changelog` |
| Access routes | The direct API. Gateways: OpenRouter lists `typesafe/jev-1.13` at `POST https://openrouter.ai/api/alpha/decisions` (probed with no key: 401; "alpha" is OpenRouter's word). A third-party guide names Vercel AI Gateway (`typesafe-ai/jev`); not probed. The docs index names no gateway. | OpenRouter's site; `/sdk/python/changelog` |
| JavaScript SDK | `@typesafe-ai/sdk` 0.6.0 of 2026-09-15, with a breaking change. First public release 0.5.7. | `/sdk/javascript/changelog` |
| Vendor's agent skill | A skill for building with Jev (how to write questions and state). Use it after a fit verdict, in the build step. Do not copy it here. | `/agent-skill` |

## Patterns and cookbooks in the index on 2026-09-21

Each entry is a shape of site to look for. `docs-index-snapshot.tsv` holds the full list, and
the script reports every entry added after this date.

Patterns: speculative fan-out, confidence-gated routing, composite scoring, intent routing.

Cookbooks: self-consistency (nouls, choices), parallel questions, re-ranking, line-by-line
search, structure recovery, function calling, skill suggestion, knowledge graph entity
alignment, classifying RAG passages, double-checking citations, guardrails for LLMs, SDE
cascade (structured data extraction: cheap extract, verify, escalate), date extraction,
pre-parsed value extraction, hierarchical classification, autoresearch feature discovery,
classification using confidence.

## Evidence notes (dated; none of them re-run by this skill's author)

- The re-ranking cookbook compares against keyword search (BM25) only. It has no comparison
  with a cross-encoder reranker.
- The entity alignment cookbook ships an answer key and reports how many pairs went to each
  of its three routes. It reports no accuracy against the key.
- The vendor's headline evaluation measures agreement with the averaged answers of two large
  models. It does not measure agreement with human labels. No calibration curve was public.
- Third-party tests found covered content moderation and spam only. None covered
  entity matching, fact contradiction or answer checking.
- A third-party Jev guide that the owner supplied (author "Nate", no URL on record)
  reports small tests on items that its author wrote to be hard. Treat each as a lead.
  - Support routing, 12 messages: keyword rules 4 right, Jev 11 right. The miss came back at 0.53
    confidence, every hit at 0.75 or more.
  - A 1-to-10 Score ordered headlines sensibly, but gave real published headlines 5 to 7.
  - Two options that overlap ("16GB", "16 GB") split the probability.
  - It cites two public runs by other people: a tax-document classifier (N. Saxena, about 34
    times lower cost) and eight questions over 3,282 posts (I. Nuttall). Neither was read here.
