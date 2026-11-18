#!/usr/bin/env python3
"""Save a YouTube video as one text source: the captions, with a marker at each
moment the picture changes (a slide, a screen, a demo).

Usage: youtube_source.py <url> <out-dir> [--max-frames N]

Writes <out-dir>/source.md, <out-dir>/frames/tSSSSS.jpg (SSSSS = second in the
video) and <out-dir>/sheet_NN.jpg (contact sheets, 16 frames each).
Needs ffmpeg, and yt-dlp or uvx on PATH. No browser.
"""
import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

CUE = re.compile(r"(\d+):(\d\d):(\d\d)\.\d+ --> ")
TAG = re.compile(r"<[^>]+>")
SCORE = re.compile(r"pts_time:([0-9.]+)\s*\n\s*lavfi\.scene_score=([0-9.]+)")
UNREAD = "(not read yet)"
BLOCK_S = 30
VIDEO_EXT = {".mp4", ".webm", ".mkv"}


def stamp(s):
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def flat(s):
    """One line of outside text, so that no line break in it can start a forged heading or FRAME marker."""
    return " ".join(str(s).split())


def parse_vtt(text):
    """Return [(start_second, line)] without the rolling repeats of auto-captions."""
    out, start = [], None
    for raw in text.splitlines():
        m = CUE.match(raw)
        if m:
            h, mi, s = map(int, m.groups())
            start = h * 3600 + mi * 60 + s
        elif raw == "":
            start = None  # a cue ended; a cue id can follow, and it is not caption text
        else:
            line = flat(html.unescape(TAG.sub("", raw)))
            if start is not None and line and not (out and out[-1][1] == line):
                out.append((start, line))
    return out


def pick(scored, max_frames):
    """Keep the first frame and the most-changed others; one frame per second.

    scored: [(second, scene_score, path)] in time order. Returns the kept rows.
    """
    by_second = {}
    for row in scored:
        if row[0] not in by_second or row[1] > by_second[row[0]][1]:
            by_second[row[0]] = row
    rows = sorted(by_second.values())
    top = sorted(rows[1:], key=lambda r: -r[1])[: max(max_frames - 1, 0)]
    return sorted(rows[:1] + top)


def render(lines, frame_seconds):
    """Join caption lines into blocks of BLOCK_S seconds, with one marker per frame."""
    events = [(t, 1, text) for t, text in lines] + [(t, 0, "") for t in frame_seconds]
    out, block_end = [], -1
    for t, is_text, text in sorted(events):
        if not is_text:
            out.append(f"\n\n> FRAME [{stamp(t)}] frames/t{t:05d}.jpg | on screen: {UNREAD}")
            block_end = -1
        elif t >= block_end:
            out.append(f"\n\n[{stamp(t)}] {text}")
            block_end = t + BLOCK_S
        else:
            out.append(" " + text)
    return "".join(out).strip() + "\n"


def download(url, dl):
    ytdlp = ["yt-dlp"] if shutil.which("yt-dlp") else ["uvx", "yt-dlp@latest"]
    # --ignore-errors: YouTube answers HTTP 429 for some caption tracks (the translated `en` of an
    # English video), and one refused track must not stop the video.
    base = [*ytdlp, "--ignore-errors", "--write-subs", "--write-auto-subs", "--sub-format", "vtt",
            "--no-playlist", "--no-progress", "-o", "v.%(ext)s"]
    rc = subprocess.run([*base, "-f", "bv*[height<=720]/b[height<=720]", "--sub-langs", "en.*",
                         "--write-info-json", "--", url], cwd=dl).returncode
    videos = [p for p in dl.glob("v.*") if p.suffix in VIDEO_EXT]
    if not videos:
        sys.exit(f"yt-dlp gave no video file (exit {rc}). Use the fallback in getting-the-source.md.")
    if rc:
        print(f"WARNING: yt-dlp exit {rc}; the video is here, the captions may be absent.", file=sys.stderr)
    info = json.loads((dl / "v.info.json").read_text())
    lang = str(info.get("language") or "").split("-")[0]
    if not list(dl.glob("v.*.vtt")) and lang != "en" and re.fullmatch(r"[a-z]{2,3}", lang):
        # The video's own language, asked for by name: `.*-orig` matched 22 tracks on one video and got 429.
        subprocess.run([*base, "--skip-download", "--sub-langs", f"{lang}.*", "--", url], cwd=dl)
    subs = sorted(dl.glob("v.*.vtt"), key=lambda p: p.name != "v.en.vtt")
    return videos[0], (subs[0] if subs else None), info


def extract_frames(video, out, max_frames):
    """One keyframe per scene change, the max_frames most-changed kept. Returns their seconds."""
    fdir = out / "frames"
    fdir.mkdir()  # fails when it exists: this script deletes frames, so it only works in its own folder
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-skip_frame", "nokey", "-i", str(video),
         "-vf", "select='eq(n,0)+gt(scene,0.05)',metadata=print:file=-", "-fps_mode", "vfr",
         "-q:v", "3", str(fdir / "f_%05d.jpg")], capture_output=True, text=True)
    files = sorted(fdir.glob("f_*.jpg"))
    scores = SCORE.findall(r.stdout)
    if r.returncode or len(files) != len(scores):
        sys.exit(f"ffmpeg failed (exit {r.returncode}, {len(files)} files, {len(scores)} scores): {r.stderr[-800:]}")
    scored = [(int(float(t)), float(sc), f) for (t, sc), f in zip(scores, files)]
    kept = pick(scored, max_frames)
    for _, _, f in scored:
        if f not in [k[2] for k in kept]:
            f.unlink()
    for t, _, f in kept:
        f.rename(fdir / f"t{t:05d}.jpg")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-pattern_type", "glob", "-i",
                    str(fdir / "t*.jpg"), "-vf", "scale=480:-1,tile=4x4", "-fps_mode", "vfr",
                    str(out / "sheet_%02d.jpg")], check=True)
    return [k[0] for k in kept], len(scored)


def source_md(info, url, method, lines, seconds):
    """The text of source.md. Outside text is flattened or quoted with '| ': it is data from the video."""
    day = info.get("upload_date") or ""
    sheets = [f"sheet_{i // 16 + 1:02d}.jpg: " + " ".join(stamp(t) for t in seconds[i:i + 16])
              for i in range(0, len(seconds), 16)]
    chapters = [f"[{stamp(int(c['start_time']))}] {flat(c['title'])}" for c in info.get("chapters") or []]
    return "\n".join([
        f"URL: {flat(info.get('webpage_url', url))}",
        f"Author: {flat(info.get('uploader', '?'))}",
        f"Date: {day[:4]}-{day[4:6]}-{day[6:]}" if day else "Date: ?",
        f"Method: {method}",
        f"Title: {flat(info.get('title', '?'))}",
        f"Duration: {stamp(int(info.get('duration') or 0))}",
        "", "## Description", "", *(f"| {ln}" for ln in (info.get("description") or "(none)").splitlines()),
        "", "## Chapters", "", *(chapters or ["(none)"]),
        "", "## Contact sheets (left to right, then top to bottom)", "", *sheets,
        "", "## Transcript with frames", "", render(lines, seconds)])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--max-frames", type=int, default=48)
    a = ap.parse_args()
    if not re.match(r"https?://", a.url):
        sys.exit("The first argument must be an http(s) URL.")
    dl = a.out_dir / "dl"
    dl.mkdir(parents=True)
    video, vtt, info = download(a.url, dl)
    seconds, found = extract_frames(video, a.out_dir, a.max_frames)
    lines = parse_vtt(vtt.read_text()) if vtt else []
    method = (f"youtube_source.py; captions {vtt.name if vtt else 'NONE'}; "
              f"{len(seconds)} of {found} scene-change keyframes kept")
    (a.out_dir / "source.md").write_text(source_md(info, a.url, method, lines, seconds))
    print(f"{a.out_dir / 'source.md'}: {len(lines)} caption lines, {len(seconds)} frames, {-(-len(seconds) // 16)} sheets")
    if not lines:
        print("WARNING: no captions. Get the transcript with the fallback in getting-the-source.md.", file=sys.stderr)


if __name__ == "__main__":
    main()
