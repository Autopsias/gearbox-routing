# Plan Achievement Infographic Templates

Five SVG templates ship with the skill. Pick one based on the plan's underlying narrative.

## phase-journey
**Best for:** staged rollouts. Most plans fit here when you can express the work as "Now → Phase 1 → Phase 2 → … → Goal."

**Visual:** horizontal flow. Anchor card on the far left ("Now: current state"), N phase cards in the middle (each with name, tagline, item count, progress bar), anchor card on the far right ("Goal: target state"), arrows between.

**Spec fields:**
```json
"infographic": {
  "type": "phase-journey",
  "title": "From <em>fragile</em> to <em>production-grade</em>",
  "narrative": "...",
  "phases": [
    {"num": 1, "name": "Foundation",  "tagline": "auth + observability",  "items": ["eng-01", "eng-02"]},
    {"num": 2, "name": "Hardening",   "tagline": "perf + security",       "items": ["eng-03", "eng-04", "eng-05"]}
  ],
  "anchor_now":  {"name": "v2.5 brittle", "tagline": "no auth · gap-y observability"},
  "anchor_goal": {"name": "v3.0 ready",   "tagline": "auth · observable · documented"}
}
```

**Recommend when:** the plan is sequential, with discrete phases that each unlock the next.

## maturity-ladder
**Best for:** capability-building plans. "We need to go from no observability to production-grade."

**Visual:** vertical staircase. Bottom anchor = current capability, N levels stepping up (each = a tier with progress fill), top anchor = target capability.

**Spec fields:**
```json
"infographic": {
  "type": "maturity-ladder",
  "title": "Climbing from <em>ad-hoc</em> to <em>continuous</em>",
  "levels": [
    {"num": 1, "name": "Ad-hoc",     "tagline": "manual + reactive",     "items": ["ops-01", "ops-02"]},
    {"num": 2, "name": "Repeatable", "tagline": "playbooks + alerts",    "items": ["ops-03"]},
    {"num": 3, "name": "Defined",    "tagline": "SLOs + dashboards",     "items": ["ops-04", "ops-05"]},
    {"num": 4, "name": "Continuous", "tagline": "auto-remediation",      "items": ["ops-06"]}
  ],
  "anchor_bottom": {"name": "Today",          "tagline": "manual ops"},
  "anchor_top":    {"name": "Continuous Ops", "tagline": "self-healing"}
}
```

**Recommend when:** the work fits a maturity-model framing (reactive → proactive, manual → automated, ad-hoc → continuous).

## hub-spoke
**Best for:** cross-cutting initiatives. Multiple workstreams converge on one outcome.

**Visual:** central circular hub showing the goal, N spoke nodes radiating out (each = a workstream with progress), connector lines between hub and spokes.

**Spec fields:**
```json
"infographic": {
  "type": "hub-spoke",
  "title": "Five workstreams converging on <em>v3.0</em>",
  "hub":    {"name": "v3.0 ship", "tagline": "ready by Q3 close"},
  "spokes": [
    {"name": "Backend",  "tagline": "auth + perf",   "items": ["be-01", "be-02"]},
    {"name": "Frontend", "tagline": "redesign",      "items": ["fe-01", "fe-02", "fe-03"]},
    {"name": "Mobile",   "tagline": "iOS + Android", "items": ["mo-01", "mo-02"]},
    {"name": "DevOps",   "tagline": "k8s migration", "items": ["op-01"]},
    {"name": "Docs",     "tagline": "API + guides",  "items": ["doc-01", "doc-02"]}
  ]
}
```

**Recommend when:** you have 4-7 parallel workstreams contributing to one shared outcome.

## before-after
**Best for:** transformation / change management. The plan moves the org from one state to another.

**Visual:** two cards side by side. Left = BEFORE (current state with bullets). Right = AFTER (target state with bullets). Arrow between with progress %.

**Spec fields:**
```json
"infographic": {
  "type": "before-after",
  "title": "Migrating from <em>monolith</em> to <em>services</em>",
  "before": {
    "name": "Monolith (today)",
    "bullets": [
      "Single deployable",
      "Shared database",
      "30-min deploys",
      "Coupled team velocity"
    ]
  },
  "after": {
    "name": "Service mesh (target)",
    "bullets": [
      "8 deployable services",
      "Service-owned data",
      "<5-min deploys",
      "Independent team velocity"
    ]
  },
  "workstreams": [
    {"name": "Extract auth service", "items": ["m-01", "m-02"]},
    {"name": "Extract billing",       "items": ["m-03", "m-04"]},
    {"name": "Database split",        "items": ["m-05", "m-06"]}
  ]
}
```

**Recommend when:** the plan tells a story of "moving from X to Y" and bullets capture the contrast clearly.

## pillars
**Best for:** structural improvements. The plan rests on N foundational workstreams that together support a goal.

**Visual:** roof at top (goal), N vertical pillars (each = workstream with progress fill), foundation bar at bottom (current capability).

**Spec fields:**
```json
"infographic": {
  "type": "pillars",
  "title": "Building <em>defensible engineering culture</em> on four pillars",
  "roof":       {"name": "Defensible engineering culture"},
  "pillars": [
    {"name": "Code review",     "tagline": "every PR",           "items": ["cul-01", "cul-02"]},
    {"name": "Testing",         "tagline": "coverage > 80%",     "items": ["cul-03", "cul-04"]},
    {"name": "Postmortems",     "tagline": "blameless",          "items": ["cul-05"]},
    {"name": "Documentation",   "tagline": "decision records",   "items": ["cul-06", "cul-07"]}
  ],
  "foundation": {"name": "Existing process + people"}
}
```

**Recommend when:** the plan addresses multiple aspects of one structural thing (culture, platform, brand) and you can name 3-5 pillars.

## custom
**Best for:** anything the 5 structured templates can't represent without distortion. Geographic maps, network/dependency graphs, mountain-climb illustrations, Sankey flows, custom illustrations, swim-lane journey maps. The narrative needs a bespoke visual.

**Visual:** whatever raw SVG the user (or Claude) provides. The build script wraps it in the standard infographic shell (header, narrative, overall %), embeds the SVG verbatim, and auto-binds progress data to elements you mark with `data-group` and `data-render` attributes.

**Spec fields:**
```json
"infographic": {
  "type": "custom",
  "title": "Climbing <em>Mt Production</em>",
  "narrative": "Three campsites between us and the summit. Each fills as items in that bundle complete.",
  "viewBox": "0 0 1200 400",
  "svg_inline": "<polygon points='100,350 600,80 1100,350' fill='var(--surface-2)' stroke='var(--border-strong)'/>...the rest of your SVG markup (no outer <svg> wrapper)...",
  "groups": [
    {"id": "basecamp", "items": ["m-01", "m-02"]},
    {"id": "midcamp",  "items": ["m-03", "m-04", "m-05"]},
    {"id": "summit",   "items": ["m-06", "m-07"]}
  ]
}
```

**Data-binding hooks** (set as attributes on SVG elements):
| Attribute | Effect |
|---|---|
| `data-group="GROUP_ID"` | Required. Binds the element to a group in `groups[]`. |
| `data-render="progress-fill"` | Sets `width = data-orig-width * progress` (filled left-to-right). Set `data-orig-width` on the element. |
| `data-render="progress-height"` | Sets `height = data-orig-height * progress`. Set `data-fill-from="bottom"` to fill upward (with auto-y adjustment). |
| `data-render="progress-stroke"` | Sets `stroke-dasharray = pathLength * progress`. Use for ring/path progress. |
| `data-render="count"` | Replaces text content with `"X of N"`. |
| `data-render="percent"` | Replaces text content with `"X%"`. |
| `data-render="opacity"` | Sets `opacity = 0.3 + (base - 0.3) * progress`. Set `data-base-opacity="1"` for full at-completion opacity. |
| `data-render="status-class"` | Adds CSS class `is-complete` / `is-active` / `is-empty` based on progress. |

**Mountain-climb example (continued):**
```html
<!-- Inside svg_inline: -->
<polygon points="100,350 600,80 1100,350" fill="var(--surface-2)" stroke="var(--border-strong)"/>
<!-- Basecamp: circle that gains opacity as items complete -->
<circle data-group="basecamp" data-render="opacity" data-base-opacity="1" cx="200" cy="320" r="22" fill="var(--accent)"/>
<text data-group="basecamp" data-render="count" x="200" y="365" text-anchor="middle" font-family="var(--font-mono)" font-size="11">0 of 0</text>
<text x="200" y="305" text-anchor="middle" font-family="var(--font-sans)" font-size="13" font-weight="700">Basecamp</text>
<!-- Midcamp: same pattern -->
<circle data-group="midcamp" data-render="opacity" data-base-opacity="1" cx="600" cy="200" r="22" fill="var(--accent)"/>
<text data-group="midcamp" data-render="percent" x="600" y="245" text-anchor="middle" font-family="var(--font-mono)" font-size="11">0%</text>
<text x="600" y="185" text-anchor="middle" font-family="var(--font-sans)" font-size="13" font-weight="700">Midcamp</text>
<!-- Summit: status-class colors when reached -->
<circle data-group="summit" data-render="status-class" cx="1000" cy="100" r="22" fill="var(--accent-soft)" class="pj-phase-rect"/>
<text x="1000" y="85" text-anchor="middle" font-family="var(--font-sans)" font-size="13" font-weight="700">Summit</text>
```

**Custom JS escape hatch.** If the data-binding hooks aren't enough, supply `renderer_js` in the spec — a string of JavaScript that runs as `function(svg, itemsArr, GROUPS) { ... }`. Total freedom, but use sparingly:
```json
"renderer_js": "GROUPS.forEach(g => { /* ...your logic... */ });"
```

**Recommend `custom` when:** the plan has a strong visual metaphor that none of the structured templates capture without distortion. Don't reach for it just because the structured ones are "too plain" — try them first.

### Design principles for custom SVG

The structured templates ship with polished CSS. Custom SVGs you author have to MEET that bar — a stick-figure triangle with three circles will look amateur next to the rest of the page. Apply these principles:

**1. Layer for depth.** Background atmosphere → midground shapes → foreground accents. A flat single-fill shape reads as "diagram from a textbook." A layered scene reads as "designed."
- Sky/atmosphere wash via `<linearGradient>` covering the canvas
- Distant elements (background range, blurred outlines) at low opacity / hazy color
- Foreground elements at full opacity / saturated color
- Use `<filter>` for blur, glow, drop shadow

**2. Use `<defs>` for gradients, filters, and reusable shapes.** Define once, reference via `url(#id)`. Every gradient should use page CSS variables (`var(--accent)`, `var(--purple)`, etc.) so dark mode works automatically.

**3. One visual destination, heaviest of all.** The "goal" element (summit, hub, end-state) should be the largest, brightest, most detailed thing in the SVG. Add halo, glow, drop shadow, doubled stroke. The eye should land there first.

**4. Active states use light, not just opacity.** A 0.3 → 1.0 opacity ramp is "okay." A glowing inner circle, a saturated fill, a soft drop shadow when active — that's "alive." Combine `data-render="opacity"` with `data-render="status-class"` for richer state changes.

**5. Typography matches the page.** Inter for labels (`font-family="var(--font-sans)"`), JetBrains Mono for counts/percents (`font-family="var(--font-mono)"`). Wrap labels in tinted pill backgrounds (`<rect rx="11" fill="var(--surface)" stroke="var(--border)"/>` + centered `<text>`) instead of raw floating text. Match the visual chip style of the rest of the page.

**6. Use page CSS variables for all colors.** No hardcoded hex. Theme switching must work for free.

**7. Animate transitions.** The base CSS provides smooth transitions on `width`, `height`, `stroke-dasharray`, `opacity`, `fill`, `stroke` for any element with a `data-render` attribute. Lean into this — when an item moves to DONE, the trail visibly fills, the camp visibly lights up.

**8. Add ambient detail.** Altitude markers on the right edge of a mountain. Compass rose on a map. Distance ticks on a timeline. Tiny background patterns. These aren't decoration — they're the difference between "diagram" and "designed."

### Reference example

The polished mountain-climb example in `assets/custom-examples/mountain-climb.svg` demonstrates all of these principles. Reference it with `"svg_inline_file": "/path/to/skill/assets/custom-examples/mountain-climb.svg"` in the spec, OR copy the SVG inline as a starting point and modify.

The bar to clear: when you take a screenshot of the rendered infographic, it should feel like it belongs on the same page as the donut rings and frosted-glass header. If it looks like it was lifted from a different document, you haven't applied the principles above.

### `svg_inline` vs `svg_inline_file`

Spec accepts either:
- `"svg_inline": "<rect ...>...."` — raw SVG markup as a string. Works for short SVGs.
- `"svg_inline_file": "/abs/path/to/file.svg"` — references a standalone .svg file. Better for polished SVGs (>30 lines) that benefit from real syntax highlighting in your editor.

Use `svg_inline_file` for any SVG over ~30 lines.

## How to recommend a template

When the user describes their plan, listen for the underlying narrative shape:
- "First X, then Y, then Z" → **phase-journey**
- "Current state ad-hoc / want continuous" → **maturity-ladder**
- "5 teams working on one launch" → **hub-spoke**
- "From monolith to microservices" → **before-after**
- "Build trust on 4 pillars" → **pillars**
- "It's like a [specific visual metaphor]" / "I want it to look like [X]" → **custom**

When in doubt, **phase-journey** is the safe default.
