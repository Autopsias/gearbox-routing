# Blindspot HTML shape

Reuse this skeleton. Fill `{{...}}` placeholders. Category colors are the only
per-category styling — everything else is shared. No external requests: no
fonts, no CDN, no images by URL.

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Blindspot pass — {{TARGET_PATH}}</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 880px;
         margin: 2rem auto; padding: 0 1.5rem; line-height: 1.5;
         background: #fff; color: #111; }
  @media (prefers-color-scheme: dark) {
    body { background: #16181d; color: #e8e8e8; }
    .card { background: #1f2228 !important; border-color: #333 !important; }
    code, pre { background: #0d0e11 !important; color: #ddd !important; }
  }
  header.stats { display: flex; gap: 1.5rem; flex-wrap: wrap; margin-bottom: 2rem;
                 padding: 1rem; border-radius: 10px; background: #f2f2f4; }
  @media (prefers-color-scheme: dark) { header.stats { background: #22252b; } }
  .stat b { display: block; font-size: 1.4rem; }
  .card { border: 1px solid #ddd; border-radius: 10px; padding: 1rem 1.25rem;
          margin-bottom: 1rem; background: #fafafa; }
  .tag { display: inline-block; font-size: .75rem; font-weight: 600;
         padding: .15rem .6rem; border-radius: 999px; margin-bottom: .5rem; }
  .tag.landmine { background: #fde2e1; color: #8a1f1f; }
  .tag.convention { background: #e2ecfd; color: #1f3d8a; }
  .tag.history { background: #f3e2fd; color: #5a1f8a; }
  .tag.missing { background: #fdf3e2; color: #8a5a1f; }
  pre { background: #eee; padding: .6rem .8rem; border-radius: 6px;
        overflow-x: auto; white-space: pre-wrap; }
  button.copy { font-size: .8rem; padding: .25rem .7rem; border-radius: 6px;
                border: 1px solid #999; background: transparent; cursor: pointer; }
  footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #ccc; }
</style>
</head>
<body>

<h1>Blindspot pass — <code>{{TARGET_PATH}}</code></h1>

<header class="stats">
  <div class="stat"><b>{{FILES_SCANNED}}</b>files scanned</div>
  <div class="stat"><b>{{COMMITS_SCANNED}}</b>commits reviewed</div>
  <div class="stat"><b>{{FINDING_COUNT}}</b>findings</div>
  <div class="stat"><b>{{SCAN_DATE}}</b>scanned</div>
</header>

<!-- repeat one .card per finding -->
<div class="card">
  <span class="tag {{category_css_class}}">{{CATEGORY}}</span>
  <h3>{{TITLE}}</h3>
  <p><b>What:</b> {{WHAT}}</p>
  <p><b>Why it bites:</b> {{WHY_IT_BITES}}</p>
  <p><b>Prompt fix:</b></p>
  <pre id="fix-{{n}}">{{PROMPT_FIX_TEXT}}</pre>
  <button class="copy" onclick="copyEl('fix-{{n}}', this)">Copy fix</button>
</div>
<!-- /repeat -->

<footer>
  <h2>Assembled improved prompt</h2>
  <pre id="assembled">{{ALL_PROMPT_FIXES_JOINED}}</pre>
  <button class="copy" onclick="copyEl('assembled', this)">Copy assembled prompt</button>
</footer>

<script>
function copyEl(id, btn) {
  const text = document.getElementById(id).innerText;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = orig, 1200);
  });
}
</script>
</body>
</html>
```

`category_css_class` mapping: Landmine → `landmine`, Convention →
`convention`, History → `history`, Missing concept → `missing`.

The assembled prompt in the footer should read as one coherent paragraph an
engineer could paste as-is — not a bullet dump of the individual fixes. Open
with the target path and task intent, then fold in each fix as a constraint
or "watch out for X" clause.
