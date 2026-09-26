/**
 * Smoke-sensor data store — module-level singleton for the 1 Hz smoke path
 * (combined PMS5003 + BME680 telemetry, one CSV line per sample). Same
 * pattern as csiStore: no React re-render per sample; the VOC canvas pulls
 * via rAF, stat tiles update via a coalesced subscription.
 */

import type { SmokeSample } from '../types';

// ---------------------------------------------------------------------------
// Stored sample
// ---------------------------------------------------------------------------

export interface StoredSmokeSample {
  t: number;              // epoch seconds (server applied receive-time fallback)
  pm1_0: number;
  pm2_5: number;
  pm10: number;
  cnt0_3: number;
  cnt0_5: number;
  cnt1_0: number;
  cnt2_5: number;
  cnt5_0: number;
  cnt10: number;
  temp_c: number | undefined;      // BME680 block — undefined on legacy
  pressure_hpa: number | undefined; // firmware (11-column lines)
  humidity_pct: number | undefined;
  gas_kohm: number | undefined;
  altitude_m: number | undefined;
  rssi: number;
  receivedAt: number;     // performance.now() at receive — drives staleness
}

// ---------------------------------------------------------------------------
// Ring buffer
// ---------------------------------------------------------------------------

/** Max samples in the scrolling window (~10 minutes at 1 Hz). */
export const MAX_SAMPLES = 600;

/** Sensor is offline after this many ms without a fresh sample. */
export const STALE_AFTER_MS = 15_000;

const ring: StoredSmokeSample[] = [];
let lastSample: StoredSmokeSample | null = null;

// ---------------------------------------------------------------------------
// Gas auto-scale (peak-hold ceiling + min-hold floor)
// ---------------------------------------------------------------------------

// Gas resistance DROPS under VOC exposure (~50–300 kΩ baseline → ~1–5 kΩ),
// so a 0-anchored peak-hold scale would hide the event. Both bounds use
// exponential hold with τ ≈ 3.3 min: the ceiling decays down and the floor
// drifts up, so a fresh drop re-opens the window. The renderer's stable-Y
// hysteresis removes residual tick vibration.
const DECAY = 0.995;
const DECAY_UP = 1 / DECAY;  // ≈ 1.005 — min-hold drifts upward at the same τ

/** kΩ floor for the gas Y ceiling (gas can legitimately read below 1 kΩ). */
const GAS_SCALE_FLOOR = 1;

let gasEmaMax = GAS_SCALE_FLOOR;
let gasEmaMin = Number.POSITIVE_INFINITY;  // stays ∞ until the first gas sample

// ---------------------------------------------------------------------------
// Subscribers
// ---------------------------------------------------------------------------

type SmokeSub = (s: StoredSmokeSample) => void;

const subs = new Set<SmokeSub>();

let rafScheduled = false;

// ---------------------------------------------------------------------------
// Push sample (called from WebSocket onmessage)
// ---------------------------------------------------------------------------

export function pushSmokeSample(msg: SmokeSample): void {
  // QoS-0 duplicate / out-of-order guard: samples must be strictly newer
  if (lastSample && msg.t <= lastSample.t) return;

  const s: StoredSmokeSample = {
    t: msg.t,
    pm1_0: msg.pm1_0,
    pm2_5: msg.pm2_5,
    pm10: msg.pm10,
    cnt0_3: msg.cnt0_3,
    cnt0_5: msg.cnt0_5,
    cnt1_0: msg.cnt1_0,
    cnt2_5: msg.cnt2_5,
    cnt5_0: msg.cnt5_0,
    cnt10: msg.cnt10,
    temp_c: msg.temp_c,
    pressure_hpa: msg.pressure_hpa,
    humidity_pct: msg.humidity_pct,
    gas_kohm: msg.gas_kohm,
    altitude_m: msg.altitude_m,
    rssi: msg.rssi,
    receivedAt: performance.now(),
  };

  ring.push(s);
  if (ring.length > MAX_SAMPLES) ring.shift();
  lastSample = s;

  if (s.gas_kohm !== undefined) {
    gasEmaMax = Math.max(gasEmaMax * DECAY, s.gas_kohm);
    gasEmaMin = Math.min(gasEmaMin * DECAY_UP, s.gas_kohm);
  }

  // Coalesce notifications into one rAF tick
  if (!rafScheduled) {
    rafScheduled = true;
    requestAnimationFrame(() => {
      rafScheduled = false;
      const latest = lastSample;
      if (!latest) return;
      for (const cb of subs) {
        try { cb(latest); } catch { /* ignore */ }
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Read API
// ---------------------------------------------------------------------------

export function getLatestSmokeSample(): StoredSmokeSample | null {
  return lastSample;
}

export function getSmokeSamples(): readonly StoredSmokeSample[] {
  return ring;
}

/** Gas Y-axis ceiling: EMA peak-hold with headroom, floored at 1 kΩ. */
export function getGasAutoScaleMax(): number {
  const m = gasEmaMax * 1.15;
  return m < GAS_SCALE_FLOOR ? GAS_SCALE_FLOOR : m;
}

/** Gas Y-axis floor: EMA min-hold with headroom (0 before any gas data). */
export function getGasAutoScaleMin(): number {
  if (!Number.isFinite(gasEmaMin)) return 0;
  return Math.max(0, gasEmaMin * 0.7);
}

// ---------------------------------------------------------------------------
// Subscribe
// ---------------------------------------------------------------------------

export function onSmokeSample(cb: SmokeSub): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}
