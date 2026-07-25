**MIKADO GIT GUARD: If `git_mode == "lightweight"`, do NOT use `git stash push/pop/drop` in this phase. Instead: make the change directly, verify with `bash -n`, and continue. If verification fails, restore from `{file}.bak` and try a different grouping.**

---

# Mikado Method Details

## PHASE 2: Incremental Migration (Mikado Loop)

**For each logical grouping (CRUD, validation, utils, etc.):**

```
1. git stash push -m "mikado-{function_name}-$(date +%s)"
2. Create new module file
3. COPY (don't move) functions to new module
4. Update facade to import from new module
5. Run tests
6. If PASS: git stash drop, continue
7. If FAIL: git stash pop, note prerequisite, try different grouping
```

**Example Python migration:**

```python
# Step 1: Create services/user/repository.py
"""Repository functions for user data access."""
from typing import Optional
from .models import User

def get_user(user_id: str) -> Optional[User]:
    # Copied from _legacy.py
    ...

def create_user(data: dict) -> User:
    # Copied from _legacy.py
    ...
```

```python
# Step 2: Update services/user/__init__.py facade
from .repository import get_user, create_user  # Now from new module
from ._legacy import UserService  # Still from legacy (not migrated yet)

__all__ = ['UserService', 'get_user', 'create_user']
```

```bash
# Step 3: Run tests
pytest tests/unit/user -v

# If pass: remove functions from _legacy.py, continue
# If fail: revert, analyze why, find prerequisite
```

**Repeat until _legacy only has unmigrated items.**

## PHASE 3: Update Test Imports (If Needed)

**Most tests should NOT need changes** because facade preserves import paths.

**Only update when tests use internal paths:**

```bash
# Find tests with internal imports
grep -r "from services.user.repository import" tests/
grep -r "from services.user._legacy import" tests/
```

**For each test file needing updates:**
1. `git stash push -m "test-import-{filename}"`
2. Update import to use facade path
3. Run that specific test file
4. If PASS: `git stash drop`
5. If FAIL: `git stash pop`, investigate

## PHASE 4: Cleanup

**Only after ALL tests pass:**

```bash
# 1. Verify _legacy.py is empty or removable
wc -l services/user/_legacy.py

# 2. Remove _legacy.py
rm services/user/_legacy.py

# 3. Update facade to final form (remove _legacy import)
# Edit __init__.py to import from actual modules only

# 4. Final test gate
pytest tests/unit/user -v
pytest tests/integration/user -v  # If exists
```

## PHASE 5: Verify Threshold Reduction (CRITICAL)

**Before reporting completion, MUST verify actual LOC reduction:**

```bash
# 1. Check largest file size in new structure
find services/user/ -name "*.py" -exec wc -l {} + | sort -rn | head -1

# 2. Compare against target threshold (default: 500 LOC)
# 3. If largest file >= threshold: STATUS = "partial" NOT "fixed"
```

**Verification logic:**

```python
# Pseudo-code for threshold verification
target_threshold = int(os.getenv("REFACTOR_THRESHOLD", "500"))

# Check all new files
new_files = ["services/user/__init__.py", "services/user/service.py", ...]
max_loc = max(wc_l(f) for f in new_files if exists(f))

if max_loc >= target_threshold:
    status = "partial"
    message = f"Refactoring incomplete: largest file is {max_loc} LOC (target: <{target_threshold})"
else:
    status = "fixed"
    message = f"Refactoring successful: largest file is {max_loc} LOC (target: <{target_threshold})"
```

**MANDATORY OUTPUT INCLUDE:**
- `largest_file_loc`: Actual LOC count of largest new file
- `target_threshold`: The threshold being used (default 500)
- `meets_threshold`: true/false (whether largest file < threshold)
- `status`: "fixed" if meets_threshold=true, else "partial"

## Mikado Loop Sub-Tasks (TaskList Integration)

For complex migrations, create sub-tasks within PHASE 2:

```
# When starting each Mikado step:
mikado_task = TaskCreate(
  subject="Migration 2.1: Extract repository functions",
  description="Move get_user, create_user to repository.py",
  activeForm="Extracting repository functions"
)
TaskUpdate(taskId=mikado_task, addBlockedBy=[phase2_task])

# After successful migration:
TaskUpdate(
  taskId=mikado_task,
  status="completed",
  metadata={"functions_moved": 2, "test_gate": "passed"}
)
```
