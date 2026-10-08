"""Plan Achievement placement for add-session: which infographic group a new item or session joins.

Split out of plan_mutate.py (file-size ratchet, 2026-09-29). plan_mutate sets up sys.path for
build_plan before it imports this module."""
import build_plan as bp
from plan_journal import MutationError

_INFOGRAPHIC_GROUPS = bp.INFOGRAPHIC_GROUP_KEYS


def attach_to_infographic(spec, item_ids, group_name=None, session_id=None):
    """Add `item_ids` to a Plan-Achievement group. Returns (group_name, warning).

    A plan whose groups list SESSIONS (and no group lists items) gets `session_id` added
    instead: adding an `items` list to one group would make the coverage check count every
    other item as ungrouped and refuse the whole mutation."""
    info = spec.get("infographic") or {}
    groups = info.get(_INFOGRAPHIC_GROUPS.get(info.get("type"), ""), None)
    if not item_ids or not isinstance(groups, list) or not groups:
        return None, None
    wanted = group_name
    if wanted is None:
        # Default: the item's own category LABEL, when a group is named for it.
        labels = {c["key"]: c.get("label", "") for c in spec["categories"]}
        by_id = {it["id"]: it for it in spec["items"]}
        cats = {labels.get(by_id[i]["category"], "").strip().lower()
                for i in item_ids if i in by_id}
        names = {str(g.get("name", "")).strip().lower() for g in groups}
        hit = cats & names
        wanted = next(iter(hit)) if len(hit) == 1 else None
    if wanted is None:
        return None, (
            f"item(s) {', '.join(item_ids)} were not added to any Plan Achievement "
            f"group ({', '.join(str(g.get('name')) for g in groups)}), so that "
            "section's counters exclude them. Re-run with --infographic-group NAME "
            "to place them."
        )
    by_session = (session_id is not None and any(isinstance(g, dict) and g.get("sessions") for g in groups)
                  and not any(isinstance(g, dict) and g.get("items") for g in groups))
    for g in groups:
        if str(g.get("name", "")).strip().lower() == str(wanted).strip().lower():
            if by_session:
                if session_id not in g.setdefault("sessions", []):
                    g["sessions"].append(session_id)
                return g.get("name"), None
            g.setdefault("items", [])
            g["items"] += [i for i in item_ids if i not in g["items"]]
            return g.get("name"), None
    raise MutationError(
        f"--infographic-group {group_name!r} matches no group in the Plan "
        f"Achievement section. Groups: "
        f"{[str(g.get('name')) for g in groups]}"
    )
