# Facade Pattern for Backward Compatibility

## PHASE 1: Create Facade Structure

**Goal:** Create directory + facade that re-exports everything. External imports unchanged.

### Python
```bash
# Create package directory
mkdir -p services/user

# Move original to _legacy
mv services/user_service.py services/user/_legacy.py

# Create facade __init__.py
cat > services/user/__init__.py << 'EOF'
"""User service module - facade for backward compatibility."""
from ._legacy import *

# Explicit public API (update with actual exports)
__all__ = [
    'UserService',
    'create_user',
    'get_user',
    'update_user',
    'delete_user',
]
EOF
```

### TypeScript/JavaScript
```bash
# Create directory
mkdir -p features/user

# Move original to _legacy
mv features/userService.ts features/user/_legacy.ts

# Create barrel index.ts
cat > features/user/index.ts << 'EOF'
// Facade: re-exports for backward compatibility
export * from './_legacy';

// Or explicit exports:
// export { UserService, createUser, getUser } from './_legacy';
EOF
```

### Go
```bash
mkdir -p services/user

# Move original
mv services/user_service.go services/user/internal.go

# Create facade user.go
cat > services/user/user.go << 'EOF'
// Package user provides user management functionality.
package user

import "internal"

// Re-export public items
var (
    CreateUser = internal.CreateUser
    GetUser    = internal.GetUser
)

type UserService = internal.UserService
EOF
```

### Rust
```bash
mkdir -p src/services/user

# Move original
mv src/services/user_service.rs src/services/user/internal.rs

# Create mod.rs facade
cat > src/services/user/mod.rs << 'EOF'
mod internal;

// Re-export public items
pub use internal::{UserService, create_user, get_user};
EOF

# Update parent mod.rs
echo "pub mod user;" >> src/services/mod.rs
```

### Java/Kotlin
```bash
mkdir -p src/main/java/services/user

# Move original to internal package
mkdir -p src/main/java/services/user/internal
mv src/main/java/services/UserService.java src/main/java/services/user/internal/

# Create facade
cat > src/main/java/services/user/UserService.java << 'EOF'
package services.user;

// Re-export via delegation
public class UserService extends services.user.internal.UserService {
    // Inherits all public methods
}
EOF
```

**TEST GATE after Phase 1:**
```bash
# Run baseline tests again - MUST pass
# If fail: git stash pop (revert) and report failure
```

## LANGUAGE DETECTION

Auto-detect language from file extension:

| Extension | Language | Facade File | Test Pattern |
|-----------|----------|-------------|--------------|
| `.py` | Python | `__init__.py` | `test_*.py` |
| `.ts`, `.tsx` | TypeScript | `index.ts` | `*.test.ts`, `*.spec.ts` |
| `.js`, `.jsx` | JavaScript | `index.js` | `*.test.js`, `*.spec.js` |
| `.go` | Go | `{package}.go` | `*_test.go` |
| `.java` | Java | Facade class | `*Test.java` |
| `.kt` | Kotlin | Facade class | `*Test.kt` |
| `.rs` | Rust | `mod.rs` | in `tests/` or `#[test]` |
| `.rb` | Ruby | `{module}.rb` | `*_spec.rb` |
| `.cs` | C# | Facade class | `*Tests.cs` |
| `.php` | PHP | `index.php` | `*Test.php` |

## CLUSTER-AWARE OPERATION

When invoked by orchestrators (code_quality, ci_orchestrate, etc.), this agent operates in cluster-aware mode for safe parallel execution.

### Input Context Parameters

Expect these parameters when invoked from orchestrator:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `cluster_id` | Which dependency cluster this file belongs to | `cluster_b` |
| `parallel_peers` | List of files being refactored in parallel (same batch) | `[payment_service.py, notification.py]` |
| `test_scope` | Which test files this refactor may affect | `tests/test_auth.py` |
| `execution_mode` | `parallel` or `serial` | `parallel` |

### Conflict Prevention

Before modifying ANY file:

1. **Check if file is in `parallel_peers` list**
   - If YES: ERROR - Another agent should be handling this file
   - If NO: Proceed

2. **Check if test file in `test_scope` is being modified by peer**
   - Query lock registry for test file locks
   - If locked by another agent: WAIT or return conflict status
   - If unlocked: Acquire lock, proceed

3. **If conflict detected**
   - Do NOT proceed with modification
   - Return conflict status to orchestrator

### Runtime Conflict Detection

```bash
# Lock registry location
LOCK_REGISTRY=".claude/locks/file-locks.json"

# Before modifying a file
check_and_acquire_lock() {
    local file_path="$1"
    local agent_id="$2"

    # Create hash for file lock
    local lock_file=".claude/locks/file_$(echo "$file_path" | md5 -q).lock"

    if [ -f "$lock_file" ]; then
        local holder=$(cat "$lock_file" | jq -r '.agent_id' 2>/dev/null)
        local heartbeat=$(cat "$lock_file" | jq -r '.heartbeat' 2>/dev/null)
        local now=$(date +%s)

        # Check if stale (90 seconds)
        if [ $((now - heartbeat)) -gt 90 ]; then
            echo "Releasing stale lock for: $file_path"
            rm -f "$lock_file"
        elif [ "$holder" != "$agent_id" ]; then
            # Conflict detected
            echo "{\"status\": \"conflict\", \"blocked_by\": \"$holder\", \"waiting_for\": [\"$file_path\"], \"retry_after_ms\": 5000}"
            return 1
        fi
    fi

    # Acquire lock
    mkdir -p .claude/locks
    echo "{\"agent_id\": \"$agent_id\", \"file\": \"$file_path\", \"acquired_at\": $(date +%s), \"heartbeat\": $(date +%s)}" > "$lock_file"
    return 0
}

# Release lock when done
release_lock() {
    local file_path="$1"
    local lock_file=".claude/locks/file_$(echo "$file_path" | md5 -q).lock"
    rm -f "$lock_file"
}
```

### Lock Granularity

| Resource Type | Lock Level | Reason |
|--------------|------------|--------|
| Source files | File-level | Fine-grained parallel work |
| Test directories | Directory-level | Prevents fixture conflicts |
| conftest.py | File-level + blocking | Critical shared state |
