#!/usr/bin/env python3
"""
egress_sanitizer.py — SV-03 (v2 Source Verification I)
======================================================
The **deterministic** web-egress sanitizer + egress decision + manifest diff for the
Office Trust Layer. This is the *mechanism* the contract reserves: `conventions.md`
§2.5 fixes the rule (confidential/restricted ⇒ sanitise-or-skip; restricted-Board ⇒
skipped-egress without a signed waiver) and `sensitivity-routing-policy.md` §5.1 says
"the deterministic egress sanitizer, the gate, and the manifest diff live here".

Why deterministic (review finding #8): "abstracted" left too much to the implementing
agent. Sending a claim to an `external-web` provider is an **egress event**; what may
leave the machine must be decided by *rule*, not by a model's judgement, and must be
**testable token-by-token**. Nothing here calls a model or the network.

What it does, in order, to a claim before it can become a web query:
  1. **NER / named-entity masking** — explicit entities from the caller (the claim's
     §2.3 `named-entity` materiality + the packet's source labels/owners) AND built-in
     structured patterns (Project/Deal/Operation code-names, person-name runs, emails,
     account numbers) → stable `<ENTITY_n>` / `<PERSON_n>` / `<AMOUNT_n>` placeholders
     (the §3 mask-placeholder style). The reverse map is **local-only** (never in the
     manifest, per §5.2).
  2. **Numeric-range coarsening** — absolute monetary magnitudes (€21B, $4.2m,
     1,234,567) are coarsened to a non-identifying bucket ("tens of €B") or dropped.
     Small ratios / percentages are kept (they are the checkable fact and rarely
     identifying), unless the caller bans them.
  3. **Source-label removal** — `S<n>` source ids, `[S3]`, "per the board pack",
     "according to <X>", and uploaded file basenames are stripped — a query must never
     reveal the provenance trail.
  4. **Banned-token check** — a caller-supplied banned list (project code-names, MNPI
     tickers, …). Any banned token is masked; a banned token that survives masking is a
     hard stop → the query is **blocked** (never egressed), recorded, not failed.

And it decides *whether* egress may happen at all:
  * `decide_egress(sensitivity, travel_class, waiver)` — `public`/`internal` ⇒ light
    sanitise; `confidential`/`restricted` ⇒ mask-required; **`restricted` + Board/
    Regulatory ⇒ `skipped-egress`** unless a named-owner waiver **with signoff** permits
    a sanitised query.
  * `egress_gate(...)` — the mechanical precondition (mirrors `manifest_gate.py`):
    returns `{status: ok|blocked, ...}`; `blocked` means *do not egress* — the caller
    records the eligible claim `not-checked-budget` (§2.2), it is **not** a `failed` run.

And it records what left:
  * `egress_record(raw_claim, sanitised)` — the manifest-safe diff: the **raw-claim
    sha256**, the **exact egressed (sanitised) query** (safe — it is sanitised), and a
    structured transformation summary (mask/coarsen/label/banned counts + placeholders).
    It never stores the raw claim text or the reverse map (§5.2).

Stdlib only; pure functions; no model, no network. The whole point is that the same
input always yields the same egressed query, so a fixture can assert it token-by-token.
"""

from __future__ import annotations

import hashlib
import math as _math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Egress modes & decision (conventions.md §2.5; sensitivity-routing-policy.md §5.1)
# ---------------------------------------------------------------------------
MODE_ALLOW_LIGHT = "allow-light"        # public/internal: strip labels + banned tokens
MODE_MASK_REQUIRED = "mask-required"    # confidential/restricted (non-Board): full mask + coarsen
MODE_SKIPPED = "skipped-egress"         # restricted + Board/Regulatory, no signed waiver: do NOT egress

SENSITIVITY_CLASSES = {"public", "internal", "confidential", "restricted"}
TRAVEL_CLASSES = {"internal", "travels", "board-regulatory"}

# Waiver fields required to permit a restricted-Board sanitised egress. Mirrors the
# readiness/routing waiver discipline (conventions.md §4 / sensitivity-routing-policy.md §6):
# a named owner + a signoff state of "approved" before any egress of restricted material.
EGRESS_WAIVER_REQUIRED_FIELDS = ("owner", "scope", "rationale", "signoff")


def decide_egress(sensitivity: str, travel_class: str,
                  waiver: Optional[dict] = None) -> dict:
    """Decide whether — and how — a claim of this sensitivity may egress under this
    travel class. Pure policy; no I/O. Returns
    `{egress_allowed, mode, reason, waiver_valid}`."""
    sens = (sensitivity or "internal").lower()
    tc = (travel_class or "internal").lower()
    if sens not in SENSITIVITY_CLASSES:
        sens = "restricted"  # default-up on doubt (policy §2)
    waiver_valid, waiver_problems = _validate_egress_waiver(waiver)

    # restricted + Board/Regulatory: skipped unless a named-owner signed waiver.
    if sens == "restricted" and tc == "board-regulatory":
        if waiver_valid:
            return {"egress_allowed": True, "mode": MODE_MASK_REQUIRED,
                    "reason": "restricted Board/Regulatory egress permitted by a named-owner "
                              "signed waiver — sanitised query only",
                    "waiver_valid": True}
        return {"egress_allowed": False, "mode": MODE_SKIPPED,
                "reason": "restricted Board/Regulatory defaults to skipped-egress; "
                          "eligible claim recorded not-checked-budget "
                          + ("(waiver invalid: %s)" % ", ".join(waiver_problems) if waiver else
                             "(no named-owner signed waiver)"),
                "waiver_valid": False}

    # confidential / restricted (non-Board): mask + coarsen required.
    if sens in ("confidential", "restricted"):
        return {"egress_allowed": True, "mode": MODE_MASK_REQUIRED,
                "reason": "%s claim egresses only as a masked + coarsened sanitised query" % sens,
                "waiver_valid": waiver_valid}

    # public / internal: light sanitise (labels + banned tokens still stripped).
    return {"egress_allowed": True, "mode": MODE_ALLOW_LIGHT,
            "reason": "%s claim egresses as a light-sanitised query (source labels + banned "
                      "tokens removed)" % sens,
            "waiver_valid": waiver_valid}


def _validate_egress_waiver(waiver: Optional[dict]) -> Tuple[bool, List[str]]:
    if not waiver:
        return False, ["no waiver"]
    problems: List[str] = []
    for f in EGRESS_WAIVER_REQUIRED_FIELDS:
        v = waiver.get(f)
        present = bool(v.strip()) if isinstance(v, str) else bool(v)
        if not present:
            problems.append(f"missing {f}")
    if waiver.get("signoff") != "approved":
        problems.append("signoff is not 'approved'")
    return (not problems), problems


def egress_gate(sensitivity: str, travel_class: str,
                waiver: Optional[dict] = None) -> dict:
    """The mechanical egress precondition (mirrors manifest_gate.gate). `blocked` means:
    do NOT call the external provider; record the eligible claim `not-checked-budget`.
    Distinct from `failed` — the work is well-defined but gated."""
    d = decide_egress(sensitivity, travel_class, waiver)
    status = "ok" if d["egress_allowed"] else "blocked"
    return {
        "status": status,
        "egress_allowed": d["egress_allowed"],
        "mode": d["mode"],
        "reason": d["reason"],
        "waiver_valid": d["waiver_valid"],
    }


# ---------------------------------------------------------------------------
# Deterministic sanitizer
# ---------------------------------------------------------------------------
# Money / magnitude: an optional currency symbol or code, a number, an optional
# scale word. Captures absolute monetary magnitudes that could identify a deal.
_CURRENCY = r"(?:€|\$|£|¥|EUR|USD|GBP|JPY|CHF)"
# FIXME(egress-scale): no trailing \b + _MONEY_RE eats the gap space, so a bare
# number before a b/m/k/t word corrupts the query; see test_known_defect_scale_*.
_SCALE = r"(?:bn|b|billion|mm|m|million|k|thousand|tn|t|trillion)"
_MONEY_RE = re.compile(
    rf"(?P<pre>{_CURRENCY}\s?)?(?P<num>\d[\d.,]*)\s?(?P<scale>{_SCALE})?(?P<post>\s?{_CURRENCY})?",
    re.IGNORECASE,
)
# Structured code-names: "Project Peninsula", "Deal Falcon", "Operation X".
_CODENAME_RE = re.compile(r"\b(?:Project|Deal|Operation|Programme|Program|Codename)\s+[A-Z][\w-]+", re.UNICODE)
# Emails and long digit runs (account / id numbers).
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
_ACCOUNT_RE = re.compile(r"\b\d{6,}\b")
# Source labels: S1 / [S3] / (S12) and provenance phrases / uploaded file basenames.
_SOURCE_ID_RE = re.compile(r"[\[(]?\bS\d+\b[\])]?")
_FILE_RE = re.compile(r"\b[\w-]+\.(?:xlsx|xls|docx|doc|pptx|ppt|pdf|csv)\b", re.IGNORECASE)
_PROVENANCE_RE = re.compile(
    r"\b(?:per|according to|as stated in|from)\s+(?:the\s+)?(?:board\s*pack|board\s*deck|"
    r"investor\s*deck|data\s*room|source\s*packet|model|appendix|exhibit)\b",
    re.IGNORECASE,
)
# A person-name run: 2-3 capitalised words (handles "Ana Sofia Martins"); used only when
# the caller does not pass the entity explicitly. Conservative to avoid over-masking.
_PERSON_RUN_RE = re.compile(r"\b([A-Z][a-z]+)(?:\s+[A-Z][a-z]+){1,2}\b")

# Coarse buckets for monetary magnitude (non-identifying).
def _coarsen_amount(num_str: str, scale: Optional[str], cur: str) -> str:
    try:
        base = float(num_str.replace(",", "")) if num_str else 0.0
    except ValueError:
        base = 0.0
    scale = (scale or "").lower()
    mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6,
            "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "tn": 1e12, "trillion": 1e12}.get(scale, 1.0)
    val = base * mult
    cur = cur or "value"
    if val >= 1e12:
        return f"order-of-{cur}-trillions"
    if val >= 1e11:
        return f"hundreds-of-{cur}-billions"
    if val >= 1e10:
        return f"tens-of-{cur}-billions"
    if val >= 1e9:
        return f"single-digit-{cur}-billions"
    if val >= 1e8:
        return f"hundreds-of-{cur}-millions"
    if val >= 1e6:
        return f"{cur}-millions"
    if val >= 1e3:
        return f"{cur}-thousands"
    return "a-small-amount"


@dataclass
class SanitizeResult:
    raw_claim: str
    egressed_query: str
    blocked: bool = False        # sanitisation hard-stop (a banned token survived)
    skipped: bool = False        # policy skip (mode == skipped-egress): nothing egressed
    block_reason: str = ""
    masked: List[str] = field(default_factory=list)       # placeholders issued
    coarsened: List[str] = field(default_factory=list)    # buckets substituted
    removed_labels: List[str] = field(default_factory=list)
    banned_hits: List[str] = field(default_factory=list)  # banned tokens that were present
    mask_map: Dict[str, str] = field(default_factory=dict)  # placeholder -> original (LOCAL ONLY)

    @property
    def raw_claim_sha256(self) -> str:
        return hashlib.sha256(self.raw_claim.encode("utf-8")).hexdigest()


class EgressSanitizer:
    """Deterministic claim → sanitised query transformer. Construct once per run with
    the run's banned-token list; call `sanitize()` per claim."""

    def __init__(self, banned_tokens: Sequence[str] = (), coarsen_percentages: bool = False) -> None:
        # longest-first so "Project Peninsula North" masks before "Peninsula"
        self.banned_tokens = sorted({t for t in banned_tokens if t}, key=len, reverse=True)
        self.coarsen_percentages = coarsen_percentages

    # -- the transform -----------------------------------------------------
    def sanitize(self, claim: str, *, sensitivity: str = "internal",
                 entities: Sequence[str] = (), mode: Optional[str] = None) -> SanitizeResult:
        """Transform `claim` into an egress-safe query under `mode` (or the mode implied
        by `sensitivity` when `mode` is None). Deterministic: same input → same output."""
        if mode is None:
            mode = (MODE_MASK_REQUIRED if (sensitivity or "internal").lower()
                    in ("confidential", "restricted") else MODE_ALLOW_LIGHT)
        # MODE_SKIPPED ⇒ egress is not permitted: nothing is constructed and nothing
        # leaves. Defence in depth — a restricted-Board claim cannot egress verbatim even
        # if sanitize() is called directly with the skipped mode.
        if mode == MODE_SKIPPED:
            return SanitizeResult(raw_claim=claim, egressed_query="", skipped=True,
                                  block_reason="skipped-egress: external check not permitted "
                                               "at this sensitivity/travel-class")
        res = SanitizeResult(raw_claim=claim, egressed_query=claim)
        text = claim
        counters = {"ENTITY": 0, "PERSON": 0, "AMOUNT": 0, "BANNED": 0}

        def issue(kind: str, original: str) -> str:
            counters[kind] += 1
            ph = f"<{kind}_{counters[kind]}>"
            res.mask_map[ph] = original
            res.masked.append(ph)
            return ph

        # 1a. banned tokens FIRST (longest-first), always masked regardless of mode.
        for tok in self.banned_tokens:
            if re.search(re.escape(tok), text, re.IGNORECASE):
                res.banned_hits.append(tok)
                text = re.sub(re.escape(tok), issue("BANNED", tok), text, flags=re.IGNORECASE)

        # 1b–1d. NER masking + numeric identifiers are the CONFIDENTIAL/RESTRICTED behaviour
        # (policy §3: light/excerpt for internal, `mask` for confidential+). In light mode we
        # keep the substance (so a public/internal claim still searches well) and only strip
        # the provenance trail (step 3) + banned tokens (step 1a, already done).
        if mode == MODE_MASK_REQUIRED:
            # caller-supplied named entities (longest-first), masked first.
            for ent in sorted({e for e in entities if e}, key=len, reverse=True):
                if re.search(re.escape(ent), text, re.IGNORECASE):
                    kind = "PERSON" if _looks_like_person(ent) else "ENTITY"
                    text = re.sub(re.escape(ent), issue(kind, ent), text, flags=re.IGNORECASE)
            # structured code-names + emails (identifiers).
            for m in list(_CODENAME_RE.finditer(text)):
                text = text.replace(m.group(0), issue("ENTITY", m.group(0)), 1)
            for m in list(_EMAIL_RE.finditer(text)):
                text = text.replace(m.group(0), issue("ENTITY", m.group(0)), 1)
            # heuristic person-name runs the caller didn't name.
            for m in list(_PERSON_RUN_RE.finditer(text)):
                run = m.group(0)
                if "<" in run:  # already partly masked
                    continue
                text = text.replace(run, issue("PERSON", run), 1)

        # 3. source-label removal (ALWAYS — never reveal the provenance trail, any mode).
        for rx, tag in ((_PROVENANCE_RE, "provenance-phrase"), (_FILE_RE, "filename"),
                        (_SOURCE_ID_RE, "source-id")):
            for m in list(rx.finditer(text)):
                res.removed_labels.append(m.group(0))
                text = text.replace(m.group(0), "", 1)

        if mode == MODE_MASK_REQUIRED:
            # account numbers (after source ids, so an [S3]-style token isn't caught as digits)
            for m in list(_ACCOUNT_RE.finditer(text)):
                text = text.replace(m.group(0), issue("ENTITY", m.group(0)), 1)
            # 2. numeric coarsening of monetary magnitudes (light mode keeps figures).
            text = self._coarsen(text, res, counters)

        # 4. banned-token survival check (defence in depth) — if a banned token text
        #    survived masking, BLOCK egress rather than leak it.
        for tok in self.banned_tokens:
            if re.search(re.escape(tok), text, re.IGNORECASE):
                res.blocked = True
                res.block_reason = f"banned token survived sanitisation: {tok!r}"
                break

        res.egressed_query = _normalise_ws(text)
        if res.blocked:
            res.egressed_query = ""  # nothing leaves on a block
        return res

    def _coarsen(self, text: str, res: SanitizeResult, counters: dict) -> str:
        # percentages handled first (kept by default; coarsened only if asked)
        if self.coarsen_percentages:
            text = re.sub(r"\d[\d.,]*\s?%",
                          lambda m: self._bucket(res, "a-percentage"), text)
        # monetary magnitudes
        def repl(m: re.Match) -> str:
            pre, num, scale, post = m.group("pre"), m.group("num"), m.group("scale"), m.group("post")
            # only coarsen when there is a currency marker OR a scale word OR a large bare number,
            # so plain small integers ("3 segments") survive.
            cur = (pre or post or "").strip()
            is_money = bool(cur) or bool(scale) or (num and len(re.sub(r"[.,]", "", num)) >= 4)
            if not is_money:
                return m.group(0)
            cur_label = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}.get(cur, cur or "value")
            bucket = _coarsen_amount(num, scale, cur_label)
            return self._bucket(res, bucket)
        return _MONEY_RE.sub(repl, text)

    @staticmethod
    def _bucket(res: SanitizeResult, bucket: str) -> str:
        res.coarsened.append(bucket)
        return bucket


def _looks_like_person(s: str) -> bool:
    # crude: two/three capitalised words, no digits, no Inc/Ltd/Corp suffix
    if re.search(r"\b(Inc|Ltd|LLC|Corp|PLC|GmbH|SA|SGPS|NV|AG)\b", s):
        return False
    parts = s.split()
    return 1 < len(parts) <= 3 and all(p[:1].isupper() for p in parts) and not any(ch.isdigit() for ch in s)


def _normalise_ws(s: str) -> str:
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    s = re.sub(r"\(\s*\)", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


# ---------------------------------------------------------------------------
# Manifest record (the raw-claim → egressed-query diff)
# ---------------------------------------------------------------------------
def egress_record(result: SanitizeResult, *, sensitivity: str, mode: str,
                  egress_allowed: bool) -> dict:
    """The manifest-safe egress record (sensitivity-routing-policy.md §2.5 / §5.2).

    Carries the **raw-claim sha256** (never the raw text), the **exact egressed
    (sanitised) query** (safe — it is sanitised), and a transformation summary so
    "what left the machine" is auditable. The reverse mask map is NOT included
    (local-only). `diff` is a human/string view of the transformation."""
    diff_lines = []
    if result.masked:
        diff_lines.append("masked %d entit%s (%s)" % (
            len(result.masked), "y" if len(result.masked) == 1 else "ies", ", ".join(result.masked)))
    if result.coarsened:
        diff_lines.append("coarsened %d magnitude(s) -> %s" % (len(result.coarsened),
                                                               ", ".join(result.coarsened)))
    if result.removed_labels:
        diff_lines.append("removed %d source label(s)" % len(result.removed_labels))
    if result.banned_hits:
        diff_lines.append("banned tokens present + masked: %d" % len(result.banned_hits))
    if not diff_lines:
        diff_lines.append("no transformation needed")
    return {
        "raw_claim_sha256": result.raw_claim_sha256,
        "egressed_query": result.egressed_query,   # sanitised — safe to record
        "sensitivity": sensitivity,
        "mode": mode,
        "egress_allowed": egress_allowed,
        "blocked": result.blocked,
        "block_reason": result.block_reason,
        "transform": {
            "masked_placeholders": list(result.masked),
            "coarsened_buckets": list(result.coarsened),
            "removed_label_count": len(result.removed_labels),
            "banned_hit_count": len(result.banned_hits),
        },
        "diff": "; ".join(diff_lines),
    }


# ===========================================================================
# CF-01 (S08) — LOCAL-ONLY semantic egress backstop (conventions.md §2.5)
# ===========================================================================
# The deterministic sanitizer above is the FIRST guard. It is regex-driven, so a
# PARAPHRASED or OBFUSCATED leak it has no pattern for — "twenty-one billion euros"
# (numbers-as-words), a code-name split across tokens, a source label woven into prose
# — can slip into the egressed query. The backstop below is the SECOND guard: a
# semantic pass that flags an egressed query that is too CLOSE to a known confidential
# exemplar even when no literal token survived.
#
# The whole point of finding #6: the backstop must not become a NEW leak.
#   * It is STRICTLY LOCAL. The embedding provider is a pure-Python feature hasher; it
#     calls no model and opens no socket. A pluggable provider lets a fixture inject a
#     stub and a socket tripwire to PROVE zero external calls.
#   * The manifest records MASKED CLASSES + local SALTED HASHES of the exemplars and any
#     leaked token — NEVER the raw confidential text, and never the salt itself.
#   * The raw-claim -> egressed-query DIFF and the reverse map stay LOCAL-ONLY (in the
#     instance), exactly like the sanitizer's mask_map (§5.2).
#   * A VERBATIM confidential token reaching the egressed query is a HARD FAIL (blocked).


def local_salted_hash(token: str, salt: str) -> str:
    """A local, non-reversible fingerprint of a confidential token: sha256(salt || token)
    over the NORMALISED token. The salt is local-only (never recorded); only its own
    fingerprint may be recorded so two runs with the same salt are correlatable without
    revealing it."""
    norm = _normalise_token(token)
    return hashlib.sha256(("%s\x1f%s" % (salt, norm)).encode("utf-8")).hexdigest()


def _normalise_token(s: str) -> str:
    """lowercase, drop hyphens/underscores, collapse non-alphanumerics to single spaces."""
    s = (s or "").lower().replace("-", " ").replace("_", " ")
    s = re.sub(r"[^\w%€$£¥]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s{2,}", " ", s).strip()


class LocalHashingEmbedder:
    """A deterministic, network-free embedding provider: char-trigram + word feature
    hashing into a fixed-dimension L2-normalised vector. Pure stdlib. `is_local` is True
    and `calls` counts embed() invocations so a fixture can assert the path is exercised
    locally; `external_calls` is structurally 0 (there is no network method to call)."""

    is_local = True
    name = "LocalHashingEmbedder"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.calls = 0
        self.external_calls = 0

    def embed(self, text: str) -> List[float]:
        self.calls += 1
        norm = _normalise_token(text)
        vec = [0.0] * self.dim
        feats: List[str] = norm.split()                       # word features
        for w in list(feats):                                  # + char trigrams per word
            pad = "  %s  " % w
            feats += [pad[i:i + 3] for i in range(len(pad) - 2)]
        for f in feats:
            h = int(hashlib.sha256(f.encode("utf-8")).hexdigest(), 16)
            vec[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
        n = _math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / n for x in vec]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class _Exemplar:
    text: str
    classes: Tuple[str, ...]
    salted_hash: str
    vec: List[float]
    created: str
    expires: str


class LocalSemanticEgressBackstop:
    """Second-line, LOCAL-ONLY semantic egress guard over a confidential exemplar corpus.

    Construct with the run's confidential exemplars (each {text, classes}), a LOCAL salt,
    a retention window, and (optionally) an injected embedding provider. `scan()` checks an
    egressed query for (1) a VERBATIM confidential token (hard fail) and (2) a semantic
    match to an exemplar the deterministic sanitizer missed; both BLOCK egress. `manifest_record()`
    emits the masked-classes + hashes record — never raw tokens."""

    SEMANTIC_THRESHOLD = 0.72

    def __init__(self, exemplars: Sequence[dict], *, salt: str,
                 retention_days: int = 90, embedder=None,
                 now: Optional[str] = None, threshold: Optional[float] = None) -> None:
        if not salt:
            raise ValueError("a local salt is required (exemplars are stored salted)")
        self._salt = salt                       # LOCAL-ONLY — never recorded
        self.salt_fingerprint = hashlib.sha256(salt.encode("utf-8")).hexdigest()[:16]
        self.retention_days = int(retention_days)
        self.embedder = embedder or LocalHashingEmbedder()
        self.threshold = self.SEMANTIC_THRESHOLD if threshold is None else threshold
        self.external_calls = 0
        # the raw-claim -> egressed-query DIFF stays here, LOCAL-ONLY (never in the record).
        self.local_diff_log: List[dict] = []
        created = now or _dt_now()
        exp = _plus_days(created, self.retention_days)
        self._exemplars: List[_Exemplar] = []
        for e in exemplars:
            txt = e["text"] if isinstance(e, dict) else str(e)
            classes = tuple(e.get("classes", ())) if isinstance(e, dict) else ()
            self._exemplars.append(_Exemplar(
                text=txt, classes=classes,
                salted_hash=local_salted_hash(txt, salt),
                vec=self.embedder.embed(txt), created=created, expires=exp))

    # -- retention ---------------------------------------------------------
    def active_exemplars(self, now: Optional[str] = None) -> List[_Exemplar]:
        now = now or _dt_now()
        return [x for x in self._exemplars if x.expires >= now]

    def purge_expired(self, now: Optional[str] = None) -> int:
        before = len(self._exemplars)
        self._exemplars = self.active_exemplars(now)
        return before - len(self._exemplars)

    # -- the scan ----------------------------------------------------------
    def scan(self, egressed_query: str, *, confidential_tokens: Sequence[str] = (),
             now: Optional[str] = None) -> dict:
        """Inspect an already-sanitised egressed query before it may leave the machine.

        Returns a manifest-SAFE dict (no raw tokens): verbatim_leak / semantic_match /
        blocked / egress_allowed + per-match {class, exemplar_hash, score}. A verbatim
        confidential token (normalised substring) is a HARD FAIL; a windowed cosine match
        to an active exemplar at/above the threshold is a semantic catch. Both block."""
        q_norm = _normalise_token(egressed_query)
        active = self.active_exemplars(now)

        # (1) VERBATIM confidential token check — the hard fail.
        leaked_hashes: List[str] = []
        for tok in confidential_tokens:
            tnorm = _normalise_token(tok)
            if tnorm and tnorm in q_norm:
                leaked_hashes.append(local_salted_hash(tok, self._salt))
        verbatim_leak = bool(leaked_hashes)

        # (2) SEMANTIC match — windowed cosine of the query against each exemplar (local).
        q_words = q_norm.split()
        matches: List[dict] = []
        for ex in active:
            n = max(1, len(_normalise_token(ex.text).split()))
            best = 0.0
            # slide windows of n-1..n+1 words so an exemplar phrase EMBEDDED in a longer
            # sentence still matches (the obfuscated-in-prose case).
            for w in (n - 1, n, n + 1):
                if w < 1:
                    continue
                for i in range(0, max(1, len(q_words) - w + 1)):
                    window = " ".join(q_words[i:i + w])
                    if not window:
                        continue
                    score = _cosine(self.embedder.embed(window), ex.vec)
                    if score > best:
                        best = score
            if best >= self.threshold:
                matches.append({"class": (ex.classes[0] if ex.classes else "confidential"),
                                "classes": list(ex.classes),
                                "exemplar_hash": ex.salted_hash,
                                "score": round(best, 4)})
        semantic_match = bool(matches)

        blocked = verbatim_leak or semantic_match
        result = {
            "verbatim_leak": verbatim_leak,
            "semantic_match": semantic_match,
            "blocked": blocked,
            "egress_allowed": not blocked,
            "matches": matches,
            "leaked_token_hashes": leaked_hashes,
            "block_reason": ("verbatim confidential token in the egressed query"
                             if verbatim_leak else
                             "semantic proximity to a confidential exemplar"
                             if semantic_match else ""),
        }
        # the diff stays LOCAL-ONLY (raw text held in the instance, never returned/recorded).
        self.local_diff_log.append({"egressed_query": egressed_query,
                                     "blocked": blocked, "matches": len(matches),
                                     "verbatim_leak": verbatim_leak})
        return result

    # -- manifest record (masked classes + hashes; NEVER raw tokens) -------
    def manifest_record(self, scan_result: dict, now: Optional[str] = None) -> dict:
        """The manifest-safe semantic-backstop record (§2.5 / §5.2). Records masked
        CLASSES + local SALTED HASHES + retention + matches + the zero external-call count.
        It NEVER carries the raw exemplar text, the raw query, or the salt."""
        active = self.active_exemplars(now)
        classes = sorted({c for ex in active for c in ex.classes}) or ["confidential"]
        created = active[0].created if active else _dt_now()
        expires = active[0].expires if active else _plus_days(created, self.retention_days)
        return {
            "local_only": True,
            "provider": getattr(self.embedder, "name", type(self.embedder).__name__),
            "provider_is_local": bool(getattr(self.embedder, "is_local", False)),
            "external_calls": self.external_calls,
            "embed_calls": getattr(self.embedder, "calls", None),
            "salt_fingerprint": self.salt_fingerprint,
            "classes": classes,
            "exemplar_hashes": [ex.salted_hash for ex in active],
            "matches": scan_result.get("matches", []),
            "leaked_token_hashes": scan_result.get("leaked_token_hashes", []),
            "verbatim_hardfail": bool(scan_result.get("verbatim_leak")),
            "blocked": bool(scan_result.get("blocked")),
            "egress_allowed": bool(scan_result.get("egress_allowed")),
            "retention": {"retention_days": self.retention_days,
                          "created": created, "expires": expires,
                          "active_exemplars": len(active)},
            "diff_kept_local": True,
        }


def _dt_now() -> str:
    import datetime as _d
    return _d.datetime.now(_d.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plus_days(iso: str, days: int) -> str:
    import datetime as _d
    try:
        base = _d.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_d.timezone.utc)
    except ValueError:
        base = _d.datetime.now(_d.timezone.utc)
    return (base + _d.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# CLI: sanitise a claim from argv for quick manual inspection.
# ---------------------------------------------------------------------------
def _main(argv: Sequence[str]) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Office Trust Layer egress sanitizer (SV-03).")
    ap.add_argument("claim")
    ap.add_argument("--sensitivity", default="internal")
    ap.add_argument("--travel-class", default="travels")
    ap.add_argument("--entities", default="", help="comma-separated entities to mask")
    ap.add_argument("--banned", default="", help="comma-separated banned tokens")
    args = ap.parse_args(argv)

    gate = egress_gate(args.sensitivity, args.travel_class, waiver=None)
    ents = [e.strip() for e in args.entities.split(",") if e.strip()]
    banned = [b.strip() for b in args.banned.split(",") if b.strip()]
    san = EgressSanitizer(banned_tokens=banned)
    res = san.sanitize(args.claim, sensitivity=args.sensitivity, entities=ents, mode=gate["mode"])
    rec = egress_record(res, sensitivity=args.sensitivity, mode=gate["mode"],
                        egress_allowed=gate["egress_allowed"])
    print(json.dumps({"gate": gate, "egressed_query": res.egressed_query, "record": rec}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(__import__("sys").argv[1:]))
