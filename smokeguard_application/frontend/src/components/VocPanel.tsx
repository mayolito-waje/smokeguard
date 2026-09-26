/**
 * VocPanel — bottom-left panel: BME680 gas-resistance strip chart + the
 * four environmental stat tiles (temperature, pressure, humidity, altitude).
 *
 * Same skeleton as StripChart: the canvas pulls the latest sample from the
 * store via rAF (no React re-render per sample); the tiles update via the
 * store's rAF-coalesced subscription (~1 Hz). Staleness is derived from the
 * sample's receive time, so tiles and trace dim when the sensor goes
 * offline. Legacy firmware (no BME680) leaves the chart in its empty state
 * while the Air Quality panel stays live.
 */

import { useRef, useEffect, useCallback, useState } from 'react';
import {
  getLatestSmokeSample,
  getGasAutoScaleMax,
  getGasAutoScaleMin,
  onSmokeSample,
  STALE_AFTER_MS,
  MAX_SAMPLES,
  type StoredSmokeSample,
} from '../store/smokeStore';
import { drawVocChart } from '../renderers/drawVocChart';
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

function fmtEnv(v: number | undefined): string {
  return v === undefined ? '—' : v.toFixed(1);
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

export default function VocPanel({ theme }: Props) {
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
      // Legacy firmware sends no BME680 block — never plot fake zeros
      if (latest.gas_kohm !== undefined) {
        bufferRef.current.push(latest);
        while (bufferRef.current.length > MAX_SAMPLES) {
          bufferRef.current.shift();
        }
      }
    }

    // --- render ---
    const ctx = setupCanvas(canvas, w, h);
    drawVocChart(ctx, w, h, {
      buffer: bufferRef.current,
      yMax: getGasAutoScaleMax(),
      yMin: getGasAutoScaleMin(),
      theme: themeRef.current,
      stale: staleRef.current,
    });

    rafRef.current = requestAnimationFrame(draw);
  }, []);

  // Start / restart rAF loop
  useEffect(() => {
    rafRef.current = requestAnimationFrame(draw);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [draw]);

  return (
    <section className={`voc-panel${stale ? ' stale' : ''}`}>
      <div className="voc-toolbar">
        <span className="voc-title">Volatile Organic Compounds (VOC)</span>
        <div className="voc-controls">
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
      <div className="voc-body">
        <div ref={containerRef} className="voc-canvas-wrap">
          <canvas ref={canvasRef} />
        </div>
        <div className="voc-stats">
          <div className="voc-tiles">
            <div className="voc-tile">
              <span className="voc-tile-label">Temperature</span>
              <span className="voc-tile-value">
                {fmtEnv(sample?.temp_c)}
                <small> °C</small>
              </span>
            </div>
            <div className="voc-tile">
              <span className="voc-tile-label">Pressure</span>
              <span className="voc-tile-value">
                {fmtEnv(sample?.pressure_hpa)}
                <small> hPa</small>
              </span>
            </div>
            <div className="voc-tile">
              <span className="voc-tile-label">Humidity</span>
              <span className="voc-tile-value">
                {fmtEnv(sample?.humidity_pct)}
                <small> %</small>
              </span>
            </div>
            <div className="voc-tile">
              <span className="voc-tile-label">Altitude</span>
              <span className="voc-tile-value">
                {fmtEnv(sample?.altitude_m)}
                <small> m</small>
              </span>
            </div>
          </div>
          <div className="voc-stale-note">
            {ageSec === null ? 'No data yet' : `Last sample ${ageSec}s ago`}
          </div>
        </div>
      </div>
    </section>
  );
}
