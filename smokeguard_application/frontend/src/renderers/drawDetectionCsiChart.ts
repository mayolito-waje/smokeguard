/**
 * Detection-detail CSI renderer — notebook-style visualization
 * (CSI_RESEARCH.ipynb cell 20): for the 3 picked subcarriers, plot the
 * ORIGINAL amplitude (dotted, alpha 0.4) against the fully PREPROCESSED
 * amplitude (solid, lw 2) over NORMALIZED time (0 … 1), matching the
 * matplotlib figure (':', alpha=0.4 vs '-', linewidth=2).
 */

import { PALETTE, lineColor, type Theme } from './drawStripChart';
import type { DetectionCsiFrame } from '../types';

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

const PAD = { top: 14, right: 20, bottom: 34, left: 56 };

/** Fraction of plot height used by the trace area. */
const TRACE_FRAC = 0.82;

// ---------------------------------------------------------------------------
// Static nice-number Y scale (no hysteresis — this chart is static per event)
// ---------------------------------------------------------------------------

function niceStep(rough: number): number {
  if (rough <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const norm = rough / mag;
  if (norm <= 1.5) return mag;
  if (norm <= 3) return 2 * mag;
  if (norm <= 7) return 5 * mag;
  return 10 * mag;
}

function staticYScale(rawMax: number): { yCeil: number; yStep: number } {
  const max = rawMax > 0 ? rawMax * 1.05 : 1;
  const yStep = niceStep(max / 5);
  let yCeil = yStep * Math.ceil(max / yStep);
  if (yCeil < max) yCeil += yStep;
  return { yCeil, yStep };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface DetectionCsiChartOptions {
  /** Subcarrier picks: index into the active vector + display label. */
  picks: { subIdx: number; label: string }[];
  /** Original per-frame amplitudes (active subcarriers only). */
  frames: DetectionCsiFrame[];
  /** Fully preprocessed amplitudes, processed[pickIdx][frameIdx]. */
  processed: number[][];
  theme: Theme;
}

export function drawDetectionCsiChart(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  opts: DetectionCsiChartOptions,
): void {
  const { picks, frames, processed, theme } = opts;
  const nBuf = frames.length;
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

  // ---- empty state ----
  if (nBuf === 0) {
    ctx.fillStyle = C.inkMuted;
    ctx.font = '13px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('No CSI data captured for this event', (pL + pR) / 2, (pT + pB) / 2);
    return;
  }

  // ---- X axis = normalized time (matches the notebook) ----
  const xPx = (fi: number) => pL + (fi / Math.max(1, nBuf - 1)) * pW;

  // ---- Y scale over all plotted data (original + preprocessed) ----
  let rawMax = 0;
  for (const pick of picks) {
    for (let fi = 0; fi < nBuf; fi++) {
      const v = frames[fi].amp[pick.subIdx];
      if (v > rawMax) rawMax = v;
    }
  }
  for (const col of processed) {
    for (const v of col) {
      if (v > rawMax) rawMax = v;
    }
  }
  const { yCeil, yStep } = staticYScale(rawMax);
  const nY = Math.round(yCeil / yStep);
  const traceH = pH * TRACE_FRAC;
  const traceTop = pT + (pH - traceH);
  const traceBot = pB;
  const yPx = (amp: number) => traceBot - (amp / yCeil) * traceH;

  // ---- horizontal grid + zero baseline ----
  ctx.strokeStyle = C.grid;
  ctx.lineWidth = 0.6;
  for (let i = 1; i < nY; i++) {
    const y = traceBot - (i / nY) * traceH;
    ctx.beginPath();
    ctx.moveTo(pL, y);
    ctx.lineTo(pR, y);
    ctx.stroke();
  }
  ctx.strokeStyle = C.zeroLine;
  ctx.lineWidth = 1.0;
  ctx.beginPath();
  ctx.moveTo(pL, traceBot);
  ctx.lineTo(pR, traceBot);
  ctx.stroke();

  // ---- vertical time grid (normalized 0 / .25 / .5 / .75 / 1) ----
  const nT = 4;
  for (let i = 0; i <= nT; i++) {
    const x = pL + (pW / nT) * i;
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    ctx.moveTo(x, pT);
    ctx.lineTo(x, pB);
    ctx.stroke();
  }

  // ---- trace lines: original dotted + preprocessed solid per pick ----
  const xs = new Float32Array(nBuf);
  for (let fi = 0; fi < nBuf; fi++) xs[fi] = xPx(fi);

  ctx.lineJoin = 'round';
  picks.forEach((pick, pi) => {
    const color = lineColor(pick.subIdx, 0, Math.max(1, frames[0].amp.length - 1));

    // original (notebook: ':', alpha=0.4)
    ctx.globalAlpha = 0.4;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    for (let fi = 0; fi < nBuf; fi++) {
      const y = yPx(frames[fi].amp[pick.subIdx]);
      if (fi === 0) ctx.moveTo(xs[fi], y);
      else ctx.lineTo(xs[fi], y);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    // preprocessed (notebook: '-', linewidth=2)
    ctx.globalAlpha = 1;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    const col = processed[pi];
    if (col && col.length === nBuf) {
      ctx.beginPath();
      for (let fi = 0; fi < nBuf; fi++) {
        const y = yPx(col[fi]);
        if (fi === 0) ctx.moveTo(xs[fi], y);
        else ctx.lineTo(xs[fi], y);
      }
      ctx.stroke();
    }
  });
  ctx.globalAlpha = 1;

  // ---- legend (top-left): color swatch per pick + style key ----
  ctx.font = '10px monospace';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  let ly = pT + 6;
  for (const pick of picks) {
    const color = lineColor(pick.subIdx, 0, Math.max(1, frames[0].amp.length - 1));
    ctx.fillStyle = color;
    ctx.fillRect(pL + 8, ly + 2, 12, 3);
    ctx.fillStyle = C.inkMuted;
    ctx.fillText(`Subcarrier ${pick.label}`, pL + 26, ly);
    ly += 15;
  }
  ctx.fillStyle = C.inkMuted;
  ctx.fillText('solid = preprocessed CSI · dotted = original CSI', pL + 8, ly);

  // ---- X-axis normalized-time labels ----
  ctx.fillStyle = C.inkMuted;
  ctx.font = '9px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (let i = 0; i <= nT; i++) {
    ctx.fillText((i / nT).toFixed(2), pL + (pW / nT) * i, pB + 7);
  }

  // ---- Y-axis amplitude ticks ----
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
  ctx.fillText(`${nBuf} frames`, pR, pT + 2);
}
