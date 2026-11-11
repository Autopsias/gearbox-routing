"""Colour theme for the human-gate digest page: a neutral default plus an optional override.

A theme is a mapping of CSS variable to value. A key ending in `@dark` applies in dark mode only.
The override comes from the caller, or from the `THEME` hook of an installed provider pack.
"""
from __future__ import annotations

import os
import re
import sys

NEUTRAL = {
    "--accent": "#2F5D8A", "--accent-dark": "#1F4468", "--accent-tint": "rgba(47,93,138,.10)",
    "--accent@dark": "#6FA3D6",
}
_KEY = re.compile(r"^--[a-z][a-z0-9-]*(@dark)?$")
_VAL = re.compile(r"^[#\w(),.%\s-]{1,60}$")  # no `;`, `{`, `}` or quotes: a value cannot break out of the rule


def pack_theme() -> dict:
    """The THEME of the installed provider packs, or {} when there is none. Never raises."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "..", "references", "scripts"))
        try:
            import adapter_registry as AR
            return dict(AR.PACK_HOOKS.get("theme") or {})
        finally:
            sys.path.pop(0)
    except Exception:  # noqa: BLE001 - no registry or no pack means the neutral theme
        return {}


def theme_css(theme=None) -> str:
    """The `:root` rules for `theme` laid over the neutral default (invalid entries are ignored)."""
    merged = dict(NEUTRAL)
    merged.update({k: str(v) for k, v in dict(theme or {}).items()
                   if _KEY.match(str(k)) and _VAL.match(str(v))})
    light = ";".join("%s:%s" % kv for kv in merged.items() if not kv[0].endswith("@dark"))
    dark = ";".join("%s:%s" % (k[:-5], v) for k, v in merged.items() if k.endswith("@dark"))
    return ":root{%s}\n@media(prefers-color-scheme:dark){:root{%s}}" % (light, dark)


BASE_CSS = r""":root{--bg:#FAF9F5;--surface:#FFFFFF;--surface-2:#F5F2EA;
--border:#E4DECE;--text:#1B1814;--muted:#706D65;--ok:#02911A;--warn:#C97800;--bad:#B43030;}
@media(prefers-color-scheme:dark){:root{--bg:#14110D;--surface:#1B1814;--surface-2:#221E18;
--border:#332D24;--text:#FAF9F5;--muted:#C6BFA8;}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);
font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
font-size:15px;line-height:1.5}
.wrap{max-width:1040px;margin:0 auto;padding:24px clamp(16px,3vw,40px)}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;
border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:18px;flex-wrap:wrap}
h1{font-size:23px;margin:0;font-weight:650;letter-spacing:-0.01em}
h1 em{font-style:normal;color:var(--accent)}
.verdict{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
padding:7px 14px;border-radius:999px;color:#fff;white-space:nowrap}
.sub{color:var(--muted);font-size:13px;margin-top:4px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:16px 0}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.kpi .v{font-size:26px;font-weight:720;line-height:1}.kpi .l{font-size:12px;color:var(--muted);margin-top:4px}
.kpi.alarm{border-color:var(--bad)}.kpi.alarm .v{color:var(--bad)}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--border);
border-radius:12px;overflow:hidden;font-size:13px}
th{background:var(--surface-2);text-align:left;padding:9px 11px;font-size:11px;text-transform:uppercase;
letter-spacing:.04em;color:var(--muted)}
td{padding:9px 11px;border-top:1px solid var(--border);vertical-align:top}
td.num,td.rank{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
code{font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
.ev{color:var(--muted);font-size:11.5px;margin-top:3px;max-width:340px}
.sev{font-weight:700}.sim{display:inline-block;font-size:11px;color:var(--accent-dark);
background:var(--accent-tint);border-radius:999px;padding:1px 8px;margin-left:6px}
a{color:var(--accent);text-decoration:none;font-weight:600}a:hover{text-decoration:underline}
.tail{margin:18px 0;padding:14px 16px;border-radius:12px;border:1px solid var(--bad);
background:rgba(180,48,48,.06)}.tail.none{border-color:var(--border);background:var(--surface)}
.tail h2{margin:0 0 6px;font-size:15px;color:var(--bad)}.tail.none h2{color:var(--text)}
.ack{margin:16px 0;padding:14px 16px;border-radius:12px;border:1px solid var(--warn);
background:rgba(201,120,0,.08)}.ack.ok{border-color:var(--ok);background:rgba(2,145,26,.08)}
.inv{list-style:none;display:flex;gap:8px;flex-wrap:wrap;padding:0;margin:14px 0 0}
.inv li{font-size:12px;padding:5px 11px;border-radius:999px;border:1px solid var(--border)}
.inv li.ok{color:var(--ok)}.inv li.bad{color:var(--bad);border-color:var(--bad);font-weight:700}
.foot{color:var(--muted);font-size:12px;margin-top:18px;border-top:1px solid var(--border);padding-top:12px}
.muted{color:var(--muted)}
"""


def page_css(theme=None) -> str:
    return BASE_CSS + theme_css(theme)
