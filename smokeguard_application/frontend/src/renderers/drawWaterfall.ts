/**
 * 3-D perspective waterfall renderer — "travelling wave" CSI amplitude.
 *
 * Draws the last ~200 amplitude curves stacked with perspective:
 *   – newest frame at the bottom / front (brightest, full-size)
 *   – oldest frames at the top / back (compressed, faded)
 *   – each curve is a filled polygon with a blue gradient
 *
 * X-axis = signed subcarrier index (DC = 0 at exact centre, symmetric).
 * Y-axis = amplitude.
 * Depth  = time (new → old, bottom → top).
 *
 * Non-data subcarriers (guard bands + DC null) are clamped to baseline so
 * they appear as flat gaps in the waterfall curves.  Subtle shaded
 * backgrounds mark those regions.
 */

import type { SubcarrierMetadata } from '../types';
import type { StoredFrame } from '../store/csiStore';

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------

const PAD = { top: 14, right: 16, bottom: 34, left: 52 };

/** Fraction of plot height used by the 3-D stack. */
const STACK_FRAC = 0.80;

/** Amplitude compression for the oldest frame (0 = none). */
const PERSP_COMPRESS = 0.22;

/** Fill opacity — newest frame. */
const ALPHA_PEAK = 0.88;

/** Fill opacity — oldest frame. */
const ALPHA_FLOOR = 0.14;

/** Vertical mesh-line step (1 = every subcarrier). */
const MESH_STEP = 3;

// ---------------------------------------------------------------------------
// Colour palette
// ---------------------------------------------------------------------------

const C = {
  bg:            '#0d0d0d',
  grid:          '#1e1e1c',
  inkMuted:      '#6b6b66',
  dcLine:        'rgba(255, 255, 255, 0.22)',   // centre DC marker
  nullRegion:    'rgba(255, 255, 255, 0.025)',   // guard / DC-null shading
  nullBoundary:  'rgba(255, 255, 255, 0.06)',    // data ↔ null edge
  dcShade:       'rgba(255, 80, 80, 0.06)',      // DC null column tint
  wave:          [57, 135, 229] as [number, number, number],   // #3987e5
  waveGlow:      [120, 190, 255] as [number, number, number],
  waveDim:       [30, 70, 130] as [number, number, number],
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function scNum(idx: number, ofs: number): number {
  return idx - ofs;
}

function dcOffset(n: number): number {
  return Math.floor(n / 2);
}

// ---------------------------------------------------------------------------
// Data mask — which subcarrier indices carry real CSI data
// ---------------------------------------------------------------------------

/**
 * Build a boolean array: `true` for data subcarriers, `false` for guard
 * bands and the DC null.  Falls back to all-true when metadata is absent.
 */
function buildDataMask(
  numSub: number,
  meta: SubcarrierMetadata | null | undefined,
): boolean[] {
  const mask = new Array<boolean>(numSub).fill(true);
  if (!meta) return mask;

  // Lower guard band (indices below data_start_idx)
  for (let j = 0; j < meta.data_start_idx && j < numSub; j++) mask[j] = false;

  // DC null
  if (meta.dc_null_idx >= 0 && meta.dc_null_idx < numSub) {
    mask[meta.dc_null_idx] = false;
  }

  // Upper guard band (indices above data_upper_end)
  if (meta.data_upper_end >= 0) {
    for (let j = meta.data_upper_end + 1; j < numSub; j++) mask[j] = false;
  }

  return mask;
}

// ---------------------------------------------------------------------------
// Public draw API
// ---------------------------------------------------------------------------

export interface WaterfallOptions {
  /** Ring buffer of recent frames — index 0 = oldest, last = newest. */
  buffer: StoredFrame[];
  /** Auto-scale Y max (from the store). */
  yMax: number;
  /** Whether the chart is paused. */
  paused: boolean;
}

/**
 * Render the 3-D perspective waterfall onto `ctx`.
 * `w` and `h` are CSS-pixel dimensions (canvas already DPR-scaled).
 */
export function drawWaterfall(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  opts: WaterfallOptions,
): void {
  const { buffer, yMax } = opts;
  const nBuf = buffer.length;

  // ---- clear ----
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, w, h);

  const pL = PAD.left;
  const pR = w - PAD.right;
  const pT = PAD.top;
  const pB = h - PAD.bottom;
  const pW = pR - pL;
  const pH = pB - pT;
  if (pW <= 0 || pH <= 0) return;

  // ---- subcarrier layout (symmetric so DC = 0 is exactly centred) ----
  const numSub = nBuf > 0 ? buffer[0].amp.length : 64;
  const dcOfs = dcOffset(numSub);
  const scAbsMax = Math.max(dcOfs, numSub - 1 - dcOfs);           // [-32,32] for 64
  const xPx = (sc: number) => pL + ((sc + scAbsMax) / (2 * scAbsMax)) * pW;

  // Precompute X positions
  const xs = new Float32Array(numSub);
  for (let j = 0; j < numSub; j++) xs[j] = xPx(scNum(j, dcOfs));

  // ---- data mask ----
  const meta = nBuf > 0 ? buffer[0].metadata : null;
  const isData = buildDataMask(numSub, meta);

  // ---- data-scale Y max ----
  const dataMax = bufferMax(buffer, yMax, isData);
  const stackH = pH * STACK_FRAC;
  const stackTop = pT + (pH - stackH);
  const stackBot = pB;

  // ---- non-data region shading (behind stack) ----
  drawNullRegions(ctx, xs, numSub, isData, meta, stackTop, stackBot);

  // ---- prominent DC centre line ----
  const dcX = xPx(0);
  ctx.strokeStyle = C.dcLine;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(dcX, pT);
  ctx.lineTo(dcX, pB);
  ctx.stroke();

  // ---- data ↔ null boundary markers ----
  drawBoundaries(ctx, xs, numSub, isData, stackTop, stackBot);

  // ---- 3-D waterfall ----
  if (nBuf > 0) {
    renderStack(ctx, buffer, numSub, xs, isData, dataMax, stackTop, stackBot);
  } else {
    ctx.fillStyle = C.inkMuted;
    ctx.font = '13px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Waiting for CSI data…', (pL + pR) / 2, (pT + pB) / 2);
  }

  // ---- X-axis labels (signed subcarrier indices, 0 always shown) ----
  ctx.fillStyle = C.inkMuted;
  ctx.font = '9px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';

  // Pick tick spacing that lands on 0
  const nTicks = Math.min(9, numSub);
  const rawStep = (2 * scAbsMax) / nTicks;
  // Snap to a nice round number
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  let niceStep = mag;
  if (rawStep / mag >= 7) niceStep = mag * 10;
  else if (rawStep / mag >= 3.5) niceStep = mag * 5;
  else if (rawStep / mag >= 1.7) niceStep = mag * 2;

  const tickMin = Math.ceil(-scAbsMax / niceStep) * niceStep;
  const tickMax = Math.floor(scAbsMax / niceStep) * niceStep;
  for (let sc = tickMin; sc <= tickMax; sc += niceStep) {
    // Skip labels that fall inside non-data guard regions when too close to edge
    const px = xPx(sc);
    if (px < pL - 4 || px > pR + 4) continue;
    ctx.fillText(String(sc), px, pB + 7);
  }

  // Highlight the 0 tick
  ctx.fillStyle = 'rgba(255,255,255,0.5)';
  ctx.fillText('0', xPx(0), pB + 7);
  ctx.fillStyle = C.inkMuted;

  // ---- Y-axis ticks ----
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  const nY = 5;
  for (let i = 0; i <= nY; i++) {
    const val = (dataMax / nY) * i;
    const y = stackBot - (val / dataMax) * stackH;
    ctx.fillText(val >= 1000 ? `${(val / 1000).toFixed(1)}k` : val.toFixed(0), pL - 8, y);
  }

  // ---- frame counter (diagnostic) ----
  ctx.fillStyle = C.inkMuted;
  ctx.font = '9px monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'top';
  ctx.fillText(`${nBuf} frames`, pR, pT + 2);
}

// ---------------------------------------------------------------------------
// Non-data region shading
// ---------------------------------------------------------------------------

function drawNullRegions(
  ctx: CanvasRenderingContext2D,
  xs: Float32Array,
  numSub: number,
  isData: boolean[],
  meta: SubcarrierMetadata | null | undefined,
  top: number,
  bot: number,
): void {
  const h = bot - top;

  // Collect contiguous non-data runs
  const runs: Array<[number, number]> = [];  // [startIdx, endIdx] inclusive
  let inNull = false;
  let start = 0;
  for (let j = 0; j < numSub; j++) {
    if (!isData[j] && !inNull) { start = j; inNull = true; }
    if (isData[j] && inNull) { runs.push([start, j - 1]); inNull = false; }
  }
  if (inNull) runs.push([start, numSub - 1]);

  for (const [a, b] of runs) {
    const x0 = xs[a];
    const x1 = xs[b];
    const w = x1 - x0 + (xs[1] - xs[0]);  // span to cover the last subcarrier too

    // Is this the DC null column?
    const isDc = meta != null && a === meta.dc_null_idx && b === meta.dc_null_idx;

    ctx.fillStyle = isDc ? C.dcShade : C.nullRegion;
    ctx.fillRect(x0 - 0.5, top, w + (isDc ? 1 : 0), h);
  }
}

// ---------------------------------------------------------------------------
// Data ↔ null boundary markers
// ---------------------------------------------------------------------------

function drawBoundaries(
  ctx: CanvasRenderingContext2D,
  xs: Float32Array,
  numSub: number,
  isData: boolean[],
  top: number,
  bot: number,
): void {
  ctx.strokeStyle = C.nullBoundary;
  ctx.lineWidth = 0.8;

  for (let j = 1; j < numSub; j++) {
    if (isData[j] !== isData[j - 1]) {
      const x = (xs[j] + xs[j - 1]) / 2;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bot);
      ctx.stroke();
    }
  }
}

// ---------------------------------------------------------------------------
// Stack rendering
// ---------------------------------------------------------------------------

function renderStack(
  ctx: CanvasRenderingContext2D,
  buffer: StoredFrame[],
  numSub: number,
  xs: Float32Array,
  isData: boolean[],
  yMax: number,
  stackTop: number,
  stackBot: number,
): void {
  const n = buffer.length;
  const stackH = stackBot - stackTop;

  // ---- Precompute Y-row for every frame ----
  const rows: Float32Array[] = [];
  for (let fi = 0; fi < n; fi++) {
    const age = n - 1 - fi;               // 0 = newest
    const t = age / Math.max(1, n - 1);   // 0..1
    const yBase = stackBot - t * stackH;  // newest at bottom, oldest at top
    const persp = 1.0 - t * PERSP_COMPRESS;

    const amp = buffer[fi].amp;
    const row = new Float32Array(numSub);
    for (let j = 0; j < numSub; j++) {
      if (isData[j]) {
        row[j] = yBase - ((amp[j] ?? 0) / yMax) * stackH * persp;
      } else {
        row[j] = yBase;  // non-data → clamp to baseline
      }
    }
    rows.push(row);
  }

  // ---- Pass 1: filled polygons (back → front) ----
  for (let fi = 0; fi < n; fi++) {
    const age = n - 1 - fi;
    const t = age / Math.max(1, n - 1);
    const yBase = stackBot - t * stackH;
    const alpha = ALPHA_FLOOR + (1 - t) * (ALPHA_PEAK - ALPHA_FLOOR);
    const row = rows[fi];

    ctx.beginPath();
    ctx.moveTo(xs[0], row[0]);
    for (let j = 1; j < numSub; j++) ctx.lineTo(xs[j], row[j]);
    ctx.lineTo(xs[numSub - 1], yBase);
    ctx.lineTo(xs[0], yBase);
    ctx.closePath();

    // Gradient: bright blue at curve crest → dark at baseline
    const grad = ctx.createLinearGradient(0, yBase - stackH, 0, yBase);
    grad.addColorStop(0, rgbaStr(C.wave, alpha * 0.75));
    grad.addColorStop(0.4, rgbaStr(C.wave, alpha * 0.20));
    grad.addColorStop(1, 'rgba(57,135,229,0.0)');
    ctx.fillStyle = grad;
    ctx.fill();
  }

  // ---- Pass 2: curve outlines (back → front) ----
  for (let fi = 0; fi < n; fi++) {
    const age = n - 1 - fi;
    const t = age / Math.max(1, n - 1);
    const alpha = ALPHA_FLOOR + (1 - t) * (ALPHA_PEAK - ALPHA_FLOOR);
    const row = rows[fi];

    // Draw data segments independently so non-data gaps aren't connected
    drawDataSegments(ctx, row, xs, numSub, isData,
      rgbaStr(C.wave, alpha * 0.50), 0.45);
  }

  // ---- Pass 3: thin vertical mesh lines ----
  ctx.lineWidth = 0.3;
  for (let j = 0; j < numSub; j += MESH_STEP) {
    if (!isData[j]) continue;  // skip mesh in non-data columns
    ctx.beginPath();
    for (let fi = 0; fi < n; fi++) {
      const y = rows[fi][j];
      if (fi === 0) ctx.moveTo(xs[j], y);
      else ctx.lineTo(xs[j], y);
    }
    ctx.strokeStyle = rgbaStr(C.waveDim, 0.08);
    ctx.stroke();
  }

  // ---- Pass 4: newest-frame glow highlight (data segments only) ----
  const lastRow = rows[n - 1];
  ctx.strokeStyle = rgbaStr(C.waveGlow, 0.9);
  ctx.lineWidth = 1.6;
  ctx.lineJoin = 'round';
  ctx.shadowColor = 'rgba(57, 135, 229, 0.55)';
  ctx.shadowBlur = 6;
  drawDataSegments(ctx, lastRow, xs, numSub, isData,
    rgbaStr(C.waveGlow, 0.9), 1.6, true);
  ctx.shadowBlur = 0;
}

// ---------------------------------------------------------------------------
// Draw connected segments over data subcarriers only
// ---------------------------------------------------------------------------

function drawDataSegments(
  ctx: CanvasRenderingContext2D,
  row: Float32Array,
  xs: Float32Array,
  numSub: number,
  isData: boolean[],
  strokeStyle: string,
  lineWidth: number,
  useShadow = false,
): void {
  ctx.strokeStyle = strokeStyle;
  ctx.lineWidth = lineWidth;

  let segStart = -1;
  for (let j = 0; j <= numSub; j++) {
    if (j < numSub && isData[j]) {
      if (segStart < 0) segStart = j;
    } else {
      // End of a data run — draw it
      if (segStart >= 0 && j - segStart >= 2) {
        ctx.beginPath();
        ctx.moveTo(xs[segStart], row[segStart]);
        for (let k = segStart + 1; k < j; k++) {
          ctx.lineTo(xs[k], row[k]);
        }
        ctx.stroke();
      }
      segStart = -1;
    }
  }
  // Suppress unused warning
  void (useShadow);
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function rgbaStr([r, g, b]: [number, number, number], alpha: number): string {
  return `rgba(${r},${g},${b},${alpha.toFixed(4)})`;
}

/** Compute the maximum amplitude from data subcarriers in the buffer. */
function bufferMax(
  buffer: StoredFrame[],
  fallback: number,
  isData: boolean[],
): number {
  if (buffer.length === 0) return Math.max(fallback, 1);
  let mx = 0;
  // Sample sparsely for speed
  const fStep = Math.max(1, Math.floor(buffer.length / 30));
  const sStep = Math.max(1, Math.floor((buffer[0]?.amp.length ?? 64) / 16));
  for (let fi = 0; fi < buffer.length; fi += fStep) {
    const a = buffer[fi].amp;
    for (let j = 0; j < a.length; j += sStep) {
      if (isData[j] && a[j] > mx) mx = a[j];
    }
  }
  return mx > 0 ? mx * 1.12 : Math.max(fallback, 1); // 12% headroom
}
