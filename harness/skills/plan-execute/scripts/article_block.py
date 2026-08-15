"""Read + atomically mutate per-article state in PLAN.html.

PLAN.html is canonical. Each <article> is wrapped in unique comment anchors:
    <!-- ARTICLE:<id>:BEGIN -->  ... <article id="<id>" ...> ... </article>  <!-- ARTICLE:<id>:END -->

Mutation strategy (P1/P3): locate the block between the BEGIN/END anchors,
derive a new block from the old one via targeted substitutions (data-status,
data-updated, the header pill, the notes-content), then rewrite the WHOLE file
once (atomic temp+rename). One transactional write per closeout — never N
non-atomic edits.

This module never dispatches Tasks and never touches manifest/run_state. It only
reads and writes PLAN.html.
"""

import html as _html
import os
import re
import tempfile
from datetime import date
from pathlib import Path

NOTE_MAX = 500
VALID_STATUSES = {
    "TODO",
    "DOING",
    "DONE",
    "BLOCKED",
    "DEFERRED",
    "WONTFIX",
    "AWAITS_REVIEW",
    "PARTIAL",
}


class AnchorError(Exception):
    pass


def _today():
    return date.today().isoformat()


def _begin(aid):
    return f"<!-- ARTICLE:{aid}:BEGIN -->"


def _end(aid):
    return f"<!-- ARTICLE:{aid}:END -->"


def extract_block(html_text, aid):
    """Return (start, end, block) for the region INCLUDING the anchors.

    Preflight: exactly one BEGIN and one END comment for `aid`, in order.
    """
    b, e = _begin(aid), _end(aid)
    if html_text.count(b) != 1:
        raise AnchorError(f"expected exactly 1 '{b}', found {html_text.count(b)}")
    if html_text.count(e) != 1:
        raise AnchorError(f"expected exactly 1 '{e}', found {html_text.count(e)}")
    start = html_text.index(b)
    end = html_text.index(e) + len(e)
    if end <= start:
        raise AnchorError(f"END anchor precedes BEGIN anchor for {aid!r}")
    return start, end, html_text[start:end]


def preflight(html_text, aid):
    """Validate the block is well-formed enough to mutate. Raises AnchorError."""
    _, _, block = extract_block(html_text, aid)
    if 'data-status="' not in block:
        raise AnchorError(f"block {aid!r} missing data-status attribute")
    if 'data-updated="' not in block:
        raise AnchorError(f"block {aid!r} missing data-updated attribute")
    if '<span class="pill status-' not in block:
        raise AnchorError(f"block {aid!r} missing status pill")
    if 'class="notes-content"' not in block:
        raise AnchorError(f"block {aid!r} missing notes-content")
    return True


def read_status(html_text, aid):
    _, _, block = extract_block(html_text, aid)
    m = re.search(r'data-status="([^"]*)"', block)
    return m.group(1) if m else "TODO"


def read_all_statuses(html_text):
    """Map every anchored article id -> current data-status."""
    out = {}
    for m in re.finditer(r"<!-- ARTICLE:([a-z0-9-]+):BEGIN -->", html_text):
        aid = m.group(1)
        try:
            out[aid] = read_status(html_text, aid)
        except AnchorError:
            continue
    return out


def _escape_note(text):
    text = str(text)
    if len(text) > NOTE_MAX:
        text = text[: NOTE_MAX - 1] + "…"
    return _html.escape(text, quote=False)


def _mutate_block(block, *, status, updated, note):
    if status not in VALID_STATUSES:
        raise AnchorError(f"invalid status {status!r}")

    # data-status (first occurrence — the article tag)
    block = re.sub(r'data-status="[^"]*"', f'data-status="{status}"', block, count=1)
    # data-updated (first occurrence)
    block = re.sub(r'data-updated="[^"]*"', f'data-updated="{updated}"', block, count=1)
    # header pill: <span class="pill status-XXX ...">TEXT</span>  (first occurrence)
    block = re.sub(
        r'(<span class="pill )status-\w+("[^>]*>)[^<]*(</span>)',
        rf"\1status-{status}\2{status}\3",
        block,
        count=1,
    )

    # notes-content: append a note paragraph
    if note:
        note_p = f'<p class="note"><strong>{updated}:</strong> {_escape_note(note)}</p>'
        # Replace the "No notes yet" placeholder if present, else insert before </div>
        if '<span class="empty">No notes yet.</span>' in block:
            block = block.replace(
                '<div class="notes-content"><span class="empty">No notes yet.</span></div>',
                f'<div class="notes-content">{note_p}</div>',
                1,
            )
        else:
            # insert before the closing </div> of the FIRST notes-content
            m = re.search(r'(<div class="notes-content">)(.*?)(</div>)', block, flags=re.DOTALL)
            if m:
                block = (
                    block[: m.start()]
                    + m.group(1)
                    + m.group(2)
                    + note_p
                    + m.group(3)
                    + block[m.end() :]
                )
    return block


# --------------------------------------------------------------------------
# RP-06 — the rendered plan change log.
#
# `_changelog.ndjson` is the record; this is its ONE rendered surface. Same
# comment-anchor + whole-file-rewrite discipline the article blocks use, so a
# plan amendment is visible on the page instead of living only in a sibling
# file nobody opens. Plans built before this section exists have no anchors —
# every writer here is a tolerant no-op on them, never an error.
# --------------------------------------------------------------------------
CHANGES_BEGIN = "<!-- PLAN-CHANGES:BEGIN -->"
CHANGES_END = "<!-- PLAN-CHANGES:END -->"
_CHANGES_LIST_RE = re.compile(
    r'(<ul class="hotspot-list" data-role="plan-changes">)(.*?)(</ul>)', re.DOTALL
)
_EMPTY_CHANGE_LI = (
    '<li class="hotspot-item"><span class="hotspot-title">No plan changes '
    "recorded — this plan has run as it was built.</span></li>"
)


def _change_li(entry):
    at = str(entry.get("at", ""))[:10] or "(undated)"
    op = str(entry.get("op", "change"))
    sid = str(entry.get("session") or "—")
    summary = str(entry.get("summary") or "")
    # The subcommand rides in the pill, not in the sentence — printing it twice
    # made the row read as a stutter on the rendered page.
    return (
        '<li class="hotspot-item"><span class="hotspot-title">'
        f"<strong>{_html.escape(at, quote=False)}</strong> · "
        f"<code>{_html.escape(sid, quote=False)}</code> — {_escape_note(summary)}"
        f'</span><span class="hotspot-pill low">{_html.escape(op, quote=False)}</span></li>'
    )


def render_change_log(entries):
    """The <li> rows for the Plan-changes list, oldest first."""
    return "".join(_change_li(e) for e in entries) or _EMPTY_CHANGE_LI


def set_change_log(html_text, entries):
    """Pure: return `html_text` with the Plan-changes list replaced.

    Unchanged (no error) when the page predates the section — old plans keep
    working, they just have nowhere to show the log.
    """
    if CHANGES_BEGIN not in html_text or CHANGES_END not in html_text:
        return html_text
    start = html_text.index(CHANGES_BEGIN)
    end = html_text.index(CHANGES_END) + len(CHANGES_END)
    region = html_text[start:end]
    new_region, n = _CHANGES_LIST_RE.subn(
        lambda m: m.group(1) + render_change_log(entries) + m.group(3), region, count=1
    )
    if not n:
        return html_text
    return html_text[:start] + new_region + html_text[end:]


def write_change_log(html_path, entries):
    """Atomically rewrite PLAN.html's Plan-changes list. False if absent."""
    html_path = Path(html_path)
    text = html_path.read_bytes().decode("utf-8")
    new_text = set_change_log(text, entries)
    if new_text == text:
        return False
    _atomic_write_html(html_path, new_text)
    return True


def _atomic_write_html(html_path, new_text):
    fd, tmp = tempfile.mkstemp(dir=str(html_path.parent), prefix=".tmp-", suffix=".html")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(new_text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, html_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


_NOTES_RE = re.compile(r'<div class="notes-content">.*?</div>', re.DOTALL)
_SHIP_ATTR_RE = re.compile(r'data-shipping="[^"]*"')
_SHIP_PILL_RE = re.compile(
    r'<span class="pill ship-badge status-\w+"[^>]*data-role="ship-badge"[^>]*>[^<]*</span>'
)


def carry_over_state(old_html, new_html):
    """Carry per-article runtime state from `old_html` onto freshly rendered
    `new_html`; returns the new text. Pure — writes nothing.

    This is the rebuild half of this module's mutation contract: everything
    `apply_mutation` and `update_shipping_badge` write into an article must
    survive `build_plan.py --rebuild --preserve-state`, because PLAN.html is
    the ONLY place per-session/item status lives and /plan-execute dispatches
    from it. Add a field to those writers and add it here, in the same file.

    Only ids anchored in BOTH documents are touched: a session/item ADDED by
    the rebuild keeps its freshly rendered TODO, and one REMOVED by the
    rebuild stays gone.
    """
    for m in re.finditer(r"<!-- ARTICLE:([a-z0-9-]+):BEGIN -->", old_html):
        aid = m.group(1)
        try:
            _, _, old_block = extract_block(old_html, aid)
            start, end, new_block = extract_block(new_html, aid)
        except AnchorError:
            continue  # id was added or removed by this rebuild
        # status + data-updated + the header pill, via the same writer the
        # runtime uses (raises on a corrupt status rather than silently
        # downgrading it to TODO — that silence is the bug this function fixes)
        um = re.search(r'data-updated="([^"]*)"', old_block)
        block = _mutate_block(
            new_block,
            status=read_status(old_html, aid),
            updated=um.group(1) if um else _today(),
            note=None,
        )
        # accumulated closeout notes + the post-session shipping badge
        for pattern in (_NOTES_RE, _SHIP_ATTR_RE, _SHIP_PILL_RE):
            prior = pattern.search(old_block)
            if prior:
                block = pattern.sub(lambda _m, _p=prior: _p.group(0), block, count=1)
        new_html = new_html[:start] + block + new_html[end:]
    # The plan change log is runtime state too — a re-render (mutation or
    # `--rebuild --preserve-state`) would otherwise reset it to the empty
    # section the template ships.
    prior_changes = _CHANGES_LIST_RE.search(old_html)
    if prior_changes:
        new_html = _CHANGES_LIST_RE.sub(
            lambda _m, _p=prior_changes: _p.group(0), new_html, count=1
        )
    return new_html


def _badge_class(badge):
    """Map a shipping badge to a CSS status suffix for color."""
    if badge.startswith("SHIP-FAILED"):
        return "BLOCKED"
    if badge in ("committed", "pushed", "PR-open", "deployed"):
        return "DONE"
    if badge in ("SHIP-PENDING",):
        return "DOING"
    return "TODO"


def update_shipping_badge(html_path, aid, badge):
    """Set the per-session shipping badge (``data-shipping`` + the ship-badge
    pill) for article ``aid``. Tolerant: a no-op (returns False) if the article
    has no badge slot, so plans built before this feature never break. The
    dashboard must never imply "shipped" when it didn't, so this is driven from
    the SAME write that updates ``_shipping_state``."""
    html_path = Path(html_path)
    text = html_path.read_bytes().decode("utf-8")
    try:
        start, end, block = extract_block(text, aid)
    except AnchorError:
        return False
    if 'data-shipping="' not in block and 'data-role="ship-badge"' not in block:
        return False

    safe = _html.escape(str(badge), quote=True)
    new_block = re.sub(r'data-shipping="[^"]*"', f'data-shipping="{safe}"', block, count=1)
    new_block = re.sub(
        r'(<span class="pill ship-badge )status-\w+("[^>]*data-role="ship-badge"[^>]*>)[^<]*(</span>)',
        rf"\1status-{_badge_class(badge)}\2{_html.escape(str(badge), quote=False)}\3",
        new_block,
        count=1,
    )
    if new_block == block:
        return False
    _atomic_write_html(html_path, text[:start] + new_block + text[end:])
    return True


def mutate_text(html_text, aid, *, status, note=None, updated=None):
    """Pure: return `html_text` with `aid`'s block updated. Writes nothing.

    The in-memory half of `apply_mutation`, so a caller assembling a whole
    generation before committing it (see plan_mutate.py) uses the SAME writer
    the runtime does instead of forking a second one. Raises AnchorError.
    """
    preflight(html_text, aid)
    start, end, block = extract_block(html_text, aid)
    new_block = _mutate_block(block, status=status, updated=updated or _today(), note=note)
    return html_text[:start] + new_block + html_text[end:]


def apply_mutation(html_path, aid, *, status, note=None, updated=None):
    """Atomically rewrite PLAN.html with `aid`'s block updated.

    Returns the new status. Raises AnchorError on preflight failure (file
    untouched in that case).
    """
    html_path = Path(html_path)
    # Byte-level read/write: no universal-newline translation, so CRLF or LF
    # line endings survive the edit cycle unchanged (P14).
    text = html_path.read_bytes().decode("utf-8")
    new_text = mutate_text(text, aid, status=status, note=note, updated=updated)
    _atomic_write_html(html_path, new_text)
    return status
