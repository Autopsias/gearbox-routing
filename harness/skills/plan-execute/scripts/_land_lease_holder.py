"""A SEPARATE PROCESS that takes the repo-scoped `git:` lease and holds it.

Exists because an in-process fixture proves nothing here: the whole claim is that
the lease excludes between separate `run.py` invocations, and s03b measured a
check-then-write lock letting 4 of 4 simultaneous contenders "acquire" the same
resource. So the v6-plan-shipping-during-a-v7-land proof drives BOTH sides as
real OS processes, and this is the other side.

    python3 _land_lease_holder.py <plan-dir> <hold-seconds>

**The resource is DERIVED here, exactly as production derives it** — from
`wt.repo_root`, i.e. `git rev-parse --show-toplevel` — and is never passed in.
That is not tidiness. Handed the resource as a string, this held
`git:/var/folders/...` while the land computed `git:/private/var/folders/...`
for the SAME repo; the two slug to two different lock files, both sides
"acquired" it, and the exclusion proof passed nothing while looking green. It is
the identical `same file? False` defect s03b measured. The READY line therefore
prints the LOCK FILE PATH as well, so the parent can assert both processes are
contending for one file instead of assuming it.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ship_locks as sl  # noqa: E402
import worktree as wt  # noqa: E402


def resource_for(plan_dir):
    return f"git:{wt.repo_root(plan_dir)}"


def main():
    plan_dir, seconds = sys.argv[1], float(sys.argv[2])
    resource = resource_for(plan_dir)
    sl.acquire_ship_lock(plan_dir, resource, guarded_timeout=600)
    print(f"READY {resource} {sl._ship_lock_path(plan_dir, resource)}", flush=True)
    try:
        time.sleep(seconds)
    finally:
        sl.release_ship_lock(plan_dir, resource)


if __name__ == "__main__":
    main()
