// Custom infographic — user-supplied raw SVG with data-binding hooks.
// Walks elements marked data-group="GROUP_ID" and updates them based on
// the progress of items in that group. Supported render hooks:
//   data-render="progress-fill"   → sets width = data-orig-width * progress
//   data-render="progress-height" → sets height = data-orig-height * progress (fills bottom-up if data-fill-from="bottom")
//   data-render="progress-stroke" → sets stroke-dasharray for ring/path progress
//   data-render="count"           → sets text content to "X of N"
//   data-render="percent"         → sets text content to "X%"
//   data-render="opacity"         → sets opacity from 0.3 (empty) → data-base-opacity (full); base defaults to 1
//   data-render="status-class"    → adds class "is-complete" / "is-active" / "is-empty" based on progress
//
// If renderer_js is provided in the spec, that overrides this default behavior entirely.
function render_custom(svg, itemsArr) {
  if (typeof CUSTOM_RENDERER_JS === 'string' && CUSTOM_RENDERER_JS.length > 0) {
    try {
      // eslint-disable-next-line no-new-func
      (new Function('svg', 'itemsArr', 'GROUPS', CUSTOM_RENDERER_JS))(svg, itemsArr, GROUPS);
      return;
    } catch (e) {
      console.error('Custom renderer_js threw:', e);
      // fall through to default behavior
    }
  }

  // Default data-binding behavior
  let totalDone = 0, totalAll = 0;
  GROUPS.forEach(g => {
    const groupItems = (g.items || []).map(id => itemsArr.find(it => it.id === id)).filter(Boolean);
    const total = (g.items || []).length;
    let done = 0, doing = 0;
    groupItems.forEach(it => {
      const s = (it.dataset.status || 'TODO').toLowerCase();
      if (s === 'done') done++;
      else if (s === 'doing') doing++;
    });
    totalDone += done; totalAll += total;
    const pct = total > 0 ? (done / total) : 0;

    // progress-fill
    svg.querySelectorAll(`[data-group="${g.id}"][data-render="progress-fill"]`).forEach(el => {
      const orig = parseFloat(el.dataset.origWidth || el.getAttribute('width') || 0);
      el.setAttribute('width', orig * pct);
    });
    // progress-height (fills upward by default; downward if data-fill-from="bottom")
    svg.querySelectorAll(`[data-group="${g.id}"][data-render="progress-height"]`).forEach(el => {
      const origH = parseFloat(el.dataset.origHeight || el.getAttribute('height') || 0);
      const newH = origH * pct;
      el.setAttribute('height', newH);
      if (el.dataset.fillFrom === 'bottom') {
        const origY = parseFloat(el.dataset.origY || el.getAttribute('y') || 0);
        el.setAttribute('y', origY + (origH - newH));
      }
    });
    // progress-stroke (for ring/path progress)
    svg.querySelectorAll(`[data-group="${g.id}"][data-render="progress-stroke"]`).forEach(el => {
      const len = parseFloat(el.dataset.pathLength || el.getTotalLength?.() || 0);
      if (len > 0) {
        el.setAttribute('stroke-dasharray', `${len * pct} ${len}`);
      }
    });
    // count
    svg.querySelectorAll(`[data-group="${g.id}"][data-render="count"]`).forEach(el => {
      el.textContent = `${done} of ${total}`;
    });
    // percent
    svg.querySelectorAll(`[data-group="${g.id}"][data-render="percent"]`).forEach(el => {
      el.textContent = Math.round(pct * 100) + '%';
    });
    // opacity
    svg.querySelectorAll(`[data-group="${g.id}"][data-render="opacity"]`).forEach(el => {
      const base = parseFloat(el.dataset.baseOpacity || 1);
      el.setAttribute('opacity', String(0.3 + (base - 0.3) * pct));
    });
    // status-class
    svg.querySelectorAll(`[data-group="${g.id}"][data-render="status-class"]`).forEach(el => {
      el.classList.remove('is-complete', 'is-active', 'is-empty');
      if (pct >= 1) el.classList.add('is-complete');
      else if (pct > 0 || doing > 0) el.classList.add('is-active');
      else el.classList.add('is-empty');
    });
  });

  // Overall %
  const ovEl = document.getElementById('ach-overall-pct');
  if (ovEl) ovEl.textContent = (totalAll > 0 ? Math.round(totalDone / totalAll * 100) : 0) + '%';
}
