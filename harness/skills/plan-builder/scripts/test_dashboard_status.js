// Run: node --test skills/plan-builder/scripts/test_dashboard_status.js
// Execute the template's real status repaint against a tiny DOM boundary.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');

const template = readFileSync(join(__dirname, '../assets/base-template.html'), 'utf8');
const repaint = template.match(/      const next = nextSession\(\)[^]*?(?=\n    }\n\n    function setupFloatingProgress)/);
assert.ok(repaint, 'status repaint must be present, never test an empty extraction');
const selector = template.match(/    function nextSession\(\) {[^]*?\n    }/);
const arc = template.match(/    function renderSessionArc\(\) {[^]*?\n    }/);
const click = template.match(/    document\.querySelectorAll\('\[data-action="open-next"\]'\)[^]*?(?=\n\n    recomputeCounts\(\))/);
assert.ok(selector && arc && click, 'all next-session consumers must be present');

function display(statuses) {
  const nodes = Object.fromEntries(['next-line', 'floating-progress', 'fp-next', 'session-arc'].map(id => [id, { innerHTML: '' }]));
  const sessions = statuses.map((status, i) => ({
    id: `s${i + 1}`, dataset: { status },
    querySelector: query => query === 'h3.title' ? { textContent: `Task ${i + 1}` } : null,
  }));
  const location = { hash: '' };
  runInNewContext([selector[0], arc[0], repaint[0], 'renderSessionArc();', click[0]].join('\n'), {
    sessions, counts: { all: 0, done: 0 },
    location,
    document: {
      querySelector: () => null, getElementById: id => nodes[id] || null,
      querySelectorAll: () => [{ addEventListener: (_, callback) => callback({ preventDefault() {} }) }],
    },
  });
  const nextId = nodes['next-line'].innerHTML.match(/href="#(s\d+)"/)?.[1] || '';
  assert.equal(location.hash, nextId, 'Open-next must agree with Up next');
  assert.equal(nodes['session-arc'].innerHTML.match(/arc-seg is-next"[^]*?data-session-id="([^"]+)"/)?.[1] || '', nextId, 'arc must agree with Up next');
  return { next: nodes['next-line'].innerHTML, floating: nodes['fp-next'].textContent || nodes['fp-next'].innerHTML };
}

test('unfinished sessions never masquerade as completion', () => {
  const active = display(['BLOCKED', 'DOING']);
  assert.match(active.next, /S2.*in progress/); // known-positive selection
  const blocked = display(['DONE', 'BLOCKED']);
  assert.match(blocked.next, /S2.*blocked/i);
  assert.match(blocked.floating, /#s2/);
  for (const status of ['NEW_STATE', 'doing', '__proto__']) {
    const unknown = display(['DONE', status]);
    assert.match(unknown.next, /S2.*status unrecognized/i);
    assert.match(unknown.floating, /#s2/);
  }
  for (const statuses of [['DONE'], ['DONE', 'WONTFIX', 'DEFERRED']]) {
    const terminal = display(statuses);
    assert.match(terminal.next, /All sessions closed/);
    assert.match(terminal.floating, /closed/);
  }
  const empty = display([]);
  assert.match(empty.next, /No sessions defined/);
  assert.match(empty.floating, /no sessions/);
  assert.match(display(['BLOCKED', undefined]).next, /S2/);
  assert.match(display(['TODO', 'PARTIAL', 'DOING', 'AWAITS_REVIEW']).next, /S4/);
});
