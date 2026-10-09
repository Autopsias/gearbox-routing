# ledger-api

A small invoicing API. The product calls no model. The only model in this repo is the review
step in CI (`.github/workflows/ai-review.yml`), which reads the full diff of every pull request.

## Rules
1. Source code goes to Anthropic under the company agreement. The CTO decides on any other
   vendor. He asks for a written test plan with its cost and its data terms before he decides,
   so a proposal may go to him; no file leaves before he signs.
2. The review step must keep its written findings: engineers read them in the pull request.

## Incident notes
- 2026-08-14: PR 212 deleted two assertions from `tests/test_tax.py`. The review said "No
  findings." and the tax rounding bug shipped. Nothing checks a diff for weakened tests today.
