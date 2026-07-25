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
    new_text = text[:start] + new_block + text[end:]
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
    return True


def apply_mutation(html_path, aid, *, status, note=None, updated=None):
    """Atomically rewrite PLAN.html with `aid`'s block updated.

    Returns the new status. Raises AnchorError on preflight failure (file
    untouched in that case).
    """
    html_path = Path(html_path)
    # Byte-level read/write: no universal-newline translation, so CRLF or LF
    # line endings survive the edit cycle unchanged (P14).
    text = html_path.read_bytes().decode("utf-8")
    preflight(text, aid)
    start, end, block = extract_block(text, aid)
    new_block = _mutate_block(block, status=status, updated=updated or _today(), note=note)
    new_text = text[:start] + new_block + text[end:]

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
    return status
