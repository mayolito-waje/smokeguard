/**
 * Air-quality strip-chart renderer — PM1.0 / PM2.5 / PM10 over time.
 *
 * Same skeleton as drawStripChart (theme surfaces, stable Y hysteresis,
 * recessive grid) but for 3 scalar series instead of 64 subcarrier traces.
 * One Y axis only — all three series share the µg/m³ scale; particle counts
 * live in the stat rail beside the chart.
 */

import type { Theme } from './drawStripChart';
import type { StoredSmokeSample } from '../store/smokeStore';

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

const PAD = { top: 14, right: 20, bottom: 34, left: 56 };

/** Fraction of plot height used by the trace area. */
const TRACE_FRAC = 0.82;

/** Minimum time span in seconds (avoid x/0 when buffer has 1 sample). */
const MIN_SPAN_S = 1.0;

// ---------------------------------------------------------------------------
// Theme-aware palette (surfaces match the CSI chart per theme)
// ---------------------------------------------------------------------------

interface ChartColors {
  bg: string;
  grid: string;
  zeroLine: string;
  inkMuted: string;
  nowLine: string;
}

const PALETTE: Record<Theme, ChartColors> = {
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
// Series colours (validated — do not change without re-running the validator)
//
//   node scripts/validate_palette.js "#2a78d6,#eb6834,#199e70" --mode light --surface "#f8faf8"
//     → ALL CHECKS PASS (worst adjacent CVD ΔE 8.4 protan; normal 27.1;
//       contrast >= 3:1 on #f8faf8)
//   node scripts/validate_palette.js "#3987e5,#d95926,#199e70" --mode dark  --surface "#0d0d0d"
//     → ALL CHECKS PASS (worst adjacent CVD ΔE 9.4 deutan; normal 26.5;
//       contrast >= 3:1 on #0d0d0d)
//
// Identity is never colour-alone anyway: the HTML legend in the toolbar plus
// direct end-labels carry it.
// ---------------------------------------------------------------------------

export interface SmokeColors {
  pm1_0: string;
  pm2_5: string;
  pm10: string;
}

export const SERIES_COLORS: Record<Theme, SmokeColors> = {
  light: { pm1_0: '#2a78d6', pm2_5: '#eb6834', pm10: '#199e70' },
  dark:  { pm1_0: '#3987e5', pm2_5: '#d95926', pm10: '#199e70' },
};

const SERIES_KEYS = ['pm1_0', 'pm2_5', 'pm10'] as const;

// ---------------------------------------------------------------------------
// Stable Y-axis scale (hysteresis so labels don't vibrate)
// ---------------------------------------------------------------------------

// Module-level is correct: exactly one SmokePanel mounts. State is
// independent of the CSI renderer's globals.
let _stableYCeil = 100;
let _stableYStep = 20;

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

/** Format a time offset: "-Ns" under 90 s, else "-Nm". */
function fmtOffset(sec: number): string {
  return sec < 90 ? `${Math.round(sec)}s` : `${Math.round(sec / 60)}m`;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface SmokeChartOptions {
  buffer: StoredSmokeSample[];
  yMax: number;    // EMA-derived fallback from getSmokeAutoScaleMax()
  paused: boolean; // kept for API parity with drawStripChart
  theme: Theme;
  stale: boolean;  // sensor offline — dim traces + badge
}

export function drawSmokeChart(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  opts: SmokeChartOptions,
): void {
  const { buffer, theme, stale } = opts;
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

  // ---- time axis ----
  const tMin = nBuf > 0 ? buffer[0].t : 0;
  const tMax = nBuf > 0 ? buffer[nBuf - 1].t : tMin + MIN_SPAN_S;
  const tSpan = Math.max(tMax - tMin, MIN_SPAN_S);
  const xPx = (t: number) => pL + ((t - tMin) / tSpan) * pW;

  // ---- Y scale (0 at bottom, stable nice-number ceiling) ----
  const rawMax = computeDataMax(buffer, opts.yMax);
  const { yCeil, yStep } = getStableYScale(rawMax);
  const nY = Math.round(yCeil / yStep);          // number of tick intervals
  const traceH = pH * TRACE_FRAC;
  const traceTop = pT + (pH - traceH);
  const traceBot = pB;
  const yPx = (v: number) => traceBot - (v / yCeil) * traceH;

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

  // ---- zero baseline ----
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

  // ---- series lines / empty state ----
  if (nBuf > 0) {
    drawSeries(ctx, buffer, SERIES_KEYS, xPx, yPx, pL, pR, traceTop, traceBot, theme, stale);
  } else {
    ctx.fillStyle = C.inkMuted;
    ctx.font = '13px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Waiting for air-quality data…', (pL + pR) / 2, (pT + pB) / 2);
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
      ? fmtOffset(offset)
      : `−${fmtOffset(offset)}`;
    ctx.fillText(label, x, pB + 7);
  }

  // ---- Y-axis ticks (µg/m³) ----
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= nY; i++) {
    const val = yStep * i;
    const y = traceBot - (i / nY) * traceH;
    const label = Number.isInteger(val) ? val.toFixed(0) : val.toFixed(1);
    ctx.fillText(label, pL - 9, y);
  }

  // ---- info line (top-right) ----
  const spanLabel = fmtOffset(tSpan);
  ctx.fillStyle = C.inkMuted;
  ctx.font = '9px monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'top';
  ctx.fillText(`${nBuf} samples  ${spanLabel}`, pR, pT + 2);

  // ---- offline badge (top-left) ----
  if (stale && nBuf > 0) {
    ctx.fillStyle = C.inkMuted;
    ctx.font = '9px monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText('SENSOR OFFLINE', pL, pT + 2);
  }
}

// ---------------------------------------------------------------------------
// Draw the three PM polylines + direct end-labels
// ---------------------------------------------------------------------------

function drawSeries(
  ctx: CanvasRenderingContext2D,
  buffer: StoredSmokeSample[],
  keys: readonly (keyof SmokeColors)[],
  xPx: (t: number) => number,
  yPx: (v: number) => number,
  pL: number,
  pR: number,
  traceTop: number,
  traceBot: number,
  theme: Theme,
  stale: boolean,
): void {
  const nBuf = buffer.length;
  const colors = SERIES_COLORS[theme];

  // Precompute X positions for every sample (shared across series)
  const xs = new Float32Array(nBuf);
  for (let fi = 0; fi < nBuf; fi++) {
    xs[fi] = xPx(buffer[fi].t);
  }

  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.globalAlpha = stale ? 0.35 : 1;

  for (const key of keys) {
    ctx.strokeStyle = colors[key];
    ctx.beginPath();
    for (let fi = 0; fi < nBuf; fi++) {
      const y = yPx(buffer[fi][key]);
      if (fi === 0) ctx.moveTo(xs[fi], y);
      else ctx.lineTo(xs[fi], y);
    }
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // ---- direct end-labels (inkMuted — identity never rides on colour alone) ----
  const labels: Array<{ key: (typeof keys)[number]; y: number }> = keys.map(
    (key) => ({ key, y: yPx(buffer[nBuf - 1][key]) }),
  );

  ctx.font = '10px monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';

  const placed: number[] = [];
  // Draw PM2.5 always; PM10 then PM1.0 only when clear of placed labels.
  // Values are physically ordered (PM1.0 <= PM2.5 <= PM10), so collisions
  // only occur when traces nearly coincide.
  for (const key of ['pm2_5', 'pm10', 'pm1_0'] as const) {
    const label = labels.find((l) => l.key === key);
    if (!label) continue;
    if (placed.some((y) => Math.abs(y - label.y) < 13)) continue;
    const y = Math.min(Math.max(label.y, traceTop + 8), traceBot - 4);
    ctx.fillStyle = PALETTE[theme].inkMuted;
    ctx.fillText(labelText(key), pR - 6, y);
    placed.push(y);
  }
}

function labelText(key: keyof SmokeColors): string {
  if (key === 'pm1_0') return 'PM1.0';
  if (key === 'pm2_5') return 'PM2.5';
  return 'PM10';
}

// ---------------------------------------------------------------------------
// Auto-scale from the buffer
// ---------------------------------------------------------------------------

function computeDataMax(
  buffer: StoredSmokeSample[],
  fallback: number,
): number {
  if (buffer.length === 0) return Math.max(fallback, 1);
  let mx = 0;
  const fStep = Math.max(1, Math.floor(buffer.length / 40));
  for (let fi = 0; fi < buffer.length; fi += fStep) {
    const s = buffer[fi];
    if (s.pm1_0 > mx) mx = s.pm1_0;
    if (s.pm2_5 > mx) mx = s.pm2_5;
    if (s.pm10 > mx) mx = s.pm10;
  }
  return mx > 0 ? mx * 1.14 : Math.max(fallback, 1);
}
