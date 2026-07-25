// Phase Journey — horizontal flow: Now → Phase 1 → Phase 2 → … → Goal.
// Each phase fills with green progress as items in that phase complete.
// Data needed: PHASES (array of {num, name, tagline, items[]}), ANCHOR_NOW, ANCHOR_GOAL.
window.render_phase_journey = function (svg, itemsArr) {
  const W = 1200, H = 240;
  const anchorW = 140, phaseW = 200, arrow = 20;
  const startX = (W - (2*anchorW + PHASES.length*phaseW + (PHASES.length+1)*arrow)) / 2;
  let x = startX;
  const rowMid = H / 2;
  const phaseH = 130;
  const phaseY = rowMid - phaseH/2;
  const anchorH = 100;
  const anchorY = rowMid - anchorH/2;

  const arrowPath = (ax) => {
    const ay = rowMid;
    return `<path class="pj-arrow" d="M ${ax} ${ay-5} L ${ax+arrow-4} ${ay-5} L ${ax+arrow-4} ${ay-9} L ${ax+arrow} ${ay} L ${ax+arrow-4} ${ay+9} L ${ax+arrow-4} ${ay+5} L ${ax} ${ay+5} Z"/>`;
  };

  let html = '';

  // NOW anchor
  html += `<rect class="pj-anchor-rect" x="${x}" y="${anchorY}" width="${anchorW}" height="${anchorH}" rx="10"/>`;
  html += `<text class="pj-anchor-text" x="${x + anchorW/2}" y="${anchorY + 22}" text-anchor="middle">Now</text>`;
  html += `<text class="pj-anchor-name" x="${x + anchorW/2}" y="${anchorY + 50}" text-anchor="middle">${ANCHOR_NOW.name}</text>`;
  const nowLines = (ANCHOR_NOW.tagline || '').split(' · ');
  nowLines.forEach((line, i) => {
    html += `<text class="pj-anchor-tagline" x="${x + anchorW/2}" y="${anchorY + 72 + i*14}" text-anchor="middle">${line}</text>`;
  });
  x += anchorW;

  let totalDone = 0, totalAll = 0;

  PHASES.forEach((phase) => {
    html += arrowPath(x);
    x += arrow;

    const phaseItems = (phase.items || []).map(id => itemsArr.find(it => it.id === id)).filter(Boolean);
    const total = (phase.items || []).length;
    let done = 0, doing = 0;
    phaseItems.forEach(it => {
      const s = (it.dataset.status || 'TODO').toLowerCase();
      if (s === 'done') done++;
      else if (s === 'doing') doing++;
    });
    totalDone += done; totalAll += total;
    const pct = total > 0 ? (done / total) : 0;
    const isComplete = done === total && total > 0;
    const isActive = !isComplete && (doing > 0 || done > 0);
    const phaseClass = isComplete ? 'is-complete' : (isActive ? 'is-active' : '');

    html += `<rect class="pj-phase-rect ${phaseClass}" x="${x}" y="${phaseY}" width="${phaseW}" height="${phaseH}" rx="10"/>`;
    html += `<text class="pj-phase-num" x="${x + 14}" y="${phaseY + 22}">PHASE ${phase.num}</text>`;
    html += `<text class="pj-phase-name" x="${x + 14}" y="${phaseY + 46}">${phase.name}</text>`;
    html += `<text class="pj-phase-tagline" x="${x + 14}" y="${phaseY + 64}">${phase.tagline || ''}</text>`;
    html += `<text class="pj-count" x="${x + 14}" y="${phaseY + 86}">${total} items</text>`;
    const pbX = x + 14, pbY = phaseY + 96, pbW = phaseW - 28, pbH = 8;
    html += `<rect class="pj-progress-bg" x="${pbX}" y="${pbY}" width="${pbW}" height="${pbH}" rx="4"/>`;
    html += `<rect class="pj-progress-fill" x="${pbX}" y="${pbY}" width="${pbW * pct}" height="${pbH}" rx="4"/>`;
    html += `<text class="pj-count" x="${pbX}" y="${pbY + 24}">${done} of ${total}</text>`;
    html += `<text class="pj-count-pct" x="${pbX + pbW}" y="${pbY + 24}" text-anchor="end">${Math.round(pct * 100)}%</text>`;

    x += phaseW;
  });

  html += arrowPath(x);
  x += arrow;

  html += `<rect class="pj-anchor-rect" x="${x}" y="${anchorY}" width="${anchorW}" height="${anchorH}" rx="10"/>`;
  html += `<text class="pj-anchor-text" x="${x + anchorW/2}" y="${anchorY + 22}" text-anchor="middle">Goal</text>`;
  html += `<text class="pj-anchor-name" x="${x + anchorW/2}" y="${anchorY + 50}" text-anchor="middle">${ANCHOR_GOAL.name}</text>`;
  const goalLines = (ANCHOR_GOAL.tagline || '').split(' · ');
  goalLines.forEach((line, i) => {
    html += `<text class="pj-anchor-tagline" x="${x + anchorW/2}" y="${anchorY + 72 + i*14}" text-anchor="middle">${line}</text>`;
  });

  svg.innerHTML = html;

  const overallPct = totalAll > 0 ? Math.round(totalDone / totalAll * 100) : 0;
  const ovEl = document.getElementById('ach-overall-pct');
  if (ovEl) ovEl.textContent = overallPct + '%';
};
