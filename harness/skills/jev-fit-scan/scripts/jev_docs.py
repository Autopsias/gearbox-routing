#!/usr/bin/env python3
"""Fetch the live Jev (TypeSafe) docs and say what changed since the bundled snapshot.

The docs index is the ONLY hardcoded URL. Every other page is discovered from it,
because page URLs carry model versions (e.g. model-jaggedness/jev-1.13.md) and move.

  jev_docs.py --out DIR            fetch index + core pages into DIR, print the diff
  jev_docs.py --write-snapshot DATE   maintainer: rewrite the snapshot from the live docs
  jev_docs.py --self-test

Exit 0 = live docs fetched. Exit 2 = docs unreachable (caller falls back to the snapshot).
Stdlib only.
"""
import argparse
import hashlib
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

INDEX_URLS = ["https://docs.typesafe.ai/llms.txt"]
SNAPSHOT = Path(__file__).resolve().parent.parent / "references" / "docs-index-snapshot.tsv"
LINK_RE = re.compile(r"^- \[(?P<title>[^\]]+)\]\((?P<url>https?://[^)\s]+)\)(?::\s*(?P<desc>.*))?$")

# Pages that carry the facts a fit verdict depends on. Matched on the URL path, so a
# new model version or a renamed weak-points page is still picked up.
_TOP = r"^https?://[^/]+"
READ_FIRST = re.compile(
    _TOP + r"/(models|api|primitives|confidence|legal|patterns)\.md$"
    r"|/model-jaggedness/|/sdk/(python|javascript)/changelog\.md$"
)
# Saved and hashed too, but only worth reading when you design a test for a candidate.
ON_DEMAND = re.compile(
    _TOP + r"/agent-skill\.md$|/primitives/[^/]+\.md$|/concepts/(system-one|how-to-build)[^/]*\.md$"
)


def is_core(url):
    return bool(READ_FIRST.search(url) or ON_DEMAND.search(url))


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "jev-fit-scan/1 (docs reader)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_index(text):
    pages = {}
    for line in text.splitlines():
        m = LINK_RE.match(line.strip())
        if m:
            pages[m["url"]] = {"title": m["title"], "desc": (m["desc"] or "").strip()}
    return pages


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def read_snapshot(path):
    """-> (date, {url: (title, desc_hash, page_hash)})"""
    if not path.exists():
        return None, {}
    date, rows = None, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# snapshot"):
            date = line.split()[-1]
        elif line and not line.startswith("#"):
            url, title, desc_hash, page_hash = (line.split("\t") + ["", "", ""])[:4]
            rows[url] = (title, desc_hash, page_hash)
    return date, rows


def diff(live, live_hashes, snap):
    new = sorted(u for u in live if u not in snap)
    gone = sorted(u for u in snap if u not in live)
    redescribed = sorted(u for u in live if u in snap and snap[u][1] and snap[u][1] != sha(live[u]["desc"]))
    changed = sorted(u for u, h in live_hashes.items() if u in snap and snap[u][2] and snap[u][2] != h)
    return new, gone, redescribed, changed


def fetch_index():
    for url in INDEX_URLS:
        try:
            text = fetch(url)
        except Exception as e:  # noqa: BLE001 - any network failure means the same thing here
            print(f"index fetch failed: {url}: {e}", file=sys.stderr)
            continue
        if parse_index(text):
            return text
    return None


def save_core_pages(live, out):
    """-> ({url: page hash}, {url: (file name, size)}, [failures])"""
    hashes, saved, failed = {}, {}, []
    for url in sorted(u for u in live if is_core(u)):
        try:
            body = fetch(url)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{url} ({e})")
            continue
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.split("://", 1)[1])
        (out / name).write_text(body, encoding="utf-8")
        hashes[url], saved[url] = sha(body), (name, len(body))
    return hashes, saved, failed


def print_report(live, hashes, saved, failed, out, snap_path):
    """Print the diff and save it as <out>/diff.txt, so agents in deep mode can read it."""
    date, snap = read_snapshot(snap_path)
    changes = dict(zip(("NEW page", "REMOVED page", "NEW DESCRIPTION (numbers may differ)",
                        "CORE PAGE CHANGED (re-read; snapshot facts from it are suspect)"), diff(live, hashes, snap)))
    lines = [f"LIVE: {len(live)} pages in the index, {len(hashes)} core pages saved to {out}",
             f"SNAPSHOT: {date or 'none'} ({len(snap)} pages)"]
    for label, urls in changes.items():
        lines += [f"  {label}: {u}  [{live.get(u, {}).get('title') or snap.get(u, ('',))[0]}]" for u in urls]
    if not any(changes.values()):
        lines.append("  no difference from the snapshot")
    lines += [f"  FETCH FAILED: {f}" for f in failed]
    for label, rx in (("READ FIRST", READ_FIRST), ("ON DEMAND (when you design a test)", ON_DEMAND)):
        lines.append(f"{label}:")
        lines += [f"  {out / name}  ({size // 1000 or 1} kB)" for u, (name, size) in saved.items()
                  if rx.search(u) and not (rx is ON_DEMAND and READ_FIRST.search(u))]
    lines.append("The live pages win over the snapshot. Cookbooks are not saved: fetch one from the index when a site matches it.")
    print("\n".join(lines))
    (out / "diff.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="directory for the fetched pages (default: a new temp dir)")
    ap.add_argument("--snapshot", default=str(SNAPSHOT))
    ap.add_argument("--write-snapshot", metavar="DATE", help="maintainer: rewrite the snapshot, stamped DATE (YYYY-MM-DD)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()

    index_text = fetch_index()
    if index_text is None:
        print("OFFLINE: the Jev docs index is unreachable or empty. Use references/jev-snapshot.md "
              "and label every Jev fact with the snapshot date.")
        return 2
    live = parse_index(index_text)
    out = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="jev-docs-"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "llms.txt").write_text(index_text, encoding="utf-8")
    hashes, saved, failed = save_core_pages(live, out)

    if a.write_snapshot:
        rows = [f"# snapshot {a.write_snapshot}", "# url<TAB>title<TAB>desc_hash<TAB>core_page_hash"]
        rows += [f"{u}\t{live[u]['title']}\t{sha(live[u]['desc'])}\t{hashes.get(u, '')}" for u in sorted(live)]
        Path(a.snapshot).write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"snapshot written: {a.snapshot} ({len(live)} pages, {len(hashes)} core hashes)")
        return 0
    print_report(live, hashes, saved, failed, out, Path(a.snapshot))
    return 0


def self_test():
    sample = ("- [Models](https://d/models.md)\n"
              "- [Re-ranking](https://d/cookbooks/rerank.md): top-1 from 5% to 18%\n"
              "- [Jev 1.14 jaggedness](https://d/model-jaggedness/jev-1.14.md): edges\n")
    live = parse_index(sample)
    assert len(live) == 3 and live["https://d/models.md"]["desc"] == ""
    assert is_core("https://d/model-jaggedness/jev-1.14.md") and not is_core("https://d/cookbooks/rerank.md")
    assert is_core("https://d/api.md") and not is_core("https://d/sdk/python/api.md")
    snap = {"https://d/models.md": ("Models", sha(""), "OLDHASH"),
            "https://d/cookbooks/rerank.md": ("Re-ranking", sha("top-1 from 5% to 12%"), ""),
            "https://d/model-jaggedness/jev-1.13.md": ("Jev 1.13 jaggedness", sha("edges"), "x")}
    new, gone, redescribed, changed = diff(live, {"https://d/models.md": "NEWHASH"}, snap)
    assert new == ["https://d/model-jaggedness/jev-1.14.md"], new
    assert gone == ["https://d/model-jaggedness/jev-1.13.md"], gone
    assert redescribed == ["https://d/cookbooks/rerank.md"], redescribed
    assert changed == ["https://d/models.md"], changed
    print("self-test ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
