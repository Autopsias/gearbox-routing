// Hub & Spoke — central node = goal, spokes radiating out = workstreams.
// Each spoke connects to a node showing workstream name + progress.
// Data: HUB ({name, tagline}), SPOKES (array of {name, tagline, items[]}).
window.render_hub_spoke = function (svg, itemsArr) {
  const W = 1000, H = 480;
  const cx = W / 2, cy = H / 2;
  const hubR = 90;
  const spokeR = 200;
  const nodeW = 180, nodeH = 78;

  let html = '';
  let totalDone = 0, totalAll = 0;

  // Spokes
  const n = SPOKES.length;
  SPOKES.forEach((spoke, i) => {
    const angle = (-Math.PI/2) + (i * 2 * Math.PI / n);
    const nx = cx + Math.cos(angle) * spokeR;
    const ny = cy + Math.sin(angle) * spokeR;
    const phaseItems = (spoke.items || []).map(id => itemsArr.find(it => it.id === id)).filter(Boolean);
    const total = (spoke.items || []).length;
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

    // Connector line (hub → node)
    const hubEdgeX = cx + Math.cos(angle) * hubR;
    const hubEdgeY = cy + Math.sin(angle) * hubR;
    const nodeEdgeX = nx - Math.cos(angle) * (nodeW/2);
    const nodeEdgeY = ny - Math.sin(angle) * (nodeH/2);
    html += `<line x1="${hubEdgeX}" y1="${hubEdgeY}" x2="${nodeEdgeX}" y2="${nodeEdgeY}" stroke="var(--border-strong)" stroke-width="1.5"/>`;

    // Workstream node
    html += `<rect class="pj-phase-rect ${cls}" x="${nx - nodeW/2}" y="${ny - nodeH/2}" width="${nodeW}" height="${nodeH}" rx="8"/>`;
    html += `<text class="pj-phase-name" x="${nx}" y="${ny - nodeH/2 + 22}" text-anchor="middle">${spoke.name}</text>`;
    if (spoke.tagline) html += `<text class="pj-phase-tagline" x="${nx}" y="${ny - nodeH/2 + 38}" text-anchor="middle">${spoke.tagline}</text>`;
    // Progress bar
    const pbW = nodeW - 28, pbH = 6;
    const pbX = nx - pbW/2, pbY = ny + nodeH/2 - pbH - 10;
    html += `<rect class="pj-progress-bg" x="${pbX}" y="${pbY}" width="${pbW}" height="${pbH}" rx="3"/>`;
    html += `<rect class="pj-progress-fill" x="${pbX}" y="${pbY}" width="${pbW * pct}" height="${pbH}" rx="3"/>`;
    html += `<text class="pj-count-pct" x="${nx}" y="${ny + nodeH/2 - 18}" text-anchor="middle">${done}/${total} · ${Math.round(pct*100)}%</text>`;
  });

  // Hub (drawn last so it sits on top)
  html += `<circle cx="${cx}" cy="${cy}" r="${hubR}" fill="var(--accent)" stroke="var(--accent-dark)" stroke-width="2"/>`;
  html += `<text x="${cx}" y="${cy - 12}" text-anchor="middle" fill="white" font-family="var(--font-sans)" font-size="11" font-weight="700" letter-spacing="0.12em">GOAL</text>`;
  html += `<text x="${cx}" y="${cy + 8}" text-anchor="middle" fill="white" font-family="var(--font-sans)" font-size="14" font-weight="700">${HUB.name}</text>`;
  if (HUB.tagline) {
    const lines = HUB.tagline.split(' · ');
    lines.forEach((line, i) => {
      html += `<text x="${cx}" y="${cy + 26 + i*13}" text-anchor="middle" fill="white" opacity="0.85" font-family="var(--font-sans)" font-size="10.5">${line}</text>`;
    });
  }

  svg.innerHTML = html;
  const ovEl = document.getElementById('ach-overall-pct');
  if (ovEl) ovEl.textContent = (totalAll > 0 ? Math.round(totalDone / totalAll * 100) : 0) + '%';
};
