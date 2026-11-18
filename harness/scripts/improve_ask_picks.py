"""Owner picks that are not AskUserQuestion calls, for improve_ask_classes.py.

Two more question forms, turned into the same entry dicts the main scan builds:
(a) an assistant message that ends in a numbered or lettered option list, answered
by an owner message that is only a pick ('1', 'option 2', 'B, fix everything');
(b) a mcp__ccd_turn__wrap_up button whose label the owner message repeats.
The caller classifies each entry with its own classifier, so its exclusions apply.
"""
import json
import re

OPT_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?\(?(\d{1,2}|[A-Ea-e])(?:\*\*)?[.)]\s*(?:\*\*)?\s*(.+?)\s*$")
# A pick is the token alone or followed at once by punctuation; '1 more thing: x' is not one.
PICK = re.compile(r"^(?:option\s+)?(\d{1,2}|[a-e])(?:\s*[.,):\-]\s*\S.{0,50})?[.!\s]*$", re.I)
GO_AHEAD = re.compile(r"^(yes|yep|yeah|ok|okay|go|go ahead|do it|proceed|approved?|sure)[.!\s]*$", re.I)
NO_REPLY = re.compile(r"^(no|nope|nah)[.!\s]*$", re.I)
TAIL_LINES = 3  # non-option lines allowed after the list ("Which one?")


def options_at_end(text):
    """Labels of the option list that ends the message, or [] when none (needs 2+, from 1 or A)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    opts, skipped = [], 0
    for ln in reversed(lines[-20:]):
        m = OPT_LINE.match(ln)
        if m:
            opts.append((m.group(1).lower(), m.group(2)))
        elif opts:
            break
        else:
            skipped += 1
            if skipped > TAIL_LINES:
                return []
    opts.reverse()
    keys = [k for k, _ in opts]
    first = ("1", "a")
    if len(opts) < 2 or keys[0] not in first or keys != [str(i + 1) if keys[0] == "1" else chr(97 + i) for i in range(len(opts))]:
        return []
    return [label for _, label in opts]


def _go_ahead_index(word, labels):
    """Index of the one label that is, or starts with, the reply word; None when zero or several do."""
    hit = [i for i, lb in enumerate(labels) if re.match(re.escape(word) + r"\b", lb.lower().replace("**", "").strip())]
    return hit[0] if len(hit) == 1 else None


def pick_index(msg, labels):
    """Zero-based option the owner message picks out of labels, or None when it is not only a pick.

    A bare go-ahead or refusal picks only when exactly one label matches it; it never means option 1."""
    msg, n = msg.strip(), len(labels)
    if GO_AHEAD.match(msg) or NO_REPLY.match(msg):
        return _go_ahead_index(msg.lower().rstrip(".! "), labels)
    m = PICK.match(msg)
    if not m:
        return None
    tok = m.group(1).lower()
    i = int(tok) - 1 if tok.isdigit() else ord(tok) - 97
    return i if 0 <= i < n else None


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _assistant_question(content, ts, prev):
    """The (question, opts, header, ts) an assistant turn asks, or prev when it asks nothing."""
    wrap = [p for p in content if isinstance(p, dict) and p.get("name") == "mcp__ccd_turn__wrap_up"] if isinstance(content, list) else []
    labels = [a["label"] for p in wrap for a in (p.get("input") or {}).get("actions") or [] if isinstance(a, dict) and a.get("label")]
    text = _text(content)
    if labels:
        inp = wrap[-1].get("input") or {}
        return ("\n".join(x for x in (inp.get("ask"), inp.get("recap"), text) if x), labels, "wrap-up", ts)
    if text.strip():
        opts = options_at_end(text)
        return (text, opts, "", ts) if opts else None
    return prev


def pick_entries(lines, cutoff, repo, session, parse_ts, classify):
    """Entries for both forms found in one transcript's lines (see module docstring)."""
    out, prev = [], None  # prev = (question, opts, header, question ts) of the last assistant turn
    for line in lines:
        is_a, is_u = re.search(r'"type": ?"assistant"', line), re.search(r'"type": ?"user"', line)
        if not (is_a or is_u) or is_u and '"tool_result"' in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        is_a, is_u = d.get("type") == "assistant", d.get("type") == "user"
        if not (is_a or is_u):
            continue
        if not isinstance(d, dict) or d.get("isSidechain") or not isinstance(d.get("message"), dict):
            continue
        content = d["message"].get("content")
        if is_a:
            prev = _assistant_question(content, parse_ts(d.get("timestamp")), prev)
            continue
        msg = _text(content)
        if prev and msg.strip() and prev[3] and prev[3] >= cutoff:  # window and date follow the question
            q, opts, header, qts = prev
            if header == "wrap-up":
                hit = [o for o in opts if o.lower() == msg.strip().lower()]
                ans = hit[0] if hit else None
            else:
                i = pick_index(msg, opts)
                ans = opts[i] if i is not None else None
            if ans is not None:
                out.append({"date": qts.strftime("%Y-%m-%d"), "repo": repo, "question": q, "header": header,
                            "opts": opts, "answer": ans, "session": session, "key": classify(q, opts, header),
                            "form": "button" if header == "wrap-up" else "prose"})
        if msg.strip():
            prev = None
    return out
