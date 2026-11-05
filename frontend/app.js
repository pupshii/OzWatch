// OzWatch frontend. Plain ES module, no build step.
//
// Data flow:
//   1. GET /api/layers                -> which layers exist + attribution + status
//   2. GET /api/layers/:key/features  -> initial GeoJSON snapshot per layer
//   3. EventSource /api/stream        -> small "delta" messages (upserts/removals)
// On every (re)connect of the stream we re-fetch the snapshots, so nothing is
// missed while the connection was down.

/* global maplibregl */

const BASEMAP = "https://tiles.openfreemap.org/styles/positron"; // free, no API key
const AUSTRALIA = [[110, -45], [156, -9]];

// Per-layer look and popup. New layers add an entry here.
const LAYER_STYLE = {
  cameras: { color: "#0b6bcb", radius: 5, popup: cameraPopup },
};
const DEFAULT_STYLE = { color: "#6b7280", radius: 4, popup: genericPopup };

const state = {
  layers: new Map(), // key -> { info, status, features: Map(id -> geojson), visible, row }
};

const map = new maplibregl.Map({
  container: "map",
  style: BASEMAP,
  bounds: AUSTRALIA,
  fitBoundsOptions: { padding: 20 },
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
window.ozwatch = { map, state }; // handy in the browser dev console and for end-to-end tests

map.on("load", async () => {
  const layers = await getJSON("/api/layers");
  for (const info of layers) addLayer(info);
  await refreshAll();
  connectStream();
  setInterval(renderAllMeta, 30_000); // keep "updated 3 min ago" fresh
});

// ---------- layers ----------

function addLayer(info) {
  const style = LAYER_STYLE[info.key] || DEFAULT_STYLE;
  const entry = { info, status: info.status, features: new Map(), visible: true, row: null };
  state.layers.set(info.key, entry);

  map.addSource(info.key, { type: "geojson", data: emptyFC(), attribution: info.attribution });
  map.addLayer({
    id: info.key,
    type: "circle",
    source: info.key,
    paint: {
      "circle-color": style.color,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, style.radius - 2, 10, style.radius + 2],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.5,
    },
  });
  map.on("click", info.key, (e) => {
    const f = e.features[0];
    const node = style.popup(f.properties, info);
    new maplibregl.Popup({ maxWidth: "360px" }).setLngLat(f.geometry.coordinates).setDOMContent(node).addTo(map);
  });
  map.on("mouseenter", info.key, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", info.key, () => (map.getCanvas().style.cursor = ""));

  // Panel row
  const row = document.getElementById("layer-row").content.firstElementChild.cloneNode(true);
  row.querySelector(".name").textContent = info.title;
  row.querySelector(".swatch").style.background = style.color;
  row.querySelector("input").addEventListener("change", (ev) => {
    entry.visible = ev.target.checked;
    map.setLayoutProperty(info.key, "visibility", entry.visible ? "visible" : "none");
  });
  document.getElementById("layers").append(row);
  entry.row = row;

  // Attribution (required by the source licences)
  const li = document.createElement("li");
  const a = el("a", info.attribution);
  a.href = info.attribution_url;
  a.target = "_blank";
  a.rel = "noopener";
  li.append(a, document.createTextNode(` — ${info.licence}`));
  document.getElementById("attributions").append(li);

  renderMeta(entry);
}

async function refreshAll() {
  // Status too, not just features: the server may have restarted with new settings
  // (e.g. an API key added) and we could have missed its status messages meanwhile.
  for (const info of await getJSON("/api/layers")) {
    const entry = state.layers.get(info.key);
    if (entry) entry.status = info.status;
  }
  await Promise.all(
    [...state.layers.keys()].map(async (key) => {
      const fc = await getJSON(`/api/layers/${encodeURIComponent(key)}/features`);
      const entry = state.layers.get(key);
      entry.features = new Map(fc.features.map((f) => [f.id, f]));
      pushToMap(key);
    }),
  );
}

function applyDelta(msg) {
  const entry = state.layers.get(msg.layer);
  if (!entry) return;
  for (const f of msg.upserts) entry.features.set(f.id, f);
  for (const id of msg.removed) entry.features.delete(id);
  pushToMap(msg.layer);
}

function pushToMap(key) {
  const entry = state.layers.get(key);
  map.getSource(key).setData({ type: "FeatureCollection", features: [...entry.features.values()] });
  renderMeta(entry);
}

// ---------- live stream ----------

function connectStream() {
  const conn = document.getElementById("conn");
  const es = new EventSource("/api/stream");
  es.onopen = () => {
    conn.textContent = "live";
    conn.className = "conn live";
    refreshAll().catch(console.error); // catch up on anything missed while disconnected
  };
  es.onerror = () => {
    conn.textContent = "reconnecting…";
    conn.className = "conn down";
    // EventSource retries automatically.
  };
  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "delta") applyDelta(msg);
    else if (msg.type === "status") {
      const entry = state.layers.get(msg.layer);
      if (entry) {
        entry.status = msg.status;
        renderMeta(entry);
      }
    }
  };
}

// ---------- panel ----------

function renderAllMeta() {
  for (const entry of state.layers.values()) renderMeta(entry);
}

function renderMeta(entry) {
  const { row, status } = entry;
  if (!row) return;
  row.querySelector(".count").textContent = entry.features.size.toLocaleString();
  row.querySelector(".dot").className = `dot ${status.state}`;
  row.querySelector(".demo").hidden = !status.demo;
  const updated = status.last_success ? `updated ${timeAgo(status.last_success)}` : "no data yet";
  const err = status.state !== "ok" && status.last_error ? ` · ${status.last_error}` : "";
  row.querySelector(".updated").textContent = updated + err;
  row.querySelector(".meta").title = status.last_success
    ? `Last successful fetch ${fmtTime(status.last_success)} (polls every ${status.poll_seconds}s)`
    : "";
}

// ---------- popups ----------

function cameraPopup(p, info) {
  const root = el("div", null, "popup");
  root.append(el("h3", p.title || "Traffic camera"));
  if (p.view) root.append(el("p", p.view, "sub"));

  const wrap = el("div", null, "img-wrap");
  const msg = el("div", "Loading image…", "img-msg");
  wrap.append(msg);
  root.append(wrap);

  const when = el("span");
  const btn = el("button", "Refresh");
  const row = el("div", null, "row");
  row.append(when, btn);
  root.append(row);
  root.append(credit(info));

  const load = () => {
    if (!p.image_url) {
      msg.textContent = p.sample
        ? "Sample camera, so there's no live image. Add your TfNSW API token to .env to see real cameras."
        : "No image available for this camera.";
      btn.hidden = true;
      return;
    }
    const img = new Image();
    img.alt = `Traffic camera: ${p.title || ""}`;
    img.referrerPolicy = "no-referrer";
    img.onload = () => {
      wrap.replaceChildren(img);
      when.textContent = `Image loaded ${fmtTime(new Date())}`;
    };
    img.onerror = () => {
      wrap.replaceChildren(msg);
      msg.textContent = "Image unavailable right now.";
      when.textContent = "";
    };
    // Cache-buster: the camera JPEG URL stays the same while the picture updates (~every minute).
    const sep = p.image_url.includes("?") ? "&" : "?";
    img.src = `${p.image_url}${sep}t=${Date.now()}`;
  };
  btn.addEventListener("click", load);
  load();
  return root;
}

function genericPopup(p, info) {
  const root = el("div", null, "popup");
  root.append(el("h3", p.title || p.id));
  const skip = new Set(["id", "layer", "title"]);
  for (const [k, v] of Object.entries(p)) {
    if (skip.has(k) || v === null || v === "") continue;
    root.append(el("div", `${k}: ${v}`, "sub"));
  }
  root.append(credit(info));
  return root;
}

function credit(info) {
  const c = el("div", "Source: ", "credit");
  const a = el("a", info.attribution);
  a.href = info.attribution_url;
  a.target = "_blank";
  a.rel = "noopener";
  c.append(a);
  return c;
}

// ---------- helpers ----------

// Build DOM with textContent only: values from third-party feeds never become HTML.
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

function emptyFC() {
  return { type: "FeatureCollection", features: [] };
}

function timeAgo(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

function fmtTime(d) {
  return new Date(d).toLocaleTimeString("en-AU", { timeZone: "Australia/Sydney", timeZoneName: "short" });
}
