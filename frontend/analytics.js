// Analytics drawer: fetches /api/analytics and draws small SVG charts.
// No chart library: a column chart, a bar list and a table cover everything here.
// Every chart has a hover tooltip, a table view and a CSV download.

import { getParam, setParam } from "./urlstate.js";

const REFRESH_MS = 60_000;
const SVG_NS = "http://www.w3.org/2000/svg";

// How each section is drawn. `value` is the measure; `label` gives the category text.
const VIEWS = {
  flights_sydney_hourly: { kind: "columns", value: "aircraft", unit: "aircraft", note: "Last column = the current hour so far. Builds up as history accumulates." },
  fires_hourly: { kind: "columns", value: "incidents", unit: "incidents", note: "Last column = the current hour so far. Gaps = no successful poll that hour." },
  flight_altitudes: { kind: "bars", label: "label", value: "aircraft", unit: "aircraft", keepZero: true },
  ships_by_type: { kind: "bars", label: "category", value: "ships", unit: "ships", extra: ["moving", "moving"] },
  busiest_ports: { kind: "bars", label: "port", value: "ships", unit: "ships", extra: ["stationary", "at anchor or berthed"] },
  fires_by_level: { kind: "bars", label: "alert_level", value: "incidents", unit: "incidents", chips: "fire" },
  cameras_by_region: { kind: "bars", label: "label", value: "cameras", unit: "cameras" },
  source_health: { kind: "health" },
};
const ORDER = [
  "flights_sydney_hourly", "flight_altitudes", "ships_by_type", "busiest_ports",
  "fires_by_level", "fires_hourly", "cameras_by_region", "source_health",
];
const LAYER_NAMES = { cameras: "Traffic cameras", fires: "NSW RFS", quakes: "Earthquakes", ships: "Ships", flights: "Flights" };

let timer = null;
let tip = null;
let chipColors = {};

export function initAnalytics({ fireColors }) {
  chipColors = { fire: fireColors };
  const drawer = document.getElementById("analytics");
  const button = document.getElementById("analytics-toggle");
  tip = document.getElementById("viz-tip");
  const setOpen = (open) => {
    drawer.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    clearInterval(timer);
    setParam("analytics", open ? 1 : null); // shareable: the link opens with the panel showing
    if (open) {
      load();
      timer = setInterval(load, REFRESH_MS);
    }
  };
  button.addEventListener("click", () => setOpen(drawer.hidden));
  drawer.querySelector(".close").addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (e) => e.key === "Escape" && !drawer.hidden && setOpen(false));
  if (getParam("analytics") === "1") setOpen(true);
}

async function load() {
  const body = document.querySelector("#analytics .cards");
  const stamp = document.querySelector("#analytics .stamp");
  let data;
  try {
    const r = await fetch("/api/analytics");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    data = await r.json();
  } catch (err) {
    stamp.textContent = `Couldn't load analytics (${err.message}). Retrying in a minute.`;
    return;
  }
  if (!data.available) {
    body.replaceChildren(el("p", data.reason, "empty"));
    stamp.textContent = "";
    return;
  }
  stamp.textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString("en-AU", { timeZone: "Australia/Sydney", hour: "numeric", minute: "2-digit" })} · refreshes every minute`;
  // Keep each card's table/chart choice across refreshes.
  const showing = Object.fromEntries([...body.querySelectorAll(".card")].map((c) => [c.dataset.key, c.dataset.view]));
  body.replaceChildren(...ORDER.filter((k) => data.sections[k]).map((k) => card(k, data.sections[k], showing[k])));
}

// ---------- cards ----------

function card(key, section, view = "chart") {
  const spec = VIEWS[key];
  const root = el("section", null, "card");
  root.dataset.key = key;
  root.dataset.view = view;
  root.append(el("h3", section.title), el("p", section.description, "desc"));

  const area = el("div", null, "viz");
  root.append(area);
  if (section.error) {
    area.append(el("p", "Couldn't run this query. See the server log.", "empty"));
    return root;
  }
  const rows = section.rows;
  const draw = () => {
    area.replaceChildren();
    if (root.dataset.view === "table" || spec.kind === "health") area.append(table(key, rows, spec));
    else if (!rows.length || rows.every((r) => !r[spec.value])) area.append(el("p", "No data yet.", "empty"));
    else area.append(spec.kind === "columns" ? columns(rows, spec) : bars(rows, spec));
  };
  draw();
  if (spec.note && spec.kind === "columns") root.append(el("p", spec.note, "note"));

  const actions = el("div", null, "actions");
  if (spec.kind !== "health") {
    const toggle = el("button", root.dataset.view === "table" ? "Chart" : "Table");
    toggle.type = "button";
    toggle.addEventListener("click", () => {
      root.dataset.view = root.dataset.view === "table" ? "chart" : "table";
      toggle.textContent = root.dataset.view === "table" ? "Chart" : "Table";
      draw();
    });
    actions.append(toggle);
  }
  const csvBtn = el("button", "CSV");
  csvBtn.type = "button";
  csvBtn.addEventListener("click", () => downloadCsv(`ozwatch-${key}.csv`, rows));
  actions.append(csvBtn);
  root.append(actions);
  return root;
}

// ---------- column chart (24 hourly values) ----------

function columns(rows, spec) {
  const W = 320, H = 150, pad = { l: 28, r: 6, t: 8, b: 20 };
  const vals = rows.map((r) => r[spec.value]);
  const max = niceMax(Math.max(1, ...vals.filter((v) => v != null)));
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const step = iw / rows.length;
  const bw = Math.max(2, step - 2); // 2px gap between columns
  const y = (v) => pad.t + ih - (v / max) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${spec.unit} per hour` });
  for (const t of [0, max / 2, max]) {
    svg.append(svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y(t), y2: y(t), class: t ? "grid" : "axis" }));
    svg.append(svgEl("text", { x: pad.l - 4, y: y(t) + 3, class: "tick", "text-anchor": "end" }, fmtNum(t)));
  }
  rows.forEach((r, i) => {
    const x = pad.l + i * step + (step - bw) / 2;
    const v = r[spec.value];
    if (v) svg.append(svgEl("path", { d: roundedTop(x, y(v), bw, pad.t + ih - y(v), Math.min(4, bw / 2)), class: "mark" }));
    const hour = new Date(r.hour);
    if (hour.getMinutes() === 0 && i % 6 === 0) {
      svg.append(svgEl("text", { x: x + bw / 2, y: H - 5, class: "tick", "text-anchor": "middle" }, hourLabel(hour)));
    }
    // Hit target: the whole column height, wider than the bar.
    const hit = svgEl("rect", { x: pad.l + i * step, y: pad.t, width: step, height: ih, class: "hit" });
    const next = new Date(hour.getTime() + 3600_000);
    const text = `${hourLabel(hour)}–${hourLabel(next)}: ${v == null ? "no data" : `${fmtNum(v)} ${spec.unit}`}`;
    hoverTip(hit, text);
    svg.append(hit);
  });
  return svg;
}

// ---------- horizontal bar list ----------

function bars(rows, spec) {
  const list = spec.keepZero ? rows : rows.filter((r) => r[spec.value] > 0);
  const max = Math.max(1, ...list.map((r) => r[spec.value]));
  const wrap = el("div", null, "bars");
  for (const r of list) {
    const row = el("div", null, "bar-row");
    const label = el("span", null, "bar-label");
    if (spec.chips) {
      const chip = el("span", null, "chip");
      chip.style.background = chipColors[spec.chips][r[spec.label]] || "#888";
      label.append(chip);
    }
    label.append(document.createTextNode(r[spec.label]));
    const track = el("span", null, "bar-track");
    const fill = el("span", null, "bar-fill");
    fill.style.width = `${(r[spec.value] / max) * 100}%`;
    if (r[spec.value] > 0) fill.classList.add("nonzero");
    track.append(fill);
    const value = el("span", fmtNum(r[spec.value]), "bar-value");
    row.append(label, track, value);
    let text = `${r[spec.label]}: ${fmtNum(r[spec.value])} ${spec.unit}`;
    if (spec.extra) text += ` (${fmtNum(r[spec.extra[0]])} ${spec.extra[1]})`;
    hoverTip(row, text);
    wrap.append(row);
  }
  return wrap;
}

// ---------- tables ----------

function table(key, rows, spec) {
  if (spec.kind === "health") return healthTable(rows);
  const cols = Object.keys(rows[0] || {}).filter((c) => !["band", "from_ft", "to_ft", "region"].includes(c) || !rows[0].label);
  const t = el("table", null, "data");
  const head = el("tr");
  cols.forEach((c) => head.append(el("th", c.replace(/_/g, " "))));
  t.append(head);
  for (const r of rows) {
    const tr = el("tr");
    cols.forEach((c) => tr.append(el("td", c === "hour" ? hourLabel(new Date(r[c])) : r[c] == null ? "–" : String(r[c]))));
    t.append(tr);
  }
  return t;
}

function healthTable(rows) {
  if (!rows.length) return el("p", "No polls recorded yet.", "empty");
  const t = el("table", null, "data health");
  const head = el("tr");
  ["Source", "Success", "Runs", "Last problem"].forEach((h) => head.append(el("th", h)));
  t.append(head);
  for (const r of rows) {
    const tr = el("tr");
    tr.append(el("td", LAYER_NAMES[r.layer] || r.layer));
    const pct = el("td");
    const meter = el("span", null, "meter");
    const fill = el("span", null, "meter-fill");
    fill.style.width = `${r.success_pct}%`;
    meter.append(fill);
    // Text carries the state; the meter only illustrates it.
    pct.append(meter, document.createTextNode(` ${r.success_pct}%`));
    tr.append(pct, el("td", fmtNum(r.runs)));
    tr.append(el("td", r.last_error ? `${r.last_error} (${hourLabel(new Date(r.last_error_at))})` : "None"));
    t.append(tr);
  }
  return t;
}

// ---------- helpers ----------

function hoverTip(target, text) {
  target.addEventListener("pointerenter", () => {
    tip.textContent = text;
    tip.hidden = false;
  });
  target.addEventListener("pointermove", (e) => {
    const x = Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = `${x}px`;
    tip.style.top = `${e.clientY + 12}px`;
  });
  target.addEventListener("pointerleave", () => (tip.hidden = true));
}

function downloadCsv(name, rows) {
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const esc = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function roundedTop(x, y, w, h, r) {
  // Bar anchored to the baseline: only the data end (top) is rounded.
  r = Math.min(r, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function niceMax(v) {
  const p = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
}

function hourLabel(d) {
  return d.toLocaleTimeString("en-AU", { timeZone: "Australia/Sydney", hour: "numeric" }).replace(":00", "");
}

function fmtNum(n) {
  return Number(n).toLocaleString("en-AU", { maximumFractionDigits: 1 });
}

function svgEl(tag, attrs, text) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  return n;
}

function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
