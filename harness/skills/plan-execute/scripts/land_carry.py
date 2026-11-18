"""R6 §17.1 / R7 §18.2 — keep a land's owner answers and review passes across a
main move that only harvested runtime output (the harvest route), or, with the
phase-2 switch `on`, that moved code touching none of the plan's files (the code
route) (plan-isolation-contract.md §17.1, §18.2).

ONE decision, `decide`, called once per new candidate by `land_gate.step_gate`.
Reset is the default; carry is the exception, and needs every check below. The
first check that fails names the reason, in this order (§18.2):

    legacy-state, main-unmoved, not-ancestor, classify-error,
    [code route only: harness-class (switch not `on`), force-reset-path],
    plan-path-touched, patch-id, plan-side-changed, blob, gate-set-key, registry

In `shadow` the code route still resets with `harness-class`, and `shadow` holds
what `on` would have decided. Any other git error or missing field is `git-error`.
Paths are classified by `scripts/gearbox-classify.py`'s `classify_paths`, the same
function harvest uses, so there is no second path list here.

On a carry only SETTLED records of REVIEW gates (a `pass`) and accepted on-box
answers (`verified-on-box`) are kept, re-stamped with the new key. Every test
gate re-runs on the new tree: tests read live-state files (`_plans/*/spec.json`),
so a harvest can turn one red.

"Old" always means the BASIS candidate, the one `decide` compared: a record is
kept only when it is stamped with the basis's key (`_basis_key`), and the ack is
re-bound only when it is bound to the basis's candidate. `st["gate_key"]` is not
read for this: `step_worktree` pops it whenever main moved before `land` ran.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
from fnmatch import fnmatch
from pathlib import Path

import land_gate_split as lgs
import run_state_io as rsi
import safe_json as sj
import verify_pass as vp
import shipping as shp
from land_state import now, save
from worktree import git

CARRY_VERSION = 2
_CLASSIFIER = Path(__file__).resolve().parents[3] / "scripts" / "gearbox-classify.py"
_REGISTRY_PATHS = (".claude/eval-gates.json",
                   "skills/plan-execute/references/eval-gates.default.json")
_SETTLED_ONBOX = "verified-on-box"
# §18.8: a code-route move touching any of these never carries. Matched by base
# name at any depth (more resets, never fewer); `settings.json` is handled apart.
_FORCE_PATHS = ("scripts/gearbox-classify.py", "scripts/deploy.pathspec", *_REGISTRY_PATHS)
_FORCE_NAMES = ("Makefile", "model-routing.yaml", "package-lock.json", "uv.lock", "poetry.lock",
                "Pipfile.lock", "pyproject.toml", "pytest.ini", "setup.cfg", "conftest.py")
_FORCE_GLOBS = ("requirements*.txt", "*.lock")


class _GitError(Exception):
    pass


def _g(args, cwd):
    rc, out, err = git(args, cwd, strip=False)
    if rc != 0:
        raise _GitError(f"git {' '.join(args[:3])}: {(err or out).strip()[:200]}")
    return out


def _z(out):
    return [p for p in out.split("\0") if p]


def _classifier():
    """`gearbox-classify.py` by file path: its name has a hyphen. Raises on failure."""
    spec = importlib.util.spec_from_file_location("gearbox_classify", _CLASSIFIER)
    if spec is None or spec.loader is None:
        raise _GitError(f"cannot load {_CLASSIFIER}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, mod.load_pathspec(str(_CLASSIFIER.parent / "deploy.pathspec"))


def _patch_id(root, main, plan_head):
    # Three dots: the plan's own change from the merge base. After `step_sync` this
    # equals `<main>..<plan_head>`; inside `_resync` (no sync) it still is the plan.
    diff = subprocess.run(["git", "diff", f"{main}...{plan_head}", "--", ".",
                           ":(exclude)_plans"], cwd=root, capture_output=True, check=True)
    pid = subprocess.run(["git", "patch-id", "--stable"], cwd=root, input=diff.stdout,
                         capture_output=True, check=True)
    return pid.stdout.decode().split()[0] if pid.stdout.strip() else ""


def _tree(root, sha, paths=None):
    """{path: blob} of `sha`, restricted to `paths` when given."""
    rows = (r.split("\t", 1) for r in _z(_g(["ls-tree", "-r", "-z", "--full-tree", sha], root)))
    tree = {p: meta.split()[2] for meta, p in rows}
    return tree if paths is None else {p: tree.get(p) for p in sorted(paths)}


def basis(plan_dir, ctx, st, gate_set_key):
    """The facts a later candidate is compared against (§17.1 "State fields")."""
    root, main, head = ctx["root"], st["expected"], st["plan_head"]
    return {
        "carry_version": CARRY_VERSION, "main_head": main, "plan_head": head,
        "merge_sha": st["merge_sha"],
        "plan_commits": sorted(_g(["rev-list", "--no-merges", head, f"^{main}"], root).split()),
        "plan_paths": sorted(_z(_g(["diff", "--no-renames", "--name-only", "-z",
                                     f"{main}...{head}"], root))),
        "patch_id": _patch_id(root, main, head),
        "gate_set_key": gate_set_key,
        "registry_blobs": _tree(root, st["merge_sha"], _REGISTRY_PATHS),
        "outer_registry_digest": _outer_registry_digest(plan_dir),
    }


def _outer_registry_digest(plan_dir):
    """Digest of the merged outer gate registry, read STRICTLY: an absent file is
    absent, as in `shipping.eval_gates`, but an unreadable, malformed or non-object
    file is None, which the `registry` check always fails (§17.1 invariant 1)."""
    merged = {}
    for path in (shp._bundled_default("eval-gates.default.json"),
                 shp.find_project_root(plan_dir) / ".claude" / "eval-gates.json"):
        try:
            os.lstat(path)   # not exists(): a dangling symlink is present, then unreadable
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            return None
        try:
            data = sj.load_dict(path.read_bytes())   # None: bad JSON, bad UTF-8, non-object
        except OSError:
            return None
        if data is None:
            return None
        merged.update(data)
    return hashlib.sha256(json.dumps(merged, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _show(root, rev):
    """settings.json text at `rev`, None only when the file is absent there.
    Any other git failure raises, so decide() resets with git-error, not a
    harness-class reset that hides the cause. Strict UTF-8: a bad byte raises."""
    if rev is None:
        return None
    ls = subprocess.run(["git", "ls-tree", "--name-only", rev, "--", "settings.json"],
                        cwd=root, capture_output=True, text=True)
    if ls.returncode != 0:
        raise RuntimeError(f"git ls-tree {rev}: {ls.stderr.strip()}")
    if not ls.stdout.strip():
        return None
    p = subprocess.run(["git", "show", f"{rev}:settings.json"], cwd=root, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"git show {rev}:settings.json: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout.decode("utf-8")


def _units(root, old_main, new_main):
    """(paths, before, after) to classify: every commit of old..new against EACH
    of its parents, as `git log -m --name-only` lists them (§17.1 invariant 4).
    Never only the net diff: a source change and its revert still reset, and so
    does a merge pair that selects then restores a harness file. A merge also
    re-reports what its side parent lacked; merges on main are rare, and that
    only adds resets. `--no-renames` lists both sides of a rename."""
    units = []
    for line in _g(["rev-list", "--parents", f"{old_main}..{new_main}"], root).splitlines():
        commit, *parents = line.split()
        if not parents:   # a root commit (unrelated history merged in): diff against empty
            args = ["diff-tree", "-r", "--root", "--no-commit-id", "--no-renames",
                    "--name-only", "-z", commit]
            units.append((_z(_g(args, root)), None, commit))
        for parent in parents:
            args = ["diff", "--no-renames", "--name-only", "-z", parent, commit]
            units.append((_z(_g(args, root)), parent, commit))
    return units


def _range_classes(root, old_main, new_main):
    """Classify every unit of `_units`. -> (paths, counts, harness paths, errors).
    `paths` is §18.2's moved paths: every path of every commit, against each parent."""
    gc, spec = _classifier()
    seen, counts, errors = set(), {}, []
    for paths, before, after in _units(root, old_main, new_main):
        texts = ((_show(root, before), _show(root, after)) if "settings.json" in paths
                 else (None, None))
        res = gc.classify_paths(paths, texts[0], texts[1], spec)
        errors += res["errors"]
        for klass in ("live_state", "churn", "harness"):
            for p in res[klass]:
                if (klass, p) not in seen:
                    seen.add((klass, p))
                    counts[klass] = counts.get(klass, 0) + 1
    return {p for _, p in seen}, counts, {p for k, p in seen if k == "harness"}, errors


def force_reset_hits(paths, harness):
    """§18.8 — the moved paths on the force-reset list, sorted. `settings.json` is a
    hit only when some commit changed a non-churn key (it is in `harness`, the paths
    `classify_paths` called harness): churn keys arrive in many harvests."""
    def hit(p):
        name = p.rsplit("/", 1)[-1]
        return (p.startswith("githooks/") or p in _FORCE_PATHS or name in _FORCE_NAMES
                or any(fnmatch(name, g) for g in _FORCE_GLOBS))
    return sorted(p for p in paths
                  if (p in harness if p == "settings.json" else hit(p)))


def _mode():
    """§18.11's switch, `land_phase2.mode()`. Unknown, unreadable, or no `land_phase2`
    yet (s04 builds it) is `off`: a fault can never turn the code route on."""
    try:
        import land_phase2  # noqa: PLC0415
        mode = land_phase2.mode()
    except Exception:  # noqa: BLE001
        return "off"
    return mode if mode in ("shadow", "on") else "off"


def _checks(plan_dir, ctx, st, b, n, out):
    """Yield (reason, failed?, classes) in §18.2's order, lazily. `harness-class`
    fails exactly when the code route is taken; `decide` reads it per mode.
    Sets `out["moved_paths"]` and `out["route"]` once the range is classified."""
    root, slug = ctx["root"], ctx["slug"]
    # No main move, yet a new candidate (a rebuilt merge, a new gate set): nothing
    # was harvested, so there is nothing to carry across. Approving a merge means
    # approving THAT merge (land_state.candidate).
    yield "main-unmoved", b["main_head"] == n["main_head"], {}
    rc = git(["merge-base", "--is-ancestor", b["main_head"], n["main_head"]], root)[0]
    if rc not in (0, 1):
        raise _GitError("merge-base --is-ancestor")
    yield "not-ancestor", rc != 0, {}
    try:
        paths, counts, harness, errors = _range_classes(root, b["main_head"], n["main_head"])
    except Exception as e:  # noqa: BLE001 — §18.2 check 4: listing the range failed
        out["error"] = str(e)[:300]
        yield "classify-error", True, {}
        return
    out.update(moved_paths=sorted(paths), route="code" if harness else "harvest")
    yield "classify-error", bool(errors), counts
    yield "harness-class", bool(harness), counts
    if harness:
        yield "force-reset-path", bool(force_reset_hits(paths, harness)), counts
    plan_paths = set(b["plan_paths"]) | set(n["plan_paths"])
    own = f"_plans/{slug}/"
    yield "plan-path-touched", any(p in plan_paths or p.startswith(own) for p in paths), counts
    yield "patch-id", n["patch_id"] != b["patch_id"], counts
    yield "plan-side-changed", (n["plan_commits"] != b["plan_commits"]
                                or st.get("resolution_required") is not False), counts
    yield "blob", (_tree(root, b["merge_sha"], plan_paths)
                   != _tree(root, n["merge_sha"], plan_paths)), counts
    yield "gate-set-key", n["gate_set_key"] != b["gate_set_key"], counts
    yield "registry", (n["registry_blobs"] != b["registry_blobs"]
                       or n["outer_registry_digest"] != b["outer_registry_digest"]
                       or n["outer_registry_digest"] is None), counts


def _first_failure(plan_dir, ctx, st, b, n, out, mode, shadowed):
    """The first failing reason, or None for carry. A failed `harness-class` (the
    code route) stops here in `off`; in `shadow` it is noted and the checks go on
    as `on` would run them, so `decide` can log both answers."""
    for reason, failed, classes in _checks(plan_dir, ctx, st, b, n, out):
        out["classes"] = classes
        if not failed:
            continue
        if reason != "harness-class" or mode == "off":
            return reason
        if mode == "shadow":
            shadowed.append(True)
    return None


def decide(plan_dir, ctx, st, gate_set_key):
    """carry or reset for the candidate now in `st`, against `st["carry_basis"]`.

    -> {"decision", "reason", "classes", "carry_version", "basis", "route",
    "moved_paths"} plus `shadow` in shadow mode; `basis` is the new candidate's
    (None when it could not be read, so the next move resets)."""
    out = {"decision": "reset", "reason": None, "classes": {}, "route": None,
           "moved_paths": [], "carry_version": CARRY_VERSION, "basis": None}
    mode, shadowed = _mode(), []
    try:
        out["basis"] = n = basis(plan_dir, ctx, st, gate_set_key)
        b = st.get("carry_basis")
        reason = ("legacy-state" if _legacy(b)
                  else _first_failure(plan_dir, ctx, st, b, n, out, mode, shadowed))
    except Exception as e:  # noqa: BLE001 — any failure at all is a reset (§18.2 inv. 2)
        out["error"] = str(e)[:300]
        reason = "git-error"
    if shadowed:   # §18.11: shadow resets exactly as today and logs what `on` would do
        out["shadow"] = {"decision": "reset" if reason else "carry", "reason": reason,
                         **({"error": out.pop("error")} if "error" in out else {})}
        reason = "harness-class"
    out.update(decision="reset" if reason else "carry", reason=reason)
    return out


_KEY_PARTS = ("main_head", "plan_head", "merge_sha", "gate_set_key")
_ACK_PARTS = ("main_head", "plan_head", "merge_sha")


def _legacy(b):
    """A basis today's code (or a corrupt file) wrote: never carried from. The four
    key parts must be there, or the key its records were stamped with is unknown."""
    return (not isinstance(b, dict) or b.get("carry_version") != CARRY_VERSION
            or not all(isinstance(b.get(k), str) and b[k] for k in _KEY_PARTS))


def _basis_key(b):
    """The `gate_key` `land_gate.step_gate` stamped on the records of the candidate
    `b` describes (same four parts, same order). Read from the basis, never from
    `st["gate_key"]`: `land_steps.step_worktree` pops that when main moved before
    this `land`, which is the common carry, and only `land_push._resync` keeps it."""
    return ":".join(b[k] for k in _KEY_PARTS)


def _keep(rec, gid, gates, settled, old_key, key, phase2_open=False):
    """The record copied to the new `key` (GAT-01: by copy, the gate is never invoked), or None.
    A record settled under any key but the basis candidate's is stale: drop it."""
    if isinstance(rec, dict) and rec.get("gate_key") == old_key and gid in gates \
            and rec.get("outcome") == settled:
        return lgs.carry(rec, gates[gid], key, old_key, phase2_open=phase2_open)
    return None


def _phase2_open(st):
    """§18.3/§18.11: a non-review gate carries only in phase 2 with the setting `on`."""
    try:
        import land_phase2  # noqa: PLC0415
        return land_phase2.mode() == "on" and land_phase2.is_open(st)
    except Exception:  # noqa: BLE001 — a fault never opens phase 2
        return False


def apply(plan_dir, ctx, st, gates, key, verdict):
    """Act on `verdict` for the new `key`, save, and log `land_carry` — all before
    `step_gate` can park or return (§17.1 invariant 14)."""
    by_id, old_basis, legacy = dict(gates), st.get("carry_basis"), _legacy(st.get("carry_basis"))
    if legacy:
        verdict = {**verdict, "decision": "reset", "reason": verdict["reason"] or "legacy-state"}
    old_key = st.get("gate_key") if legacy else _basis_key(old_basis)
    from_main = None if legacy else old_basis["main_head"]
    kept, ack, p2, would = {}, st.get("ack"), _phase2_open(st), set()
    if verdict["decision"] == "carry":
        for field, settled in (("skill_gates", "pass"), ("argv_gates", "pass"),
                               ("on_box_gates", _SETTLED_ONBOX)):
            recs = st.get(field) or {}
            kept[field] = {gid: new for gid, rec in recs.items()
                           if (new := _keep(rec, gid, by_id, settled, old_key, key, p2))}
            # read before st.update replaces the records: what `on` in phase 2 would carry
            would |= {gid for gid, rec in recs.items()
                      if _keep(rec, gid, by_id, settled, old_key, key, True)}
        if ack and all(ack.get(k) == old_basis[k] for k in _ACK_PARTS):
            ack.update({"plan_head": st.get("plan_head"), "main_head": st.get("expected"),
                        "merge_sha": st.get("merge_sha")})
            ack.setdefault("carries", []).append(
                {"at": now(),
                 "from_key": old_key, "to_key": key, "main_head": st.get("expected"),
                 "merge_sha": st.get("merge_sha"), "route": verdict.get("route"),
                 "moved_paths": verdict.get("moved_paths") or []})
            # §18.9: what moved on main since the owner's ack, never shortened under it.
            st["moved_since_ack"] = sorted({p for c in ack["carries"]
                                            for p in c.get("moved_paths") or []})
        elif ack:   # bound to a candidate `decide` never compared: not re-bound
            st["invalidated_from"], st["ack"] = ack, None
    else:
        kept = {"skill_gates": {}, "argv_gates": {}, "on_box_gates": {}}
        if ack:
            st["invalidated_from"], st["ack"] = ack, None
    if not st.get("ack"):
        st.pop("moved_since_ack", None)
    extra = {k: verdict[k] for k in ("route", "moved_paths", "shadow") if k in verdict}
    carried = sorted({g for recs in kept.values() for g in recs})
    if verdict["decision"] == "carry" and _mode() == "shadow":   # log what `on` would carry
        extra["shadow_carried"] = sorted(would - set(carried))
    # GAT-01: the record of the split, read by the land brief and by `finish`. `ci_only_gates`
    # holds the carried gates CI is the only check of: a review gate is never CI-checked.
    ci_only = [g for g in carried if lgs.phase2(by_id[g]) == "carry" and not vp.is_review_gate(by_id[g])]
    # ONE save: a crash between two saves would leave a new gate_key beside a stale carried record.
    st.update({"gate_key": key, **kept,
               "last_carry": {"decision": verdict["decision"], "reason": verdict["reason"],
                              **extra},
               "carried_gates": carried, "ci_only_gates": ci_only,
               "ran_gates": sorted(set(by_id) - set(carried)),
               "carried_from_main": from_main if carried else None})
    if verdict.get("basis"):
        st["carry_basis"] = verdict["basis"]
    else:
        st.pop("carry_basis", None)
    if verdict["decision"] == "reset":   # §18.1: a reset ends phase 2 (marker first) and goes on as phase 1,
        # before the save and the log write: a failed log write must not skip the close
        import land_phase2 as lp2  # noqa: PLC0415 — lp2 imports this module
        if lp2.is_open(st):
            lp2.leave(plan_dir, ctx, st, "reset", verdict["reason"])
    save(plan_dir, ctx, st)
    rsi.log_event(plan_dir, "land_carry", session_ids=[], slug=ctx["slug"],
                  decision=verdict["decision"], reason=verdict["reason"],
                  from_key=old_key, to_key=key, classes=verdict["classes"],
                  from_main=from_main, to_main=st.get("expected"),
                  carried=carried, rerun=sorted(set(by_id) - set(carried)),
                  carry_version=CARRY_VERSION, **extra,
                  **({"error": verdict["error"]} if verdict.get("error") else {}))


def drop_unsettled(plan_dir, ctx, st, unsettled, key):
    """A re-run gate that did not settle green on a CARRIED candidate resets the
    ack before `step_gate` parks (§17.1 invariant 13): the carry was conditional
    on the re-run. The ack moves to `invalidated_from`, as on any reset."""
    ack = st.get("ack")
    if unsettled and ack and (ack.get("carries") or [{}])[-1].get("to_key") == key:
        st["invalidated_from"], st["ack"] = ack, None
        log_reset(plan_dir, ctx, "gate-result", gates=sorted(unsettled), to_key=key)


def log_reset(plan_dir, ctx, reason, **fields):
    """A `land_carry` reset decided after the gates ran (§17.1 invariant 13)."""
    rsi.log_event(plan_dir, "land_carry", session_ids=[], slug=ctx["slug"],
                  decision="reset", reason=reason, carry_version=CARRY_VERSION, **fields)
