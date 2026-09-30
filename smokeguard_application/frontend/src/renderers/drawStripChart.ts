/**
 * Strip-chart renderer — pulse-monitor style CSI amplitude over time.
 *
 * X-axis = elapsed time (newest packet at the right edge).
 * Y-axis = amplitude (auto-scaled from data subcarriers).
 * Each data subcarrier is its own coloured polyline.
 *
 * Guard bands and the DC null are always zero, so they are skipped
 * entirely — only data subcarriers are drawn.
 */

import type { SubcarrierMetadata } from '../types';
import type { StoredFrame } from '../store/csiStore';

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

const PAD = { top: 14, right: 20, bottom: 34, left: 56 };

/** Fraction of plot height used by the trace area. */
const TRACE_FRAC = 0.82;

/** Minimum time span in seconds (avoid x/0 when buffer has 1 frame). */
const MIN_SPAN_S = 1.0;

// ---------------------------------------------------------------------------
// Theme-aware palette
// ---------------------------------------------------------------------------

export interface ChartColors {
  bg: string;
  grid: string;
  zeroLine: string;
  inkMuted: string;
  nowLine: string;
}

export const PALETTE: Record<Theme, ChartColors> = {
  dark: {
    bg:        '#0d0d0d',
    grid:      'rgba(255,255,255,0.045)',
    zeroLine:  'rgba(255,255,255,0.20)',
    inkMuted:  '#6b6b66',
    nowLine:   'rgba(255,255,255,0.10)',
  },
  light: {
    bg:        '#f8faf8',
    grid:      'rgba(0,0,0,0.06)',
    zeroLine:  'rgba(0,0,0,0.12)',
    inkMuted:  '#8899a6',
    nowLine:   'rgba(0,0,0,0.08)',
  },
};

// ---------------------------------------------------------------------------
// Stable Y-axis scale (hysteresis so labels don't vibrate)
// ---------------------------------------------------------------------------

let _stableYCeil = 20;
let _stableYStep = 4;

/** Snap a rough step size to a nice round number. */
function niceStep(rough: number): number {
  if (rough <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const norm = rough / mag;
  if (norm <= 1.5) return mag;
  if (norm <= 3) return 2 * mag;
  if (norm <= 7) return 5 * mag;
  return 10 * mag;
}

/** Return a stable {yCeil, yStep} pair with hysteresis. */
function getStableYScale(rawMax: number): { yCeil: number; yStep: number } {
  if (rawMax > _stableYCeil || rawMax < _stableYCeil * 0.45) {
    const rough = rawMax / 5;                // target ~5 ticks
    _stableYStep = niceStep(rough);
    _stableYCeil = _stableYStep * Math.ceil(rawMax / _stableYStep);
    if (_stableYCeil < rawMax) _stableYCeil += _stableYStep;
  }
  return { yCeil: _stableYCeil, yStep: _stableYStep };
}

// ---------------------------------------------------------------------------
// Data mask
// ---------------------------------------------------------------------------

function buildDataMask(
  numSub: number,
  meta: SubcarrierMetadata | null | undefined,
): boolean[] {
  const mask = new Array<boolean>(numSub).fill(true);
  if (!meta) return mask;
  for (let j = 0; j < meta.data_start_idx && j < numSub; j++) mask[j] = false;
  if (meta.dc_null_idx >= 0 && meta.dc_null_idx < numSub) {
    mask[meta.dc_null_idx] = false;
  }
  if (meta.data_upper_end >= 0) {
    for (let j = meta.data_upper_end + 1; j < numSub; j++) mask[j] = false;
  }
  return mask;
}

// ---------------------------------------------------------------------------
// Subcarrier line colour
// ---------------------------------------------------------------------------

/** Spectral colour, mapped across the data subcarriers only. */
export function lineColor(j: number, dataStart: number, dataEnd: number): string {
  const t = (j - dataStart) / Math.max(1, dataEnd - dataStart); // 0 … 1
  const hue = 225 - t * 218;                    // 225° (blue) → 7° (red)
  return `hsla(${hue.toFixed(0)}, 68%, 56%, 0.80)`;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export type Theme = 'light' | 'dark';

export interface StripChartOptions {
  buffer: StoredFrame[];
  yMax: number;
  paused: boolean;
  theme: Theme;
}

export function drawStripChart(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  opts: StripChartOptions,
): void {
  const { buffer, theme } = opts;
  const nBuf = buffer.length;
  const C = PALETTE[theme];

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

  // ---- per-frame metadata ----
  const numSub = nBuf > 0 ? buffer[0].amp.length : 64;
  const meta = nBuf > 0 ? buffer[0].metadata : null;
  const isData = buildDataMask(numSub, meta);
  const dataStart = meta?.data_start_idx ?? 0;
  const dataEnd = meta && meta.data_upper_end >= 0 ? meta.data_upper_end : numSub - 1;

  // ---- time axis ----
  const tMin = nBuf > 0 ? buffer[0].t : 0;
  const tMax = nBuf > 0 ? buffer[nBuf - 1].t : tMin + MIN_SPAN_S;
  const tSpan = Math.max(tMax - tMin, MIN_SPAN_S);
  const xPx = (t: number) => pL + ((t - tMin) / tSpan) * pW;

  // ---- Y scale (0 at bottom, stable nice-number ceiling) ----
  const rawMax = computeDataMax(buffer, opts.yMax, isData);
  const { yCeil, yStep } = getStableYScale(rawMax);
  const nY = Math.round(yCeil / yStep);          // number of tick intervals
  const traceH = pH * TRACE_FRAC;
  const traceTop = pT + (pH - traceH);
  const traceBot = pB;
  const yPx = (amp: number) => traceBot - (amp / yCeil) * traceH;

  // ---- horizontal grid ----
  ctx.strokeStyle = C.grid;
  ctx.lineWidth = 0.6;
  for (let i = 1; i < nY; i++) {
    const y = traceBot - (i / nY) * traceH;
    ctx.beginPath();
    ctx.moveTo(pL, y);
    ctx.lineTo(pR, y);
    ctx.stroke();
  }

  // ---- zero-amplitude baseline ----
  ctx.strokeStyle = C.zeroLine;
  ctx.lineWidth = 1.0;
  ctx.beginPath();
  ctx.moveTo(pL, traceBot);
  ctx.lineTo(pR, traceBot);
  ctx.stroke();

  // ---- vertical time grid ----
  const nT = 5;
  for (let i = 0; i <= nT; i++) {
    const t = tMin + (tSpan / nT) * i;
    const x = xPx(t);
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.moveTo(x, pT);
    ctx.lineTo(x, pB);
    ctx.stroke();
  }

  // ---- "now" marker at right edge ----
  if (nBuf > 0) {
    const xNow = xPx(tMax);
    ctx.strokeStyle = C.nowLine;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(xNow, pT);
    ctx.lineTo(xNow, pB);
    ctx.stroke();
  }

  // ---- subcarrier lines ----
  if (nBuf > 0) {
    drawSubcarrierLines(ctx, buffer, numSub, isData, dataStart, dataEnd, xPx, yPx);
  } else {
    ctx.fillStyle = C.inkMuted;
    ctx.font = '13px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Waiting for CSI data…', (pL + pR) / 2, (pT + pB) / 2);
  }

  // ---- X-axis time labels ----
  ctx.fillStyle = C.inkMuted;
  ctx.font = '9px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (let i = 0; i <= nT; i++) {
    const offset = tSpan - (tSpan / nT) * i;   // seconds ago
    const t = tMin + (tSpan / nT) * i;
    const x = xPx(t);
    const label = i === 0
      ? `${offset.toFixed(1)}s`
      : `−${offset.toFixed(1)}s`;
    ctx.fillText(label, x, pB + 7);
  }

  // ---- Y-axis amplitude ticks (stable nice numbers) ----
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= nY; i++) {
    const val = yStep * i;
    const y = traceBot - (i / nY) * traceH;
    const label = val >= 1000
      ? `${(val / 1000).toFixed(1)}k`
      : Number.isInteger(val)
        ? val.toFixed(0)
        : val.toFixed(1);
    ctx.fillText(label, pL - 9, y);
  }

  // ---- frame counter ----
  ctx.fillStyle = C.inkMuted;
  ctx.font = '9px monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'top';
  ctx.fillText(`${nBuf} frames  ${tSpan.toFixed(1)}s`, pR, pT + 2);
}

// ---------------------------------------------------------------------------
// Draw one polyline per subcarrier
// ---------------------------------------------------------------------------

function drawSubcarrierLines(
  ctx: CanvasRenderingContext2D,
  buffer: StoredFrame[],
  numSub: number,
  isData: boolean[],
  dataStart: number,
  dataEnd: number,
  xPx: (t: number) => number,
  yPx: (amp: number) => number,
): void {
  const nBuf = buffer.length;

  // Precompute X positions for every frame (shared across subcarriers)
  const xs = new Float32Array(nBuf);
  for (let fi = 0; fi < nBuf; fi++) {
    xs[fi] = xPx(buffer[fi].t);
  }

  ctx.lineJoin = 'round';
  ctx.lineWidth = 0.42;

  for (let j = 0; j < numSub; j++) {
    // Guard bands and the DC null are always zero — skip them.
    if (!isData[j]) continue;

    ctx.strokeStyle = lineColor(j, dataStart, dataEnd);

    // Build and stroke the full polyline in one go
    ctx.beginPath();
    let moved = false;
    for (let fi = 0; fi < nBuf; fi++) {
      const y = yPx(buffer[fi].amp[j]);
      if (!moved) { ctx.moveTo(xs[fi], y); moved = true; }
      else ctx.lineTo(xs[fi], y);
    }
    ctx.stroke();
  }
}

// ---------------------------------------------------------------------------
// Auto-scale from data subcarriers
// ---------------------------------------------------------------------------

function computeDataMax(
  buffer: StoredFrame[],
  fallback: number,
  isData: boolean[],
): number {
  if (buffer.length === 0) return Math.max(fallback, 1);
  let mx = 0;
  const fStep = Math.max(1, Math.floor(buffer.length / 40));
  const sStep = Math.max(1, Math.floor((buffer[0]?.amp.length ?? 64) / 20));
  for (let fi = 0; fi < buffer.length; fi += fStep) {
    const a = buffer[fi].amp;
    for (let j = 0; j < a.length; j += sStep) {
      if (isData[j] && a[j] > mx) mx = a[j];
    }
  }
  return mx > 0 ? mx * 1.14 : Math.max(fallback, 1);
}
