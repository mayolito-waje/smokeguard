/**
 * StripChart — real-time pulse-monitor style CSI amplitude over time.
 *
 * X-axis = elapsed time (newest packet at the right edge).
 * Y-axis = amplitude.  Each subcarrier is its own coloured polyline.
 *
 * Maintains a ring buffer of ~600 amplitude snapshots (~6 s at 100 Hz).
 * Renders at display refresh rate via requestAnimationFrame, pulling the
 * latest frame directly from the store (no React re-render per CSI frame).
 */

import { useRef, useEffect, useCallback, useState } from 'react';
import {
  getLatestFrame,
  getAutoScaleMax,
  type StoredFrame,
} from '../store/csiStore';
import { drawStripChart, type Theme } from '../renderers/drawStripChart';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Max frames in the scrolling window (~6 seconds at 100 Hz). */
const SCROLL_DEPTH = 600;

// ---------------------------------------------------------------------------
// Canvas setup (called on each frame)
// ---------------------------------------------------------------------------

function setupCanvas(
  canvas: HTMLCanvasElement,
  w: number,
  h: number,
): CanvasRenderingContext2D {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  canvas.style.width = `${w}px`;
  canvas.style.height = `${h}px`;
  const ctx = canvas.getContext('2d')!;
  ctx.scale(dpr, dpr);
  return ctx;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  theme: Theme;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function StripChart({ theme }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef(0);
  const lastFrameRef = useRef<StoredFrame | null>(null);
  const bufferRef = useRef<StoredFrame[]>([]);
  const pausedRef = useRef(false);
  const themeRef = useRef<Theme>(theme);

  const [paused, setPaused] = useState(false);

  useEffect(() => { pausedRef.current = paused; }, [paused]);
  useEffect(() => { themeRef.current = theme; }, [theme]);

  // ---- rAF draw loop ----
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) {
      rafRef.current = requestAnimationFrame(draw);
      return;
    }

    // --- sizing ---
    const rect = container.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;
    if (w <= 0 || h <= 0) {
      rafRef.current = requestAnimationFrame(draw);
      return;
    }

    // --- pull latest frame from store ---
    const frame = getLatestFrame();
    if (frame && frame !== lastFrameRef.current && !pausedRef.current) {
      lastFrameRef.current = frame;
      bufferRef.current.push(frame);
      while (bufferRef.current.length > SCROLL_DEPTH) {
        bufferRef.current.shift();
      }
    }

    // --- render ---
    const ctx = setupCanvas(canvas, w, h);
    drawStripChart(ctx, w, h, {
      buffer: bufferRef.current,
      yMax: getAutoScaleMax(),
      paused,
      theme: themeRef.current,
    });

    rafRef.current = requestAnimationFrame(draw);
  }, [paused]);

  // Start / restart rAF loop
  useEffect(() => {
    rafRef.current = requestAnimationFrame(draw);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [draw]);

  return (
    <div className="strip-chart">
      <div className="sc-toolbar">
        <span className="sc-title">Channel State Information (CSI)</span>
        <div className="sc-controls">
          <label className="sc-toggle">
            <input
              type="checkbox"
              checked={paused}
              onChange={(e) => setPaused(e.target.checked)}
            />
            Pause
          </label>
          {paused && <span className="sc-paused-badge">PAUSED</span>}
        </div>
      </div>
      <div ref={containerRef} className="sc-canvas-wrap">
        <canvas ref={canvasRef} />
      </div>
    </div>
  );
}
