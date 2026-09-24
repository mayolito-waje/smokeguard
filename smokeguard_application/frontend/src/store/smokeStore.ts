/**
 * Air-quality (PMS5003) data store — module-level singleton for the 1 Hz
 * smoke path. Same pattern as csiStore: no React re-render per sample;
 * the canvas pulls via rAF, stat tiles update via a coalesced subscription.
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

/** µg/m³ floor for the Y axis. */
const SCALE_FLOOR = 10;

const ring: StoredSmokeSample[] = [];
let lastSample: StoredSmokeSample | null = null;

// ---------------------------------------------------------------------------
// Auto-scale (per-series peak-hold)
// ---------------------------------------------------------------------------

// Peak-hold with exponential decay: spikes are caught instantly and the
// scale decays back to baseline with τ ≈ 3.3 min. The renderer's stable-Y
// hysteresis removes residual tick vibration.
const DECAY = 0.995;
const emaMax = { pm1_0: SCALE_FLOOR, pm2_5: SCALE_FLOOR, pm10: SCALE_FLOOR };

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
    rssi: msg.rssi,
    receivedAt: performance.now(),
  };

  ring.push(s);
  if (ring.length > MAX_SAMPLES) ring.shift();
  lastSample = s;

  emaMax.pm1_0 = Math.max(emaMax.pm1_0 * DECAY, s.pm1_0);
  emaMax.pm2_5 = Math.max(emaMax.pm2_5 * DECAY, s.pm2_5);
  emaMax.pm10 = Math.max(emaMax.pm10 * DECAY, s.pm10);

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

export function getSmokeAutoScaleMax(): number {
  const m = Math.max(emaMax.pm1_0, emaMax.pm2_5, emaMax.pm10) * 1.15;
  return m < SCALE_FLOOR ? SCALE_FLOOR : m;
}

// ---------------------------------------------------------------------------
// Subscribe
// ---------------------------------------------------------------------------

export function onSmokeSample(cb: SmokeSub): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}
