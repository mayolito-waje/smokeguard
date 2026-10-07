/**
 * Detection detail view — one event's captured CSI window and smoke/VOC
 * window.
 *
 * CSI: preprocessed with the notebook pipeline (Hampel → Butterworth →
 * Gaussian) and drawn with 3 random subcarriers, original (dotted) vs
 * preprocessed (solid) over normalized time.
 *
 * Smoke/VOC: gas-resistance chart (drawVocChart, static buffer) plus
 * average tiles for the other metrics.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { pickRandomSubcarriers, preprocessMatrix } from '../dsp/preprocess';
import { drawDetectionCsiChart } from '../renderers/drawDetectionCsiChart';
import { drawVocChart } from '../renderers/drawVocChart';
import type { Theme } from '../renderers/drawStripChart';
import { authFetch } from '../session';
import type { StoredSmokeSample } from '../store/smokeStore';
import type { DetectionCsiFrame, DetectionEventDetail, DetectionSmokeSample } from '../types';

// ---------------------------------------------------------------------------
// Canvas setup (same DPR handling as WaterfallChart)
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

/** Mean over non-null values (BME680 fields are null on legacy firmware). */
function average(values: (number | null | undefined)[]): number | null {
  let sum = 0;
  let n = 0;
  for (const v of values) {
    if (v === null || v === undefined) continue;
    sum += v;
    n++;
  }
  return n > 0 ? sum / n : null;
}

function fmtNum(v: number | null, digits = 1, suffix = ''): string {
  return v === null ? '—' : `${v.toFixed(digits)}${suffix}`;
}

/** Signed subcarrier number for an active-vector index. ESP-IDF FFT-bin order:
 *  index 0 is DC, and indices >= wrap map to idx - subcarrierCount. */
function signedScLabel(activeIndices: number[], subcarrierCount: number, subIdx: number): string {
  const idx = activeIndices[subIdx];
  const wrap = Math.ceil(subcarrierCount / 2); // first negative subcarrier
  return String(idx < wrap ? idx : idx - subcarrierCount);
}

/** Indices into the active-subcarrier vector whose traces carry signal.
 *  The DC null and guard bands are always exactly zero; pre-fix events stored
 *  them in `active_indices`, so filter them out before picking subcarriers. */
function liveSubcarriers(frames: DetectionCsiFrame[]): number[] {
  if (frames.length === 0) return [];
  const n = frames[0].amp.length;
  const live: number[] = [];
  for (let j = 0; j < n; j++) {
    for (const f of frames) {
      if ((f.amp[j] ?? 0) > 0) {
        live.push(j);
        break;
      }
    }
  }
  return live;
}

interface TileProps {
  label: string;
  value: number | null;
  digits?: number;
  suffix?: string;
}

function AvgTile({ label, value, digits = 1, suffix = '' }: TileProps) {
  return (
    <div className="voc-tile">
      <div className="voc-tile-label">{label}</div>
      <div className="voc-tile-value">{fmtNum(value, digits, suffix)}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  id: number;
  theme: Theme;
  onBack: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function DetectionDetailView({ id, theme, onBack }: Props) {
  const [detail, setDetail] = useState<DetectionEventDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [picks, setPicks] = useState<number[]>([]);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const csiCanvasRef = useRef<HTMLCanvasElement>(null);
  const csiWrapRef = useRef<HTMLDivElement>(null);
  const gasCanvasRef = useRef<HTMLCanvasElement>(null);
  const gasWrapRef = useRef<HTMLDivElement>(null);
  const [sizes, setSizes] = useState<{ csi: [number, number]; gas: [number, number] }>({
    csi: [0, 0],
    gas: [0, 0],
  });

  // ---- fetch event detail ----
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/detection/events/${id}`)
      .then(r => {
        if (r.status === 404) throw new Error('not-found');
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: DetectionEventDetail) => {
        if (cancelled) return;
        setDetail(d);
        setPicks(pickRandomSubcarriers(liveSubcarriers(d.csi.frames), 3));
      })
      .catch(e => {
        if (!cancelled) setError(e.message === 'not-found' ? 'Event not found' : 'Failed to load event detail');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [id]);

  // ---- keep canvas sizes in sync with their containers ----
  useEffect(() => {
    const measure = () => {
      const csiRect = csiWrapRef.current?.getBoundingClientRect();
      const gasRect = gasWrapRef.current?.getBoundingClientRect();
      setSizes({
        csi: csiRect ? [csiRect.width, csiRect.height] : [0, 0],
        gas: gasRect ? [gasRect.width, gasRect.height] : [0, 0],
      });
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (csiWrapRef.current) ro.observe(csiWrapRef.current);
    if (gasWrapRef.current) ro.observe(gasWrapRef.current);
    return () => ro.disconnect();
  }, [detail]); // re-attach when the wrap divs mount after loading

  // ---- preprocess the CSI window (notebook pipeline) ----
  const processed = useMemo(() => {
    if (!detail || detail.csi.frames.length === 0) return [];
    return preprocessMatrix(detail.csi.frames.map(f => f.amp));
  }, [detail]);

  // ---- subcarriers with signal (DC/guard traces are flat zero — skip) ----
  const live = useMemo(
    () => (detail ? liveSubcarriers(detail.csi.frames) : []),
    [detail],
  );

  // ---- smoke aggregates + gas buffer ----
  const gasBuffer = useMemo<StoredSmokeSample[]>(() => {
    if (!detail) return [];
    return detail.smoke.samples.map((s: DetectionSmokeSample): StoredSmokeSample => ({
      t: s.timestamp_real,
      pm1_0: s.pm1_0,
      pm2_5: s.pm2_5,
      pm10: s.pm10,
      cnt0_3: s.cnt0_3,
      cnt0_5: s.cnt0_5,
      cnt1_0: s.cnt1_0,
      cnt2_5: s.cnt2_5,
      cnt5_0: s.cnt5_0,
      cnt10: s.cnt10,
      temp_c: s.temp_c ?? undefined,
      pressure_hpa: s.pressure_hpa ?? undefined,
      humidity_pct: s.humidity_pct ?? undefined,
      gas_kohm: s.gas_kohm ?? undefined,
      altitude_m: s.altitude_m ?? undefined,
      rssi: s.rssi,
      receivedAt: 0,
    }));
  }, [detail]);

  const hasGas = gasBuffer.some(s => s.gas_kohm !== undefined);

  const avgs = useMemo(() => {
    if (!detail) return null;
    const s = detail.smoke.samples;
    return {
      pm1_0: average(s.map(x => x.pm1_0)),
      pm2_5: average(s.map(x => x.pm2_5)),
      pm10: average(s.map(x => x.pm10)),
      temp_c: average(s.map(x => x.temp_c)),
      pressure_hpa: average(s.map(x => x.pressure_hpa)),
      humidity_pct: average(s.map(x => x.humidity_pct)),
      altitude_m: average(s.map(x => x.altitude_m)),
      rssi: average(s.map(x => x.rssi)),
    };
  }, [detail]);

  // ---- draw the CSI canvas (static per event, redraw on size/theme/picks) ----
  useEffect(() => {
    const canvas = csiCanvasRef.current;
    const [w, h] = sizes.csi;
    if (!canvas || !detail || w <= 0 || h <= 0) return;
    const ctx = setupCanvas(canvas, w, h);
    drawDetectionCsiChart(ctx, w, h, {
      picks: picks.map(subIdx => ({
        subIdx,
        label: signedScLabel(detail.csi.active_indices, detail.csi.subcarrier_count, subIdx),
      })),
      frames: detail.csi.frames,
      processed: picks.map(subIdx => processed.map(f => f[subIdx])),
      theme,
    });
  }, [detail, picks, processed, sizes.csi, theme]);

  // ---- draw the gas canvas (static buffer, live-scale hysteresis re-anchors) ----
  useEffect(() => {
    const canvas = gasCanvasRef.current;
    const [w, h] = sizes.gas;
    if (!canvas || w <= 0 || h <= 0) return;
    const ctx = setupCanvas(canvas, w, h);
    if (hasGas) {
      drawVocChart(ctx, w, h, {
        buffer: gasBuffer,
        yMax: 0,   // fallbacks — the renderer computes the range from the buffer
        yMin: 0,
        theme,
        stale: false,
      });
    } else {
      // manual empty state (drawVocChart draws its own "waiting" text otherwise)
      ctx.fillStyle = theme === 'dark' ? '#0d0d0d' : '#f8faf8';
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = theme === 'dark' ? '#6b6b66' : '#8899a6';
      ctx.font = '13px monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('No smoke sensor data captured for this event', w / 2, h / 2);
    }
  }, [gasBuffer, hasGas, sizes.gas, theme]);

  // ---- delete the event (irreversible; confirm first) ----
  async function handleDelete() {
    if (!detail || deleting) return;
    const ok = window.confirm(
      `Delete detected event #${detail.id}? This removes it from the database and cannot be undone.`,
    );
    if (!ok) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await authFetch(`/api/detection/events/${detail.id}`, { method: 'DELETE' });
      if (res.status === 404) throw new Error('not-found');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      onBack();
    } catch (e) {
      setDeleteError(
        e instanceof Error && e.message === 'not-found'
          ? 'Event already deleted'
          : 'Failed to delete event',
      );
      setDeleting(false);
    }
  }

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <div className="detail-view">
      <div className="detail-toolbar">
        <button className="btn-back" onClick={onBack}>← Back</button>
        {detail && (
          <>
            <span className="detail-title">Detected Smoking Activity #{detail.id}</span>
            <span className="detail-meta">
              {new Date(detail.detected_at * 1000).toLocaleString()}
              {' · '}{detail.csi_frames} CSI frames
              {' · '}{detail.smoke_samples} AQI + VOC data
            </span>
          </>
        )}
        <div className="detail-toolbar-right">
          {deleteError && <span className="detail-delete-error">{deleteError}</span>}
          {detail && (
            <button
              className="btn-delete"
              onClick={handleDelete}
              disabled={deleting}
              title="Delete this event from the database"
              aria-label="Delete this event"
            >
              🗑️
            </button>
          )}
        </div>
      </div>

      {loading && <div className="history-status">Loading…</div>}
      {error && <div className="history-status history-error">{error}</div>}

      {!loading && !error && detail && (
        <>
          <div className="detail-section">
            <div className="detail-section-header">
              <span className="detail-section-title">Preprocessed CSI</span>
              {detail.csi.frames.length > 0 && (
                <div className="detail-controls">
                  <span className="detail-meta">
                    {detail.csi.frames.length} frames
                    {' · '}
                    {detail.csi.frames.length > 1
                      ? (detail.csi.frames[detail.csi.frames.length - 1].t - detail.csi.frames[0].t).toFixed(1) + ' s'
                      : '0 s'}
                  </span>
                  <button
                    className="btn-simulate"
                    onClick={() => setPicks(pickRandomSubcarriers(live, 3))}
                  >
                    Reshuffle subcarriers
                  </button>
                </div>
              )}
            </div>
            <div className="detail-csi-wrap" ref={csiWrapRef}>
              <canvas ref={csiCanvasRef} />
            </div>
          </div>

          <div className="detail-section">
            <div className="detail-section-header">
              <span className="detail-section-title">VOC — gas resistance</span>
              <span className="detail-meta">{gasBuffer.length} samples</span>
            </div>
            <div className="detail-gas-wrap" ref={gasWrapRef}>
              <canvas ref={gasCanvasRef} />
            </div>
          </div>

          <div className="detail-section">
            <div className="detail-section-header">
              <span className="detail-section-title">Air quality — window averages</span>
            </div>
            {detail.smoke.samples.length === 0 ? (
              <div className="detail-meta">No smoke sensor data captured for this event</div>
            ) : (
              <div className="detail-tiles">
                <AvgTile label="PM1.0" value={avgs?.pm1_0 ?? null} digits={0} />
                <AvgTile label="PM2.5" value={avgs?.pm2_5 ?? null} digits={0} />
                <AvgTile label="PM10" value={avgs?.pm10 ?? null} digits={0} />
                <AvgTile label="Temperature" value={avgs?.temp_c ?? null} suffix=" °C" />
                <AvgTile label="Pressure" value={avgs?.pressure_hpa ?? null} suffix=" hPa" />
                <AvgTile label="Humidity" value={avgs?.humidity_pct ?? null} suffix=" %" />
                <AvgTile label="Altitude" value={avgs?.altitude_m ?? null} suffix=" m" />
                <AvgTile label="RSSI" value={avgs?.rssi ?? null} digits={0} suffix=" dBm" />
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
