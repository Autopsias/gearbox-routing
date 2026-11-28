# /rust-migration-review — external evidence

## Contents

- [How to use this file](#how-to-use-this-file)
- [Durable lessons](#durable-lessons)
- [Dated numbers](#dated-numbers)
- [Live research queries](#live-research-queries)

---

## How to use this file

The sources below were read through Exa on 2026-11-28. The lessons are durable: they
describe mechanisms, not versions. The numbers are dated: quote them with their source and
date, and re-check any number older than about a year with the live queries before a
decision rests on it. Cite the URL whenever a lesson changes a verdict.

## Durable lessons

1. **Profile first; Rust helps CPU- and memory-bound code only.** If the profile top is a
   network call, a database query, or a library already written in C or Rust, a port will
   not help that path. Parsing, tokenization, compression, numeric loops and repeated
   allocation are credible targets. ([Rust for Pythonistas decision guide, 2026-07-26](https://www.rustpython.com/guides/python-to-rust-performance))
2. **The boundary has a price.** Every crossing converts data. Anthropic's first, partial
   port (output parsing called from Python) cut API CPU by about 8–10%, but each token then
   crossed between Python and Rust three times, and the team judged that the gain did not
   justify the complexity. Prisma removed its Rust query engine partly because data was
   serialized from JavaScript to Rust and back. ([Anthropic, 2026-11-25](https://www.anthropic.com/research/migrating-api-to-rust);
   [Prisma, 2025-01-30](https://www.prisma.io/blog/from-rust-to-typescript-a-new-chapter-for-prisma-orm))
3. **Port one component at a time, never one function or one file at a time.** The Big Book
   of Rust Interop calls the component route likely to succeed and the function-, data
   structure- or file-level route "doomed to failure in nearly all circumstances". C is the
   easiest host, C++ depends on its style, and managed languages depend on their binding
   tools. Build integration is easiest when the Rust part is a leaf of the dependency graph.
   ([Big Book of Rust Interop: estimating](https://nrc.github.io/big-book-ffi/estimating.html))
4. **Pick a unit with a large footprint and simple behavior.** Grab chose a service that
   served tens of thousands of requests per second but had narrow functionality, treated it
   as a black box behind its gRPC contract, and rewrote it from the contract rather than line
   by line. A rewrite only "for Rust" was not a business case, and one proficient engineer
   was named as a sustainability risk. ([Grab, 2025-06-20](https://engineering.grab.com/counter-service-how-we-rewrote-it-in-rust))
5. **When an AI agent drafts the port, verification is the main cost.** Anthropic's first
   draft of the core request path took about a week; proving parity took production traffic
   as the oracle, because about 700 integration tests did not cover all behavior and the
   Python version kept changing. Bun's port could reuse its TypeScript test suite to judge
   both versions. ([Anthropic, 2026-11-25](https://www.anthropic.com/research/migrating-api-to-rust))
6. **Machine translation still needs repair loops.** On CRUST-Bench (100 C repositories with
   safe-Rust interfaces and tests), the best frontier models of 2025 passed 13–22% of tasks
   in one shot and 32–48% with a repair loop. ([CRUST-Bench, 2025](https://arxiv.org/abs/2504.15254))
7. **For memory safety, new code matters most.** Vulnerabilities concentrate in new or
   recently changed code, so writing new code in a memory-safe language cuts the rate fast,
   even when old C/C++ stays. Android's memory-safety share of vulnerabilities fell from
   about 70% to 24% in 2024 and below 20% in 2025. ([ACM Queue, 2025](https://queue.acm.org/doi/10.1145/3773096);
   [Google, 2025-11-13](https://blog.google/security/rust-in-android-move-fast-fix-things/))
8. **Rewrites add new bugs and can stall in the last 5%.** Some rewrites are faster only
   because they are fresh rewrites. The curl/hyper integration reached about 95% and was then
   abandoned. Binary size, platform support and interop are real costs.
   ([JetBrains, 2026-08-10](https://blog.jetbrains.com/rust/2026/08/10/rewriting-in-rust/))
9. **Deployment can decide the case.** Prisma needed a binary per operating system and
   OpenSSL version, and large Rust binaries did not fit some serverless and edge runtimes.
   ([Prisma, 2025-01-30](https://www.prisma.io/blog/from-rust-to-typescript-a-new-chapter-for-prisma-orm))
10. **The contributor pool cuts both ways.** fish moved from C++ to Rust partly because few
    people contributed C++ (17 people with 10 or more commits in 11 years). Prisma moved away
    from Rust partly because contributors needed both Rust and TypeScript.
    ([fish, 2024-12-28](https://fishshell.com/blog/rustport/); [Prisma, 2025-01-30](https://www.prisma.io/blog/from-rust-to-typescript-a-new-chapter-for-prisma-orm))
11. **Code structure matters when two codebases live side by side.** The TypeScript team
    chose Go for its native compiler port because it keeps the existing code structure, so
    changes port easily between the old and new codebases during a long overlap. Languages
    that force a rethink of memory, mutation and data structures suit a ground-up rewrite
    better. ([microsoft/typescript-go discussion 411, 2025-03-05](https://github.com/microsoft/typescript-go/discussions/411))
12. **Start small and representative, then run old and new in parallel.** OTTO started with
    small, well-defined, non-critical serverless functions, then built endpoints in a Rust
    service that ran beside the old one. ([OTTO, 2026-08-17](https://www.otto.de/jobs/en/blogs/techblog/rust-migration-lambdas-microservices/))
13. **Plan the team before the code.** Know why you want Rust, find a champion, watch the bus
    factor, set a policy for crates and async, budget learning time, and grow talent from
    existing teams, because experienced Rust hires are scarce and expensive.
    ([corrode, 2024-06-14](https://corrode.dev/blog/successful-rust-business-adoption-checklist/))

## Dated numbers

| Claim | Number | Source | Date |
|---|---|---|---|
| Anthropic Messages API, Python → Rust | About 70% less CPU per request; about 20% lower median time to first token; no downtime, no feature freeze | [anthropic.com](https://www.anthropic.com/research/migrating-api-to-rust) | 2026-11-25 |
| Grab Counter Service, Go → Rust | About 20% of the original resources ("70% infrastructure savings"); p99 latency similar or slightly worse | [engineering.grab.com](https://engineering.grab.com/counter-service-how-we-rewrote-it-in-rust) | 2025-06-20 |
| Android, Rust vs C/C++ | About 1000x lower memory-safety vulnerability density; 4x lower rollback rate; 25% less time in code review | [blog.google](https://blog.google/security/rust-in-android-move-fast-fix-things/) | 2025-11-13 |
| Google developer ramp-up (1,000+ developers, 2022 data) | Over 2/3 confident to contribute within 2 months; 1/3 as productive as in their prior language within 2 months, over 50% within 4 months | [opensource.googleblog.com](https://opensource.googleblog.com/2023/06/rust-fact-vs-fiction-5-insights-from-googles-rust-journey-2022.html) | 2023-06-27 |
| Prisma, Rust engine → TypeScript | Up to 3.4x faster queries without cross-language serialization; bundle from about 14 MB to 1.6 MB | [prisma.io](https://www.prisma.io/blog/from-rust-to-typescript-a-new-chapter-for-prisma-orm) | 2025-01-30 |
| fish shell, C++ → Rust | About two years from the port pull request to the 4.0 beta with no C++ left | [fishshell.com](https://fishshell.com/blog/rustport/) | 2024-12-28 |
| CRUST-Bench, C → safe Rust | 13–22% of 100 repositories one-shot; 32–48% with a repair loop | [arxiv.org](https://arxiv.org/abs/2504.15254) | 2025 |
| CISA product security bad practices (critical infrastructure) | New product lines in C/C++ where a memory-safe language is available: "dangerous"; existing products: publish a memory-safety roadmap by the end of 2025 | [cisa.gov](https://www.cisa.gov/resources-tools/resources/product-security-bad-practices) | 2025-01 (v2.0) |

## Live research queries

Run these with Exa `web_search_exa` (natural-language query plus an objective), then read
the best pages with `web_fetch_exa`. Replace the angle-bracket parts.

1. **Crate for a dependency.** Query: "Rust crate equivalent of <package> (<ecosystem>)".
   Objective: rank lib.rs and crates.io pages; pull last release date, downloads and
   maintainer count.
2. **Binding tool status.** Query: "<tool, e.g. PyO3 / napi-rs / cxx> limitations and
   current release". Objective: official docs or changelog; pull the current version,
   supported host versions, and known limits.
3. **Case studies for this pair.** Query: "engineering blog: migrating <workload> from
   <language> to Rust, measured results". Objective: first-hand posts with before and after
   numbers, what was ported first, and what went wrong.
4. **Counter-examples for this pair.** Query: "team removed Rust or reversed a Rust migration
   from <language>, reasons". Objective: first-hand posts; pull the stated reasons and costs.
5. **Refresh a dated number.** Query: "<claim from the table above>, latest data".
   Objective: the primary source (vendor or government page); pull the newest figure and
   its date.
