// Shareable view state in the URL hash, e.g.
//   #map=9.2/-33.87/151.21&layers=ships,flights&analytics=1
// MapLibre owns `map=` (zoom/lat/lon, kept up to date as you pan); we own the rest.
// Values are written raw (no %-encoding) so MapLibre can still read its "/"-separated value.

export function getParam(key) {
  return parse().get(key);
}

export function setParam(key, value) {
  const params = parse();
  if (value == null || value === "") params.delete(key);
  else params.set(key, String(value));
  const hash = [...params].map(([k, v]) => `${k}=${v}`).join("&");
  history.replaceState(null, "", hash ? `#${hash}` : location.pathname + location.search);
}

function parse() {
  const out = new Map();
  for (const part of location.hash.replace(/^#/, "").split("&")) {
    const i = part.indexOf("=");
    if (i > 0) out.set(part.slice(0, i), decodeURIComponent(part.slice(i + 1)));
  }
  return out;
}
