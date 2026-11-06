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

/** A small pointer sitting on the badge's rim, pointing up (north). Rotate it with icon-rotate. */
export function headingPointer(color) {
  const ctx = canvas();
  ctx.beginPath();
  ctx.moveTo(C, 0.8);
  ctx.lineTo(C - 7, C - R + 6);
  ctx.lineTo(C + 7, C - R + 6);
  ctx.closePath();
  ctx.lineJoin = "round";
  ctx.lineWidth = 2.5;
  ctx.strokeStyle = "#ffffff";
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.fill();
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
