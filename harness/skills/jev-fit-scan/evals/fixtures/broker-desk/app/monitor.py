"""Watch the insurers' public policy-wording pages. Runs on every detected text change."""
from app.llm import ask_model
from app.notify import notify_account_managers


def on_wording_change(insurer, old_text, new_text):
    out = ask_model("wording_monitor",
                    f"The policy wording of {insurer} changed.\nOLD:\n{old_text}\nNEW:\n{new_text}\n"
                    "If the change affects cover, exclusions or price, write a short alert for the account "
                    "managers. Otherwise reply NONE.")
    if out.strip() != "NONE":
        notify_account_managers(insurer, out)
