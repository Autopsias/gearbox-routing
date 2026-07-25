"""Load + light-validate the IMMUTABLE dispatch graph (manifest.json).

The manifest is never mutated by /plan-execute. Mutable runtime state lives in
run_state.json (see run_state_io.py). This module only reads the manifest and
checks it stays consistent with PLAN.html's article ids.
"""

import hashlib
import json
from pathlib import Path


class ManifestError(Exception):
    pass


def load_manifest(plan_dir):
    p = Path(plan_dir) / "manifest.json"
    if not p.exists():
        raise ManifestError(f"manifest.json not found in {plan_dir}")
    try:
        m = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest.json is not valid JSON: {e}") from e

    version = m.get("plan_schema_version")
    if version is None or version < 2:
        raise ManifestError(
            f"plan_schema_version is {version!r} — /plan-execute requires >= 2. "
            "This looks like a v1 (Cowork-era) plan. Rebuild via "
            "`/plan-builder --rebuild <spec.json>` or view it read-only in a browser."
        )
    if "sessions" not in m or not isinstance(m["sessions"], list):
        raise ManifestError("manifest.json missing 'sessions' list")
    return m


def session_by_id(manifest):
    return {s["id"]: s for s in manifest["sessions"]}


def all_item_ids(manifest):
    return {it["id"] for it in manifest.get("items", [])}


def all_session_ids(manifest):
    return {s["id"] for s in manifest["sessions"]}


def manifest_digest(plan_dir):
    """sha256 of the on-disk manifest.json bytes. ``_shipping_state`` binds to
    this so a rebuilt manifest forces a ``state-drift`` refusal rather than a
    stale skip (Codex CRITICAL)."""
    p = Path(plan_dir) / "manifest.json"
    return hashlib.sha256(p.read_bytes()).hexdigest()
