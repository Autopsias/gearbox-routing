# broker-desk

The support desk of an insurance broker. About 70,000 tickets are in the archive, and about 250
new tickets arrive each working day. All ticket text is English.

## Rules
1. Ticket text is customer data. It goes to OpenAI under the company agreement. The operations
   lead decides on any other vendor. She asks for a written test plan with its cost and its data
   terms before she decides, so a proposal may go to her; no ticket leaves before she signs.
2. A reply to a customer is always written by the drafting model and sent by a person.

## Notes
- 2026-08-30: tickets that the keyword rules put in `other` wait for a person to route them. The
  median wait in that queue was 9 hours last month. `logs/categories.jsonl` holds one week.
- 2026-09-02: the complaint study (`scripts/complaint_study.py`) ran once, on a sample. Marketing
  asked for the full archive. Nobody approved the cost.
