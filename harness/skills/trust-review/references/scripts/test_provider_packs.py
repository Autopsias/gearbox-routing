"""Public tests: provider-pack interface and the Markdown-folder corpus. Names no private provider.

Each test imports a FRESH copy of the registry from a scratch folder that holds only the public
files, so the result never depends on which packs sit beside the real registry.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PUBLIC = tuple(f.name for f in HERE.glob("*.py")
               if not f.name.startswith(("test_", "provider_pack_")))   # every published script, no pack
PACK = '''
PROVIDERS = {
    "internal_search": [
        {"id": "acme_mcp", "kind": "mcp", "match": [["acme"], ["search"]], "note": "synthetic",
         "before": "obsidian", "preferred": True},
        {"id": "acme_cli", "kind": "local", "note": "synthetic", "before": "obsidian", "preferred": True}],
    "internal_read": [
        {"id": "acme_mcp_read", "kind": "mcp", "match": [["acme"], ["get"]], "note": "synthetic",
         "before": "obsidian_read", "preferred": True}],
}
THEME = {"accent": "#123456"}
def local_caps():
    return ["acme_cli"]
'''


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    d = tmp_path / "scripts"
    d.mkdir()
    for f in PUBLIC:
        shutil.copy(HERE / f, d / f)
    for var in ("OTL_PROVIDER_PACKS", "TRUST_CORPUS_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.syspath_prepend(str(d))
    for m in ("local_corpus", "pack_loader", "internal_corpus_adapter", "verification"):
        sys.modules.pop(m, None)
    return d


def fresh(d):
    name = "ar_" + str(abs(hash(str(d))))
    spec = importlib.util.spec_from_file_location(name, d / "adapter_registry.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod      # dataclasses resolves annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def write_pack(tmp, body=PACK, name="synthetic_pack.py"):
    p = Path(tmp) / name
    p.write_text(body)
    return str(p)


def corpus(tmp):
    c = Path(tmp) / "notes"
    (c / "Trust Layer" / "Quarantine").mkdir(parents=True)
    (c / "sub").mkdir()
    (c / "a.md").write_text("Revenue grew 12 percent in 2025.\nOther line.\n")
    (c / "sub" / "b.md").write_text("Churn fell to 3 percent.\n")
    (c / "Trust Layer" / "Quarantine" / "q.md").write_text("source_authority: derived-provenance\nZebra quokka claim.\n")
    return c


def test_no_pack_binds_no_private_id(scratch):
    R = fresh(scratch)
    reg = R.AdapterRegistry(available_tools=["mcp__acme__search", "mcp__obsidian__search"], local_caps=[])
    assert R.PACKS_LOADED == []
    assert reg.resolve("internal_search").provider_id == "obsidian"
    for op, ad in reg.resolve_all().items():
        assert ad is None or ad.provider_id.startswith(("acme",)) is False


def test_synthetic_pack_binds_preferred_before_generic(scratch, tmp_path, monkeypatch):
    monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path))
    R = fresh(scratch)
    assert len(R.PACKS_LOADED) == 1 and R.PACKS_FAILED == []
    reg = R.AdapterRegistry(available_tools=["mcp__acme__search", "mcp__acme__get", "mcp__obsidian__search"],
                            local_caps=[])
    for op, pid in (("internal_search", "acme_mcp"), ("internal_read", "acme_mcp_read")):
        ad = reg.resolve(op)
        assert ad.provider_id == pid and not ad.degraded
    # known negative: without the tools the generic rung is back, and it IS degraded
    reg2 = R.AdapterRegistry(available_tools=["mcp__obsidian__search"], local_caps=[])
    assert reg2.resolve("internal_search").provider_id == "obsidian" and reg2.resolve("internal_search").degraded
    assert R.PACK_HOOKS["theme"] == {"accent": "#123456"}
    assert "acme_cli" in R._pack_local_caps()


def test_broken_pack_is_skipped_and_reported(scratch, tmp_path, monkeypatch):
    bad = write_pack(tmp_path, "raise RuntimeError('boom')\n", "bad_pack.py")
    worse = write_pack(tmp_path, "PROVIDERS = {'no_such_op': [{'id': 'x', 'kind': 'local'}]}\n", "worse_pack.py")
    good = write_pack(tmp_path)
    import os
    monkeypatch.setenv("OTL_PROVIDER_PACKS", os.pathsep.join([bad, worse, good]))
    R = fresh(scratch)
    assert [Path(f).name for f, _ in R.PACKS_FAILED] == ["bad_pack.py", "worse_pack.py"]
    assert "boom" in R.PACKS_FAILED[0][1]
    assert [Path(p).name for p in R.PACKS_LOADED] == ["synthetic_pack.py"]
    assert R.AdapterRegistry([], []).resolve("internal_search").kind == R.KIND_BUILTIN


def test_unknown_before_id_lands_ahead_of_the_builtin_terminal(scratch, tmp_path, monkeypatch):
    body = PACK.replace('"before": "obsidian_read"', '"before": "no_such_id"')
    monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path, body))
    R = fresh(scratch)
    ids = [p.id for p in R.OPERATIONS["internal_read"].providers]
    assert ids.index("acme_mcp_read") < ids.index("packet_excerpt") == len(ids) - 1


def test_pack_file_beside_registry_is_discovered(scratch):
    (scratch / "provider_pack_synth.py").write_text(PACK)
    R = fresh(scratch)
    assert [Path(p).name for p in R.PACKS_LOADED] == ["provider_pack_synth.py"]


def test_corpus_dir_binds_preferred_and_answers(scratch, tmp_path, monkeypatch):
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(corpus(tmp_path)))
    R = fresh(scratch)
    reg = R.AdapterRegistry.probe(available_tools=[])
    for op, pid in (("internal_search", "local_vault_rg"), ("internal_read", "local_vault_read")):
        ad = reg.resolve(op)
        assert ad.provider_id == pid and not ad.degraded
    assert reg.resolve("internal_semantic_search").provider_id == "lexical_fallback"
    assert reg.resolve("vault_write").provider_id == "local_vault_write"
    import internal_corpus_adapter as C
    import local_corpus as L
    rows = C.parse_internal_search(L.search("revenue percent"))
    assert rows and rows[0]["locator"].endswith("a.md") and "Revenue" in rows[0]["snippet"]
    assert "Revenue" in L.read(rows[0]["locator"])


def test_read_only_corpus_does_not_advertise_write(scratch, tmp_path, monkeypatch):
    c = corpus(tmp_path)
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c))
    R = fresh(scratch)
    assert "local_vault_write" in R._corpus_caps()          # known positive: writable corpus
    q = c / "Trust Layer" / "Quarantine"
    q.chmod(0o555)
    try:
        if os.access(q, os.W_OK):
            pytest.skip("running as a user that ignores directory modes")
        caps = R._corpus_caps()
        assert "local_vault_rg" in caps and "local_vault_read" in caps and "local_vault_write" not in caps
        reg = R.AdapterRegistry.probe(available_tools=[])
        assert reg.resolve("vault_write").provider_id != "local_vault_write"
    finally:
        q.chmod(0o755)


def test_fallback_walk_reports_unreadable_directory(scratch, tmp_path, monkeypatch):
    c = corpus(tmp_path)
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c))
    import local_corpus as L
    monkeypatch.setattr(L.shutil, "which", lambda _n: None)
    (c / "sub").chmod(0o000)
    try:
        if os.access(c / "sub", os.R_OK):
            pytest.skip("running as a user that ignores directory modes")
        out = L.search("revenue grew")
        assert out["incomplete"] and any("sub" in e for e in out["errors"])
    finally:
        (c / "sub").chmod(0o755)


def test_unset_corpus_dir_binds_nothing_and_missing_one_fails_loudly(scratch, tmp_path, monkeypatch):
    monkeypatch.delenv("TRUST_CORPUS_DIR", raising=False)
    assert fresh(scratch)._corpus_caps() == []
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(tmp_path / "nope"))
    R = fresh(scratch)
    import local_corpus as L
    assert R._corpus_caps() and L.search("revenue grew")["error"]   # bound, so the failure is visible, not a clean empty


@pytest.mark.parametrize("use_rg", [True, False])
def test_quarantine_never_returned(scratch, tmp_path, monkeypatch, use_rg):
    c = corpus(tmp_path)
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c))
    import local_corpus as L
    if not use_rg:
        monkeypatch.setattr(L.shutil, "which", lambda _n: None)
    elif not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    # known positive: the same sentence in a normal note IS found
    assert L.search("zebra quokka") == {"query": "zebra quokka", "results": []}
    (c / "sub" / "c.md").write_text("Zebra quokka claim.\n")
    hits = L.search("zebra quokka")["results"]
    assert [Path(h["path"]).name for h in hits] == ["c.md"]
    with pytest.raises(PermissionError):
        L.read(str(c / "Trust Layer" / "Quarantine" / "q.md"))


def test_write_lands_only_in_quarantine(scratch, tmp_path, monkeypatch):
    c = corpus(tmp_path)
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c))
    import local_corpus as L
    dest = L.write_quarantined("../../evil/x", "note")
    assert dest.parent == c / "Trust Layer" / "Quarantine" and dest.read_text() == "note"
    assert L.search("note")["results"] == []


def test_resolve_command_json(scratch, tmp_path):
    out = subprocess.run([sys.executable, str(scratch / "adapter_registry.py"), "--resolve", "--json",
                          "--tools", "mcp__acme__search", "--local-caps", ""],
                         capture_output=True, text=True, check=True,
                         env={"PATH": "/usr/bin:/bin", "OTL_PROVIDER_PACKS": write_pack(tmp_path)})
    info = json.loads(out.stdout)
    assert info["bindings"]["internal_search"] == {"provider": "acme_mcp", "degraded": False}
    assert len(info["packs_loaded"]) == 1 and info["packs_failed"] == []


def test_malformed_hook_leaves_registry_unchanged(scratch, tmp_path, monkeypatch):
    bad = write_pack(tmp_path, PACK + "\nPARSERS = None\n", "badhook_pack.py")
    monkeypatch.setenv("OTL_PROVIDER_PACKS", bad)
    R = fresh(scratch)
    assert len(R.PACKS_FAILED) == 1 and R.PACKS_LOADED == []
    assert "acme_mcp" not in [p.id for p in R.OPERATIONS["internal_search"].providers]
    assert R.PACK_HOOKS == {"parsers": {}, "labels": [], "theme": {}}


def test_quarantine_root_and_symlink_bypasses(scratch, tmp_path, monkeypatch):
    c = corpus(tmp_path)
    import local_corpus as L
    # a corpus root that is itself inside quarantine is refused
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c / "Trust Layer" / "Quarantine"))
    assert L.corpus_dir() is None
    # a Markdown symlink pointing at a quarantined note is never returned or read
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c))
    (c / "alias.md").symlink_to(c / "Trust Layer" / "Quarantine" / "q.md")
    assert L.search("zebra quokka")["results"] == []
    with pytest.raises(PermissionError):
        L.read("alias.md")
    # known positive: a plain note still reads
    assert "Revenue" in L.read("a.md")


def test_rg_failure_is_an_error_not_clean(scratch, tmp_path, monkeypatch):
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    c = corpus(tmp_path)
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c))
    import local_corpus as L
    real = subprocess.run
    monkeypatch.setattr(L.subprocess, "run", lambda a, **k: subprocess.CompletedProcess(a, 2, "", "boom"))
    with pytest.raises(L.CorpusSearchError):
        L.search("revenue grew")
    monkeypatch.setattr(L.subprocess, "run", real)
    assert L.search("revenue grew")["results"]   # known positive


def test_fallback_reports_unreadable_file(scratch, tmp_path, monkeypatch):
    c = corpus(tmp_path)
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c))
    import local_corpus as L
    monkeypatch.setattr(L.shutil, "which", lambda _n: None)
    assert "incomplete" not in L.search("revenue grew")
    real = Path.read_text

    def bad(self, *a, **k):
        if self.name == "b.md":
            raise PermissionError("denied")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", bad)
    out = L.search("revenue grew")
    assert out["incomplete"] and any("b.md" in e for e in out["errors"])


def test_quarantine_write_refuses_symlinks(scratch, tmp_path, monkeypatch):
    c = corpus(tmp_path)
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c))
    import local_corpus as L
    outside = tmp_path / "outside"
    outside.mkdir()
    q = c / "Trust Layer" / "Quarantine"
    shutil.rmtree(q)
    q.symlink_to(outside)
    with pytest.raises(PermissionError):
        L.write_quarantined("x", "note")
    assert not list(outside.iterdir())
    q.unlink()
    q.mkdir()
    (q / "y.md").symlink_to(outside / "t.md")
    with pytest.raises(PermissionError):
        L.write_quarantined("y", "note")
    assert not (outside / "t.md").exists()


@pytest.mark.parametrize("match", [None, [], (), [[]], [[""]], [["  "]], [[1]], ["abc"]])
def test_mcp_pack_without_match_is_rejected(scratch, tmp_path, match):
    entry = '{"id": "bad_mcp", "kind": "mcp", "note": "x"' + ("" if match is None else f', "match": {match!r}') + "}"
    pack = write_pack(tmp_path, f'PROVIDERS = {{"internal_search": [{entry}]}}\n', "bad.py")
    import os
    os.environ["OTL_PROVIDER_PACKS"] = pack
    try:
        R = fresh(scratch)
    finally:
        del os.environ["OTL_PROVIDER_PACKS"]
    assert [f[0] for f in R.PACKS_FAILED] == [pack]
    assert "bad_mcp" not in [p.id for p in R.OPERATIONS["internal_search"].providers]


def test_mcp_pack_with_valid_match_loads(scratch, tmp_path):
    import os
    pack = write_pack(tmp_path, 'PROVIDERS = {"internal_search": [{"id": "ok_mcp", "kind": "mcp", "match": [["abc"], ["x", "y"]]}]}\n', "ok.py")
    os.environ["OTL_PROVIDER_PACKS"] = pack
    try:
        R = fresh(scratch)
    finally:
        del os.environ["OTL_PROVIDER_PACKS"]
    assert not R.PACKS_FAILED
    assert "ok_mcp" in [p.id for p in R.OPERATIONS["internal_search"].providers]


def test_rg_on_path_alone_does_not_bind_corpus(scratch, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: "/usr/bin/rg" if n == "rg" else None)
    R = fresh(scratch)
    assert "local_vault_rg" not in R.detect_local_caps()


def test_fallback_search_never_opens_symlink_leaving_corpus(scratch, tmp_path, monkeypatch):
    import local_corpus as L
    c = corpus(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("zebra secret line\n")
    (c / "leak.md").symlink_to(outside / "secret.md")
    monkeypatch.setenv("TRUST_CORPUS_DIR", str(c))
    monkeypatch.setattr(L.shutil, "which", lambda *a, **k: None)   # force the stdlib walk
    opened = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (opened.append(self.name), real(self, *a, **k))[1])
    L.search("zebra secret")
    assert "leak.md" not in opened


@pytest.mark.parametrize("entry", ['{"id": "k", "kind": "bogus", "note": "x"}'])
def test_unknown_provider_kind_fails_pack(scratch, tmp_path, monkeypatch, entry):
    pack = write_pack(tmp_path, f'PROVIDERS = {{"internal_search": [{entry}]}}\n', "kind.py")
    monkeypatch.setenv("OTL_PROVIDER_PACKS", pack)
    R = fresh(scratch)
    assert [f[0] for f in R.PACKS_FAILED] == [pack] and "'k'" in R.PACKS_FAILED[0][1]
    assert "k" not in [p.id for p in R.OPERATIONS["internal_search"].providers]


def test_non_callable_parser_fails_pack(scratch, tmp_path, monkeypatch):
    pack = write_pack(tmp_path, PACK + "\nPARSERS = {'x': 'notcallable'}\n", "parser.py")
    monkeypatch.setenv("OTL_PROVIDER_PACKS", pack)
    R = fresh(scratch)
    assert len(R.PACKS_FAILED) == 1 and R.PACKS_LOADED == []


def test_systemexit_in_pack_import_and_local_caps_is_contained(scratch, tmp_path, monkeypatch):
    exits = write_pack(tmp_path, "raise SystemExit(3)\n", "exits.py")
    capsx = write_pack(tmp_path, "def local_caps():\n    raise SystemExit(4)\n", "capsx.py")
    monkeypatch.setenv("OTL_PROVIDER_PACKS", os.pathsep.join([exits, capsx]))
    R = fresh(scratch)
    assert [Path(f[0]).name for f in R.PACKS_FAILED] == ["exits.py", "capsx.py"]   # both fail at load
    assert R._pack_local_caps() == []


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs non-root POSIX")
def test_corpus_dir_requires_search_permission(tmp_path, monkeypatch):
    import local_corpus as L
    d = tmp_path / "noexec"
    d.mkdir()
    d.chmod(0o600)   # readable, not searchable: listing and opening would fail
    try:
        monkeypatch.setenv("TRUST_CORPUS_DIR", str(d))
        assert L.corpus_dir() is None
    finally:
        d.chmod(0o700)


@pytest.mark.parametrize("body", [
    'PROVIDERS = {"internal_search": [{"id": ["x"], "kind": "local"}]}',
    'PROVIDERS = {"internal_search": [{"id": "", "kind": "local"}]}',
])
def test_non_string_provider_id_fails_pack(scratch, tmp_path, monkeypatch, body):
    monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path, body))
    R = fresh(scratch)
    R.AdapterRegistry.probe(available_tools=[]).resolve("internal_search")   # must not raise TypeError
    assert R.PACKS_FAILED and "ValueError" in R.PACKS_FAILED[0][1]


def test_non_string_local_cap_is_reported_not_crashing(scratch, tmp_path, monkeypatch):
    p = write_pack(tmp_path, "def local_caps():\n    return [['bad']]\n")
    monkeypatch.setenv("OTL_PROVIDER_PACKS", p)
    R = fresh(scratch)
    R.AdapterRegistry.probe(available_tools=[]).resolve("internal_search")
    assert R.PACKS_LOADED == [] and "non-empty strings" in R.PACKS_FAILED[0][1]


def test_can_write_refuses_symlinked_ancestor_of_quarantine(scratch, tmp_path):
    import local_corpus as L
    c = corpus(tmp_path)
    assert L.can_write(c.resolve())   # known positive
    real = tmp_path / "elsewhere"
    (real / "Quarantine").mkdir(parents=True)
    shutil.rmtree(c / "Trust Layer")
    (c / "Trust Layer").symlink_to(real)
    assert not L.can_write(c.resolve())


def test_malformed_label_row_fails_pack(scratch, tmp_path, monkeypatch):
    monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path, 'LABELS = [("token-only",)]'))
    R = fresh(scratch)
    assert R.PACKS_LOADED == [] and "LABELS" in R.PACKS_FAILED[0][1]


def test_one_pack_reached_by_two_spellings_loads_once(scratch, tmp_path, monkeypatch):
    p = write_pack(tmp_path, PACK, "alias_pack.py")
    link = tmp_path / "alias_link.py"
    link.symlink_to(p)
    monkeypatch.setenv("OTL_PROVIDER_PACKS", os.pathsep.join([p, str(link)]))
    R = fresh(scratch)
    assert len(R.PACKS_LOADED) == 1 and R.PACKS_FAILED == []


def test_string_local_caps_result_is_a_failure_not_characters(scratch, tmp_path, monkeypatch):
    monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path, 'def local_caps():\n    return "abc"\n'))
    R = fresh(scratch)
    assert R._pack_local_caps() == [] and "list of strings" in R.PACKS_FAILED[-1][1]


def test_provider_behind_a_pack_builtin_fails_pack(scratch, tmp_path, monkeypatch):
    R0 = fresh(scratch)
    op = "internal_search"
    fallback = next(x.id for x in R0.OPERATIONS[op].providers if x.kind == "builtin")
    body = ('PROVIDERS = {"%s": [{"id": "b1", "kind": "builtin"}, '
            '{"id": "l1", "kind": "local", "before": "%s"}]}' % (op, fallback))
    monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path, body, "behind.py"))
    R = fresh(scratch)
    assert R.PACKS_LOADED == [] and "never run" in R.PACKS_FAILED[0][1]


def test_builtin_placed_before_other_providers_fails_pack(scratch, tmp_path, monkeypatch):
    R0 = fresh(scratch)
    op = "internal_search"
    first = R0.OPERATIONS[op].providers[0].id
    body = 'PROVIDERS = {"%s": [{"id": "b2", "kind": "builtin", "before": "%s"}]}' % (op, first)
    monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path, body, "hide.py"))
    R = fresh(scratch)
    assert R.PACKS_LOADED == [] and "would hide" in R.PACKS_FAILED[0][1]


def test_string_label_row_fails_pack(scratch, tmp_path, monkeypatch):
    monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path, 'LABELS = ["ab"]', "strlabel.py"))
    R = fresh(scratch)
    assert R.PACKS_LOADED == [] and "LABELS" in R.PACKS_FAILED[0][1]


def test_pack_with_malformed_caps_adds_no_providers(scratch, tmp_path, monkeypatch):
    body = ('PROVIDERS = {"internal_search": [{"id": "capx", "kind": "mcp", "match": [["capx"]]}]}\n'
            'def local_caps():\n    return "abc"\n')
    monkeypatch.setenv("OTL_PROVIDER_PACKS", write_pack(tmp_path, body, "capsbad.py"))
    R = fresh(scratch)
    assert R.PACKS_LOADED == [] and "list of strings" in R.PACKS_FAILED[0][1]
    assert all(p.id != "capx" for p in R.OPERATIONS["internal_search"].providers)
