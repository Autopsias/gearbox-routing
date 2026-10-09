"""One-off study for marketing: what do customers complain about? Ran once, 2026-09-02."""
import json
import random

from app.archive import load_tickets
from app.llm import ask_model

TOPICS = ["price", "claim_delay", "claim_refusal", "staff", "paperwork", "other"]
TONES = ["calm", "annoyed", "angry"]

tickets = load_tickets()                      # about 70,000
sample = random.Random(7).sample(tickets, 200)  # the full archive is too expensive on this model
rows = []
for t in sample:
    out = ask_model("complaint_study",
                    f"Read this ticket.\n{t.body}\nReturn JSON with: topic (one of {TOPICS}), tone (one of {TONES}), "
                    "mentions_competitor (true or false), threatens_to_leave (true or false).")
    rows.append(json.loads(out))
json.dump(rows, open("logs/complaint-study.json", "w"))
