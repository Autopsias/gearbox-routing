"""Check that every Stageshow file and heading ui-craft names exists in the pinned copy.

python3 check_refs.py [--root <skill dir>]   exit 0 = all found, 1 = something missing

ui-craft reads upstream in place and names its sections (`craft.md` § "Type") instead of
copying them, so an upstream re-pin brings the new text. If upstream renames or drops a
section, this is what notices. Run it after every re-pin (references/upstream.md).
"""
import re
import sys
from pathlib import Path

HEADING = re.compile(r'`([\w-]+\.md)` § "([^"]+)"')
PATH = re.compile(r'`\$V/([^`\s]+)`')


def missing(root):
    v = root / "vendor" / "stageshow"
    gone = []
    for doc in [root / "SKILL.md", *sorted((root / "references").glob("*.md"))]:
        text = doc.read_text()
        for rel in PATH.findall(text):
            if not (v / rel).exists():
                gone.append(f"{doc.name}: $V/{rel} does not exist")
        for name, head in HEADING.findall(text):
            src = v / "references" / name
            heads = [h.strip() for h in re.findall(r"^#{1,4} (.+)$", src.read_text(), re.M)] if src.exists() else []
            # Prefix match: verify.md's heading is "Framing (first, because ...)".
            # A heading-free quote is a rule bullet: it must appear in the file's text.
            if not any(h.startswith(head) for h in heads) and not (src.exists() and head in src.read_text()):
                gone.append(f'{doc.name}: {name} § "{head}" not found upstream')
    return gone


def main(argv):
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else Path(__file__).resolve().parent.parent
    gone = missing(root)
    for g in gone:
        print(g)
    print(f"check_refs: {'MISSING ' + str(len(gone)) if gone else 'all references found'}")
    return 1 if gone else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
