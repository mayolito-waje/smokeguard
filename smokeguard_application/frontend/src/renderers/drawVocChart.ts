/**
 * VOC strip-chart renderer — BME680 gas resistance (kΩ) over time.
 *
 * Same skeleton as drawStripChart (theme surfaces, stable-Y hysteresis,
 * recessive grid) but for a single scalar series. Gas resistance DROPS
 * under VOC exposure (~50–300 kΩ baseline → ~1–5 kΩ), so the Y scale has
 * a min-hold floor in addition to the peak-hold ceiling — a 0-anchored
 * scale would render the event as a line hugging the bottom.
 *
 * Deliberate deviation from the dataviz baseline: no hover tooltip, same
 * as the existing CSI canvas chart (drawStripChart). The stat tiles
 * beside the chart act as the table view, and the direct end-label
 * carries the current value.
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
  baseLine: string;
  inkMuted: string;
  nowLine: string;
}

const PALETTE: Record<Theme, ChartColors> = {
  dark: {
    bg:        '#0d0d0d',
    grid:      'rgba(255,255,255,0.045)',
    baseLine:  'rgba(255,255,255,0.20)',
    inkMuted:  '#6b6b66',
    nowLine:   'rgba(255,255,255,0.10)',
  },
  light: {
    bg:        '#f8faf8',
    grid:      'rgba(0,0,0,0.06)',
    baseLine:  'rgba(0,0,0,0.12)',
    inkMuted:  '#8899a6',
    nowLine:   'rgba(0,0,0,0.08)',
  },
};

// ---------------------------------------------------------------------------
// Series colour (validated — do not change without re-running the validator)
//
// Reuses the green from the old PM palette, which passed on these exact
// surfaces:
//   node scripts/validate_palette.js "#199e70" --mode light --surface "#f8faf8"
//     → ALL CHECKS PASS
//   node scripts/validate_palette.js "#199e70" --mode dark  --surface "#0d0d0d"
//     → ALL CHECKS PASS
//
// A single series needs no legend — the toolbar title names it, and the
// direct end-label carries the current value.
// ---------------------------------------------------------------------------

export const GAS_COLOR: Record<Theme, string> = {
  light: '#199e70',
  dark:  '#199e70',
};

// ---------------------------------------------------------------------------
// Stable Y-axis scale (hysteresis on both bounds so labels don't vibrate)
// ---------------------------------------------------------------------------

// Module-level is correct: exactly one VocPanel mounts. State is
// independent of the CSI renderer's globals.
let _stableYCeil = 100;
let _stableYFloor = 0;
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

/**
 * Return a stable {yCeil, yStep, yFloor} triple with hysteresis on both
 * bounds, plus a span guard so a constant reading never collapses the axis.
 */
function getStableYScale(
  rawMax: number,
  rawMin: number,
): { yCeil: number; yStep: number; yFloor: number } {
  if (rawMax > _stableYCeil || rawMax < _stableYCeil * 0.45) {
    const rough = rawMax / 5;                // target ~5 ticks
    _stableYStep = niceStep(rough);
    _stableYCeil = _stableYStep * Math.ceil(rawMax / _stableYStep);
    if (_stableYCeil < rawMax) _stableYCeil += _stableYStep;
  }
  if (rawMin < _stableYFloor || rawMin > _stableYFloor * 1.2) {
    _stableYFloor = rawMin;
  }
  const span = _stableYCeil - _stableYFloor;
  const minSpan = Math.max(_stableYCeil * 0.06, 0.5);
  if (span < minSpan) {
    _stableYFloor = Math.max(0, _stableYCeil - minSpan);
  }
  return { yCeil: _stableYCeil, yStep: _stableYStep, yFloor: _stableYFloor };
}

/** Format a time offset: "-Ns" under 90 s, else "-Nm". */
function fmtOffset(sec: number): string {
  return sec < 90 ? `${Math.round(sec)}s` : `${Math.round(sec / 60)}m`;
}

/** Compact kΩ tick label: integers ≥ 10, one decimal below. */
function fmtKohm(v: number): string {
  return v >= 10 ? v.toFixed(0) : v.toFixed(1);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface VocChartOptions {
  buffer: StoredSmokeSample[];
  yMax: number;  // EMA-derived fallback from getGasAutoScaleMax()
  yMin: number;  // EMA-derived fallback from getGasAutoScaleMin()
  theme: Theme;
  stale: boolean;  // sensor offline — dim trace + badge
}

export function drawVocChart(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  opts: VocChartOptions,
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

  // ---- Y scale (floor at bottom, stable nice-number ceiling) ----
  const { rawMax, rawMin } = computeDataRange(buffer, opts.yMax, opts.yMin);
  const { yCeil, yStep, yFloor } = getStableYScale(rawMax, rawMin);
  const span = Math.max(yCeil - yFloor, 1e-9);
  const nY = Math.max(1, Math.round(span / yStep));  // tick intervals
  const traceH = pH * TRACE_FRAC;
  const traceTop = pT + (pH - traceH);
  const traceBot = pB;
  const yPx = (v: number) => traceBot - ((v - yFloor) / span) * traceH;

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

  // ---- floor baseline ----
  ctx.strokeStyle = C.baseLine;
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

  // ---- series line / empty state ----
  if (nBuf > 0) {
    drawGasSeries(ctx, buffer, xPx, yPx, pL, pR, traceTop, traceBot, theme, stale);
  } else {
    ctx.fillStyle = C.inkMuted;
    ctx.font = '13px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('Waiting for VOC data…', (pL + pR) / 2, (pT + pB) / 2);
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

  // ---- Y-axis ticks (kΩ) ----
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= nY; i++) {
    const val = yFloor + (span / nY) * i;
    const y = traceBot - (i / nY) * traceH;
    ctx.fillText(fmtKohm(val), pL - 9, y);
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
// Draw the gas polyline + direct end-label
// ---------------------------------------------------------------------------

function drawGasSeries(
  ctx: CanvasRenderingContext2D,
  buffer: StoredSmokeSample[],
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

  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.globalAlpha = stale ? 0.35 : 1;
  ctx.strokeStyle = GAS_COLOR[theme];

  // Legacy samples (no BME680) carry undefined gas — skip them rather
  // than plotting fake zero-spikes.
  let started = false;
  ctx.beginPath();
  for (let fi = 0; fi < nBuf; fi++) {
    const v = buffer[fi].gas_kohm;
    if (v === undefined) continue;
    const x = xPx(buffer[fi].t);
    const y = yPx(v);
    if (!started) { ctx.moveTo(x, y); started = true; }
    else { ctx.lineTo(x, y); }
  }
  if (started) ctx.stroke();
  ctx.globalAlpha = 1;

  // ---- direct end-label (inkMuted — identity never rides on colour alone) ----
  const last = buffer[nBuf - 1];
  if (!started || last.gas_kohm === undefined) return;
  const y = Math.min(Math.max(yPx(last.gas_kohm), traceTop + 8), traceBot - 4);
  ctx.fillStyle = PALETTE[theme].inkMuted;
  ctx.font = '10px monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  ctx.fillText(`${last.gas_kohm.toFixed(1)} kΩ`, pR - 6, y);
}

// ---------------------------------------------------------------------------
// Auto-scale from the buffer
// ---------------------------------------------------------------------------

function computeDataRange(
  buffer: StoredSmokeSample[],
  fallbackMax: number,
  fallbackMin: number,
): { rawMax: number; rawMin: number } {
  if (buffer.length === 0) {
    return { rawMax: Math.max(fallbackMax, 1), rawMin: Math.max(0, fallbackMin) };
  }
  let mx = -Infinity;
  let mn = Infinity;
  const fStep = Math.max(1, Math.floor(buffer.length / 40));
  for (let fi = 0; fi < buffer.length; fi += fStep) {
    const v = buffer[fi].gas_kohm;
    if (v === undefined) continue;
    if (v > mx) mx = v;
    if (v < mn) mn = v;
  }
  if (!Number.isFinite(mx) || !Number.isFinite(mn)) {
    return { rawMax: Math.max(fallbackMax, 1), rawMin: Math.max(0, fallbackMin) };
  }
  return { rawMax: mx * 1.14, rawMin: mn * 0.86 };
}
