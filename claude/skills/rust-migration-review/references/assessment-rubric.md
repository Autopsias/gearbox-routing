# /rust-migration-review — assessment rubric

## Contents

- [Scales and evidence tiers](#scales-and-evidence-tiers)
- [Benefit](#benefit)
- [Feasibility](#feasibility)
- [Risk](#risk)
- [Effort model](#effort-model)
- [Verdict rules](#verdict-rules)
- [Whole-repository rewrite test](#whole-repository-rewrite-test)
- [Cheaper alternatives](#cheaper-alternatives)
- [Integration patterns by language pair](#integration-patterns-by-language-pair)
- [Parity strategies](#parity-strategies)

---

## Scales and evidence tiers

Score each axis from 0 to 3. For **Benefit** and **Feasibility**, higher is better. For
**Risk**, higher is worse. Do not average the axes into one number: the verdict rules read
them separately.

Print the evidence tier on every scorecard row. It caps what the row may claim.

| Tier | What you did | What you may claim |
|---|---|---|
| `E0` | Static reading only (inventory, code, manifests). | Structure, signals, dependency gaps. No measured gain. |
| `E1` | One check run: tests, a benchmark, or a profile of a sample workload. | That the measured number holds on this machine. |
| `E2` | Spike done: parity checked, old and new benchmarked 3 times each. | A measured speedup and a calibrated port rate for that unit. |
| `E3` | Production data from the user (cost, latency, incidents) or shadow traffic. | Benefit in money or in risk. |

## Benefit

| Score | Meaning |
|---|---|
| 0 | Rust changes no cost that matters: I/O-bound code, UI, glue, or work already done in a native library. |
| 1 | A plausible gain that nobody measured, or a measured gain under about 10% for the whole program. |
| 2 | Measured: the unit is a large share of CPU, memory or latency on a realistic workload; or C/C++ code parses untrusted input. |
| 3 | Measured and large in money or risk: an infrastructure cost line at scale, recurring memory-safety fixes, a distribution problem that blocks users, or a regulatory roadmap duty. |

**Amdahl check (always run it).** Whole-program speedup = 1 / ((1 − p) + p / s), where *p*
is the unit's share of runtime and *s* is the unit's own speedup. Example: s = 10 on p = 0.2
gives 1.22x for the whole program. Then subtract the boundary cost: crossings per request ×
the cost to convert the data at each crossing. If you do not know *s*, give a range and
label it as an assumption; the spike measures it.

Benefits other than speed count too, each with its own evidence: memory safety (only when
Rust replaces C/C++ or unsafe code, or replaces new code that would be written in C/C++),
fewer production incidents from the type system (anecdotal evidence, so label it), a single
static binary and fast start (CLIs, serverless functions), lower memory use (no garbage
collector), and data races caught at compile time.

## Feasibility

Start at 3. Subtract 1 for each gap below (minimum 0). A score of 0 means blocked: name the
blocker.

- A core dependency has no mature crate, and a small, safe rewrite of it is not possible.
- The unit relies on dynamic code: eval, reflection, metaprogramming, monkey-patching
  (the `dynamic_*` signals).
- The interface is rich: callbacks into the host, host objects, shared mutable state, or
  many fine-grained calls.
- There is no black-box oracle: only white-box tests in the host language, or no tests.
- The unit is bound to a framework that is the product: the views of a Django or Rails
  app, an ORM-backed admin, a UI component tree (the `framework_bound` and `ui_framework`
  signals).
- C++ with heavy templates or inheritance at the interface; C macros, unions,
  `setjmp`/`longjmp` or inline assembly (the `c_hard_constructs` signal).

## Risk

Start at 0. Add 1 for each risk below (maximum 3).

- The parity target moves: high churn, or the team cannot freeze features.
- The boundary sits on a hot path, and data must be converted at each crossing.
- Fewer than two people can write and review Rust, and there is no plan to train or hire.
- Deployment cost: per-platform binaries or wheels, OpenSSL or libc variants, edge,
  serverless or WASM limits, or a platform that Rust does not support well.
- A business-critical path with no shadow or canary route and no fast rollback.
- A long tail: much edge-case behavior, undocumented formats, or bug-compatibility that
  users rely on.

## Effort model

```text
effort = translate + verify + interop + build-and-release + ramp-up + contingency
```

Number each assumption (A1, A2, …) in the report, so the reader can replace it.

- **Translate**: code lines of the unit ÷ port rate. Take the port rate from the spike
  (`E2`). Without a spike, take it from a named analog case and cite it, or leave the term
  as a relative size and mark the total as uncalibrated. When an AI coding agent drafts the
  port, the first draft is fast and verification becomes the main cost. Do not shrink the
  verify term because the draft was fast.
- **Verify**: usually the largest term. It is low when black-box tests can judge both
  versions. It is high when tests must be ported, when golden files or differential tests
  must be built, or when only production traffic can show parity (add the shadow setup).
- **Interop**: zero for a clean replacement behind a process or network boundary; low for
  one component with a narrow plain-data interface; high for many fine-grained crossings or
  calls in both directions. Migrate one component at a time, never one function or one file
  at a time.
- **Build and release**: Cargo in the build (easier when the Rust part is a leaf of the
  dependency graph), CI jobs, cross-compilation, per-platform artifacts, signing, and a
  policy for which crates the team accepts.
- **Ramp-up**: per engineer new to Rust. Use the published ramp-up data you read in step 5
  and the team count from step 2.
- **Contingency**: default 25% for a port of a whole service or repository, because the
  last few percent of behavior is where rewrites stall. State it as an assumption.

Size bands for the scorecard: **S** ≤ 2 engineer-weeks, **M** 2–8, **L** 8–26, **XL** > 26.
Always give a range (low–high), never one number.

## Verdict rules

| Verdict | When |
|---|---|
| `PORT FIRST` | Benefit ≥ 2 at `E1` or better, Feasibility ≥ 2, Risk ≤ 1, the Amdahl gain is worth the effort, and no cheaper alternative gets most of the gain. |
| `PILOT` | Promising but unmeasured (Benefit 2 at `E0`, Feasibility ≥ 2): run the spike or a time-boxed pilot before any commitment. |
| `LATER` | Worth it, but blocked by something to fix first: missing tests, a dependency to port first, a team gap. Name the unblocker. |
| `NEW CODE ONLY` | A memory-safety goal in C/C++: write new components in Rust, keep stable old code, and port only old code that changes often or parses untrusted input. |
| `ALTERNATIVE` | A named cheaper option gets most of the gain. Say which one, and its expected gain. |
| `KEEP` | Benefit ≤ 1, I/O-bound code, UI, or framework-bound glue. |

## Whole-repository rewrite test

Recommend a port of the whole repository only when all five are true. Otherwise recommend a
route that ports one component at a time.

1. Incremental ports would put the boundary on the hot path many times, or interop would
   cost more than the rewrite.
2. A parity oracle covers real behavior: a black-box suite that can judge both versions, or
   production shadowing.
3. The product can keep shipping during the port (dual running, or one component at a time).
4. The team can own Rust for the long term: at least two reviewers and a hiring path.
5. The benefit is measured for the whole system, not inferred from one hotspot.

If both codebases must live side by side for a long time and changes must be ported between
them, a language that keeps the existing code structure can beat Rust. Say so when the
evidence points there.

## Cheaper alternatives

| Cost found | Try first |
|---|---|
| Python numeric loops | Vectorization (NumPy, Polars), Numba, Cython or mypyc |
| JSON or other serialization | A faster existing library (for example orjson or msgspec) |
| Regex or parsing | A native parser library that the platform already has |
| Python GIL contention | multiprocessing, the free-threaded CPython build, or a native extension |
| Network or database latency | Batching, indexes, caching, concurrency — not a language change |
| Node.js CPU hotspots | Worker threads, or an existing native or WASM package |
| Garbage-collector pauses (Go, JVM) | Allocation profiling, pooling, collector tuning |
| C/C++ memory safety | Hardened allocators and sanitizers now; Rust for new code |
| JVM or .NET start time | Ahead-of-time compilation (GraalVM native image, .NET Native AOT) |

## Integration patterns by language pair

These names are starting points. Check each tool's current status in step 5.

| Host | In-process pattern | Boundary cost to watch |
|---|---|---|
| Python | PyO3 extension module, built with maturin | Conversion of Python objects: pass bytes, buffers or arrays, not lists of objects |
| Node.js / TypeScript | napi-rs addon; WASM (wasm-bindgen) for browser or edge | JavaScript value conversion; prebuilt binaries per platform |
| C | bindgen and cbindgen over the C ABI | Lowest; ownership rules at the boundary |
| C++ | cxx or autocxx | Templates, inheritance and exceptions at the interface |
| Java / Kotlin | The `jni` crate | JNI call cost and local-reference management |
| Go | cgo over the C ABI | Per-call cgo overhead; a service boundary is often simpler |
| Ruby | magnus or rb-sys | Ruby object conversion; the global VM lock |
| .NET | P/Invoke over the C ABI | Marshalling cost |
| Any | A separate service (HTTP or gRPC) or a CLI subprocess | Network or process cost; best for coarse units with a stable contract |

## Parity strategies

From the cheapest to the strongest. State which level each candidate needs and what it adds
to the verify term.

1. Reuse black-box tests (CLI, HTTP, golden files) to judge both versions.
2. Golden files: record the old version's outputs on a corpus of real inputs; the new
   version must match them. When an AI agent writes the port, keep about every 5th recorded
   input away from it, and add edge cases (NaN, ±infinity, -0.0, empty input, non-ASCII, larger
   inputs) answered by the old version. A port that passes only the inputs it saw has memorized
   them.
3. Differential testing: generate inputs with property-based testing (for example
   Hypothesis, proptest or fast-check), run both versions, and compare the outputs.
4. Shadow traffic: send real requests to both versions, compare the results, normalize known
   nondeterminism, and send each difference to a fix queue.
5. Canary rollout behind a flag, with a fast rollback, after parity holds.
