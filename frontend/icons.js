// Map icons drawn in code: a round coloured badge with a white pictogram inside.
// Drawn on a canvas at 2x and handed to MapLibre as images, so there are no icon
// files to load and no third-party icon licences to track.
//
// Pictograms are drawn in a 24 x 24 grid, then scaled into the badge.

const SIZE = 56; // canvas px (2x): displayed at 28 css px when icon-size = 1
const C = SIZE / 2;
const R = 18; // badge radius; leaves room for the heading pointer and shadow

// ---------- pictograms (fg = white, bg = badge colour for cut-outs) ----------

export const GLYPHS = {
  bus(ctx, fg, bg) {
    box(ctx, 5, 2.5, 14, 16.5, 2.5, fg); // body
    box(ctx, 6.8, 5.6, 10.4, 6, 1, bg); // windscreen
    box(ctx, 8, 3.5, 8, 1.1, 0.4, bg); // destination sign
    dot(ctx, 8.4, 15.2, 1.2, bg);
    dot(ctx, 15.6, 15.2, 1.2, bg);
    box(ctx, 6.5, 18.6, 3, 3, 0.8, fg); // wheels
    box(ctx, 14.5, 18.6, 3, 3, 0.8, fg);
  },
  train(ctx, fg, bg) {
    fill(ctx, "M6 17V7.5Q6 2.5 12 2.5T18 7.5V17Q18 18.5 16.5 18.5H7.5Q6 18.5 6 17Z", fg);
    box(ctx, 8, 5.4, 8, 5, 1.2, bg);
    dot(ctx, 9, 14.6, 1.2, bg);
    dot(ctx, 15, 14.6, 1.2, bg);
    legs(ctx, fg);
  },
  metro(ctx, fg, bg) {
    box(ctx, 5.5, 2.8, 13, 15.7, 4, fg);
    box(ctx, 7.3, 5, 9.4, 6.6, 2.2, bg); // big single windscreen
    box(ctx, 7.8, 14.2, 2.6, 1.4, 0.6, bg);
    box(ctx, 13.6, 14.2, 2.6, 1.4, 0.6, bg);
    legs(ctx, fg);
  },
  lightrail(ctx, fg, bg) {
    line(ctx, "M8.5 1.4H15.5M10 1.4L12 4.6L14 1.4", fg, 1.3); // pantograph
    box(ctx, 6, 4.6, 12, 14, 2.6, fg);
    box(ctx, 7.8, 6.8, 8.4, 5.4, 1, bg);
    dot(ctx, 9, 15.3, 1.1, bg);
    dot(ctx, 15, 15.3, 1.1, bg);
    legs(ctx, fg);
  },
  ferry(ctx, fg, bg) {
    box(ctx, 10.6, 2.8, 2.8, 3.6, 0.6, fg); // funnel
    box(ctx, 7.6, 5.8, 8.8, 5.4, 1, fg); // cabin
    for (const x of [8.9, 11.2, 13.5]) box(ctx, x, 7.2, 1.6, 1.6, 0.4, bg);
    fill(ctx, "M3.5 11.4H20.5L18 17.2H6Z", fg); // hull
    line(ctx, "M3 20.3q2.25-1.6 4.5 0t4.5 0t4.5 0t4.5 0", fg, 1.5); // waves
  },
  camera(ctx, fg, bg) {
    box(ctx, 8, 4.6, 8, 3.6, 1, fg);
    box(ctx, 3, 7, 18, 12.4, 2.6, fg);
    dot(ctx, 12, 13.2, 4.1, bg);
    dot(ctx, 12, 13.2, 2.3, fg);
    dot(ctx, 17.6, 9.9, 1, bg);
  },
  plane(ctx, fg) {
    fill(ctx, "M12 2C12.8 2 13.3 2.8 13.3 3.8V9L21 13.5V15.5L13.3 13.2V18.2L15.6 20V21.6L12 20.6L8.4 21.6V20L10.7 18.2V13.2L3 15.5V13.5L10.7 9V3.8C10.7 2.8 11.2 2 12 2Z", fg);
  },
  ship(ctx, fg) {
    box(ctx, 16.4, 5.6, 3.2, 7.6, 0.6, fg); // bridge
    for (const x of [4.6, 8.4, 12.2]) box(ctx, x, 9.2, 3.4, 4, 0.5, fg); // containers
    fill(ctx, "M2.5 13.8H21.5L18.6 18.8H5.4Z", fg); // hull
    line(ctx, "M3 21q2.25-1.6 4.5 0t4.5 0t4.5 0t4.5 0", fg, 1.4);
  },
  quake(ctx, fg) {
    line(ctx, "M2 12.5H6L8.2 6.5L11.2 18.5L14.2 4L16.8 15L18.4 12.5H22", fg, 2);
  },
  flame(ctx, fg, bg) {
    fill(ctx, "M12 2C13 6 18 8.2 18 14.2C18 18.6 15.3 21.6 12 21.6C8.7 21.6 6 18.6 6 14.6C6 11 8.6 9.6 9.1 6.6C10.5 8 10.8 9.6 10.5 11.2C12 9.6 12.9 6.2 12 2Z", fg);
    fill(ctx, "M12 12.6C13.6 14 15 15.4 15 17.4C15 19.3 13.6 20.4 12 20.4C10.4 20.4 9 19.3 9 17.6C9 15.9 10.5 15 11 13.6C11.4 14.6 11.6 15.3 11.4 16.1C12.2 15.3 12.6 13.9 12 12.6Z", bg);
  },
};

// ---------- builders ----------

/** Round badge with a white pictogram. Returns ImageData for map.addImage(..., {pixelRatio: 2}). */
export function badge(color, glyph) {
  const ctx = canvas();
  ctx.save();
  ctx.shadowColor = "rgba(0, 0, 0, 0.35)";
  ctx.shadowBlur = 4;
  ctx.shadowOffsetY = 1.5;
  circle(ctx, C, C, R, "#ffffff"); // white ring + shadow
  ctx.restore();
  circle(ctx, C, C, R - 2.6, color);
  const inner = (R - 2.6) * 2 * 0.66; // pictogram fills ~2/3 of the badge
  ctx.save();
  ctx.translate(C - inner / 2, C - inner / 2);
  ctx.scale(inner / 24, inner / 24);
  GLYPHS[glyph](ctx, "#ffffff", color);
  ctx.restore();
  return ctx.getImageData(0, 0, SIZE, SIZE);
}

/** The same badge as a data: URL, for legends and swatches in the side panel. */
export function badgeURL(color, glyph) {
  const ctx = canvas();
  ctx.putImageData(badge(color, glyph), 0, 0);
  return ctx.canvas.toDataURL("image/png");
}

// ---------- drawing helpers (24-unit grid) ----------

function canvas() {
  const c = document.createElement("canvas");
  c.width = c.height = SIZE;
  return c.getContext("2d", { willReadFrequently: true });
}

function box(ctx, x, y, w, h, r, color) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
  ctx.fillStyle = color;
  ctx.fill();
}

function dot(ctx, x, y, r, color) {
  circle(ctx, x, y, r, color);
}

function circle(ctx, x, y, r, color) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

function fill(ctx, d, color) {
  ctx.fillStyle = color;
  ctx.fill(new Path2D(d));
}

function line(ctx, d, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.stroke(new Path2D(d));
}

function legs(ctx, color) {
  line(ctx, "M8.6 18.6L6.8 21.8M15.4 18.6L17.2 21.8", color, 1.8);
}

// ---------- top-down vehicle silhouettes (public transport) ----------
// Drawn pointing north so MapLibre can rotate them to the vehicle's bearing, like the
// plane and ship icons. Mode colour with a white outline; white details for windows/roof.

const TD = 64; // canvas px (2x): 32 css px tall at icon-size 1

const TOP_DOWN = {
  bus: {
    body: [rr(7.6, 1.6, 8.8, 20.8, 2.2)],
    details: [[rr(8.6, 2.5, 6.8, 2.4, 0.9), 0.9], [rr(9.6, 10, 4.8, 4.2, 0.8), 0.35], [rr(9.2, 20, 5.6, 1.3, 0.5), 0.55]],
  },
  train: {
    body: [
      new Path2D("M8.6 8.2V4.6C8.6 2.4 10 1.2 12 1.2S15.4 2.4 15.4 4.6V8.2Z"),
      rr(8.6, 9.1, 6.8, 6.4, 0.8),
      rr(8.6, 16.4, 6.8, 6.4, 0.8),
    ],
    details: [
      [new Path2D("M9.8 4.3Q12 2.7 14.2 4.3V5.1H9.8Z"), 0.9],
      [rr(11.2, 10.1, 1.6, 4.4, 0.6), 0.35],
      [rr(11.2, 17.4, 1.6, 4.4, 0.6), 0.35],
    ],
  },
  metro: {
    body: [
      new Path2D("M8.8 8.2V5.6C8.8 3 10.2 0.9 12 0.9S15.2 3 15.2 5.6V8.2Z"),
      rr(8.8, 9.1, 6.4, 6.4, 1.4),
      rr(8.8, 16.4, 6.4, 6.4, 1.4),
    ],
    details: [
      [new Path2D("M9.8 5.2Q12 1.8 14.2 5.2V6H9.8Z"), 0.9],
      [rr(9.9, 11.6, 4.2, 1.4, 0.6), 0.4],
      [rr(9.9, 18.9, 4.2, 1.4, 0.6), 0.4],
    ],
  },
  lightrail: {
    body: [
      new Path2D("M9.2 8.4V4C9.2 2.4 10.4 1.4 12 1.4S14.8 2.4 14.8 4V8.4Z"),
      rr(9.9, 8.4, 4.2, 1.1, 0.3),
      rr(9.2, 9.5, 5.6, 5, 0.6),
      rr(9.9, 14.5, 4.2, 1.1, 0.3),
      new Path2D("M9.2 15.6V20C9.2 21.6 10.4 22.6 12 22.6S14.8 21.6 14.8 20V15.6Z"),
    ],
    details: [
      [new Path2D("M10.2 3.9Q12 2.5 13.8 3.9V4.6H10.2Z"), 0.9],
      [new Path2D("M10.2 20.1Q12 21.5 13.8 20.1V19.4H10.2Z"), 0.5],
      [new Path2D("M10.8 12L12 10.6L13.2 12L12 13.4Z"), 0.6],
    ],
  },
  ferry: {
    body: [new Path2D("M12 1.2C15 4 16.6 8 16.6 12.5V19.5C16.6 21.6 14.8 22.8 12 22.8S7.4 21.6 7.4 19.5V12.5C7.4 8 9 4 12 1.2Z")],
    details: [[rr(9.2, 8.2, 5.6, 11, 1.6), 0.4], [rr(9.8, 6.3, 4.4, 2.2, 0.8), 0.9]],
  },
};

/** Top-down silhouette for a transit mode, pointing north. ImageData for map.addImage(..., {pixelRatio: 2}). */
export function topDown(mode, color) {
  const shape = TOP_DOWN[mode] || TOP_DOWN.bus;
  const c = document.createElement("canvas");
  c.width = c.height = TD;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.scale(TD / 24, TD / 24);
  // white outline (with a soft shadow) under the body, so it stands out on any background
  ctx.save();
  ctx.shadowColor = "rgba(0, 0, 0, 0.35)";
  ctx.shadowBlur = 1.5;
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 1.6;
  ctx.lineJoin = "round";
  for (const part of shape.body) ctx.stroke(part);
  ctx.restore();
  ctx.fillStyle = color;
  for (const part of shape.body) ctx.fill(part);
  for (const [part, alpha] of shape.details) {
    ctx.globalAlpha = alpha;
    ctx.fillStyle = "#ffffff";
    ctx.fill(part);
  }
  ctx.globalAlpha = 1;
  return ctx.getImageData(0, 0, TD, TD);
}

export function topDownURL(mode, color) {
  const c = document.createElement("canvas");
  c.width = c.height = TD;
  c.getContext("2d").putImageData(topDown(mode, color), 0, 0);
  return c.toDataURL("image/png");
}

function rr(x, y, w, h, r) {
  const p = new Path2D();
  p.roundRect(x, y, w, h, r);
  return p;
}
