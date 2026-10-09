"""Draft a reply to the customer. A person edits and sends it (AGENTS.md rule 2)."""
from app.llm import ask_model


def draft_reply(ticket_body, policy_summary):
    return ask_model("draft_reply", f"Write a polite reply to this ticket.\nTICKET:\n{ticket_body}\nPOLICY:\n{policy_summary}")
