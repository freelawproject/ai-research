/* Opinion page: one text with gold shading plus one stripe per system, a locus
   list carrying each system's verdict against gold, and a click inspector that
   shows the coreference group every system put the span in.
   Vanilla JS; text is only ever set with textContent. */
(function () {
  "use strict";
  var DATA = null;
  var SEGS = [];               // {el, op, s, e, gold: gid|null, sys: {key: gid|null}}
  var CUR = -1;                // focused locus id
  var PREFS_KEY = "dev_compare_prefs";
  var CSSVAR = {
    "s-eye": "--c-eye", "s-eyetxt": "--c-eyetxt", "s-gpt": "--c-gpt", "s-opus": "--c-opus", "s-enc": "--c-enc",
    "s-sonnet": "--c-sonnet", "s-kimi": "--c-kimi", "s-gptv5": "--c-gptv5"
  };
  var COLOR = {};
  var prefs = loadPrefs();

  function loadPrefs() {
    var p = { view: "gold", diffonly: false, hide: {}, flags: { miss: true, fp: true, group: true, clean: false }, sys: {} };
    try {
      var raw = localStorage.getItem(PREFS_KEY);
      if (raw) {
        var q = JSON.parse(raw);
        p.view = q.view || p.view;
        p.diffonly = !!q.diffonly;
        p.hide = q.hide || {};
        p.flags = Object.assign(p.flags, q.flags || {});
        p.sys = q.sys || {};
      }
    } catch (e) { /* private mode — defaults are fine */ }
    return p;
  }
  function savePrefs() { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) { /* ignore */ } }

  function toast(msg) {
    var t = document.getElementById("toast");
    t.textContent = msg; t.classList.add("show");
    setTimeout(function () { t.classList.remove("show"); }, 2200);
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function short(key) { return key === "gold" ? "gold" : DATA.systems[key].short; }
  function label(key) { return key === "gold" ? "annotator gold" : DATA.systems[key].label; }
  function sysOn(key) { return prefs.sys[key] !== false; }

  /* ---------- text ---------- */
  function coveringGid(ms, op, s, e) {
    for (var i = 0; i < ms.length; i++) {
      var m = ms[i];
      if (m.op === op && m.s <= s && m.e >= e) return m.gid;
    }
    return null;
  }

  function renderText() {
    var host = document.getElementById("text");
    host.textContent = "";
    var keys = DATA.order;
    var M = DATA.mentions;
    document.documentElement.style.setProperty("--nl", String(keys.length));
    keys.forEach(function (k) {
      COLOR[k] = getComputedStyle(document.documentElement)
        .getPropertyValue(CSSVAR[DATA.systems[k].css] || "").trim() || "#6b7280";
    });
    SEGS = [];
    DATA.ops.forEach(function (o) {
      var sec = el("section", "writing");
      sec.appendChild(el("div", "writing-type", o.type || "opinion"));
      var body = el("div", "optext");
      var text = o.text;
      var bounds = new Set([0, text.length]);
      var here = {};
      ["gold"].concat(keys).forEach(function (k) {
        here[k] = M[k].filter(function (m) { return m.op === o.op_id; });
        here[k].forEach(function (m) { bounds.add(m.s); bounds.add(m.e); });
      });
      var bs = Array.from(bounds).sort(function (a, b) { return a - b; });
      for (var i = 0; i < bs.length - 1; i++) {
        var s = bs[i], e = bs[i + 1];
        if (e <= s) continue;
        var gold = coveringGid(here.gold, o.op_id, s, e);
        var sys = {}, nPresent = 0;
        keys.forEach(function (k) {
          sys[k] = coveringGid(here[k], o.op_id, s, e);
          if (sys[k] !== null) nPresent++;
        });
        var chunk = text.slice(s, e);
        if (gold === null && nPresent === 0) { body.appendChild(document.createTextNode(chunk)); continue; }
        var span = el("span", "seg-t", chunk);
        span.classList.add(gold === null ? "fp" : (nPresent === keys.length ? "agree" : "miss"));
        span.tabIndex = 0;
        var rec = { el: span, op: o.op_id, s: s, e: e, gold: gold, sys: sys };
        SEGS.push(rec);
        span.addEventListener("click", function () { selectSeg(rec); });
        span.addEventListener("keydown", function (ev) { if (ev.key === "Enter") selectSeg(rec); });
        body.appendChild(span);
      }
      sec.appendChild(body);
      host.appendChild(sec);
    });
    paintStripes();
    var parts = ["gold " + M.gold.length];
    keys.forEach(function (k) { parts.push(short(k) + " " + M[k].length); });
    var un = [];
    keys.forEach(function (k) {
      if (DATA.counts[k] && DATA.counts[k].unanchored) un.push(DATA.counts[k].unanchored + " " + short(k));
    });
    document.getElementById("op-summary").textContent =
      DATA.ops.length + " writing" + (DATA.ops.length === 1 ? "" : "s") + " · " + parts.join(" · ") + " mentions" +
      (un.length ? " · unanchored: " + un.join(", ") : "");
  }

  /* stripe slots are positional: slot i = DATA.order[i], cleared when hidden */
  function paintStripes() {
    var keys = DATA.order;
    SEGS.forEach(function (r) {
      keys.forEach(function (k, i) {
        var on = r.sys[k] !== null && !prefs.hide[k];
        r.el.style.setProperty("--l" + (i + 1), on ? COLOR[k] : "transparent");
      });
    });
  }

  /* ---------- selection ---------- */
  function gidOf(rec, key) { return key === "gold" ? rec.gold : rec.sys[key]; }

  function selectSeg(rec) {
    SEGS.forEach(function (r) { r.el.classList.remove("selected", "mate"); });
    rec.el.classList.add("selected");
    var gid = gidOf(rec, prefs.view);
    if (gid !== null) {
      SEGS.forEach(function (r) { if (r !== rec && gidOf(r, prefs.view) === gid) r.el.classList.add("mate"); });
    }
    renderSel(rec);
  }

  function opText(op) {
    var t = "";
    DATA.ops.forEach(function (o) { if (o.op_id === op) t = o.text; });
    return t;
  }

  function members(key, gid) {
    var out = [];
    DATA.mentions[key].forEach(function (m) {
      if (m.gid === gid) out.push({ m: m, text: opText(m.op).slice(m.s, m.e).replace(/\s+/g, " ") });
    });
    return out;
  }

  function renderSel(rec) {
    var box = document.getElementById("sel");
    box.textContent = ""; box.classList.remove("muted");
    var full = opText(rec.op);
    box.appendChild(el("div", "sel-text", "“" + full.slice(rec.s, rec.e).replace(/\s+/g, " ") + "”"));
    var ctx = full.slice(Math.max(0, rec.s - 70), rec.s) + "⟦" + full.slice(rec.s, rec.e) + "⟧" +
              full.slice(rec.e, rec.e + 70);
    box.appendChild(el("div", "sel-ctx small muted", ctx.replace(/\s+/g, " ")));
    ["gold"].concat(DATA.order).forEach(function (key) {
      var gid = gidOf(rec, key);
      var row = el("div", "sel-row" + (key === "gold" ? "" : " " + DATA.systems[key].css));
      var head = el("div", "sel-sys");
      head.appendChild(el("i", key === "gold" ? "swatch agree" : "stripe"));
      head.appendChild(el("span", "sys-name", label(key)));
      if (gid === null) {
        head.appendChild(el("span", "badge badge-red", "not tagged"));
        row.appendChild(head); box.appendChild(row); return;
      }
      var mem = members(key, gid);
      var name = (DATA.names[key] || {})[gid];
      head.appendChild(el("span", "badge badge-indigo", gid + (name ? " · " + name : "")));
      head.appendChild(el("span", "muted small", mem.length + " mention" + (mem.length === 1 ? "" : "s")));
      row.appendChild(head);
      var list = el("div", "sel-members small");
      var seen = {}, n = 0;
      mem.forEach(function (x) {
        if (seen[x.text] || n >= 10) return;
        seen[x.text] = 1; n++;
        var b = el("button", "member", x.text);
        b.type = "button";
        b.addEventListener("click", function () { jumpTo(x.m.op, x.m.s); });
        list.appendChild(b);
      });
      if (mem.length > n) list.appendChild(el("span", "muted", "… " + (mem.length - n) + " more"));
      row.appendChild(list);
      box.appendChild(row);
    });
  }

  function jumpTo(op, s) {
    var rec = null;
    for (var i = 0; i < SEGS.length; i++) {
      if (SEGS[i].op === op && SEGS[i].s <= s && SEGS[i].e > s) { rec = SEGS[i]; break; }
    }
    if (!rec) { toast("that span is not in the rendered text"); return; }
    rec.el.scrollIntoView({ block: "center", behavior: "smooth" });
    rec.el.classList.add("flash");
    setTimeout(function () { rec.el.classList.remove("flash"); }, 1200);
    selectSeg(rec);
  }

  /* ---------- loci ---------- */
  function verdict(cell) {
    if (cell.v === "miss") return ["v-miss", "missed"];
    if (cell.v === "fp") return ["v-fp", "false pos."];
    if (cell.v === "none") return ["v-none", "—"];
    return cell.group_ok ? ["v-ok", "ok"] : ["v-group", "wrong group"];
  }
  function wrongKeys(lo) {
    return DATA.order.filter(function (k) {
      var c = lo.sys[k];
      return c && (c.v === "miss" || c.v === "fp" || (c.v === "match" && !c.group_ok));
    });
  }
  function visible(lo) {
    if (lo.clean) return !!prefs.flags.clean;
    if (!lo.flags.some(function (f) { return prefs.flags[f]; })) return false;
    var w = wrongKeys(lo);
    return w.some(sysOn);
  }

  function renderLoci() {
    var host = document.getElementById("loci");
    host.textContent = "";
    var shown = 0;
    DATA.loci.forEach(function (lo) {
      if (!visible(lo)) return;
      shown++;
      var row = el("div", "locus");
      row.dataset.id = String(lo.id);
      var head = el("div", "locus-head");
      head.appendChild(el("span", "badge badge-gray", lo.kind));
      head.appendChild(el("span", "locus-text", "“" + lo.text.slice(0, 90) + "”"));
      if (lo.gold.tagged) {
        head.appendChild(el("span", "badge badge-amber",
          "gold " + lo.gold.gid + (lo.gold.name ? " · " + lo.gold.name : "")));
      } else {
        head.appendChild(el("span", "badge badge-red", "not in gold"));
      }
      row.appendChild(head);
      var vrow = el("div", "vrow");
      DATA.order.forEach(function (k) {
        var c = lo.sys[k];
        if (!c) return;
        var v = verdict(c);
        var chip = el("span", "v " + v[0]);
        chip.appendChild(el("span", "k", short(k)));
        var txt = v[1];
        if (c.v === "match" && !c.group_ok) txt = c.gid + " (" + c.n_mates + " vs gold " + c.n_mates_gold + ")";
        else if (c.v === "fp") txt = "false pos.";
        chip.appendChild(el("span", "", txt));
        vrow.appendChild(chip);
      });
      row.appendChild(vrow);
      row.appendChild(el("div", "locus-ctx small muted", lo.ctx));
      row.tabIndex = 0;
      row.addEventListener("click", function () { focusLocus(lo.id); });
      row.addEventListener("keydown", function (ev) { if (ev.key === "Enter") focusLocus(lo.id); });
      host.appendChild(row);
    });
    if (!shown) host.appendChild(el("p", "center-note", "No loci match these filters."));
    document.getElementById("loci-count").textContent = shown + " / " + DATA.loci.length;
  }

  function focusLocus(id) {
    CUR = id;
    document.querySelectorAll("#loci .locus").forEach(function (r) {
      r.classList.toggle("current", parseInt(r.dataset.id, 10) === id);
    });
    var row = document.querySelector('#loci .locus[data-id="' + id + '"]');
    if (row) row.scrollIntoView({ block: "nearest" });
    var lo = DATA.loci[id];
    jumpTo(lo.op, lo.s);
  }
  function stepLocus(d) {
    var ids = Array.from(document.querySelectorAll("#loci .locus"))
      .map(function (r) { return parseInt(r.dataset.id, 10); });
    if (!ids.length) return;
    var i = ids.indexOf(CUR);
    focusLocus(ids[i < 0 ? (d > 0 ? 0 : ids.length - 1) : Math.min(ids.length - 1, Math.max(0, i + d))]);
  }

  /* ---------- controls ---------- */
  function applyPrefs() {
    document.body.classList.toggle("diffonly", !!prefs.diffonly);
    document.body.classList.toggle("hide-gold", !!prefs.hide.gold);
    var d = document.getElementById("diffonly");
    d.classList.toggle("active", !!prefs.diffonly);
    d.setAttribute("aria-pressed", prefs.diffonly ? "true" : "false");
    document.querySelectorAll("#layer-legend .lg").forEach(function (b) {
      var off = !!prefs.hide[b.dataset.layer];
      b.classList.toggle("off", off);
      b.setAttribute("aria-pressed", off ? "false" : "true");
    });
    document.querySelectorAll("#view-seg button").forEach(function (b) {
      var on = b.dataset.view === prefs.view;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    document.querySelectorAll("#flag-chips .chip").forEach(function (b) {
      var on = !!prefs.flags[b.dataset.flag];
      b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    document.querySelectorAll("#sys-chips .chip").forEach(function (b) {
      var on = sysOn(b.dataset.sys);
      b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function toggleLayer(k) {
    prefs.hide[k] = !prefs.hide[k]; savePrefs(); applyPrefs();
    if (DATA && k !== "gold") paintStripes();
  }
  function setView(v) {
    prefs.view = v; savePrefs(); applyPrefs();
    var sel = SEGS.filter(function (r) { return r.el.classList.contains("selected"); })[0];
    if (sel) selectSeg(sel);
  }

  function wire() {
    document.querySelectorAll("#layer-legend .lg").forEach(function (b) {
      b.addEventListener("click", function () { toggleLayer(b.dataset.layer); });
    });
    document.querySelectorAll("#view-seg button").forEach(function (b) {
      b.addEventListener("click", function () { setView(b.dataset.view); });
    });
    document.getElementById("diffonly").addEventListener("click", function () {
      prefs.diffonly = !prefs.diffonly; savePrefs(); applyPrefs();
    });
    document.querySelectorAll("#flag-chips .chip").forEach(function (b) {
      b.addEventListener("click", function () {
        prefs.flags[b.dataset.flag] = !prefs.flags[b.dataset.flag];
        savePrefs(); applyPrefs(); renderLoci();
      });
    });
    document.querySelectorAll("#sys-chips .chip").forEach(function (b) {
      b.addEventListener("click", function () {
        prefs.sys[b.dataset.sys] = !sysOn(b.dataset.sys);
        savePrefs(); applyPrefs(); renderLoci();
      });
    });
    document.addEventListener("keydown", function (ev) {
      var tag = (ev.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (ev.key === "0") { toggleLayer("gold"); }
      else if (DATA && /^[1-9]$/.test(ev.key) && DATA.order[parseInt(ev.key, 10) - 1]) {
        toggleLayer(DATA.order[parseInt(ev.key, 10) - 1]);
      } else if (ev.key === "d") {
        prefs.diffonly = !prefs.diffonly; savePrefs(); applyPrefs();
      } else if (ev.key === "j") { stepLocus(1); }
      else if (ev.key === "k") { stepLocus(-1); }
      else if (ev.key === "n") { var nl = document.getElementById("next-link"); if (nl) window.location.href = nl.href; }
      else if (ev.key === "p") { var pl = document.getElementById("prev-link"); if (pl) window.location.href = pl.href; }
      else { return; }
      ev.preventDefault();
    });
  }

  function load() {
    fetch("/api/opinion/" + encodeURIComponent(window.CID)).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (d) {
      DATA = d;
      if (prefs.view !== "gold" && d.order.indexOf(prefs.view) < 0) prefs.view = "gold";
      renderText();
      renderLoci();
      applyPrefs();
    }).catch(function (err) {
      var host = document.getElementById("text");
      host.textContent = "";
      host.appendChild(el("p", "error", "Could not load the opinion: " + err.message));
      var b = el("button", "btn-outline", "Retry");
      b.type = "button";
      b.addEventListener("click", load);
      host.appendChild(b);
    });
  }

  wire();
  applyPrefs();
  load();
})();
