#!/usr/bin/env python3
"""Inventory the places in a repo where software makes (or should make) a verdict.

This is an INDEX, not a judgment. It lists candidate sites by signal so that nothing is
missed; reading each site decides whether Jev fits. Stdlib only, read-only.

  scan_sites.py [ROOT] [--out hits.json] [--top 5] [--exclude DIR ...] [--wrapper 'REGEX']
                [--library DIR ...] [--shards N --shard-out shards.json]
  scan_sites.py --self-test

--library scans an installed library as a second root and adds its ledger rows with absolute
paths. --shards splits every ledger row (repo and libraries) into N reader shards for the deep
mode workflow.

Signals: the docstring of site_signals.py says what each one finds.

The report ends with the LEDGER: one row per source file that is a likely site. Give every row a
one-line disposition before you read any site in depth. A scan under time pressure that skipped
this step covered one of three sites behind a wrapper and never looked at the reranker.

Most real repos call models through their OWN wrapper function, so the SDK signals find only
the wrapper. The report ends with probable wrapper names; re-run with --wrapper 'name1|name2'
so that every caller of the wrapper counts as a model call.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from site_signals import CALL_CATS, CALL_SHAPE, CODE_WORDING_CATS, GROUP_TITLES, MODEL_CATS, MODEL_FAMILY, PATTERNS, PROXY_WORDS, TOOL_PAYLOAD, WORDING_CATS, Sig

SKIP_DIRS = {"node_modules", "venv", "env", "dist", "build", "out", "target", "vendor", "site-packages",
             "__pycache__", "coverage", "Pods", "opensrc", "third_party", "worktrees"}
SKIP_NAMES = re.compile(r"(\.lock$|-lock\.(json|yaml)$|\.min\.(js|css)$|\.map$|\.snap$)")
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".scala", ".rb",
            ".php", ".cs", ".swift", ".ex", ".exs", ".clj", ".sh", ".sql"}
TEMPLATE_EXT = {".j2", ".jinja", ".jinja2", ".prompt", ".tmpl", ".mustache", ".hbs"}
CONFIG_EXT = {".yaml", ".yml", ".toml"}
# Prose and data are read only when the path says "prompt": a real repo holds hundreds of MB of
# docs, plans and eval data, and scanning it all took minutes and buried the code hits.
PROSE_EXT = {".md", ".txt", ".json"}
PROMPT_PATH = re.compile(r"prompt|template|instruction", re.IGNORECASE)
# The development loop: CI, git hooks and agent tooling. A model that reviews a diff or gates a commit
# makes a verdict too. The plain names count at the repo root only: src/hooks is React, not a git hook.
DEV_LOOP = re.compile(r"(^|/)\.(github|gitlab|circleci|husky|claude|cursor|codex|agents)/|^(hooks|skills|agents|commands)/|"
                      r"(^|/)(\.pre-commit-config|\.gitlab-ci|lefthook)\.ya?ml$")
# A test that fakes the model is plumbing; a test that calls a real one is a model-graded check.
# ponytail: word match, so a real judge that says "mock" in a comment drops out; parse imports if that bites
TEST_DOUBLE = re.compile(r"\b(mock|Mock|MagicMock|AsyncMock|monkeypatch|mocker|pytest_mock|respx|vcr|cassette)\b")
# The host agent (Claude Code, Codex, Cursor) picks one of these files per prompt: a Choice that no repo code
# makes, so no model call marks it. A skill folder was listed by no scan, and skill selection gets worse as the menu grows. A codex/ port inside a skill is a copy, not a choice.
# ponytail: the count is a floor: a symlinked skill folder is not followed, and the host agent also loads user
# and plugin skills. Follow symlinks in scan() if a disposition ever turns on the exact count.
HOST_CHOICE = re.compile(r"^((?:.*/)?skills)/[^/]+/SKILL\.md$|^((?:.*/)?(?:agents|commands))/[^/]+\.md$")
HOST_MIN = 10  # ponytail: a folder of fewer files is a short menu; the failure on record came on a large menu
MAX_BYTES = 400_000


CALL_SHAPE_RX = re.compile(CALL_SHAPE)
DEF_RE = re.compile(r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:def|function|func|fn)\s+([A-Za-z]\w{5,})\s*[(<]"
                    r"|^\s*(?:export\s+)?const\s+([A-Za-z]\w{5,})\s*=\s*(?:async\s*)?\(")
COMMON = {"create", "invoke", "handler", "execute", "process", "request", "generate"}


def list_files(root):
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
                             capture_output=True, text=True, timeout=120)
        if out.returncode == 0 and out.stdout.strip():
            return [root / p for p in out.stdout.splitlines()]
    except (OSError, subprocess.SubprocessError):
        pass
    found = []  # ponytail: no .gitignore parsing outside git; SKIP_DIRS and the dot-dir rule cover the usual bulk
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and (not x.startswith(".") or DEV_LOOP.search(x + "/"))]
        found += [Path(d) / f for f in files]
    return found


def skipped(path, exclude=()):
    """`path` is relative to the repo root: a PARENT directory named "build" must not hide the repo."""
    # Dot-directories are caches and editor state, except the ones that hold the development loop.
    dev = bool(DEV_LOOP.search(path.as_posix()))
    return (any(part in SKIP_DIRS or (part.startswith(".") and not dev) for part in path.parts[:-1])
            or bool(SKIP_NAMES.search(path.name)) or any(str(path).startswith(e.rstrip("/") + "/") for e in exclude))


def wanted(path, exclude=()):
    if skipped(path, exclude):
        return False
    ext = path.suffix.lower()
    if ext in CODE_EXT or ext in TEMPLATE_EXT or ext in CONFIG_EXT:
        return True
    return ext in PROSE_EXT and bool(PROMPT_PATH.search(str(path)))


def kind(rel):
    s = rel.lower()
    if re.search(r"(^|/)(tests?|__tests__|spec|e2e|fixtures?)(/|_)|(_test|\.test|\.spec)\.", s):
        return "test"
    if s.endswith((".md", ".txt")) or re.search(r"(^|/)docs?/", s):
        return "doc"
    return "src"


def suggest_wrappers(texts, top=12):
    """Functions defined beside a raw model call, ranked by how many OTHER files call them."""
    names = {}
    for rel, text in texts.items():
        if kind(rel) == "src" and CALL_SHAPE_RX.search(text):
            for m in DEF_RE.finditer(text):
                name = m.group(1) or m.group(2)
                if not name.startswith(("_", "test")) and name.lower() not in COMMON:
                    names.setdefault(name, rel)
    if not names:
        return []
    ref = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\s*\(")
    callers = defaultdict(set)
    for rel, text in texts.items():
        if kind(rel) == "src":
            for name in set(ref.findall(text)):
                if names[name] != rel:
                    callers[name].add(rel)
    ranked = sorted(callers.items(), key=lambda kv: -len(kv[1]))[:top]
    return [(name, names[name], len(files)) for name, files in ranked if len(files) >= 2]


BAND_WORDS = re.compile(r"uncategori[sz]ed|return ['\"](other|misc|miscellaneous)|review|undecided|uncertain|ambiguous|manual|low_confidence|fallback_to|"
                        + PROXY_WORDS, re.IGNORECASE)


def build_ledger(hits):
    """The rows a scan must account for. Source files, plus tests that call a real model: other tests and
    docs repeat what the source says."""
    def src(cat):  # program code only: config, shell and SQL files echo thresholds but hold no site
        return {f: rows for f, rows in hits.get(cat, {}).items()
                if kind(f) == "src" and Path(f).suffix.lower() in CODE_EXT - {".sh", ".sql"}}

    model = set(src("wrapper_call")) | set(src("llm_call"))
    asked = set(src("prompt_verdict")) | set(src("structured_output"))
    sig = lambda f: " ".join(f"{abbr}{len(hits[cat][f])}" for abbr, cat in  # noqa: E731
                             (("w", "wrapper_call"), ("l", "llm_call"), ("s", "structured_output"),
                              ("p", "prompt_verdict"), ("x", "rewrite_filter"), ("n", "null_answer"), ("c", "cost_cut"),
                              ("k", "checkpoint"), ("h", "heuristic_verdict"), ("r", "rerank"), ("g", "guard"), ("m", "model_router"), ("t", "tool_guard"), ("a", "agent_cli"))
                             if f in hits.get(cat, {}))
    rank = lambda f: (-len(hits["prompt_verdict"].get(f, [])) - len(hits["structured_output"].get(f, [])), f)  # noqa: E731
    top = lambda cat: sorted(src(cat), key=lambda f: (-len(hits[cat][f]), f))[:5]  # noqa: E731
    bands = [f for f, rows in src("heuristic_verdict").items() if any(BAND_WORDS.search(t) for _, t in rows)]
    def why(f, cat, only=None):  # the first two lines that made this file a row, so triage needs no file open
        rows = [(no, t) for no, t in hits[cat].get(f, []) if only is None or only.search(t)]
        return [f"{no}: {t[:110]}" for no, t in rows[:2]]

    # Code and config that RUN a model: a CI step is YAML. Prose that only names a CLI made 230 of 284 rows
    # on a test repo, so it stays out; the reader follows a row to the prompt it runs.
    dev = {f for cat in ("agent_cli", "llm_call", "wrapper_call") for f in hits.get(cat, {})
           if kind(f) == "src" and (f in hits.get("agent_cli", {}) or DEV_LOOP.search(f))}
    # A judge, an audit or a browser check under tests/ or e2e/ rules while the software is built.
    # One real project held 30 test files that call a model and listed none: 16 fake it, 7 run a model judge.
    dev |= {f for cat in ("agent_cli", "llm_call", "wrapper_call") for f in hits.get(cat, {})
            if kind(f) == "test" and f not in hits.get("test_double", {})}
    by_wording = lambda cat: [{"file": f, "signals": sig(f), "why": why(f, cat)}  # noqa: E731
                              for f in sorted(src(cat), key=lambda f: (-len(hits[cat][f]), f))]
    return {
        "cost_cuts": by_wording("cost_cut"), "screens": by_wording("null_answer"), "checkpoints": by_wording("checkpoint"),
        "dev_loop": [{"file": f, "signals": sig(f), "why": why(f, "agent_cli") or why(f, "prompt_verdict") or why(f, "llm_call")}
                     for f in sorted(dev, key=lambda f: (-len(hits["agent_cli"].get(f, [])), rank(f)))],
        "rewrites": [{"file": f, "signals": sig(f), "why": why(f, "rewrite_filter")}
                     for f in sorted(src("rewrite_filter"), key=lambda f: (-len(hits["rewrite_filter"][f]), f))],
        "model_sites": [{"file": f, "signals": sig(f), "why": why(f, "prompt_verdict") or why(f, "structured_output")}
                        for f in sorted(model & asked, key=rank)],
        # Libraries and tidy repos keep prompts apart from the call: the wording is here, the call is elsewhere.
        "prompt_files": [{"file": f, "signals": sig(f), "why": why(f, "prompt_verdict")}
                         for f in sorted(set(src("prompt_verdict")) - model, key=rank) if PROMPT_PATH.search(f)],
        "bands": [{"file": f, "signals": sig(f), "why": why(f, "heuristic_verdict", BAND_WORDS)}
                  for f in sorted(bands, key=lambda f: (-len(hits["heuristic_verdict"][f]), f))],
        "rerank_top": [{"file": f, "signals": sig(f)} for f in top("rerank")],
        "guard_top": [{"file": f, "signals": sig(f)} for f in top("guard")],
        # Config counts here: a tier map often lives in YAML (litellm, a routing file), not in code.
        "routers": [{"file": f, "signals": sig(f), "why": why(f, "model_router")}
                    for f, rows in sorted(hits.get("model_router", {}).items(), key=lambda kv: (-len(kv[1]), kv[0]))
                    if kind(f) == "src" and Path(f).suffix.lower() in (CODE_EXT - {".sh", ".sql"}) | CONFIG_EXT],
        # Shell counts here: a hook is often a shell script.
        "tool_guards": [{"file": f, "signals": sig(f), "why": why(f, "tool_guard")}
                        for f, rows in sorted(hits.get("tool_guard", {}).items(), key=lambda kv: (-len(kv[1]), kv[0]))
                        if kind(f) == "src" and Path(f).suffix.lower() in CODE_EXT],
        "host_choices": [{"file": d + "/", "signals": f"{len(rows)} files", "why": [f"e.g. {rows[0][1]}"]}
                         for d, rows in sorted(hits.get("host_choice", {}).items(), key=lambda kv: (-len(kv[1]), kv[0]))
                         if len(rows) >= HOST_MIN],
    }


DEEP_AUTO_ROWS = 90  # SKILL.md: above this row count the skill switches to deep mode by itself
GROUP_ORDER = ("bands", "cost_cuts", "rewrites", "screens", "checkpoints", "routers", "model_sites", "prompt_files", "rerank_top", "guard_top", "tool_guards", "dev_loop", "host_choices")


def merge_ledger_rows(ledgers):
    """One row per file across groups and roots, groups in priority order."""
    rows = {}
    for ledger in ledgers:
        for group in GROUP_ORDER:
            for r in ledger.get(group, []):
                row = rows.setdefault(r["file"], {"file": r["file"], "groups": [], "why": []})
                row["groups"].append(group)
                row["why"] = (row["why"] + [w for w in r.get("why", []) if w not in row["why"]])[:3]
    return list(rows.values())


def make_shards(rows, n):
    """Contiguous by directory, so one reader sees a module whole; balanced by row count."""
    rows = sorted(rows, key=lambda r: (str(Path(r["file"]).parent), r["file"]))
    if not rows:
        return []
    size = -(-len(rows) // max(1, min(n, len(rows))))
    return [{"index": i, "rows": rows[k:k + size]} for i, k in enumerate(range(0, len(rows), size))]


def library_ledgers(libraries):
    """Scan each installed library as its own root; its rows carry absolute paths."""
    out = []
    for lib in libraries:
        lroot = Path(lib).resolve()
        _, lhits, _ = scan(lroot)
        ledger = build_ledger(lhits)
        for rows in ledger.values():
            for r in rows:
                r["file"] = str(lroot / r["file"])
        print(f"library {lroot}: {sum(len(v) for v in ledger.values())} ledger rows")
        out.append(ledger)
    return out


def read_source(root, path, exclude):
    """-> (repo-relative name, text), or None when the file is not one the scan reads."""
    try:
        rel_path = path.relative_to(root)
    except ValueError:
        return None
    if not wanted(rel_path, exclude):
        return None
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        return str(rel_path), path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def match_file(rel, text, patterns, hits):
    low = text.lower()
    # Whole-file check first: most files match nothing, so the per-line pass runs rarely.
    live = {cat: sigs for cat, sigs in patterns.items() if any(s.search(text, low) for s in sigs)}
    # Verdict wording is ordinary prose. Keep it only beside a model call or in a prompt file.
    if not (any(c in live for c in MODEL_CATS) or PROMPT_PATH.search(rel)):
        for cat in WORDING_CATS:
            live.pop(cat, None)
    if not (any(c in live for c in CALL_CATS) or PROMPT_PATH.search(rel)):
        for cat in CODE_WORDING_CATS:
            live.pop(cat, None)
    if not live:
        return
    for no, line in enumerate(text.splitlines(), 1):
        if len(line) > 2000:
            continue
        line_low = line.lower()
        for cat, sigs in live.items():
            if any(s.search(line, line_low) for s in sigs):
                hits[cat][rel].append((no, line.strip()[:200]))


def scan(root, exclude=(), wrapper=None):
    patterns = dict(PATTERNS)
    if wrapper:
        patterns["wrapper_call"] = [Sig(r"\b(" + wrapper + r")\s*\(")]
    hits = defaultdict(lambda: defaultdict(list))  # category -> rel path -> [(line no, text)]
    texts, n_files = {}, 0
    for path in list_files(root):
        rel_path = path.relative_to(root)
        if (m := HOST_CHOICE.match(rel_path.as_posix())) and not skipped(rel_path, exclude):
            hits["host_choice"][m.group(1) or m.group(2)].append((1, rel_path.as_posix()))
        source = read_source(root, path, exclude)
        if source is None:
            continue
        rel, text = source
        n_files += 1
        if path.suffix.lower() in CODE_EXT:
            texts[rel] = text
        if kind(rel) == "test" and (m := TEST_DOUBLE.search(text)):
            hits["test_double"][rel].append((text.count("\n", 0, m.start()) + 1, m.group(0)))
        match_file(rel, text, patterns, hits)
        if rel in hits["model_router"] and len({m.group(0).lower() if not m.group(1) else m.group(1).lower()
                                                 for m in MODEL_FAMILY.finditer(text)}) < 2:
            del hits["model_router"][rel]  # one model named: a setting, not a choice between tiers
        if rel in hits["tool_guard"] and not TOOL_PAYLOAD.search(text):
            del hits["tool_guard"][rel]  # a script that exits 2 and reads no tool call is not a guard
    return n_files, hits, suggest_wrappers(texts)


def report(root, n_files, hits, wrappers, top):
    print(f"scanned {n_files} code, config and prompt files under {root}")
    for cat in (["wrapper_call"] if "wrapper_call" in hits else []) + list(PATTERNS):
        files = hits.get(cat, {})
        by_kind = defaultdict(int)
        for f in files:
            by_kind[kind(f)] += 1
        print(f"\n== {cat}: {sum(len(v) for v in files.values())} hits in {len(files)} files "
              f"(src {by_kind['src']}, test {by_kind['test']}, doc {by_kind['doc']})")
        ranked = sorted(files.items(), key=lambda kv: (kind(kv[0]) != "src", -len(kv[1]), kv[0]))
        for f, rows in ranked[:top]:
            no, line = rows[0]
            print(f"  {len(rows):>4}  [{kind(f)}] {f}:{no}  {line[:110]}")
        if len(ranked) > top:
            print(f"  ... {len(ranked) - top} more files in the JSON")
    if wrappers:
        print("\n== probable model wrappers (defined beside a raw model call; count = files that call them)")
        for name, where, n in wrappers:
            print(f"  {n:>4}  {name}  ({where})")
        print("  Confirm by reading, then re-run with --wrapper 'name1|name2'.")
    ledger = build_ledger(hits)
    print("\n== LEDGER: give every row a one-line disposition (site kind, or 'not a site: why') before deep reading")
    print("   signals: w=wrapper call l=model call s=schema p=verdict wording x=rewrite wording n=null answer c=cost cut k=checkpoint h=heuristic r=rerank g=guard m=model router t=tool guard a=agent CLI")
    for key, title in GROUP_TITLES.items():
        print(f"\n-- {key}: {len(ledger[key])} rows - {title}")
        for row in ledger[key]:
            print(f"   {row['file']}  [{row['signals']}]")
            for line in row.get("why", []):
                print(f"        {line}")
    print("\nThis is an index. A site with no hit here can still exist: follow the call graph from every "
          "llm_call file, and drop plan, archive and evidence directories with --exclude.")


def self_test():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for sub in ("app", "prompts", ".venv", ".github/workflows", "src/hooks", "node_modules/x", "e2e", "tests"):
            (root / sub).mkdir(parents=True)
        w = lambda rel, body: (root / rel).write_text(body, encoding="utf-8")  # noqa: E731
        w("app/judge.py", "from openai import OpenAI\n"
                          "r = client.chat.completions.create(model='m', response_format=Verdict)\n"
                          "PROMPT = 'Answer yes or no: does the passage support the claim?'\n")
        w("app/check.py", "from app.llm import ask_model\nout = ask_model('Delete any sentence that the passages do not support.')\n")
        w("app/match.py", "from rapidfuzz import fuzz\nREVIEW_THRESHOLD = 0.70\nif band == 'review':\n    log.info('x')\n")
        w("app/notes.md", "We classify nothing here. yes or no?\n")
        w("app/util.py", "# classify nothing here, yes or no\n")
        w("prompts/grade.md", "Rate each answer on a scale of 1 to 5.\n")
        w("app/llm.py", "def ask_model(prompt):\n    return client.chat.completions.create(model='m', messages=prompt)\n")
        for i in (1, 2):
            w(f"app/use{i}.py", "from app.llm import ask_model\nout = ask_model('Reply with one of the following')\n")
        w("app/monitor.py", "from app.llm import ask_model\nout = ask_model('If an alert is warranted, write it. Otherwise reply NONE.')\n"
                            "def f():\n    log('analysis_complete')\n    return None\n")
        w("app/posts.py", "from app.llm import ask_model\nsample = random.sample(posts, 200)  # a full run is too expensive\nask_model(sample)\n")
        w("app/loop.py", "from app.llm import ask_model\nMAX_STEPS = 25\ndone = ask_model('Explain whether the task is complete.')\n"
                         "def g():\n    return None\n")
        w("app/route.py", "@router.post('', response_model=Out)\ndef q():\n    is_complete = True  # too slow to compute here\n")
        w("app/tickets.py", "def categorize(t):\n    return 'other'\n")
        w(".venv/x.py", "import openai\n")
        w(".github/workflows/review.yml", "run: claude -p 'Does this diff weaken a test? Answer yes or no.'\n")
        w("src/hooks/useChat.py", "import openai\n")
        w("app/review.sh", "codex exec 'review the staged diff'\n")
        w("node_modules/x/i.js", "import OpenAI from 'openai'\n")

        n, hits, wrappers = scan(root)
        assert n == 16, n  # node_modules, .venv and plain docs skipped; the prompt file and the CI step are read
        assert "app/judge.py" in hits["llm_call"] and "app/judge.py" in hits["structured_output"]
        assert "app/judge.py" in hits["prompt_verdict"], "verdict wording beside a model call must be kept"
        assert "app/util.py" not in hits["prompt_verdict"], "wording far from any model call must be dropped"
        assert "prompts/grade.md" in hits["prompt_verdict"], "a prompt file must be read even when it is .md"
        assert len(hits["heuristic_verdict"]["app/match.py"]) == 3, hits["heuristic_verdict"]["app/match.py"]
        assert not any(f.startswith(".venv") for f in hits["llm_call"]), "other dot-directories are skipped"
        assert [x[0] for x in wrappers] == ["ask_model"], wrappers
        assert "app/use1.py" not in hits["prompt_verdict"], "a wrapper caller is unseen until --wrapper names it"
        _, hits2, _ = scan(root, wrapper="ask_model")
        assert "app/use1.py" in hits2["prompt_verdict"] and "app/use1.py" in hits2["wrapper_call"]
        led = build_ledger(hits2)
        assert {r["file"] for r in led["dev_loop"]} == {".github/workflows/review.yml", "app/review.sh"}, \
            f"an agent CLI counts anywhere; a React hooks directory is not the development loop: {led['dev_loop']}"
        assert [r["file"] for r in led["rewrites"]] == ["app/check.py"], led["rewrites"]
        assert [r["file"] for r in led["screens"]] == ["app/monitor.py"], f"`return None` is code, not a null answer: {led['screens']}"
        assert [r["file"] for r in led["cost_cuts"]] == ["app/posts.py"], led["cost_cuts"]
        assert [r["file"] for r in led["checkpoints"]] == ["app/loop.py"], \
            f"a schema word alone opens no gate, and analys-is_complete is no checkpoint: {led['checkpoints']}"
        assert not build_ledger(hits)["screens"], "null-answer wording is unseen until --wrapper names the model call"
        w("prompts/judge.py", "PROMPT = 'Answer yes or no: is this the same entity?'\n")
        assert "prompts/judge.py" in {r["file"] for r in build_ledger(scan(root)[1])["prompt_files"]}
        assert [r["file"] for r in led["bands"]] == ["app/match.py", "app/tickets.py"], led["bands"]  # a catch-all bucket is a band
        assert "review" in led["bands"][0]["why"][0].lower(), led["bands"][0]
        assert {"app/judge.py", "app/use1.py", "app/use2.py"} <= {r["file"] for r in led["model_sites"]}, led["model_sites"]
        assert "app/llm.py" not in {r["file"] for r in led["model_sites"]}, "plumbing with no verdict wording stays out"
        _, hits3, _ = scan(root, exclude=["prompts"])
        assert "prompts/grade.md" not in hits3["prompt_verdict"]
        assert kind("tests/test_x.py") == "test" and kind("docs/a.md") == "doc" and kind("app/judge.py") == "src"
        merged = merge_ledger_rows([led, {"bands": [{"file": "app/match.py", "why": ["9: x"]}]}])
        match = next(r for r in merged if r["file"] == "app/match.py")
        assert match["groups"] == ["bands", "bands"] and len(match["why"]) <= 3, match
        assert len({r["file"] for r in merged}) == len(merged), "one row per file"
        shards = make_shards(merged, 2)
        assert len(shards) == 2 and sum(len(sh["rows"]) for sh in shards) == len(merged), shards
        assert make_shards(merged, 50)[-1]["index"] == len(merged) - 1 and make_shards([], 3) == []
        w("e2e/checkout_judge.py", "import anthropic\nok = client.messages.create(model='m', messages='Does the page show an error? Answer yes or no.')\n")
        w("tests/test_llm.py", "from unittest.mock import patch\nimport anthropic\nclient.messages.create(model='m')\n")
        dev = {r["file"] for r in build_ledger(scan(root)[1])["dev_loop"]}
        assert "e2e/checkout_judge.py" in dev, f"a model that rules inside a test is the development loop: {dev}"
        assert "tests/test_llm.py" not in dev, f"a unit test that fakes the model is not a site: {dev}"
        for i in range(HOST_MIN):
            (root / f"skills/s{i}").mkdir(parents=True)
            w(f"skills/s{i}/SKILL.md", "---\nname: s\n---\n")
        (root / "skills/s0/codex").mkdir()
        w("skills/s0/codex/SKILL.md", "a port\n")
        (root / "agents").mkdir()
        w("agents/a.md", "one agent\n")
        host = build_ledger(scan(root)[1])["host_choices"]
        assert [(r["file"], r["signals"]) for r in host] == [("skills/", "10 files")], \
            f"a port is no choice, and one agent is a short menu: {host}"
        assert not build_ledger(scan(root, exclude=["skills"])[1])["host_choices"], "--exclude drops a choice folder"
        w("app/style.py", '"""Score a reply for plain language. Deterministic proxies only."""\nIDIOMS = ["fell over"]\n')
        w("app/safety.py", 'BANNED_ACTIONS = ("SendItem", "DeleteItem")\n')
        bands = {r["file"] for r in build_ledger(scan(root)[1])["bands"]}
        assert "app/style.py" in bands, f"a self-declared proxy for a judgment is a band: {bands}"
        assert "app/safety.py" not in bands, f"an allowlist is an exact rule, not a judgment: {bands}"
        w("app/router.py", 'MODEL_BY_TIER = {"simple": "claude-haiku-4-5", "complex": "claude-opus-5-5"}\n'
                           "def pick_model(req):\n    return MODEL_BY_TIER[classify_tier(req)]\n")
        w("app/settings.py", 'def pick_model():\n    return "claude-sonnet-5"  # the sonnet default\n')
        w("app/pricing.py", 'PRICE = {"haiku": 1, "opus": 5}\n')
        (root / "deploy").mkdir()
        w("deploy/router.yaml", "task_class:\n  simple: gpt-5-mini\n  hard: gpt-5.6\n")
        routers = [r["file"] for r in build_ledger(scan(root)[1])["routers"]]
        assert sorted(routers) == ["app/router.py", "deploy/router.yaml"], \
            f"a tier map in code or config is a router; one model named twice, or a price table, is not: {routers}"
    print("self-test ok")
    return 0


def self_test_tool_guards():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "hooks").mkdir()
        (root / "tests").mkdir()
        w = lambda rel, body: (root / rel).write_text(body, encoding="utf-8")  # noqa: E731
        w("hooks/bash_guard.py", "cmd = json.load(sys.stdin)['tool_input']['command']\nif BLOCKED.search(cmd):\n"
                                 "    print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'deny'}}))\n")
        w("hooks/tree_guard.py", "def main():\n    cmd = payload.get('tool_input')\n    if DESTRUCTIVE.search(cmd):\n        return 2\n")
        w("hooks/write_guard.sh", 'path=$(jq -r .tool_input.file_path)\ncase "$path" in *.env) exit 2;; esac\n')
        w("hooks/usage_log.py", "name = payload['tool_input']['skill']\nprint(json.dumps({'permissionDecision': 'allow'}))\n")
        w("hooks/lint.sh", "ruff check . || exit 2\n")
        w("tests/test_guard.py", "payload = {'tool_input': {}}\nassert run(payload) == 2  # exit 2\n")
        guards = [r["file"] for r in build_ledger(scan(root)[1])["tool_guards"]]
        assert guards == ["hooks/bash_guard.py", "hooks/tree_guard.py", "hooks/write_guard.sh"], \
            f"a guard refuses a tool call; an observer allows, a plain script reads no tool call, a test is no site: {guards}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--out", help="write every hit as JSON here")
    ap.add_argument("--top", type=int, default=5, help="files shown per signal (the ledger is the main output)")
    ap.add_argument("--exclude", action="append", default=[], help="repo-relative directory to skip (repeatable)")
    ap.add_argument("--wrapper", help="regex of the repo's own model-call function names, e.g. 'ask_model|complete_text'")
    ap.add_argument("--library", action="append", default=[], help="installed library directory to scan as well")
    ap.add_argument("--shards", type=int, default=0, help="deep mode: split all ledger rows into N reader shards")
    ap.add_argument("--shard-out", help="deep mode: write the shards here as JSON")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        self_test_tool_guards()
        return self_test()
    root = Path(a.root).resolve()
    n_files, hits, wrappers = scan(root, a.exclude, a.wrapper)
    report(root, n_files, hits, wrappers, a.top)
    ledgers = [build_ledger(hits)] + library_ledgers(a.library)
    rows = merge_ledger_rows(ledgers)
    print(f"\nledger: {len(rows)} unique rows (repo and libraries); deep mode starts above {DEEP_AUTO_ROWS}")
    if a.shards and a.shard_out:
        shards = make_shards(rows, a.shards)
        Path(a.shard_out).write_text(json.dumps({"repo": str(root), "n_rows": len(rows), "shards": shards}, indent=1),
                                     encoding="utf-8")
        print(f"\nshards: {len(rows)} ledger rows in {len(shards)} shards of "
              f"{[len(sh['rows']) for sh in shards]} rows -> {a.shard_out}")
    if a.out:
        Path(a.out).write_text(json.dumps({
            "ledger": build_ledger(hits),
            "hits": {cat: {f: [{"line": no, "text": t} for no, t in rows] for f, rows in files.items()}
                     for cat, files in hits.items()}}, indent=1), encoding="utf-8")
        print(f"all hits: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
