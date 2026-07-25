# Flake Taxonomy — 6 root-cause classes

Each class: **signal fingerprint** (the observation that points here), **why it fails** (the mechanism), **generic example** (no project-specific helpers), **routes to** (fix pattern in `fix-patterns.md`).

Routing is multi-label. A real flake usually fits ONE class as primary and may have one contributing class. The discriminators in SKILL.md decide which is which.

## 1. Fixture scope leakage

**Signal fingerprint.** Test passes when run alone (`-n0`). Fails under `-n auto`. Fails harder as `-n` grows. `--dist loadfile` does NOT fix it (problem is across-files, not within-file ordering). Single-worker runs (`-n 1`) often pass.

**Why it fails.** A `session`- or `module`-scoped fixture mutates shared state (DB row, file on disk, in-process singleton, env var). Under xdist each worker is a separate process, so the fixture executes ONCE PER WORKER, not once per session — and any "first-write-wins" assumption breaks. Symptoms include "row already exists", "table not empty", "got unexpected user_id 2 expected 1".

**Generic example.**
```python
@pytest.fixture(scope="session")
def seed_user(db):
    db.execute("INSERT INTO users(id, name) VALUES (1, 'alice')")
    yield 1
    db.execute("DELETE FROM users WHERE id = 1")
```
With 4 workers, four parallel `INSERT id=1` race; three fail with UNIQUE violation, one succeeds, downstream tests see whichever worker won.

**Routes to.** Pattern 1 (per-worker resource isolation via `worker_id` + `tmp_path_factory`). Often combines with pattern 2 (xdist_group) when you must keep singleton scope.

## 2. Shared resource contention

**Signal fingerprint.** Test passes alone, fails under any parallelism. Errors mention ports (`Address already in use`), file paths (`FileExistsError`), DB names (`database "test" already exists`), or external services. `--dist loadgroup` with a custom group name FIXES it — that is the strongest discriminator for this class vs class 1.

**Why it fails.** Tests touch a hardcoded global resource: port `6379`, path `/tmp/cache.db`, DB `test_db`, lock file `/var/run/myapp.lock`. Under parallelism multiple processes claim the same name and collide.

**Generic example.**
```python
def test_cache_set():
    redis = Redis(host="localhost", port=6379, db=0)  # hardcoded!
    redis.set("k", "v")
    assert redis.get("k") == b"v"
```
Two workers both hit `db=0` on the same Redis; cross-test reads see other worker's writes.

**Routes to.** Pattern 1 (per-worker isolation via `worker_id`-suffixed names). If isolation isn't possible (truly singleton resource), pattern 2 (xdist_group serialization).

## 3. Test-order dependencies

**Signal fingerprint.** Test passes alone. Test passes when whole file runs in order. Fails with `pytest-randomly` shuffling, OR fails when run with a specific sibling AFTER it. `--dist loadfile -n auto` does NOT fix because order WITHIN file is preserved, but `pytest-randomly --randomly-seed=<seed>` will reproduce.

**Why it fails.** Test B implicitly depends on Test A having executed first (left a row in DB, mutated a class attribute, populated a cache, registered a global). Or Test C cleans up state Test B was about to read. The ordering is ambient — nothing in the test source declares the dependency.

**Generic example.**
```python
USERS = []  # module global

def test_create_user():
    USERS.append({"id": 1})

def test_user_exists():
    assert any(u["id"] == 1 for u in USERS)  # depends on prior test running
```
Run alone, `test_user_exists` fails immediately. Run after `test_create_user`, passes. Shuffle the order, fails.

**Routes to.** Pattern 1 (proper fixture/factory replacing implicit ordering). Adopt `pytest-randomly` permanently to surface this class early.

## 4. monkeypatch / import-time leakage

**Signal fingerprint.** A module-level or session-scoped `monkeypatch` (or `os.environ` mutation, or `importlib` reload, or `sys.modules` poke) appears in the conftest chain. Failing tests touch the patched attribute. Failure is order-sensitive AND worker-sensitive — the worst combination. `pytest --setup-show <node>` reveals the broad-scope patch.

**Why it fails.** `monkeypatch.setattr` reverts at fixture teardown, which fires at the END of the fixture's scope. A `session`-scoped monkeypatch holds the patch until the worker process dies; tests in unrelated files see the patched value. If an inner `function`-scoped monkeypatch then sets the same attribute and reverts to the ORIGINAL (not the outer-scoped value), state is corrupted.

**Generic example.**
```python
@pytest.fixture(scope="session", autouse=True)
def patch_env(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key-123")
    yield
```
Any test importing a module that read `os.environ["API_KEY"]` AT IMPORT TIME (cached as module global) is unaffected — the env var was patched too late. Tests that read the env var lazily see the patch. Mixed behavior across the suite.

**Routes to.** Pattern 4 (narrow monkeypatch scope — function over module over session). Document any necessary session-scoped patch with a comment naming the reason.

## 5. CI environment drift

**Signal fingerprint.** Test passes locally with high reliability (>99%). Fails in CI with non-trivial rate (>1%). Worker-correlation evidence is absent or unclear. The classic phrase "passes on my machine".

**Why it fails.** CI runners have slower CPUs (often 2 vCPU shared), less memory, container cold-starts, network latency to internal services, different file-system semantics (overlayfs, tmpfs), different timezone. Races that are imperceptibly fast on a 10-core M-series Mac become observable on a 2-vCPU GitHub runner. Cold imports take seconds. Network calls to localhost still incur overhead.

**Generic example.**
```python
async def test_with_timeout():
    result = await asyncio.wait_for(slow_call(), timeout=0.5)  # 500ms
    assert result == "ok"
```
Locally `slow_call()` finishes in 80ms; CI takes 600ms because of cold imports + network jitter. `TimeoutError`.

**Routes to.** Pattern 3 (conditional waits via `tenacity` or project's wait helper, NEVER `time.sleep` increases). Pattern 5 (`pytest-rerunfailures` ONLY for genuine cold-starts, with a one-line comment naming the cause). Reproduce locally with `taskset -c 0,1 $PYTEST_CMD` or `docker run --cpus=2 --memory=4g`.

## 6. Asyncio / event-loop concurrency

**Signal fingerprint.** Async test. One or more of: deadlocks under `-n auto` with async tests; pytest-asyncio mode change (auto vs strict) flips behavior; unfinished tasks at teardown; `RuntimeError: Event loop is closed`; `Task was destroyed but it is pending`; async cleanup in autouse fixtures hangs at 99%; tests that await on a queue/future never resolve in CI but resolve locally.

**Why it fails.** Loop-scope vs fixture-scope mismatch — function-scoped fixture using session-scoped loop or vice versa. Background task scheduled in an autouse fixture without explicit lifecycle ownership leaks past the test boundary; cleanup runs against a dying loop. Missed `await` on a coroutine creates a warning + a sometimes-completing background task. pytest-asyncio mode default changing between versions silently flips test discovery (strict requires `@pytest.mark.asyncio`; auto applies the marker implicitly).

**Generic example.**
```python
@pytest.fixture(scope="session")
async def shared_client():
    client = AsyncClient()
    await client.connect()
    yield client
    await client.close()  # runs against a possibly-dead loop

async def test_one(shared_client):  # function-scoped, but client is session-scoped
    assert await shared_client.ping()
```
Under `-n auto`, the session fixture's `await client.connect()` runs on whichever event loop pytest-asyncio creates first; later function-scoped tests get a different loop and the client's stored `loop` reference is stale.

**Routes to.** Pattern 6 (asyncio loop-scope alignment + pinned `pytest-asyncio` mode + structured cleanup with `asyncio.gather(..., return_exceptions=True)`). Often co-occurs with class 1 (fixture scope leakage) when the loop scope is bolted onto an already-mismatched fixture.

---

## Signal → class quick reference

Match these strings/patterns from a failure log to candidate classes. A match yields **medium confidence** for the primary class — one log is a hint, not proof. Always confirm via reproduction (playbook step 6). This table is the heart of Step 0 triage in `playbook.md`.

| Signal in log | Primary | Contributing | Notes |
|---|---|---|---|
| `RuntimeError: Event loop is closed` | 6 | 1 | Async cleanup ran on a dying loop |
| `Task was destroyed but it is pending` | 6 | — | Leaked background task from autouse fixture |
| `RuntimeError: This event loop is already running` | 6 | — | Loop-scope mismatch |
| `OSError: [Errno 48] Address already in use` (macOS) | 2 | — | Hardcoded port |
| `OSError: [Errno 98] Address already in use` (Linux) | 2 | — | Same |
| `FileExistsError` on hardcoded `/tmp/...` path | 2 | 1 | Path collision |
| `IntegrityError ... UNIQUE constraint failed` | 1 | 2 | Two workers INSERTed the same row |
| `IntegrityError ... duplicate key value` (Postgres) | 1 | 2 | Same |
| `database "X" already exists` | 2 | — | DB-name collision under `-n auto` |
| `KeyError` / `AttributeError` on attr a fixture should have set | 1 | 4 | Setup didn't run, or was reverted |
| `TimeoutError` / `asyncio.TimeoutError` (CI only, not local) | 5 | 6 | CPU/network jitter widens race windows |
| `ConnectionRefusedError` / `Connection reset` (CI only) | 5 | 2 | Cold-start / service-readiness race |
| `ModuleNotFoundError` only under `-n auto` | 4 | — | `sys.modules` manipulation leak |
| Exit code 137 / "killed" / OOM | 5 | — | CI memory cap |
| Exit code 139 / SIGSEGV | — (escalate) | — | Not a flake class — hand off to `/diagnose` |
| Failures cluster on a single `gw[N]` worker | 1 | 2 | First worker stamped shared state |
| Failures distribute evenly across workers | 2 | 1 | Every worker hits the same resource |
| `AssertionError` comparing against state from a sibling test | 3 | 1 | Order dependency or fixture leakage |
| Failure consistently follows one specific sibling test | 3 | — | Confirmed order dependency |
| Session-scoped fixture warning + value mismatch on env var | 4 | — | Session-scoped monkeypatch leak |

---

## Hybrid recipes (top 3 cross-class combinations)

When the diagnostic playbook surfaces signals from more than one class, prefer one of these recipes. The primary class is the dominant cause; the contributing class is real but smaller.

### CI drift × scope leak

**Pattern.** Local hardware masks a session-scoped DB fixture race. Tests pass on a 10-core M-series; fail in CI on 2 vCPU because the slower setup widens the race window enough to surface the unique-violation collision.

**Routing.** Primary = class 1 (fixture scope leakage). Contributing = class 5 (CI environment drift).

**Composition.** Apply pattern 1 (per-worker isolation) AS PRIMARY. Then pattern 5 (`pytest-rerunfailures` with documented cold-start cause) AS CONTRIBUTING — only if a genuine cold-start residual remains after isolation. Verify in the validation gate by re-running under `taskset -c 0,1` AND unconstrained CPU; both must pass.

### monkeypatch × shared resource

**Pattern.** A session-scoped `monkeypatch.setenv("REDIS_HOST", "...")` leaks across tests that all share `port=6379, db=0`. The env-var patch determines which Redis instance tests connect to; the shared port collision happens within whichever instance ends up selected.

**Routing.** Primary = class 2 (shared resource). Contributing = class 4 (monkeypatch leakage).

**Composition.** Apply pattern 1 (per-worker port/db suffix) AS PRIMARY — fixes the collision regardless of patching scope. Then pattern 4 (narrow monkeypatch scope to function-level) AS CONTRIBUTING — prevents the env leak from biasing which Redis is selected. Verify by intentionally running with a corrupted env at the session level; tests must still pass because resource-isolation makes the env irrelevant.

### order dep × random seed

**Pattern.** `pytest-randomly` shuffles into a poisoned order where Test C cleans up state Test B was about to read. The flake rate tracks the seed; pinning the seed reproduces deterministically.

**Routing.** Primary = class 3 (order dependency). Contributing = class 1 (fixture scope) — the implicit "previous test seeded the global" pattern is itself a scope-leakage anti-pattern.

**Composition.** Apply pattern 1 (replace implicit-ordering global with a proper fixture or factory) AS PRIMARY. Adopt `pytest-randomly` permanently AS CONTRIBUTING discipline — every CI run shuffles, surfacing this class within a week of introduction. Verify by running with 5+ fresh random seeds; all must pass.

---

Trigger to revisit: when a new top-3 flake category surfaces (e.g., distributed-coordination flakes from emerging async-runtime libraries, or container-network flakes from a popular orchestrator), add a class 7 here and route a new pattern in `fix-patterns.md`.
