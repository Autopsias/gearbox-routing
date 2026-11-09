"""Cut each finding out of its frame and mark it, so the owner sees it.

uv run --with pillow python mark.py <spec.json> <out dir>
uv run --with pillow python mark.py --self-test

spec.json is a list of findings; coordinates are pixels of the frame image
(a 390 phone frame at dpr 2 is 780 wide):
  [{"name": "1-breadcrumb", "title": "1. Phone: the breadcrumb wraps",
    "caption": "One sentence: what is wrong and where.",
    "panels": [{"frame": "f/home-390-top.png", "crop": [0, 125, 780, 400],
                "boxes": [[700, 145, 750, 195]],   # red: the fault
                "guides": [455, 1196],               # green dashes: where it should align
                "scale": 1.0}]}]
"""
import json
import sys
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RED, GREEN, GREY = (220, 30, 30), (20, 150, 60), (90, 90, 90)


def font(size):
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()


def panel(base, p):
    im = Image.open(base / p["frame"]).convert("RGB")
    x0, y0, x1, y1 = p.get("crop", [0, 0, im.width, im.height])
    im = im.crop((x0, y0, x1, y1))
    d = ImageDraw.Draw(im)
    for bx0, by0, bx1, by1 in p.get("boxes", []):
        d.rectangle([bx0 - x0, by0 - y0, bx1 - x0, by1 - y0], outline=RED, width=4)
    for gx in p.get("guides", []):
        for y in range(0, im.height, 14):
            d.line([gx - x0, y, gx - x0, y + 7], fill=GREEN, width=3)
    s = p.get("scale", 1.0)
    return im.resize((int(im.width * s), int(im.height * s))) if s != 1.0 else im


def sheet(base, f, out):
    panels = [panel(base, p) for p in f["panels"]]
    width = max(max(p.width for p in panels), 900) + 40
    caption = textwrap.fill(f.get("caption", ""), 82)
    top = 110 + 26 * caption.count("\n")
    img = Image.new("RGB", (width, top + 10 + sum(p.height + 20 for p in panels)), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 18), f["title"], font=font(30), fill=(0, 0, 0))
    d.multiline_text((20, 62), caption, font=font(20), fill=GREY, spacing=6)
    y = top
    for p in panels:
        img.paste(p, (20, y))
        d.rectangle([19, y - 1, 20 + p.width, y + p.height], outline=(200, 200, 200))
        y += p.height + 20
    path = out / f"{f['name']}.png"
    img.save(path)
    return path


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 2:
        sys.exit(__doc__)
    spec = Path(argv[0])
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for f in json.loads(spec.read_text()):
        print(sheet(spec.parent, f, out))
    return 0


def self_test():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        Image.new("RGB", (400, 300), "white").save(t / "frame.png")
        spec = [{"name": "x", "title": "T", "caption": "c " * 60,
                 "panels": [{"frame": "frame.png", "crop": [100, 50, 300, 250],
                             "boxes": [[150, 100, 200, 150]], "guides": [120]}]}]
        (t / "spec.json").write_text(json.dumps(spec))
        main([str(t / "spec.json"), str(t / "o")])
        im = Image.open(t / "o" / "x.png")
        assert im.width == 940, im.width                       # 900 minimum + margins
        top = 110 + 26 * textwrap.fill("c " * 60, 82).count("\n")
        assert im.getpixel((20 + 50, top + 50)) == RED          # box corner, crop-relative
        assert im.getpixel((20 + 20, top + 2)) == GREEN         # guide at x=120
        assert im.getpixel((20 + 5, top + 5)) == (255, 255, 255)
    print("mark.py self-test: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
