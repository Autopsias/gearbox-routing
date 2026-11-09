"""Reduce an append-only ledger to the latest row per key, THEN filter.

WHY THE ORDER: a filter applied first hides the row that replaced an old one, so
the old row survives as the "latest". Plan reviews found that fault in 5 plans.
A key updated from `open` to `fixed` must not come back as `open`.

Selecting the row TYPE (`kind == "finding"`, `event == "x"`) belongs before this
call: it picks which ledger rows are in play, it does not judge their state.
"""


def latest_then_filter(rows, key, keep=None):
    """The latest row of each key that passes `keep`, in first-seen key order.

    `rows` run oldest first; the caller sorts them when file order is not age.
    A tie has no special case: the row later in `rows` wins, so a stable sort
    leaves the later line of the file as the latest.
    Malformed rows are the caller's decision: this has no guard, so whatever
    `key` or `keep` raises reaches the caller, and a reader that fails closed on
    a bad row keeps doing so.
    """
    latest = {}
    for row in rows:
        latest[key(row)] = row
    return [row for row in latest.values() if keep is None or keep(row)]
