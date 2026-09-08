// ImagePreview: view one image from a list at a time.
// Canvas + slider + prev/next + per-image label. One self-contained component.
function render({ model, el }) {
  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex;flex-direction:column;gap:6px;width:100%;";

  const titleEl = document.createElement("div");
  titleEl.style.cssText = "font:13px ui-sans-serif,system-ui,sans-serif;font-weight:600;color:#222;";

  const canvas = document.createElement("canvas");
  canvas.style.cssText = "width:100%;height:auto;background:#111;border-radius:4px;";
  const ctx = canvas.getContext("2d");

  const label = document.createElement("div");
  label.style.cssText = "font:12px ui-monospace,monospace;color:#555;";

  const row = document.createElement("div");
  row.style.cssText = "display:flex;gap:8px;align-items:center;";

  const prevBtn = document.createElement("button");
  prevBtn.textContent = "◀";
  prevBtn.style.cssText = "cursor:pointer;border:1px solid #ccc;background:#fff;border-radius:4px;padding:2px 8px;";

  const counter = document.createElement("span");
  counter.style.cssText =
    "font:12px ui-monospace,monospace;color:#333;width:3em;text-align:center;";

  const nextBtn = document.createElement("button");
  nextBtn.textContent = "▶";
  nextBtn.style.cssText = prevBtn.style.cssText;

  const input = document.createElement("input");
  input.type = "range";
  input.min = 0;
  input.max = 0;
  input.style.cssText = "flex:1;accent-color:#1268d9;";

  row.append(prevBtn, counter, input, nextBtn);
  wrap.append(titleEl, canvas, label, row);

  const n = () => (model.get("images") || []).length;

  function clamp(i) {
    const c = n();
    return c ? Math.min(Math.max(i, 0), c - 1) : 0;
  }

  function draw(i) {
    const images = model.get("images") || [];
    const shapes = model.get("shapes") || [];
    const [h, w] = shapes[i] || [0, 0];
    const data = images[i];
    if (!data || !(h && w)) return;
    const ch = Math.floor(data.length / (h * w)) || 1;
    canvas.width = w;
    canvas.height = h;
    const img = ctx.createImageData(w, h);
    for (let p = 0; p < w * h; p++) {
      const base = p * 4;
      if (ch >= 3) {
        // Interleaved RGB (or RGBA; ignore alpha)
        img.data[base] = data[p * ch];
        img.data[base + 1] = data[p * ch + 1];
        img.data[base + 2] = data[p * ch + 2];
      } else {
        const v = data[p];
        img.data[base] = v;
        img.data[base + 1] = v;
        img.data[base + 2] = v;
      }
      img.data[base + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
  }

  // Render the title from plain text with a minimal markdown subset
  // (**bold**, `code`) so lightweight formatting survives transport as a
  // plain string. Everything else is escaped to avoid injecting HTML.
  function renderTitle(s) {
    const esc = String(s ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
    const bold = esc.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    return bold.replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function update() {
    const i = clamp(model.get("index"));
    input.max = Math.max(0, n() - 1);
    input.value = i;
    titleEl.innerHTML = renderTitle(model.get("title") || "");
    const names = model.get("names") || [];
    label.textContent = names[i] != null ? String(names[i]) : "image " + i;
    counter.textContent = `${i + 1}/${n()}`;
    draw(i);
  }

  function go(i) {
    model.set("index", clamp(i));
    model.save_changes();
    update();
  }

  input.addEventListener("input", () => go(Number(input.value)));
  prevBtn.addEventListener("click", () => go(model.get("index") - 1));
  nextBtn.addEventListener("click", () => go(model.get("index") + 1));

  model.on("change:index", update);
  model.on("change:title", update);
  model.on("change:images", update);
  model.on("change:shapes", update);
  model.on("change:names", update);

  el.appendChild(wrap);
  update();
}
export default { render };