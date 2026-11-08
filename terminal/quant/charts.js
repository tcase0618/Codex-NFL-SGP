/* Minimal SVG chart helpers. All inputs are backend values; nothing is synthesized here. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const C = { blue: "#2f8cff", blue2: "#5aa6ff", grn: "#20c35a", red: "#f04a4a", amb: "#f2a516", vio: "#a78bfa", cyan: "#22d3ee", mut: "#6b7a91" };
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function svg(w, h) { return el("svg", { viewBox: `0 0 ${w} ${h}`, width: "100%", preserveAspectRatio: "xMidYMid meet", role: "img" }); }
  function fmt(v, d = 1) { return v == null || !isFinite(v) ? "—" : Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(d); }
  function txt(parent, x, y, s, anchor = "start", fill) { const t = el("text", { x, y, "text-anchor": anchor }, parent); if (fill) t.setAttribute("style", `fill:${fill}`); t.textContent = s; return t; }

  // histogram from {edges, counts}; marks: [{x,label,color}]
  function histogram(h, opts = {}) {
    const W = opts.w || 520, H = opts.h || 170, L = 34, B = 22, T = 10, R = 8;
    const s = svg(W, H);
    if (!h || !h.counts || !h.counts.length) { txt(s, W / 2, H / 2, "no data", "middle"); return s; }
    const lo = h.edges[0], hi = h.edges[h.edges.length - 1];
    const max = Math.max(...h.counts) || 1;
    const x = v => L + (v - lo) / (hi - lo) * (W - L - R), y = v => H - B - v / max * (H - B - T);
    el("line", { x1: L, x2: W - R, y1: H - B, y2: H - B, class: "axis" }, s);
    h.counts.forEach((c, i) => {
      const x0 = x(h.edges[i]), x1 = x(h.edges[i + 1]);
      const mid = (h.edges[i] + h.edges[i + 1]) / 2;
      const col = opts.split != null ? (mid < opts.split ? (opts.leftColor || C.red) : (opts.rightColor || C.blue)) : (opts.color || C.blue);
      el("rect", { x: x0 + 0.5, y: y(c), width: Math.max(0.5, x1 - x0 - 1), height: H - B - y(c), fill: col, opacity: 0.85 }, s);
    });
    const ticks = 6;
    for (let i = 0; i <= ticks; i++) { const v = lo + (hi - lo) * i / ticks; txt(s, x(v), H - 7, fmt(v, 0), "middle"); }
    (opts.marks || []).forEach(m => {
      if (m.x == null) return;
      el("line", { x1: x(m.x), x2: x(m.x), y1: T, y2: H - B, stroke: m.color || C.amb, "stroke-dasharray": "3 2" }, s);
      txt(s, x(m.x) + 3, T + 9, m.label, "start", m.color || C.amb);
    });
    if (opts.title) txt(s, L, T, opts.title);
    return s;
  }

  // pmf bars [{v,p}]
  function pmf(rows, opts = {}) {
    const W = opts.w || 260, H = opts.h || 120, L = 26, B = 18, T = 8, R = 6;
    const s = svg(W, H);
    if (!rows || !rows.length) { txt(s, W / 2, H / 2, "no data", "middle"); return s; }
    const max = Math.max(...rows.map(r => r.p)) || 1, n = rows.length, bw = (W - L - R) / n;
    rows.forEach((r, i) => {
      const h = r.p / max * (H - B - T);
      el("rect", { x: L + i * bw + 1, y: H - B - h, width: Math.max(1, bw - 2), height: h, fill: opts.color || C.blue }, s);
      if (n <= 16 || i % 2 === 0) txt(s, L + i * bw + bw / 2, H - 6, String(r.v), "middle");
    });
    txt(s, 2, T + 6, (max * 100).toFixed(0) + "%");
    return s;
  }

  // line chart: series [{name,color,points:[{x,y}]}]
  function line(series, opts = {}) {
    const W = opts.w || 520, H = opts.h || 170, L = 38, B = 22, T = 10, R = 10;
    const s = svg(W, H);
    const pts = series.flatMap(se => se.points.filter(p => p.y != null && isFinite(p.y)));
    if (!pts.length) { txt(s, W / 2, H / 2, "no observed values", "middle"); return s; }
    let xmin = Math.min(...pts.map(p => p.x)), xmax = Math.max(...pts.map(p => p.x));
    let ymin = opts.ymin ?? Math.min(...pts.map(p => p.y)), ymax = opts.ymax ?? Math.max(...pts.map(p => p.y));
    if (xmax === xmin) xmax = xmin + 1;
    if (ymax === ymin) { ymax += 0.5; ymin -= 0.5; }
    const pad = (ymax - ymin) * 0.08; ymin -= pad; ymax += pad;
    const x = v => L + (v - xmin) / (xmax - xmin) * (W - L - R), y = v => H - B - (v - ymin) / (ymax - ymin) * (H - B - T);
    for (let i = 0; i <= 4; i++) { const v = ymin + (ymax - ymin) * i / 4; el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }, s); txt(s, L - 4, y(v) + 3, fmt(v, opts.dec ?? 2), "end"); }
    if (opts.zero && ymin < 0 && ymax > 0) el("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), stroke: C.mut, "stroke-dasharray": "2 3" }, s);
    if (opts.diag) el("line", { x1: x(xmin), y1: y(xmin), x2: x(xmax), y2: y(xmax), stroke: C.mut, "stroke-dasharray": "3 3" }, s);
    const xt = opts.xticks || 6;
    for (let i = 0; i <= xt; i++) { const v = xmin + (xmax - xmin) * i / xt; txt(s, x(v), H - 7, fmt(v, opts.xdec ?? 0), "middle"); }
    series.forEach(se => {
      let d = "", pen = false;
      se.points.forEach(p => {
        if (p.y == null || !isFinite(p.y)) { pen = false; return; }
        d += (pen ? "L" : "M") + x(p.x).toFixed(1) + "," + y(p.y).toFixed(1); pen = true;
      });
      if (!se.dotsOnly) el("path", { d, fill: "none", stroke: se.color || C.blue, "stroke-width": se.width || 1.6, "stroke-dasharray": se.dash || "" }, s);
      if (se.dots || se.dotsOnly) se.points.forEach(p => { if (p.y != null && isFinite(p.y)) el("circle", { cx: x(p.x), cy: y(p.y), r: p.r || 2.4, fill: se.color || C.blue }, s); });
    });
    return s;
  }

  // horizontal diverging bars [{label,value}]
  function hbars(rows, opts = {}) {
    const W = opts.w || 420, rh = 17, H = rows.length * rh + 18, L = opts.labelW || 170, R = 46;
    const s = svg(W, H);
    const max = Math.max(1e-9, ...rows.map(r => Math.abs(r.value || 0)));
    const mid = L + (W - L - R) / 2, half = (W - L - R) / 2;
    el("line", { x1: mid, x2: mid, y1: 4, y2: H - 10, stroke: C.mut }, s);
    rows.forEach((r, i) => {
      const y0 = 6 + i * rh, w = Math.abs(r.value || 0) / max * half;
      txt(s, L - 6, y0 + 10, r.label.length > 30 ? r.label.slice(0, 29) + "…" : r.label, "end");
      el("rect", { x: r.value >= 0 ? mid : mid - w, y: y0 + 2, width: w, height: rh - 6, fill: r.value >= 0 ? (opts.pos || C.grn) : (opts.neg || C.red), opacity: .9 }, s);
      txt(s, W - 4, y0 + 10, fmt(r.value, opts.dec ?? 2), "end", "#d7e0ee");
    });
    return s;
  }

  window.Charts = { histogram, pmf, line, hbars, C, fmt };
})();
