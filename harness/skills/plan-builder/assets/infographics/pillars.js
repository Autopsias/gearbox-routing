// Pillars — roof = goal, pillars = workstreams (each fills with progress), foundation = current.
// Data: ROOF ({name}), PILLARS (array of {name, tagline, items[]}), FOUNDATION ({name}).
window.render_pillars = function (svg, itemsArr) {
  const W = 1100, H = 380;
  const roofH = 60, foundH = 50;
  const innerH = H - roofH - foundH - 30;
  const pillarsTopY = roofH + 15;
  const pillarsBotY = pillarsTopY + innerH;
  const n = PILLARS.length;
  const padX = 30;
  const totalW = W - 2*padX;
  const pillarW = (totalW - (n - 1) * 14) / n;

  let totalDone = 0, totalAll = 0;
  let html = '';
  // Roof
  html += `<polygon points="${padX},${roofH} ${W - padX},${roofH} ${W - padX - 20},${roofH - 30} ${padX + 20},${roofH - 30}"
            fill="var(--accent)" stroke="var(--accent-dark)" stroke-width="2"/>`;
  html += `<text x="${W/2}" y="${roofH - 12}" text-anchor="middle" fill="white" font-family="var(--font-sans)" font-size="14" font-weight="700">${ROOF.name}</text>`;

  // Pillars
  PILLARS.forEach((pillar, i) => {
    const x = padX + i * (pillarW + 14);
    const phaseItems = (pillar.items || []).map(id => itemsArr.find(it => it.id === id)).filter(Boolean);
    const total = (pillar.items || []).length;
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

    // Pillar background
    html += `<rect class="pj-phase-rect ${cls}" x="${x}" y="${pillarsTopY}" width="${pillarW}" height="${innerH}" rx="6"/>`;
    // Progress fill (from bottom up)
    const fillH = innerH * pct;
    if (fillH > 0) {
      html += `<rect x="${x}" y="${pillarsBotY - fillH}" width="${pillarW}" height="${fillH}" fill="var(--accent)" opacity="0.25" rx="6"/>`;
    }
    // Pillar labels
    html += `<text class="pj-phase-name" x="${x + pillarW/2}" y="${pillarsTopY + 28}" text-anchor="middle">${pillar.name}</text>`;
    if (pillar.tagline) html += `<text class="pj-phase-tagline" x="${x + pillarW/2}" y="${pillarsTopY + 46}" text-anchor="middle">${pillar.tagline}</text>`;
    html += `<text class="pj-count" x="${x + pillarW/2}" y="${pillarsBotY - 14}" text-anchor="middle">${done}/${total}</text>`;
    html += `<text class="pj-count-pct" x="${x + pillarW/2}" y="${pillarsBotY + 2}" text-anchor="middle">${Math.round(pct * 100)}%</text>`;
  });

  // Foundation
  const foundY = pillarsBotY + 12;
  html += `<rect class="pj-anchor-rect" x="${padX - 14}" y="${foundY}" width="${totalW + 28}" height="${foundH}" rx="6"/>`;
  html += `<text class="pj-anchor-text" x="${W/2}" y="${foundY + 18}" text-anchor="middle">FOUNDATION</text>`;
  html += `<text class="pj-anchor-name" x="${W/2}" y="${foundY + 38}" text-anchor="middle">${FOUNDATION.name}</text>`;

  svg.innerHTML = html;
  const ovEl = document.getElementById('ach-overall-pct');
  if (ovEl) ovEl.textContent = (totalAll > 0 ? Math.round(totalDone / totalAll * 100) : 0) + '%';
};
