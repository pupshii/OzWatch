// OzWatch frontend. Plain ES module, no build step.
//
// Data flow:
//   1. GET /api/layers                -> which layers exist + attribution + status
//   2. GET /api/layers/:key/features  -> initial GeoJSON snapshot per layer
//   3. EventSource /api/stream        -> small "delta" messages (upserts/removals)
// On every (re)connect of the stream we re-fetch the snapshots, so nothing is
// missed while the connection was down.

/* global maplibregl */
import { initAnalytics } from "./analytics.js";
import { getParam, setParam } from "./urlstate.js";

const BASEMAP = "https://tiles.openfreemap.org/styles/positron"; // free, no API key
const AUSTRALIA = [[110, -45], [156, -9]];

// Ship colour by AIS category (names match the backend's ship_category()).
const SHIP_COLORS = {
  Cargo: "#2f9e44",
  Tanker: "#e03131",
  Passenger: "#7048e8",
  Fishing: "#b5651d",
  "Tug / service": "#0c8599",
  "Sailing / pleasure": "#d6336c",
  "High-speed craft": "#e8a200",
  Military: "#495057",
  Other: "#868e96",
  Unknown: "#4c5a6b", // dark slate: still visible on the light basemap
};

// Fire incidents by NSW RFS alert level (most to least urgent).
const FIRE_COLORS = {
  "Emergency Warning": "#c92a2a",
  "Watch and Act": "#f76707",
  Advice: "#f2b705",
  "Not Applicable": "#8d6e63",
};
const QUAKE_COLORS = { "Last 24 h": "#ae3ec9", "1–7 days": "#c9a3e0" };

// Per-layer look and popup. New layers add an entry here.
const LAYER_STYLE = {
  cameras: { kind: "circle", color: "#0b6bcb", radius: 3.5, stroke: 1, popup: cameraPopup },
  // live: an open popup follows the feature and re-renders when it updates
  fires: { kind: "fire", popup: firePopup, colors: FIRE_COLORS, legend: FIRE_COLORS, areas: true },
  quakes: { kind: "quake", popup: quakePopup, legend: QUAKE_COLORS, decorate: decorateQuake },
  ships: { kind: "ship", popup: shipPopup, live: true, colors: SHIP_COLORS, legend: SHIP_COLORS },
  flights: { kind: "plane", color: "#d9480f", groundColor: "#8a919c", popup: flightPopup, live: true },
};
const DEFAULT_STYLE = { kind: "circle", color: "#6b7280", radius: 4, popup: genericPopup };

const state = {
  layers: new Map(), // key -> { info, status, features: Map(id -> geojson), visible, row }
  openPopup: null, // { layer, id, popup } for the popup currently on screen
};

const map = new maplibregl.Map({
  container: "map",
  style: BASEMAP,
  bounds: AUSTRALIA,
  fitBoundsOptions: { padding: 20 },
  hash: "map", // keep zoom/lat/lon in the URL (#map=...), alongside our own params
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
window.ozwatch = { map, state }; // handy in the browser dev console and for end-to-end tests

initAnalytics({ fireColors: FIRE_COLORS });

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
  // Layers listed in the URL (#layers=a,b) start visible; no list = all visible.
  const shared = getParam("layers");
  const visible = shared == null || shared.split(",").includes(info.key);
  const entry = { info, status: info.status, features: new Map(), visible, row: null, mapLayers: [] };
  state.layers.set(info.key, entry);

  if (style.areas) {
    // Shapes (e.g. fire extents) go underneath every point layer.
    const areaKey = `${info.key}-areas`;
    map.addSource(areaKey, { type: "geojson", data: emptyFC() });
    map.addLayer(areaLayer(areaKey, style), state.firstPointLayer);
    entry.mapLayers.push(`${areaKey}-fill`, `${areaKey}-line`);
  }
  map.addSource(info.key, { type: "geojson", data: emptyFC(), attribution: info.attribution });
  const makeLayer = { plane: planeLayer, ship: shipLayer, circle: circleLayer, fire: fireLayer, quake: quakeLayer };
  map.addLayer(makeLayer[style.kind](info.key, style));
  if (style.areas) map.addLayer(areaOutline(`${info.key}-areas`, style), info.key);
  entry.mapLayers.push(info.key);
  state.firstPointLayer ??= info.key;

  const openPopup = (e) => {
    // Use our own copy of the feature: MapLibre's rendered copy can lag a delta behind.
    const id = e.features[0].properties.id;
    const f = entry.features.get(id);
    if (!f) return;
    const popup = new maplibregl.Popup({ maxWidth: "360px" })
      .setLngLat(f.geometry.coordinates)
      .setDOMContent(style.popup(f.properties, info))
      .addTo(map);
    const open = { layer: info.key, id, popup };
    state.openPopup = open;
    popup.on("close", () => {
      if (state.openPopup === open) state.openPopup = null;
    });
  };
  for (const id of entry.mapLayers.filter((l) => !l.endsWith("-line"))) {
    map.on("click", id, openPopup);
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }

  // Panel row
  const row = document.getElementById("layer-row").content.firstElementChild.cloneNode(true);
  row.querySelector(".name").textContent = info.title;
  row.querySelector("input").addEventListener("change", (ev) => {
    entry.visible = ev.target.checked;
    applyVisibility(entry);
    const on = [...state.layers.values()].filter((e) => e.visible).map((e) => e.info.key);
    setParam("layers", on.length === state.layers.size ? null : on.join(","));
  });
  const swatch = row.querySelector(".swatch");
  if (style.legend) {
    row.append(legend(style.legend));
    swatch.style.background = `conic-gradient(${Object.values(style.legend).slice(0, 4).join(", ")})`;
  } else swatch.style.background = style.color;
  document.getElementById("layers").append(row);
  entry.row = row;
  row.querySelector("input").checked = entry.visible;
  applyVisibility(entry);

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

function circleLayer(key, style) {
  return {
    id: key,
    type: "circle",
    source: key,
    paint: {
      "circle-color": style.color,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, style.radius - 2, 10, style.radius + 2],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": style.stroke ?? 1.5,
    },
  };
}

// Fires: circles coloured by alert level; more urgent = bigger and drawn on top.
function fireLayer(key, style) {
  const color = ["match", ["get", "alert_level"], ...Object.entries(style.colors).flat(), "#8d6e63"];
  const urgency = ["match", ["get", "alert_level"], "Emergency Warning", 3, "Watch and Act", 2, "Advice", 1, 0];
  return {
    id: key,
    type: "circle",
    source: key,
    layout: { "circle-sort-key": urgency },
    paint: {
      "circle-color": color,
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, ["+", 3, urgency], 10, ["+", 6, ["*", 2, urgency]]],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.5,
    },
  };
}

function areaLayer(sourceKey, style) {
  const color = ["match", ["get", "alert_level"], ...Object.entries(style.colors).flat(), "#8d6e63"];
  return {
    id: `${sourceKey}-fill`,
    type: "fill",
    source: sourceKey,
    paint: { "fill-color": color, "fill-opacity": 0.25 },
  };
}

function areaOutline(sourceKey, style) {
  const color = ["match", ["get", "alert_level"], ...Object.entries(style.colors).flat(), "#8d6e63"];
  return {
    id: `${sourceKey}-line`,
    type: "line",
    source: sourceKey,
    paint: { "line-color": color, "line-width": 1.2, "line-opacity": 0.8 },
  };
}

// Earthquakes: size by magnitude, darker if in the last 24 h.
function quakeLayer(key) {
  return {
    id: key,
    type: "circle",
    source: key,
    layout: { "circle-sort-key": ["get", "magnitude"] },
    paint: {
      "circle-color": ["case", ["get", "recent"], QUAKE_COLORS["Last 24 h"], QUAKE_COLORS["1–7 days"]],
      "circle-radius": ["interpolate", ["linear"], ["get", "magnitude"], 2.5, 5, 4, 9, 6, 18, 7, 26],
      "circle-opacity": 0.75,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.5,
    },
  };
}

function decorateQuake(f) {
  const recent = Date.now() - new Date(f.properties.timestamp).getTime() < 24 * 3600 * 1000;
  return { ...f, properties: { ...f.properties, recent } };
}

// Aircraft: a plane icon rotated to its track; grey when on the ground.
function planeLayer(key, style) {
  map.addImage(`${key}-air`, planeIcon(style.color), { pixelRatio: 2 });
  map.addImage(`${key}-ground`, planeIcon(style.groundColor), { pixelRatio: 2 });
  return {
    id: key,
    type: "symbol",
    source: key,
    layout: {
      "icon-image": ["case", ["==", ["get", "on_ground"], true], `${key}-ground`, `${key}-air`],
      "icon-rotate": ["coalesce", ["get", "heading"], 0],
      "icon-rotation-alignment": "map", // rotate relative to north, not the screen
      "icon-size": ["interpolate", ["linear"], ["zoom"], 3, 0.6, 9, 1.0],
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
  };
}

// Ships: a pointed hull rotated to heading when moving, a dot when stationary,
// coloured by category. One pre-drawn image per (category, moving) pair.
function shipLayer(key, style) {
  for (const [cat, color] of Object.entries(style.colors)) {
    map.addImage(`${key}|${cat}|m`, shipIcon(color, true), { pixelRatio: 2 });
    map.addImage(`${key}|${cat}|s`, shipIcon(color, false), { pixelRatio: 2 });
  }
  return {
    id: key,
    type: "symbol",
    source: key,
    layout: {
      "icon-image": [
        "concat", `${key}|`,
        ["match", ["get", "category"], Object.keys(style.colors), ["get", "category"], "Unknown"],
        ["case", ["==", ["get", "moving"], true], "|m", "|s"],
      ],
      "icon-rotate": ["coalesce", ["get", "bearing"], 0],
      "icon-rotation-alignment": "map",
      "icon-size": ["interpolate", ["linear"], ["zoom"], 3, 0.55, 10, 1.0],
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
  };
}

function shipIcon(color, moving) {
  const size = 48;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.scale(2, 2);
  const shape = new Path2D();
  if (moving) shape.addPath(new Path2D("M12 2.5L16 9V20.5L12 22L8 20.5V9Z"));
  else shape.arc(12, 12, 4.5, 0, Math.PI * 2);
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = "#ffffff";
  ctx.lineJoin = "round";
  ctx.stroke(shape);
  ctx.fillStyle = color;
  ctx.fill(shape);
  return ctx.getImageData(0, 0, size, size);
}

function legend(colors) {
  const box = el("div", null, "legend");
  for (const [label, color] of Object.entries(colors)) {
    const item = el("span", null, "legend-item");
    const chip = el("span", null, "chip");
    chip.style.background = color;
    item.append(chip, document.createTextNode(label));
    box.append(item);
  }
  return box;
}

function planeIcon(color) {
  const size = 48; // drawn at 2x, displayed at 24 px
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.scale(2, 2);
  const body = new Path2D(
    "M12 2C12.8 2 13.3 2.8 13.3 3.8V9L21 13.5V15.5L13.3 13.2V18.2L15.6 20V21.6L12 20.6L8.4 21.6V20" +
      "L10.7 18.2V13.2L3 15.5V13.5L10.7 9V3.8C10.7 2.8 11.2 2 12 2Z",
  );
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = "#ffffff";
  ctx.lineJoin = "round";
  ctx.stroke(body);
  ctx.fillStyle = color;
  ctx.fill(body);
  return ctx.getImageData(0, 0, size, size);
}

function applyVisibility(entry) {
  for (const id of entry.mapLayers) map.setLayoutProperty(id, "visibility", entry.visible ? "visible" : "none");
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
  const style = LAYER_STYLE[key] || DEFAULT_STYLE;
  const feats = [...entry.features.values()];
  map.getSource(key).setData({
    type: "FeatureCollection",
    features: style.decorate ? feats.map(style.decorate) : feats,
  });
  if (style.areas) {
    const areas = feats
      .filter((f) => f.properties.area)
      .map((f) => ({
        type: "Feature",
        geometry: f.properties.area,
        properties: { id: f.properties.id, alert_level: f.properties.alert_level },
      }));
    map.getSource(`${key}-areas`).setData({ type: "FeatureCollection", features: areas });
  }
  renderMeta(entry);
  followOpenPopup(key);
}

// Keep an open popup attached to a moving feature, with fresh details.
function followOpenPopup(key) {
  const open = state.openPopup;
  if (!open || open.layer !== key) return;
  const style = LAYER_STYLE[key] || DEFAULT_STYLE;
  const f = state.layers.get(key).features.get(open.id);
  if (!f || !style.live) return; // gone (keeps last details), or a static layer like cameras
  open.popup.setLngLat(f.geometry.coordinates).setDOMContent(style.popup(f.properties, state.layers.get(key).info));
}

// ---------- live stream ----------

// Pause the live stream when the tab has been hidden for a while: a background tab
// would otherwise keep downloading ship/plane updates nobody sees (data costs for
// visitors on mobile, and for the server's outbound traffic). Coming back reconnects,
// and onopen re-fetches the snapshots, so the map is instantly current again.
const PAUSE_HIDDEN_AFTER_MS = 60_000;
let stream = null;
let pauseTimer = null;

function connectStream() {
  const conn = document.getElementById("conn");
  const es = new EventSource("/api/stream");
  stream = es;
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

document.addEventListener("visibilitychange", () => {
  clearTimeout(pauseTimer);
  if (document.hidden) {
    pauseTimer = setTimeout(() => {
      stream?.close();
      stream = null;
      const conn = document.getElementById("conn");
      conn.textContent = "paused";
      conn.className = "conn";
    }, PAUSE_HIDDEN_AFTER_MS);
  } else if (!stream && state.layers.size) {
    connectStream();
  }
});

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
  const how =
    status.mode === "stream"
      ? `live stream, map updated every ${status.poll_seconds}s`
      : `polls every ${status.poll_seconds}s`;
  row.querySelector(".meta").title = status.last_success
    ? `Last update ${fmtTime(status.last_success)} (${how})`
    : how;
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

function firePopup(p, info) {
  const root = el("div", null, "popup");
  root.append(el("h3", p.title || "Incident"));
  const badge = el("span", p.alert_level, "alert");
  badge.style.background = FIRE_COLORS[p.alert_level] || FIRE_COLORS["Not Applicable"];
  const sub = el("p", null, "sub");
  sub.append(badge);
  if (p.status) sub.append(document.createTextNode(` ${p.status}`));
  root.append(sub);

  const rows = [];
  if (p.incident_type) rows.push(["Type", p.incident_type]);
  if (p.size_ha != null) rows.push(["Size", `${fmtNum(p.size_ha)} ha`]);
  if (p.location) rows.push(["Location", p.location]);
  if (p.council) rows.push(["Council", p.council]);
  if (p.agency) rows.push(["Agency", p.agency]);
  if (p.timestamp) rows.push(["Updated", `${agoText(p.timestamp)} (${fmtTime(p.timestamp)})`]);
  root.append(kvList(rows));
  if (p.link) root.append(extLink(p.link, "Details on the NSW RFS website"));
  root.append(el("p", "Not an official warning service. For current warnings use NSW RFS Fires Near Me.", "sub warn"));
  root.append(credit(info));
  return root;
}

function quakePopup(p, info) {
  const root = el("div", null, "popup");
  root.append(el("h3", `M${p.magnitude} earthquake`));
  if (p.place) root.append(el("p", p.place, "sub"));
  const rows = [["Magnitude", `${p.magnitude}${p.mag_type ? ` (${p.mag_type})` : ""}`]];
  if (p.depth_km != null) rows.push(["Depth", `${p.depth_km} km`]);
  if (p.timestamp) rows.push(["Time", `${fmtDateTime(p.timestamp)} (${agoText(p.timestamp)})`]);
  if (p.felt_reports) rows.push(["Felt reports", fmtNum(p.felt_reports)]);
  if (p.review_status) rows.push(["Status", p.review_status === "reviewed" ? "Reviewed by a seismologist" : "Automatic"]);
  if (p.tsunami_flag) rows.push(["Tsunami", "Flagged by USGS: check official warnings"]);
  root.append(kvList(rows));
  if (p.link) root.append(extLink(p.link, "USGS event page"));
  root.append(credit(info));
  return root;
}

function extLink(href, text) {
  const a = el("a", `${text} ↗`, "ext");
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function shipPopup(p, info) {
  const root = el("div", null, "popup");
  root.append(el("h3", p.name || p.title || "Vessel"));
  const sub = [p.category !== "Unknown" && p.category, p.nav_status].filter(Boolean).join(" · ");
  if (sub) root.append(el("p", sub, "sub"));

  const rows = [];
  if (p.speed_kn != null) rows.push(["Speed", `${p.speed_kn} kn (${(p.speed_kn * 1.852).toFixed(1)} km/h)`]);
  if (p.course != null && p.moving) rows.push(["Course", `${p.course}°`]);
  if (p.destination) rows.push(["Destination", p.destination]);
  if (p.length_m) rows.push(["Length", `${p.length_m} m`]);
  if (p.draught_m) rows.push(["Draught", `${p.draught_m} m`]);
  rows.push(["MMSI", p.mmsi]);
  if (p.imo) rows.push(["IMO", String(p.imo)]);
  if (p.callsign) rows.push(["Call sign", p.callsign]);
  if (p.ais_class) rows.push(["AIS class", p.ais_class === "A" ? "A (commercial)" : "B (small craft)"]);
  if (p.timestamp) rows.push(["Position", `${ageText(p.timestamp)} (${fmtTime(p.timestamp)})`]);
  root.append(kvList(rows));
  if (p.sample) root.append(el("p", "Sample vessel. Add an aisstream.io key to .env for live ships.", "sub"));
  root.append(credit(info));
  return root;
}

function flightPopup(p, info) {
  const root = el("div", null, "popup");
  root.append(el("h3", p.callsign || p.title || "Aircraft"));
  const sub = [p.category, p.origin_country && `registered in ${p.origin_country}`].filter(Boolean).join(" · ");
  if (sub) root.append(el("p", sub, "sub"));

  const rows = [];
  if (p.on_ground) rows.push(["Status", "On the ground"]);
  else if (p.altitude_ft != null)
    rows.push(["Altitude", `${fmtNum(p.altitude_ft)} ft (${fmtNum(Math.round(p.altitude_ft / 3.28084))} m)`]);
  if (p.speed_kt != null) rows.push(["Speed", `${fmtNum(p.speed_kt)} kt (${fmtNum(Math.round(p.speed_kt * 1.852))} km/h)`]);
  if (p.heading != null) rows.push(["Track", `${p.heading}°`]);
  if (p.vertical_rate_fpm != null && Math.abs(p.vertical_rate_fpm) >= 100)
    rows.push(["Vertical", `${p.vertical_rate_fpm > 0 ? "climbing" : "descending"} ${fmtNum(Math.abs(p.vertical_rate_fpm))} ft/min`]);
  if (p.squawk) rows.push(["Squawk", p.squawk]);
  if (p.icao24) rows.push(["ICAO 24-bit", p.icao24]);
  if (p.timestamp) rows.push(["Position", `${ageText(p.timestamp)} (${fmtTime(p.timestamp)})`]);
  root.append(kvList(rows));
  root.append(credit(info));
  return root;
}

function kvList(rows) {
  const dl = el("dl", null, "kv");
  for (const [k, v] of rows) dl.append(el("dt", k), el("dd", v));
  return dl;
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

function ageText(iso) {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 90) return `${s} s old`;
  if (s < 5400) return `${Math.round(s / 60)} min old`;
  if (s < 172800) return `${Math.round(s / 3600)} h old`;
  return `${Math.round(s / 86400)} days old`;
}

function agoText(iso) {
  return ageText(iso).replace(" old", " ago");
}

function fmtDateTime(d) {
  return new Date(d).toLocaleString("en-AU", {
    timeZone: "Australia/Sydney", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZoneName: "short",
  });
}

function fmtNum(n) {
  return Number(n).toLocaleString("en-AU");
}

function fmtTime(d) {
  return new Date(d).toLocaleTimeString("en-AU", { timeZone: "Australia/Sydney", timeZoneName: "short" });
}
