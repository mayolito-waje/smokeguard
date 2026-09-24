/**
 * SmokePanel — bottom panel: PM1.0/PM2.5/PM10 strip chart + stat rail.
 *
 * Same skeleton as StripChart: canvas pulls the latest sample from the
 * store via rAF (no React re-render per sample); the stat rail updates via
 * the store's rAF-coalesced subscription (~1 Hz). Staleness is derived from
 * the sample's receive time, so the tiles dim when the sensor goes offline.
 */

import { useRef, useEffect, useCallback, useState } from 'react';
import {
  getLatestSmokeSample,
  getSmokeAutoScaleMax,
  onSmokeSample,
  STALE_AFTER_MS,
  MAX_SAMPLES,
  type StoredSmokeSample,
} from '../store/smokeStore';
import { drawSmokeChart, SERIES_COLORS } from '../renderers/drawSmokeChart';
import type { Theme } from '../renderers/drawStripChart';

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
// Helpers
// ---------------------------------------------------------------------------

function fmtCount(v: number | undefined): string {
  return v === undefined ? '—' : v.toLocaleString('en-US');
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

export default function SmokePanel({ theme }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef(0);
  const lastSampleRef = useRef<StoredSmokeSample | null>(null);
  const bufferRef = useRef<StoredSmokeSample[]>([]);
  const pausedRef = useRef(false);
  const themeRef = useRef<Theme>(theme);
  const staleRef = useRef(false);

  const [paused, setPaused] = useState(false);
  const [sample, setSample] = useState<StoredSmokeSample | null>(null);
  const [stale, setStale] = useState(false);
  const [ageSec, setAgeSec] = useState<number | null>(null);

  useEffect(() => { pausedRef.current = paused; }, [paused]);
  useEffect(() => { themeRef.current = theme; }, [theme]);
  useEffect(() => { staleRef.current = stale; }, [stale]);

  // ---- Tiles update via the store's rAF-coalesced subscription (~1 Hz) ----
  useEffect(() => onSmokeSample(setSample), []);

  // ---- 1 s staleness tick (also drives the "last seen" note) ----
  useEffect(() => {
    const id = setInterval(() => {
      const s = getLatestSmokeSample();
      if (!s) {
        setAgeSec(null);
        setStale(false);
        return;
      }
      const ageMs = performance.now() - s.receivedAt;
      setAgeSec(Math.max(0, Math.round(ageMs / 1000)));
      setStale(ageMs > STALE_AFTER_MS);
    }, 1000);
    return () => clearInterval(id);
  }, []);

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

    // --- pull latest sample from store ---
    const latest = getLatestSmokeSample();
    if (latest && latest !== lastSampleRef.current && !pausedRef.current) {
      lastSampleRef.current = latest;
      bufferRef.current.push(latest);
      while (bufferRef.current.length > MAX_SAMPLES) {
        bufferRef.current.shift();
      }
    }

    // --- render ---
    const ctx = setupCanvas(canvas, w, h);
    drawSmokeChart(ctx, w, h, {
      buffer: bufferRef.current,
      yMax: getSmokeAutoScaleMax(),
      paused,
      theme: themeRef.current,
      stale: staleRef.current,
    });

    rafRef.current = requestAnimationFrame(draw);
  }, [paused]);

  // Start / restart rAF loop
  useEffect(() => {
    rafRef.current = requestAnimationFrame(draw);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [draw]);

  const colors = SERIES_COLORS[theme];

  return (
    <section className={`smoke-panel${stale ? ' stale' : ''}`}>
      <div className="smoke-toolbar">
        <span className="smoke-title">PMS5003 · Air Quality</span>
        <div className="smoke-legend">
          <span className="smoke-legend-item">
            <span className="smoke-swatch" style={{ backgroundColor: colors.pm1_0 }} />
            PM1.0
          </span>
          <span className="smoke-legend-item">
            <span className="smoke-swatch" style={{ backgroundColor: colors.pm2_5 }} />
            PM2.5
          </span>
          <span className="smoke-legend-item">
            <span className="smoke-swatch" style={{ backgroundColor: colors.pm10 }} />
            PM10
          </span>
        </div>
        <div className="smoke-controls">
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
      <div className="smoke-body">
        <div ref={containerRef} className="smoke-canvas-wrap">
          <canvas ref={canvasRef} />
        </div>
        <div className="smoke-stats">
          <div className="smoke-hero">
            <div className="smoke-hero-tile">
              <span className="smoke-hero-label">PM2.5</span>
              <span className="smoke-hero-value">
                {sample ? sample.pm2_5.toLocaleString('en-US') : '—'}
                <small> µg/m³</small>
              </span>
            </div>
            <div className="smoke-hero-tile">
              <span className="smoke-hero-label">PM10</span>
              <span className="smoke-hero-value">
                {sample ? sample.pm10.toLocaleString('en-US') : '—'}
                <small> µg/m³</small>
              </span>
            </div>
          </div>
          <div className="smoke-section-label">Particles / 0.1 L</div>
          <div className="smoke-stats-grid">
            <div className="smoke-stat-cell">
              <span className="smoke-stat-label">0.3 µm</span>
              <span className="smoke-stat-value">{fmtCount(sample?.cnt0_3)}</span>
            </div>
            <div className="smoke-stat-cell">
              <span className="smoke-stat-label">0.5 µm</span>
              <span className="smoke-stat-value">{fmtCount(sample?.cnt0_5)}</span>
            </div>
            <div className="smoke-stat-cell">
              <span className="smoke-stat-label">1.0 µm</span>
              <span className="smoke-stat-value">{fmtCount(sample?.cnt1_0)}</span>
            </div>
            <div className="smoke-stat-cell">
              <span className="smoke-stat-label">2.5 µm</span>
              <span className="smoke-stat-value">{fmtCount(sample?.cnt2_5)}</span>
            </div>
            <div className="smoke-stat-cell">
              <span className="smoke-stat-label">5.0 µm</span>
              <span className="smoke-stat-value">{fmtCount(sample?.cnt5_0)}</span>
            </div>
            <div className="smoke-stat-cell">
              <span className="smoke-stat-label">10 µm</span>
              <span className="smoke-stat-value">{fmtCount(sample?.cnt10)}</span>
            </div>
            <div className="smoke-stat-cell">
              <span className="smoke-stat-label">RSSI</span>
              <span className="smoke-stat-value">{sample ? `${sample.rssi} dBm` : '—'}</span>
            </div>
          </div>
          <div className="smoke-stale-note">
            {ageSec === null ? 'No data yet' : `Last sample ${ageSec}s ago`}
          </div>
        </div>
      </div>
    </section>
  );
}
