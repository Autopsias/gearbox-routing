"""retro_window.py — the date-window and digest helpers of retro_scan.py.

Split out of retro_scan.py to keep it under the 500-line limit; behaviour unchanged.
"""

import hashlib
from datetime import datetime, timezone


def utc_date(ts):
    """The UTC date (YYYY-MM-DD) of an ISO timestamp with Z or an offset, else None.
    A timestamp with no zone is read as UTC."""
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).date().isoformat()


def in_window(ts, window):
    """True when ts is dated inside window = (since, until), UTC, inclusive.
    window None means no bound; a missing or unreadable date is outside a bound."""
    if not window:
        return True
    day = utc_date(ts) if isinstance(ts, str) else None
    return bool(day) and day <= window[1] and not (window[0] and day < window[0])


def selection_digests(sessions):
    """The two selection digests: same files chosen, and same files with the same
    count of counted replies in each."""
    def digest(rows):
        return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()
    return {
        # proves the same files were chosen; it cannot notice the numbers moving
        "sha256_of_sorted_file_paths": digest(s["file"] for s in sessions),
        "sha256_of_sorted_path_and_in_window_reply_count": digest(
            f"{s['file']}\t{s['assistant_messages']}" for s in sessions),
    }
