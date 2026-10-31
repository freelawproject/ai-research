(function () {
  'use strict';
  const { el, renderSpans } = window.predcompare;
  const $ = (id) => document.getElementById(id);
  const PLAIN = CFG.marks !== 'gold';
  function toast(msg) { const t = $('toast'); t.textContent = msg; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 2500); }
  function portalLink(doc) { const [scan, op] = doc.split('_'); return CFG.portal.replace('{scan}', scan).replace('{op}', op); }
  function verdict(f) {
    const b = f.runs.before.diffs, a = f.runs.after.diffs;
    if (!a) return ['fixed', b ? 'now matches gold' : 'matched gold before too'];
    if (a < b) return ['better', 'fewer differences than before'];
    if (a === b) return ['same', 'same number of differences'];
    return ['worse', 'more differences than before'];
  }
  function card(f) {
    const c = el('section', 'card fcard');
    const head = el('div', 'fhead');
    const h = el('h3');
    h.appendChild(el('span', 'ftype', 'type ' + f.types.join('+')));
    const a = el('a', '', f.doc.replace('_', ' / ') + ' p' + f.p); a.href = portalLink(f.doc); a.target = '_blank'; a.rel = 'noopener';
    h.appendChild(a);
    h.appendChild(el('span', 'badge lab-' + (f.cls === 'all' ? 'separator' : f.cls), f.cls));
    head.appendChild(h);
    if (!f.missing && PLAIN) {
      head.appendChild(el('span', 'small muted', f.diff_pair ? f.diff_pair + ' ' + f.cls + ' span' + (f.diff_pair > 1 ? 's' : '') + ' differ between before and after' : 'before and after agree on ' + f.cls));
    } else if (!f.missing) {
      const [kind, why] = verdict(f);
      const v = el('span', 'verdict v-' + kind, kind); v.title = why; head.appendChild(v);
      head.appendChild(el('span', 'small muted', 'differences from gold: before ' + f.runs.before.diffs + ' · after ' + f.runs.after.diffs));
    }
    c.appendChild(head);
    const q = el('div', 'fquote');
    q.appendChild(el('div', 'small muted', 'raw output of the previous model, as quoted in the issue'));
    q.appendChild(el('code', '', f.raw));
    q.appendChild(el('div', 'small muted mt4', 'correct, per the issue: ' + f.correct));
    c.appendChild(q);
    if (f.missing) { c.appendChild(el('p', 'muted', 'anchor text not found in the portal set')); return c; }
    const panes = el('div', 'fpanes');
    for (const side of ['before', 'after']) {
      const r = f.runs[side];
      const pane = el('div', 'pane');
      const tail = PLAIN ? r.spans.length + ' span' + (r.spans.length === 1 ? '' : 's')
                         : (r.diffs ? r.diffs + ' difference' + (r.diffs > 1 ? 's' : '') + ' from gold' : 'matches gold');
      pane.appendChild(el('div', 'pane-head', side + ' · ' + r.run + ' · ' + tail));
      const body = el('div', 'doc-text fex');
      renderSpans(body, f.text, r, () => true, PLAIN);
      pane.appendChild(body);
      panes.appendChild(pane);
    }
    c.appendChild(panes);
    return c;
  }
  async function load() {
    const r = await fetch('/api/issue?' + new URLSearchParams({ edges: CFG.edges }));
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const d = await r.json();
    const root = $('cards'); root.replaceChildren();
    let fixed = 0, open = 0, total = 0;
    const byType = {};
    for (const f of d.flags) { const t = f.types[0]; (byType[t] = byType[t] || []).push(f); }
    for (const t of Object.keys(byType).sort((x, y) => x - y)) {
      root.appendChild(el('h2', 'ftitle', t + '. ' + d.types[t]));
      for (const f of byType[t]) { root.appendChild(card(f)); if (!f.missing) { total++; if (f.runs.after.diffs) open++; else fixed++; } }
    }
    $('n-fixed').textContent = fixed; $('n-open').textContent = open; $('n-total').textContent = total;
    if (PLAIN) { $('legend').hidden = true; } else { $('legend-plain').hidden = true; }
    if (PLAIN) { document.querySelectorAll('.gold-only').forEach((e) => { e.hidden = true; }); }
  }
  load().catch((e) => toast(e.message));
})();
