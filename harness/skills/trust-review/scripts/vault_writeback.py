#!/usr/bin/env python3
"""
AV-02 (S09) — two-phase vault write-back: quarantine -> (human) promotion.

conventions.md §7.7 (two-phase vault-writeback record, finding #8); the
`vault_write` operation registered in references/scripts/adapter_registry.py (S01).

WHY THIS EXISTS
---------------
Writing a finished run's provenance back into the user's own corpus is how the
Trust Layer compounds — but done naively it POISONS the corpus: an auto-written
note becomes a future "internal source", and next quarter's refresh cites the
Trust Layer's own (possibly wrong) artifact as if it were primary evidence. A
wrong waiver would launder itself into a fact.

So a write-back is TWO-PHASE:

  Phase 1 — QUARANTINE. The derived note lands in a quarantine zone, stamped
            source_authority=derived-provenance, with a schema_version, supersedes
            links, an expiry, the producing run's hash, a rollback pointer, and a
            dedupe key over artifact/run/claim IDs. The internal-corpus verifier
            treats a quarantined note as AUDIT METADATA, never a primary fact.
  Phase 2 — PROMOTION. Only an explicit NAMED-HUMAN approval moves the note into
            the citable zone (phase=promoted, approved_by + approved_at). Nothing
            is ever auto-promoted.

DEGRADATION (never a silent corpus write)
-----------------------------------------
The `vault_write` op resolves through the registry: the first available vault or
memory writer (including any a provider pack adds) -> local-vault -> the always-on
`.trust/<run>/` quarantine sidecar terminal. When no
real vault backend resolves (or no vault path is configured), the note degrades to
a written sidecar in `.trust/<run_id>/` with a STATED note — and STILL is never
auto-promoted. Resolution is via the registry; this module never hard-codes a tool.

INTERNAL-AS-SENSITIVE + OPT-IN
------------------------------
The note is classified `internal` and carries minimal excerpts + an excerpt hash,
never the whole claim text (§2.1, same redaction as internal verification). The
write-back is OPT-IN by travel class — a class not in the opt-in set is skipped
with a stated note, never written.

Pure stdlib. `dry_run=True` (the default) computes the full mapping + writes the
quarantine representation under `out_dir` WITHOUT touching any real corpus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)  # references/scripts/ = ONE canonical adapter copy
sys.path.insert(0, os.path.join(SKILL_ROOT, "references", "scripts"))

try:
    import adapter_registry as AR  # noqa: E402
except Exception:                  # pragma: no cover - registry is always present in-repo
    AR = None

SCHEMA_VERSION = "3"
SOURCE_AUTHORITY = "derived-provenance"
PHASE_QUARANTINE = "quarantine"
PHASE_PROMOTED = "promoted"

# Vault-relative zones. Quarantine is where a derived note lands; the citable zone is
# reachable ONLY by an explicit human promotion (Phase 2).
QUARANTINE_ZONE = "Trust Layer/Quarantine"
CITABLE_ZONE = "Trust Layer/Provenance"

# Opt-in by travel class — the write-back only runs for a class the user opted in.
# Conservative default: only the lowest-stakes classes auto-opt-in; Board/Regulatory
# must be opted in explicitly by the caller (it is the most sensitive corpus).
DEFAULT_OPT_IN_CLASSES = ("internal", "travels")

_NORMALIZE_WS = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def excerpt_hash(text: str) -> str:
    """sha256 of the FULL text (internal-as-sensitive §2.1) — proves WHAT the note
    carried without retaining the whole claim."""
    return _sha256(_NORMALIZE_WS.sub(" ", (text or "").strip()))


def _minimal_excerpt(text: str, max_chars: int = 160) -> str:
    s = _NORMALIZE_WS.sub(" ", (text or "").strip())
    return s if len(s) <= max_chars else s[:max_chars].rstrip() + "…"


# ---------------------------------------------------------------------------
# run hash + dedupe identity
# ---------------------------------------------------------------------------
def run_hash(run_result: dict) -> str:
    """A stable sha256 of the PRODUCING RUN — its identity fields + verdict + the set
    of finding ids/lifecycles. Deterministic (sorted), so re-hashing the same run
    yields the same value (the §7.7 run_hash + the dedupe basis)."""
    artifact = run_result.get("artifact") or {}
    basis = {
        "run_id": run_result.get("run_id", ""),
        "artifact_sha": artifact.get("sha256", ""),
        "artifact_path": artifact.get("path", ""),
        "readiness": run_result.get("readiness", ""),
        "findings": sorted("%s:%s" % (f.get("id", ""), f.get("status", f.get("lifecycle", "")))
                           for f in (run_result.get("findings") or [])),
    }
    return _sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False))


def _artifact_id(run_result: dict) -> str:
    artifact = run_result.get("artifact") or {}
    return (artifact.get("sha256") or "")[:12] or os.path.basename(
        str(artifact.get("path", "") or "artifact")) or "artifact"


def dedupe_key(artifact_id: str, run_id: str, claim_id: str = "*") -> str:
    """conventions §7.7 dedupe_key — prevents duplicate/stale corpus entries."""
    return "artifact:%s|run:%s|claim:%s" % (artifact_id, run_id, claim_id or "*")


def _note_id(artifact_id: str, run_id: str) -> str:
    return "otl-prov-%s-%s" % (artifact_id, _sha256(run_id)[:8] if run_id else "0")


# ---------------------------------------------------------------------------
# build the derived-provenance note
# ---------------------------------------------------------------------------
def _claims_audit(run_result: dict, claims=None) -> list:
    """The note BODY is audit metadata: per-claim verdict + a minimal excerpt + hash.
    Never the whole claim text (internal-as-sensitive). `claims` may be supplied
    (e.g. the verification block); else derived from the findings."""
    rows = []
    if claims:
        for c in claims:
            txt = c.get("claim") or c.get("text") or ""
            rows.append({
                "claim_id": c.get("claim_id", ""),
                "verdict": c.get("verdict", c.get("review_status", "needs-review")),
                "excerpt": _minimal_excerpt(txt),
                "excerpt_hash": excerpt_hash(txt),
            })
    else:
        for f in (run_result.get("findings") or []):
            txt = f.get("evidence", "") or ""
            rows.append({
                "claim_id": f.get("id", ""),
                "verdict": f.get("status", f.get("lifecycle", "needs-review")),
                "excerpt": _minimal_excerpt(txt),
                "excerpt_hash": excerpt_hash(txt),
            })
    return rows


def build_quarantine_note(run_result: dict, *, run_id: str = "", artifact_id: str = "",
                          supersedes=None, expiry=None, travel_class: str = "",
                          sensitivity: str = "internal", rollback_pointer: str = "",
                          claims=None) -> dict:
    """Build the Phase-1 derived-provenance note (frontmatter + body). NOT written here
    — `two_phase_vault_write` chooses the backend/zone and persists it."""
    run_id = run_id or run_result.get("run_id") or ""
    artifact_id = artifact_id or _artifact_id(run_result)
    rh = run_hash(run_result)
    rollback_pointer = rollback_pointer or (".trust/%s/" % run_id if run_id else ".trust/")
    audit = _claims_audit(run_result, claims)
    # a per-claim dedupe key list + the run-level key
    claim_keys = [dedupe_key(artifact_id, run_id, c["claim_id"]) for c in audit if c["claim_id"]]

    frontmatter = {
        # --- conventions §7.7 two-phase vault-writeback record (verbatim keys) ---
        "phase": PHASE_QUARANTINE,
        "source_authority": SOURCE_AUTHORITY,
        "schema_version": SCHEMA_VERSION,
        "supersedes": list(supersedes or []),
        "expiry": expiry or "staleness-policy:90d",
        "run_hash": rh,
        "rollback_pointer": rollback_pointer,
        "dedupe_key": dedupe_key(artifact_id, run_id),
        "approved_by": None,
        "approved_at": None,
        # --- routing / sensitivity (internal-as-sensitive, §2.1) ---
        "sensitivity": sensitivity,
        "travel_class": travel_class,
        "note_type": "trust-layer-provenance",
        "claim_dedupe_keys": claim_keys,
        "artifact_id": artifact_id,
        "run_id": run_id,
        "readiness": run_result.get("readiness", ""),
    }
    body = {
        "title": "Trust Layer provenance — %s (run %s)" % (artifact_id, run_id),
        "summary": ("DERIVED provenance, NOT a primary source. Quarantined until a named "
                    "human promotes it. readiness=%s; %d finding(s)."
                    % (run_result.get("readiness", "?"), len(run_result.get("findings") or []))),
        "claims_audit": audit,
    }
    return {
        "note_id": _note_id(artifact_id, run_id),
        "frontmatter": frontmatter,
        "body": body,
    }


def render_note_md(note: dict) -> str:
    """Markdown with a YAML frontmatter block — the on-disk note shape (Obsidian/local)."""
    fm = note["frontmatter"]
    L = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            if v:
                L.append("%s:" % k)
                for item in v:
                    L.append("  - %s" % item)
            else:
                L.append("%s: []" % k)
        elif v is None:
            L.append("%s: null" % k)
        else:
            L.append("%s: %s" % (k, v))
    L.append("---")
    L.append("")
    L.append("# %s" % note["body"]["title"])
    L.append("")
    L.append("> ⚠️ **%s**" % note["body"]["summary"])
    L.append("")
    L.append("> This note's `source_authority` is `derived-provenance`. While `phase: "
             "quarantine` it is **audit metadata only** — the internal-corpus verifier "
             "will NOT cite it as a primary fact. Promotion requires a named human.")
    L.append("")
    L.append("## Claim audit")
    L.append("")
    L.append("| claim_id | verdict | excerpt | excerpt_hash |")
    L.append("|---|---|---|---|")
    for c in note["body"]["claims_audit"]:
        L.append("| `%s` | %s | %s | `%s` |" % (
            c["claim_id"], c["verdict"], c["excerpt"].replace("|", "\\|"),
            c["excerpt_hash"][:16]))
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# the two-phase engine
# ---------------------------------------------------------------------------
def _resolve_vault_adapter(registry=None, available_tools=None, local_caps=None):
    """Resolve the vault_write op through the f2-04 registry. Returns the Adapter
    (always non-None — the op has the builtin trust_quarantine terminal)."""
    if registry is None and AR is not None:
        registry = AR.AdapterRegistry(available_tools=available_tools or [],
                                      local_caps=local_caps or [])
    if registry is None:
        return None
    return registry.resolve("vault_write")


def _is_builtin_sidecar(adapter) -> bool:
    """True when the resolved provider is the .trust quarantine sidecar terminal
    (no real vault backend) — drives the documented degradation."""
    if adapter is None:
        return True
    return getattr(adapter, "provider_id", "") == "trust_quarantine"


def _write_note(note: dict, *, out_dir: str, destination: str, use_rep_dir: bool) -> str:
    """Write the rendered note. A dry run or sidecar NEVER touches a real vault path: it
    persists the quarantine representation (.md + .json) under out_dir. Returns the path."""
    if use_rep_dir:
        base = os.path.join(out_dir, "vault-quarantine", note["note_id"])
        os.makedirs(os.path.dirname(base), exist_ok=True)
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(note, fh, indent=2)
            fh.write("\n")
        destination = base + ".md"
    else:  # pragma: no cover - a live vault write needs a bound MCP/local backend
        os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "w", encoding="utf-8") as fh:
        fh.write(render_note_md(note))
    return destination


def two_phase_vault_write(run_result: dict, *, out_dir: str, registry=None,
                          available_tools=None, local_caps=None, vault_path: str = "",
                          travel_class: str = "", sensitivity: str = "internal",
                          supersedes=None, expiry=None, opt_in_classes=None,
                          claims=None, dry_run: bool = True) -> dict:
    """Phase 1 — write the derived-provenance note to the QUARANTINE zone (or degrade
    to the .trust sidecar). Returns the §7.7 write-back record. Never promotes."""
    run_id = run_result.get("run_id") or ""
    artifact_id = _artifact_id(run_result)
    travel_class = (travel_class or "").lower()
    opt_in = tuple(opt_in_classes) if opt_in_classes is not None else DEFAULT_OPT_IN_CLASSES

    record = {
        "schema": "office-trust-layer/vault-writeback/3",
        "schema_version": SCHEMA_VERSION,
        "produced_by": "trust-review/vault_writeback.py (AV-02)",
        "produced_at": _now_iso(),
        "run_id": run_id,
        "artifact_id": artifact_id,
        "travel_class": travel_class,
        "phase": PHASE_QUARANTINE,
        "dry_run": bool(dry_run),
    }

    # ---- opt-in by travel class ---------------------------------------------------
    if travel_class not in opt_in:
        record.update({
            "written": False,
            "skipped": True,
            "skip_reason": ("write-back is opt-in by travel class; %r is not in the opt-in "
                            "set %s — nothing written, no corpus touched"
                            % (travel_class or "<unset>", list(opt_in))),
        })
        return record

    # ---- resolve the backend through the registry ---------------------------------
    vault_path = vault_path or os.environ.get("TRUST_CORPUS_DIR", "")  # default corpus; note lands in the quarantine zone
    adapter = _resolve_vault_adapter(registry, available_tools, local_caps)
    has_vault = (not _is_builtin_sidecar(adapter)) and bool(vault_path)
    binding = adapter.binding() if adapter is not None else "fallback:trust_quarantine"
    provider_id = getattr(adapter, "provider_id", "trust_quarantine") if adapter else "trust_quarantine"

    # ---- build the note + rollback pointer ----------------------------------------
    rollback_pointer = ".trust/%s/" % run_id if run_id else ".trust/"
    note = build_quarantine_note(
        run_result, run_id=run_id, artifact_id=artifact_id, supersedes=supersedes,
        expiry=expiry, travel_class=travel_class, sensitivity=sensitivity,
        rollback_pointer=rollback_pointer, claims=claims)

    # ---- choose channel + destination ---------------------------------------------
    if has_vault:
        channel = "vault"
        degraded = False
        zone = QUARANTINE_ZONE
        dest_rel = "%s/%s.md" % (zone, note["note_id"])
        destination = os.path.join(vault_path, dest_rel) if vault_path else dest_rel
        degrade_note = ""
    else:
        # documented degradation: no vault backend / no vault path => .trust sidecar,
        # STILL never auto-promoted.
        channel = "sidecar"
        degraded = True
        zone = "%s (.trust sidecar)" % QUARANTINE_ZONE
        destination = os.path.join(out_dir, "vault-quarantine", note["note_id"] + ".md")
        degrade_note = ("no vault backend resolved (provider=%s) or no vault_path configured "
                        "=> the derived note stays in the .trust quarantine sidecar and is "
                        "NEVER auto-promoted" % provider_id)

    # ---- write (dry-run writes the quarantine representation under out_dir) --------
    written_path = _write_note(note, out_dir=out_dir, destination=destination,
                               use_rep_dir=dry_run or channel == "sidecar")

    record.update({
        "written": True,
        "skipped": False,
        "channel": channel,
        "degraded": degraded,
        "degrade_note": degrade_note,
        "provider_id": provider_id,
        "adapter_binding": binding,
        "zone": zone,
        "destination": destination,
        "written_representation": written_path,
        "note_id": note["note_id"],
        "frontmatter": note["frontmatter"],
        # surfaced top-level for easy assertion / manifest embedding
        "source_authority": note["frontmatter"]["source_authority"],
        "supersedes": note["frontmatter"]["supersedes"],
        "expiry": note["frontmatter"]["expiry"],
        "run_hash": note["frontmatter"]["run_hash"],
        "rollback_pointer": note["frontmatter"]["rollback_pointer"],
        "dedupe_key": note["frontmatter"]["dedupe_key"],
        "claim_dedupe_keys": note["frontmatter"]["claim_dedupe_keys"],
        "approved_by": None,
        "approved_at": None,
        "citable": False,   # quarantine is NEVER citable
    })
    return record


def promote(record: dict, *, approver: str, approved_at: str = "", vault_path: str = "",
            out_dir: str = "", dry_run: bool = True) -> dict:
    """Phase 2 — promote a quarantined note into the CITABLE zone. REQUIRES a named
    human approver; without one the note stays quarantined and never citable."""
    out = dict(record)
    if not approver or not str(approver).strip():
        out.update({
            "promoted": False,
            "phase": PHASE_QUARANTINE,
            "citable": False,
            "promotion_error": ("promotion requires a NAMED human approver; none given — "
                                "note stays quarantined, never citable, never auto-promoted"),
        })
        return out

    approved_at = approved_at or _now_iso()
    note_id = record.get("note_id", "")
    citable_rel = "%s/%s.md" % (CITABLE_ZONE, note_id)
    destination = os.path.join(vault_path, citable_rel) if vault_path else citable_rel

    fm = dict(record.get("frontmatter") or {})
    fm["phase"] = PHASE_PROMOTED
    fm["approved_by"] = approver
    fm["approved_at"] = approved_at

    # move the representation (dry-run / sidecar) so the audit trail shows the move
    moved_path = None
    if (dry_run or record.get("channel") == "sidecar") and out_dir:
        prom_dir = os.path.join(out_dir, "vault-promoted")
        os.makedirs(prom_dir, exist_ok=True)
        moved_path = os.path.join(prom_dir, note_id + ".json")
        with open(moved_path, "w", encoding="utf-8") as fh:
            json.dump({"frontmatter": fm, "promoted_from": record.get("zone")}, fh, indent=2)
            fh.write("\n")

    out.update({
        "promoted": True,
        "phase": PHASE_PROMOTED,
        "citable": True,
        "approved_by": approver,
        "approved_at": approved_at,
        "zone": CITABLE_ZONE,
        "destination": destination,
        "promoted_representation": moved_path,
        "frontmatter": fm,
        "promotion_error": "",
    })
    return out


# ---------------------------------------------------------------------------
# verifier-facing helpers (used by internal_corpus_adapter + the fixture)
# ---------------------------------------------------------------------------
def is_quarantined_derived(frontmatter: dict) -> bool:
    """True iff this note is a DERIVED-provenance note that has NOT been promoted —
    i.e. the internal-corpus verifier must treat it as audit metadata, never a
    primary fact."""
    if not isinstance(frontmatter, dict):
        return False
    return (frontmatter.get("source_authority") == SOURCE_AUTHORITY
            and frontmatter.get("phase") != PHASE_PROMOTED)


def is_citable(frontmatter: dict) -> bool:
    """A derived note is citable ONLY once a named human promoted it (phase=promoted
    with a non-empty approved_by)."""
    if not isinstance(frontmatter, dict):
        return False
    return (frontmatter.get("phase") == PHASE_PROMOTED
            and bool(frontmatter.get("approved_by")))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AV-02 two-phase vault write-back (conventions §7.7)")
    ap.add_argument("--result", required=True, help="trust-review review-result.json")
    ap.add_argument("--out", required=True, help="output dir (.trust/<run>/)")
    ap.add_argument("--travel", default="internal", help="travel class")
    ap.add_argument("--vault-path", default="", help="configured vault root (else sidecar)")
    ap.add_argument("--tools", default="", help="comma/space available MCP tool names (probe)")
    ap.add_argument("--supersedes", default="", help="comma-separated note ids this supersedes")
    ap.add_argument("--expiry", default="", help="ISO expiry or staleness-policy")
    ap.add_argument("--opt-in", default="", help="comma list of opted-in travel classes")
    ap.add_argument("--promote", default="", help="approver name (Phase 2) — promotes the note")
    args = ap.parse_args(argv)

    result = _load(args.result)
    tools = [t for t in re.split(r"[,\s]+", args.tools) if t]
    opt_in = [c for c in re.split(r"[,\s]+", args.opt_in) if c] or None
    rec = two_phase_vault_write(
        result, out_dir=args.out, available_tools=tools, vault_path=args.vault_path,
        travel_class=args.travel, supersedes=[s for s in args.supersedes.split(",") if s],
        expiry=args.expiry or None, opt_in_classes=opt_in, dry_run=True)
    if args.promote:
        rec = promote(rec, approver=args.promote, vault_path=args.vault_path,
                      out_dir=args.out, dry_run=True)
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
