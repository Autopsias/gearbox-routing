// Before / After — two cards side by side with bullets, arrow with progress %.
// WORKSTREAMS = items grouped to show what's bridging the gap.
// Data: BEFORE ({name, bullets[]}), AFTER ({name, bullets[]}), WORKSTREAMS (array of {name, items[]}).
window.render_before_after = function (svg, itemsArr) {
  const W = 1100, H = 320;
  const cardW = 380, cardH = 240, cardY = 40;
  const beforeX = 20;
  const afterX = W - cardW - 20;
  const arrowX0 = beforeX + cardW + 20, arrowX1 = afterX - 20;
  const midY = cardY + cardH/2;

  let totalDone = 0, totalAll = 0;
  WORKSTREAMS.forEach(ws => {
    const phaseItems = (ws.items || []).map(id => itemsArr.find(it => it.id === id)).filter(Boolean);
    totalAll += (ws.items || []).length;
    phaseItems.forEach(it => {
      if ((it.dataset.status || 'TODO').toLowerCase() === 'done') totalDone++;
    });
  });
  const pct = totalAll > 0 ? (totalDone / totalAll) : 0;

  let html = '';
  // BEFORE card
  html += `<rect class="pj-anchor-rect" x="${beforeX}" y="${cardY}" width="${cardW}" height="${cardH}" rx="12"/>`;
  html += `<text class="pj-anchor-text" x="${beforeX + 18}" y="${cardY + 22}">BEFORE</text>`;
  html += `<text class="pj-anchor-name" x="${beforeX + 18}" y="${cardY + 48}">${BEFORE.name}</text>`;
  (BEFORE.bullets || []).forEach((b, i) => {
    html += `<text class="pj-phase-tagline" x="${beforeX + 18}" y="${cardY + 78 + i * 22}">• ${b}</text>`;
  });

  // AFTER card
  html += `<rect class="pj-phase-rect is-complete" x="${afterX}" y="${cardY}" width="${cardW}" height="${cardH}" rx="12"/>`;
  html += `<text class="pj-anchor-text" x="${afterX + 18}" y="${cardY + 22}" fill="var(--accent)">AFTER</text>`;
  html += `<text class="pj-anchor-name" x="${afterX + 18}" y="${cardY + 48}">${AFTER.name}</text>`;
  (AFTER.bullets || []).forEach((b, i) => {
    html += `<text class="pj-phase-tagline" x="${afterX + 18}" y="${cardY + 78 + i * 22}">• ${b}</text>`;
  });

  // Arrow (bridge with progress)
  const arrowW = arrowX1 - arrowX0;
  const aY = midY - 14;
  // Background arrow
  html += `<rect x="${arrowX0}" y="${aY + 8}" width="${arrowW - 20}" height="12" fill="var(--surface-2)" stroke="var(--hairline)" rx="6"/>`;
  html += `<polygon points="${arrowX1 - 20},${aY + 4} ${arrowX1},${aY + 14} ${arrowX1 - 20},${aY + 24}" fill="var(--surface-2)" stroke="var(--hairline)"/>`;
  // Foreground (filled to pct)
  const fillW = (arrowW - 20) * pct;
  if (fillW > 0) {
    html += `<rect x="${arrowX0}" y="${aY + 8}" width="${fillW}" height="12" fill="var(--accent)" rx="6"/>`;
  }
  // Pct label above arrow
  html += `<text x="${arrowX0 + arrowW/2}" y="${aY}" text-anchor="middle" font-family="var(--font-mono)" font-size="14" font-weight="800" fill="var(--accent)">${Math.round(pct * 100)}%</text>`;
  html += `<text x="${arrowX0 + arrowW/2}" y="${aY + 44}" text-anchor="middle" font-family="var(--font-sans)" font-size="10" font-weight="700" letter-spacing="0.12em" fill="var(--text-subtle)">${WORKSTREAMS.length} WORKSTREAMS · ${totalDone}/${totalAll}</text>`;

  svg.innerHTML = html;
  const ovEl = document.getElementById('ach-overall-pct');
  if (ovEl) ovEl.textContent = Math.round(pct * 100) + '%';
};
