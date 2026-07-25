# Fix Patterns — 6 patterns + composition + 8 anti-patterns

Each pattern has: **applies to (root-cause class)**, **before/after code**, **gotchas**. All examples use standard libraries (`pytest-xdist`, `pytest-rerunfailures`, `pytest-timeout`, `pytest-randomly`, `pytest-socket`, `pytest-asyncio`, `tenacity`) — no project-internal helpers. If the host project provides its own wait helper / mock factory, the skill prefers that over the library example.

## Pattern 1 — Per-worker resource isolation

**Applies to.** Class 1 (fixture scope leakage), class 2 (shared resource contention).

**Before.**
```python
@pytest.fixture(scope="session")
def db():
    return create_engine("postgresql:///test_db")  # collision under -n auto

@pytest.fixture(scope="session")
def cache_path():
    return Path("/tmp/cache.db")  # collision

@pytest.fixture(scope="session")
def seed_user(db):
    db.execute("INSERT INTO users(id, name) VALUES (1, 'alice')")  # UNIQUE violation
```

**After.**
```python
@pytest.fixture(scope="session")
def db(worker_id):
    # worker_id is "master" when -n0/no xdist, else "gw0", "gw1", ...
    db_name = f"test_db_{worker_id}"
    return create_engine(f"postgresql:///{db_name}")

@pytest.fixture
def cache_path(tmp_path_factory, worker_id):
    return tmp_path_factory.mktemp(f"cache_{worker_id}") / "cache.db"

@pytest.fixture
def seed_user(db):
    user_id = uuid.uuid4().int & ((1 << 31) - 1)  # unique per call
    db.execute("INSERT INTO users(id, name) VALUES (%s, 'alice')", (user_id,))
    return user_id
```

**Gotchas.** Many CI providers offer pre-created databases per worker (e.g., `DATABASE_URL_${WORKER_ID}`). Check the project's CI config before creating per-worker DBs from scratch — startup cost is real. `worker_id` is provided by `pytest-xdist`; in a non-xdist run it is `"master"`, so the suffix is harmless.

---

## Pattern 2 — `xdist_group` markers + `--dist loadgroup`

**Applies to.** Class 2 (shared resource contention) when isolation is impossible (truly singleton resource, e.g. a license server, an external sandbox account).

**Before.**
```python
def test_payment_capture():    # uses sandbox account #42
    ...

def test_payment_refund():     # uses sandbox account #42
    ...
# Under -n auto: parallel tests blow up the sandbox's concurrency limit
```

**After.**
```python
@pytest.mark.xdist_group("sandbox-42")
def test_payment_capture():
    ...

@pytest.mark.xdist_group("sandbox-42")
def test_payment_refund():
    ...
```
Run with `--dist loadgroup -n auto`. All tests sharing a group land on the same worker; serialization is automatic.

**Gotchas.** Group names must be **consistent across sibling files** — a typo (`sandbox-42` vs `sandbox42`) silently splits the group. Add a regression test: grep `xdist_group` across `tests/`, fail CI if any name appears in only ONE file. Loadgroup is a partition strategy, not a lock; if you need cross-group exclusion, use a real lock.

---

## Pattern 3 — Conditional waits via `tenacity`

**Applies to.** Class 5 (CI environment drift), and any cold-start / async-readiness situation.

**Before.**
```python
def test_health():
    start_server()
    time.sleep(2)  # hope it's ready
    assert get("/health").status_code == 200
```

**After.**
```python
from tenacity import retry, stop_after_delay, wait_fixed, retry_if_exception_type

@retry(
    stop=stop_after_delay(10),
    wait=wait_fixed(0.1),
    retry=retry_if_exception_type((ConnectionError, AssertionError)),
    reraise=True,
)
def wait_for_health():
    assert get("/health").status_code == 200

def test_health():
    start_server()
    wait_for_health()
```

**Gotchas.** If the project provides its own wait helper (e.g., `wait_for_condition`, `wait_until`, `eventually`), prefer it — discovered via the project-detection step in SKILL.md. Never replace `time.sleep(2)` with `time.sleep(5)` to "fix" a flake; that hides the race and slows the suite.

---

## Pattern 4 — Narrow `monkeypatch` scope

**Applies to.** Class 4 (monkeypatch / import-time leakage).

**Before.**
```python
@pytest.fixture(scope="session", autouse=True)
def patch_env(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key-123")  # leaks across the whole worker
    yield
```

**After.**
```python
@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key-123")  # function-scoped, reverts on test exit
    return "test-key-123"

def test_call(api_key):
    ...
```

**Gotchas.** If the env var is read AT IMPORT TIME by some module, function-scoped patching is too late — the module already cached the original value. Either patch at session scope and document why with a comment naming the module that reads at import, or refactor the module to read lazily. Use `pytest.MonkeyPatch.context()` for local-scoped patches when you need a tighter `with` block.

---

## Pattern 5 — `pytest-rerunfailures` for genuine cold-starts

**Applies to.** Class 5 (CI environment drift), CONTRIBUTING role only — never as the primary fix.

**Before.**
```ini
[tool.pytest.ini_options]
addopts = "--reruns 3"   # every test reruns 3x — masks real flakes everywhere
```

**After.**
```python
# Only the test that genuinely cold-starts the network; reason in the comment.
@pytest.mark.flaky(reruns=2, reruns_delay=1)  # cold DNS resolution on first connect
def test_external_health_endpoint():
    assert get("https://api.example.com/health").status_code == 200
```

**Gotchas.** Every `@pytest.mark.flaky` MUST have a one-line comment naming the cold-start cause (DNS, JIT, lazy import, container warm-up). No comment = remove the marker and apply pattern 3 (conditional waits) instead. Never apply `--reruns N` globally in `addopts`. Track the count of `@pytest.mark.flaky` markers in CI and fail if it grows without justification.

---

## Pattern 6 — Asyncio loop-scope alignment

**Applies to.** Class 6 (asyncio / event-loop concurrency). Often co-applied with pattern 1 when the misaligned fixture also leaks state.

**Before.**
```python
# pyproject.toml has no asyncio_mode → default depends on pytest-asyncio version

@pytest.fixture(scope="session")
async def shared_client():
    client = AsyncClient()
    await client.connect()
    yield client
    await client.close()  # may run on a dying loop

async def test_one(shared_client):  # function-scoped test, session-scoped client
    assert await shared_client.ping()
```

**After.**
```toml
# pyproject.toml — pin the mode explicitly
[tool.pytest.ini_options]
asyncio_mode = "auto"     # or "strict"; pick one and document
```

```python
@pytest.fixture(scope="session")
def event_loop():
    """Single session-scoped loop so the session-scoped client outlives function tests."""
    loop = asyncio.new_event_loop()
    yield loop
    # Cancel any leaked tasks before closing
    pending = asyncio.all_tasks(loop)
    for task in pending:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    loop.close()

@pytest.fixture(scope="session")
async def shared_client(event_loop):
    client = AsyncClient()
    await client.connect()
    yield client
    await client.close()

async def test_one(shared_client):
    assert await shared_client.ping()
```

**Gotchas.** If different fixtures in the same suite need different loop scopes, you have a structural problem — pick one (usually function-scoped) and rebuild any session-scoped resource as a process-level singleton (e.g., subprocess) accessed via fresh per-test connections. Never schedule a background task in an `autouse` fixture without explicit lifecycle ownership; if you must, register a teardown that awaits its cancellation. `asyncio.gather(..., return_exceptions=True)` is critical for fan-out cleanup; without it one cancelled task aborts the whole gather and leaves siblings dangling.

---

## Composition — applying multiple patterns when routing returns primary + contributing

Each hybrid recipe in `taxonomy.md` maps to a pattern composition. Order and verification matters:

### CI drift × scope leak
- **Apply** pattern 1 (isolation) AS PRIMARY.
- **Then** pattern 5 (rerunfailures with documented cold-start) AS CONTRIBUTING — only if a real cold-start residual remains after isolation.
- **Verify** by re-running the validation gate under BOTH `taskset -c 0,1` AND unconstrained CPU; both must reach the achieved-confidence target.

### monkeypatch × shared resource
- **Apply** pattern 1 (per-worker port/db suffix) AS PRIMARY — fixes the collision regardless of the env-var leak.
- **Then** pattern 4 (narrow monkeypatch scope) AS CONTRIBUTING — prevents the leak from biasing which resource is selected.
- **Verify** by intentionally setting a wrong env var at session level; tests must still pass because resource-isolation makes the env irrelevant.

### order dep × random seed
- **Apply** pattern 1 (replace implicit-ordering global with proper fixture/factory) AS PRIMARY.
- **Then** adopt `pytest-randomly` permanently AS CONTRIBUTING discipline — every CI run shuffles, surfacing this class within a week of regression.
- **Verify** by running with 5+ fresh `--randomly-seed` values; all must pass.

---

## Anti-patterns — do not do these

1. **Rerun-first without repro.** Marking a test `@pytest.mark.flaky` to make CI green before you reproduced and classified the cause. The flake survives, hidden.
2. **Session-scoped mutable fixtures.** Any `scope="session"` fixture that writes anywhere observable. Default to `function` scope; promote only with documented justification.
3. **Global `monkeypatch.setattr` at session scope.** Patches an attribute that other tests may legitimately touch; reverts at unpredictable boundaries.
4. **Hardcoded paths/ports/DB names.** `/tmp/cache.db`, `port=6379`, `db=0`. Replace with `tmp_path_factory` + `worker_id`-suffixed names.
5. **`time.sleep(N)` in tests.** Either too short (flaky) or too long (slow). Use pattern 3 (conditional waits) instead.
6. **Over-relying on `--reruns N` globally.** Hides real bugs. Reserve for documented cold-starts only.
7. **Blanket `--dist loadscope`.** Groups by module, often hiding cross-file class-1/4 issues. Prefer `loadgroup` with explicit group markers.
8. **Ignoring worker-correlation signal.** When step 2 reveals a single worker fails, that IS the diagnosis — investigate the fixture chain that ran first on that worker; do not reach for `--reruns`.

---

Trigger to revisit: when pytest-asyncio default mode flips again (it has done so historically), when tenacity's `retry` decorator API changes, or when `pytest-rerunfailures` adds finer-grained per-cause markers.
