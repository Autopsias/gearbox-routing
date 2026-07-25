// Flat ESLint config for validating the PLAN.html dashboard's inline <script>.
//
// PURPOSE: catch undefined-identifier bugs (e.g. a renderer referencing a
// variable that was never declared) BEFORE a broken dashboard ships. This is
// the guard against the 2026-06-06 `isOpus is not defined` class: a runtime
// ReferenceError inside renderSessionArc() aborted recomputeCounts() mid-run,
// silently freezing the session-strip / nav at its authored fallback statuses.
// `node --check` does NOT catch this (it is a runtime error, not syntax).
//
// Self-contained on purpose: the `globals` npm package is NOT resolvable from a
// global eslint install, so the browser-globals allowlist is enumerated inline.
// ES builtins (Math, JSON, Object, Array, Promise, ...) are provided implicitly
// by `ecmaVersion`, so they are intentionally absent below.
//
// The allowlist is the contract. If a NEW legitimate global is introduced in
// base-template.html or an assets/infographics/*.js renderer, the build will
// fail with that identifier named — add it here (browser global) or it is a
// real bug (typo / undeclared variable). That is the intended trade-off:
// a loud, self-documenting failure instead of a silent broken nav.

const browser = {
  // Core
  window: 'readonly', document: 'readonly', console: 'readonly',
  navigator: 'readonly', location: 'readonly', history: 'readonly',
  screen: 'readonly', performance: 'readonly', devicePixelRatio: 'readonly',
  // Storage / URL
  localStorage: 'readonly', sessionStorage: 'readonly',
  URL: 'readonly', URLSearchParams: 'readonly',
  // Timers / scheduling
  setTimeout: 'readonly', clearTimeout: 'readonly',
  setInterval: 'readonly', clearInterval: 'readonly',
  requestAnimationFrame: 'readonly', cancelAnimationFrame: 'readonly',
  requestIdleCallback: 'readonly', queueMicrotask: 'readonly',
  // Layout / styling
  getComputedStyle: 'readonly', matchMedia: 'readonly', getSelection: 'readonly',
  // Observers
  IntersectionObserver: 'readonly', MutationObserver: 'readonly',
  ResizeObserver: 'readonly',
  // DOM / events (constructors referenced by name)
  Event: 'readonly', CustomEvent: 'readonly', Element: 'readonly',
  HTMLElement: 'readonly', Node: 'readonly', NodeList: 'readonly',
  SVGElement: 'readonly', DOMParser: 'readonly', Image: 'readonly',
  // Misc browser
  alert: 'readonly', confirm: 'readonly', prompt: 'readonly',
  fetch: 'readonly', Blob: 'readonly', FileReader: 'readonly',
  CSS: 'readonly', structuredClone: 'readonly',
};

// Infographic data globals injected per type by build_plan.py (only ONE type's
// data is present per plan, but ALL 6 renderers are concatenated — so the other
// renderers reference these statically-undefined-but-never-executed globals).
// Calibrated against the assembled script + the injection sites in build_plan.py.
const infographicData = {
  INFOGRAPHIC_TYPE: 'readonly',
  // phase-journey
  ANCHOR_NOW: 'readonly', ANCHOR_GOAL: 'readonly', PHASES: 'readonly',
  // maturity-ladder
  LEVELS: 'readonly', ANCHOR_BOTTOM: 'readonly', ANCHOR_TOP: 'readonly',
  // hub-spoke
  HUB: 'readonly', SPOKES: 'readonly',
  // before-after
  BEFORE: 'readonly', AFTER: 'readonly', WORKSTREAMS: 'readonly',
  // pillars
  ROOF: 'readonly', PILLARS: 'readonly', FOUNDATION: 'readonly',
  // custom
  GROUPS: 'readonly', CUSTOM_RENDERER_JS: 'readonly',
};

export default [
  {
    // The template carries inline `eslint-disable` directives for rules this
    // minimal config does not enable; don't surface them as noise.
    linterOptions: { reportUnusedDisableDirectives: 'off' },
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'script',
      globals: { ...browser, ...infographicData },
    },
    rules: {
      'no-undef': 'error',
    },
  },
];
