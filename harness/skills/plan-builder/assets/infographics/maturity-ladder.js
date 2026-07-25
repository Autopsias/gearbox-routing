// Maturity Ladder — vertical staircase. Each level = a capability tier.
// Bottom anchor = Now (current capability), top anchor = Goal (target).
// Each level has progress fill from items.
// Data: LEVELS (array of {num, name, tagline, items[]}), ANCHOR_BOTTOM, ANCHOR_TOP.
window.render_maturity_ladder = function (svg, itemsArr) {
  const W = 800, H = 460;
  const stepCount = LEVELS.length;
  const anchorH = 60, gap = 8;
  const stepH = (H - 2*anchorH - (stepCount + 1)*gap) / stepCount;
  const stepW = 380;
  const stepXBase = (W - stepW) / 2;
  // Bottom (NOW) anchor
  const bottomY = H - anchorH;
  let html = '';
  html += `<rect class="pj-anchor-rect" x="${stepXBase - 30}" y="${bottomY}" width="${stepW + 60}" height="${anchorH - 6}" rx="10"/>`;
  html += `<text class="pj-anchor-text" x="${W/2}" y="${bottomY + 18}" text-anchor="middle">Now</text>`;
  html += `<text class="pj-anchor-name" x="${W/2}" y="${bottomY + 38}" text-anchor="middle">${ANCHOR_BOTTOM.name}</text>`;
  if (ANCHOR_BOTTOM.tagline) html += `<text class="pj-anchor-tagline" x="${W/2}" y="${bottomY + 50}" text-anchor="middle">${ANCHOR_BOTTOM.tagline}</text>`;

  let totalDone = 0, totalAll = 0;
  // Steps from bottom up
  LEVELS.forEach((level, i) => {
    const y = bottomY - gap - (i + 1) * (stepH + gap);
    const xOffset = i * 14; // staircase shift
    const x = stepXBase + xOffset;
    const phaseItems = (level.items || []).map(id => itemsArr.find(it => it.id === id)).filter(Boolean);
    const total = (level.items || []).length;
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
    const cls = isComplete ? 'is-complete' : (isActive ? 'is-active' : '');
    html += `<rect class="pj-phase-rect ${cls}" x="${x}" y="${y}" width="${stepW}" height="${stepH}" rx="8"/>`;
    html += `<text class="pj-phase-num" x="${x + 14}" y="${y + 22}">LEVEL ${level.num || (i+1)}</text>`;
    html += `<text class="pj-phase-name" x="${x + 14}" y="${y + 42}">${level.name}</text>`;
    if (level.tagline) html += `<text class="pj-phase-tagline" x="${x + 14}" y="${y + 56}">${level.tagline}</text>`;
    // Progress bar at right
    const pbW = 100, pbH = 6;
    const pbX = x + stepW - pbW - 14, pbY = y + stepH/2 - pbH/2;
    html += `<rect class="pj-progress-bg" x="${pbX}" y="${pbY}" width="${pbW}" height="${pbH}" rx="3"/>`;
    html += `<rect class="pj-progress-fill" x="${pbX}" y="${pbY}" width="${pbW * pct}" height="${pbH}" rx="3"/>`;
    html += `<text class="pj-count-pct" x="${x + stepW - 14}" y="${y + stepH/2 + 18}" text-anchor="end">${done}/${total} · ${Math.round(pct*100)}%</text>`;
  });

  // Top (GOAL) anchor
  const topY = 0;
  const topX = stepXBase + (stepCount * 14);
  html += `<rect class="pj-anchor-rect" x="${topX - 20}" y="${topY}" width="${stepW + 40}" height="${anchorH - 6}" rx="10"/>`;
  html += `<text class="pj-anchor-text" x="${topX + stepW/2}" y="${topY + 18}" text-anchor="middle">Goal</text>`;
  html += `<text class="pj-anchor-name" x="${topX + stepW/2}" y="${topY + 38}" text-anchor="middle">${ANCHOR_TOP.name}</text>`;
  if (ANCHOR_TOP.tagline) html += `<text class="pj-anchor-tagline" x="${topX + stepW/2}" y="${topY + 50}" text-anchor="middle">${ANCHOR_TOP.tagline}</text>`;

  svg.innerHTML = html;
  const ovEl = document.getElementById('ach-overall-pct');
  if (ovEl) ovEl.textContent = (totalAll > 0 ? Math.round(totalDone / totalAll * 100) : 0) + '%';
};
