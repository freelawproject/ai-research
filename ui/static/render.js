// predcompare — draw one run's spans over a text, gold as the reference.
// Shared by the compare page (two panes) and the issue page (excerpts).
(function () {
  'use strict';
  const MARKUP_RE = /<\/?[a-z][^>]*>/g;
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function spanIdxAt(spans, a) {
    for (let i = 0; i < spans.length; i++) if (spans[i].start <= a && a < spans[i].end) return i;
    return -1;
  }
  // chunk boundaries = block breaks, markup tags, the run's spans and the
  // gold spans it missed; a chunk inside a predicted span is wrapped in
  // one .sp element per span, a chunk inside a missed gold span is
  // underlined. wantCls filters which spans are drawn.
  // plain: only the run's own spans in their label colours, no status, no gold
  function renderSpans(root, text, run, wantCls, plain) {
    root.replaceChildren();
    const spans = run.spans.filter(wantCls);
    const missed = plain ? [] : run.missed.filter(wantCls);
    const cuts = new Set([0, text.length]);
    for (const s of spans) { cuts.add(s.start); cuts.add(s.end); }
    for (const s of missed) { cuts.add(s.start); cuts.add(s.end); }
    let m;
    MARKUP_RE.lastIndex = 0;
    while ((m = MARKUP_RE.exec(text))) { cuts.add(m.index); cuts.add(m.index + m[0].length); }
    for (let i = 0; i < text.length; i++) if (text[i] === '\n') { cuts.add(i); cuts.add(i + 1); }
    const pos = [...cuts].sort((a, b) => a - b);
    let block = el('div', 'blk'); block.dataset.blk = 0; let nb = 0;
    let wrap = null, wrapIdx = -1;
    for (let k = 0; k + 1 < pos.length; k++) {
      const a = pos[k], b = pos[k + 1]; if (a === b) continue;
      const piece = text.slice(a, b);
      if (piece === '\n') { root.appendChild(block); block = el('div', 'blk'); block.dataset.blk = ++nb; wrap = null; continue; }
      const ch = el('span', 'ch', piece);
      ch.dataset.start = a; ch.dataset.end = b;
      if (/^<\/?[a-z][^>]*>$/.test(piece)) ch.classList.add('mk');
      const mi = missed.findIndex((s) => s.start <= a && b <= s.end);
      const si = spanIdxAt(spans, a);
      if (mi >= 0) {
        ch.classList.add('missed'); ch.dataset.missed = mi;
        ch.title = 'gold ' + missed[mi].label + ' — not returned exactly';
        if (missed[mi].start === a) {
          const flag = el('span', 'flag missed', 'gold ' + missed[mi].label); flag.dataset.missed = mi;
          (wrap && wrapIdx === si ? wrap : block).appendChild(flag);
        }
      }
      if (si < 0) { block.appendChild(ch); wrap = null; wrapIdx = -1; continue; }
      const s = spans[si];
      if (!wrap || wrapIdx !== si) {
        wrap = el('span', 'sp lab-' + s.label + (plain ? '' : ' ' + s.status)); wrapIdx = si; wrap.dataset.span = si;
        if (s.pair) wrap.classList.add('pair');
        wrap.title = plain ? s.label + (s.pair ? ' · only this side' : '') : s.label + ' · ' + s.status + (s.note ? ' (' + s.note + ')' : '');
        wrap.appendChild(el('span', 'flag', s.label + (plain || s.status === 'match' ? '' : ' · ' + s.status + (s.note ? ': ' + s.note : ''))));
        block.appendChild(wrap);
      }
      wrap.appendChild(ch);
    }
    root.appendChild(block);
  }
  window.predcompare = { el, renderSpans };
})();
