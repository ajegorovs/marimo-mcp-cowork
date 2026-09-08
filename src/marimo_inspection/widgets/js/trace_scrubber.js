// TraceScrubber: step through rows of a (frames, points) array and draw one
// row as a line chart. Canvas + slider + prev/next + percent-jump buttons in a
// single self-contained component. All redraws happen here (no kernel round
// trip per step); `index` is synced so notebooks can bridge it to mo.state.
function render({ model, el }) {
  const wrap = document.createElement("div");
  wrap.style.cssText =
    "display:flex;flex-direction:column;gap:6px;width:100%;font:13px ui-sans-serif,system-ui,sans-serif;";

  const titleEl = document.createElement("div");
  titleEl.style.cssText = "font-weight:600;color:#222;";

  const plotWrap = document.createElement("div");
  plotWrap.style.cssText = "position:relative;width:100%;";
  const canvas = document.createElement("canvas");
  canvas.style.cssText =
    "width:100%;display:block;background:#fff;border:1px solid #e2e2e2;border-radius:4px;";
  plotWrap.appendChild(canvas);

  const label = document.createElement("div");
  label.style.cssText = "font:12px ui-monospace,monospace;color:#555;";

  const btnRow = document.createElement("div");
  btnRow.style.cssText = "display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:center;";

  const input = document.createElement("input");
  input.type = "range";
  input.min = 0;
  input.max = 0;
  input.value = 0;
  input.style.cssText = "width:100%;accent-color:#1268d9;";

  wrap.append(titleEl, plotWrap, label, btnRow, input);
  el.appendChild(wrap);

  const H = 300;
  const M = { top: 14, right: 14, bottom: 28, left: 52 };
  const dpr = () => window.devicePixelRatio || 1;

  const mkBtn = (txt) => {
    const b = document.createElement("button");
    b.textContent = txt;
    b.style.cssText =
      "cursor:pointer;border:1px solid #ccc;background:#fff;border-radius:4px;padding:1px 8px;color:#222;";
    return b;
  };

  const n = () => (model.get("frames") || []).length;
  const clamp = (i) => {
    const c = n();
    return c ? Math.max(0, Math.min(i, c - 1)) : 0;
  };
  const pcts = () => model.get("pcts") || [0.01, 0.1];

  // --- dataset-dependent controls (buttons, slider max) -------------------
  let builtKey = "";
  function buildControls() {
    const key = `${n()}|${(model.get("x") || []).length}|${pcts().join(",")}`;
    if (key === builtKey) return;
    builtKey = key;

    // (re)build the button row: [-big ... -small] ◀ ▶ [+small ... +big]
    btnRow.textContent = "";
    const desc = [...pcts()].sort((a, b) => b - a);
    const asc = [...pcts()].sort((a, b) => a - b);
    const make = (txt, d, sign) => {
      const b = mkBtn(txt);
      b.addEventListener("click", () => {
        const step = Math.max(1, Math.round(n() * d));
        go(model.get("index") + sign * step);
      });
      return b;
    };
    desc.forEach((p) => btnRow.appendChild(make(`−${Math.round(p * 100)}%`, p, -1)));
    const prev = mkBtn("◀");
    prev.addEventListener("click", () => go(model.get("index") - 1));
    btnRow.appendChild(prev);
    const next = mkBtn("▶");
    next.addEventListener("click", () => go(model.get("index") + 1));
    btnRow.appendChild(next);
    asc.forEach((p) => btnRow.appendChild(make(`+${Math.round(p * 100)}%`, p, 1)));
  }

  function go(i) {
    model.set("index", clamp(i));
    model.save_changes();
    update();
  }

  input.addEventListener("input", () => go(Number(input.value)));

  // --- canvas chart --------------------------------------------------------
  function fmt(v) {
    if (!Number.isFinite(v)) return "–";
    const a = Math.abs(v);
    return a >= 100 ? v.toFixed(0) : a >= 1 ? v.toFixed(1) : v.toFixed(3);
  }

  function niceTicks(lo, hi, k) {
    const span = hi - lo;
    if (!(span > 0) || !Number.isFinite(span)) return [];
    const raw = span / k;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
    const t0 = Math.ceil(lo / step) * step;
    const ticks = [];
    for (let v = t0; v <= hi + 1e-9; v += step) ticks.push(v);
    return ticks;
  }

  function draw() {
    const frames = model.get("frames") || [];
    const x = model.get("x") || [];
    const i = clamp(model.get("index"));
    const frame = frames[i];
    const w = Math.max(200, plotWrap.clientWidth || 600);
    canvas.width = Math.round(w * dpr());
    canvas.height = Math.round(H * dpr());
    canvas.style.height = H + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr(), 0, 0, dpr(), 0, 0);
    ctx.clearRect(0, 0, w, H);

    const plotW = w - M.left - M.right;
    const plotH = H - M.top - M.bottom;
    const xSpan = x.length > 1 ? x[x.length - 1] - x[0] : 1;
    const px = (v) => M.left + ((v - x[0]) / xSpan) * plotW;

    let yLo, yHi;
    if (model.get("y_fixed")) {
      yLo = Number(model.get("y_lo"));
      yHi = Number(model.get("y_hi"));
    } else if (frame && frame.length) {
      let mn = Infinity, mx = -Infinity;
      for (const v of frame) {
        if (v < mn) mn = v;
        if (v > mx) mx = v;
      }
      const pad = mx > mn ? (mx - mn) * 0.08 : 1;
      yLo = mn - pad;
      yHi = mx + pad;
    } else {
      yLo = 0;
      yHi = 1;
    }
    if (!(yHi > yLo)) yHi = yLo + 1;

    const py = (v) => M.top + (1 - (v - yLo) / (yHi - yLo)) * plotH;

    ctx.font = "10px ui-monospace,monospace";
    ctx.fillStyle = "#888";
    // y grid + tick labels
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.strokeStyle = "#eee";
    ctx.lineWidth = 1;
    for (const t of niceTicks(yLo, yHi, 5)) {
      const yy = py(t);
      ctx.beginPath();
      ctx.moveTo(M.left, yy);
      ctx.lineTo(M.left + plotW, yy);
      ctx.stroke();
      ctx.fillText(fmt(t), M.left - 6, yy);
    }
    // x tick labels + light vertical grid
    if (x.length > 1) {
      const nT = Math.max(2, Math.min(6, Math.floor(plotW / 70)));
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.strokeStyle = "#f2f2f2";
      for (let k = 0; k < nT; k++) {
        const j = Math.round((k * (x.length - 1)) / (nT - 1));
        const xx = px(x[j]);
        ctx.fillText(fmt(x[j]), xx, M.top + plotH + 7);
        ctx.beginPath();
        ctx.moveTo(xx, M.top);
        ctx.lineTo(xx, M.top + plotH);
        ctx.stroke();
      }
    }
    // axes
    ctx.strokeStyle = "#bbb";
    ctx.beginPath();
    ctx.moveTo(M.left, M.top);
    ctx.lineTo(M.left, M.top + plotH);
    ctx.lineTo(M.left + plotW, M.top + plotH);
    ctx.stroke();

    if (!frame || !frame.length || !x.length) return;

    // polyline + sparse markers
    ctx.strokeStyle = "#1268d9";
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.beginPath();
    frame.forEach((v, j) => {
      const xx = px(x[j]);
      const yy = py(v);
      if (j === 0) ctx.moveTo(xx, yy);
      else ctx.lineTo(xx, yy);
    });
    ctx.stroke();

    ctx.fillStyle = "#1268d9";
    const every = Math.max(1, Math.floor(x.length / 80));
    frame.forEach((v, j) => {
      if (j % every) return;
      ctx.beginPath();
      ctx.arc(px(x[j]), py(v), 2.5, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  // --- text ----------------------------------------------------------------
  function esc(s) {
    return String(s ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }
  function renderTitle(s) {
    const bold = esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    return bold.replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function update() {
    buildControls();
    const i = clamp(model.get("index"));
    input.max = Math.max(0, n() - 1);
    input.value = i;
    titleEl.innerHTML = renderTitle(model.get("title") || "");
    const times = model.get("times") || [];
    const t = times && times.length ? times[i] : null;
    label.textContent =
      t != null && Number.isFinite(t)
        ? `step ${i} / ${n() - 1} · t = ${t.toFixed(6)} s`
        : `step ${i} / ${n() - 1}`;
    draw();
  }

  [
    "index",
    "title",
    "x",
    "frames",
    "times",
    "y_lo",
    "y_hi",
    "y_fixed",
    "pcts",
  ].forEach((t) => model.on(`change:${t}`, update));

  window.addEventListener("resize", draw);
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(draw).observe(plotWrap);
  }

  update();
}
export default { render };
