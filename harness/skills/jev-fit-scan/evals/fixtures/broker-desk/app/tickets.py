"""Route a new ticket to a queue. Keyword rules, written in 2023."""
KEYWORDS = {
    "billing": ["invoice", "refund", "premium", "direct debit"],
    "claims": ["accident", "claim", "damage", "theft"],
    "policy_change": ["address", "add a driver", "cancel", "renew"],
}


def categorize(ticket_body):
    body = ticket_body.lower()
    for category, words in KEYWORDS.items():
        if any(w in body for w in words):
            return category
    return "other"  # a person routes these by hand
