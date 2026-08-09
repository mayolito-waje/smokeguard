/**
 * CSI Data Store — module-level singleton for the 100 Hz data path.
 *
 * React never re-renders on CSI frames. Canvas pulls via rAF.
 * Only throttled metrics trigger React updates (~5 Hz).
 */

import type { CsiFrame, MetricsSnapshot, SubcarrierMetadata } from '../types';

// ---------------------------------------------------------------------------
// Stored frame
// ---------------------------------------------------------------------------

export interface StoredFrame {
  seq: number;
  t: number;
  rssi: number;
  noiseFloor: number;
  subcarrierCount: number;
  amp: Float32Array;
  metadata: SubcarrierMetadata | null;
}

// ---------------------------------------------------------------------------
// Ring buffer
// ---------------------------------------------------------------------------

const MAX_FRAMES = 600;
const ring: StoredFrame[] = [];

// ---------------------------------------------------------------------------
// Metrics
// ---------------------------------------------------------------------------

let totalFrames = 0;
let pktTimestamps: number[] = [];
let currentMetrics: MetricsSnapshot = {
  rssi: 0, noiseFloor: 0, packetCount: 0,
  packetsPerSecond: 0, seq: 0,
};

// ---------------------------------------------------------------------------
// Auto-scale
// ---------------------------------------------------------------------------

let rollingMaxAmp = 1.0;
const EMA_ALPHA = 0.02;

// ---------------------------------------------------------------------------
// Subscribers
// ---------------------------------------------------------------------------

type FrameSub = () => void;
type MetricsSub = (m: MetricsSnapshot) => void;

const frameSubs = new Set<FrameSub>();
const metricsSubs = new Set<MetricsSub>();

let rafScheduled = false;

// ---------------------------------------------------------------------------
// Compute amplitude from I/Q
// ---------------------------------------------------------------------------

function computeAmp(i: number[], q: number[]): Float32Array {
  const n = Math.min(i.length, q.length);
  const out = new Float32Array(n);
  for (let j = 0; j < n; j++) {
    const v = Math.sqrt(i[j] * i[j] + q[j] * q[j]);
    out[j] = v;
    if (v > rollingMaxAmp) {
      rollingMaxAmp = rollingMaxAmp * (1 - EMA_ALPHA) + v * EMA_ALPHA;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Push frame (called from WebSocket onmessage)
// ---------------------------------------------------------------------------

export function pushFrame(msg: CsiFrame): void {
  const amp = computeAmp(msg.i, msg.q);

  const stored: StoredFrame = {
    seq: msg.seq,
    t: msg.t,
    rssi: msg.rssi,
    noiseFloor: msg.noise_floor,
    subcarrierCount: msg.subcarrier_count,
    amp,
    metadata: msg.metadata,
  };

  ring.push(stored);
  if (ring.length > MAX_FRAMES) ring.shift();

  totalFrames++;

  // Packet rate (1-second window)
  const now = performance.now();
  pktTimestamps.push(now);
  while (pktTimestamps.length && pktTimestamps[0] < now - 1000) {
    pktTimestamps.shift();
  }

  currentMetrics = {
    rssi: msg.rssi,
    noiseFloor: msg.noise_floor,
    packetCount: totalFrames,
    packetsPerSecond: pktTimestamps.length,
    seq: msg.seq,
  };

  // Coalesce frame notifications into one rAF tick
  if (!rafScheduled) {
    rafScheduled = true;
    requestAnimationFrame(() => {
      rafScheduled = false;
      for (const cb of frameSubs) {
        try { cb(); } catch { /* ignore */ }
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Read API
// ---------------------------------------------------------------------------

export function getLatestFrame(): StoredFrame | null {
  return ring.length > 0 ? ring[ring.length - 1] : null;
}

export function getAutoScaleMax(): number {
  return Math.max(rollingMaxAmp * 1.15, 1.0);
}

export function getMetrics(): MetricsSnapshot {
  return currentMetrics;
}

// ---------------------------------------------------------------------------
// Subscribe
// ---------------------------------------------------------------------------

export function onFrame(cb: FrameSub): () => void {
  frameSubs.add(cb);
  return () => { frameSubs.delete(cb); };
}

export function onMetrics(cb: MetricsSub): () => void {
  metricsSubs.add(cb);
  return () => { metricsSubs.delete(cb); };
}

// ---------------------------------------------------------------------------
// Metrics polling (for React)
// ---------------------------------------------------------------------------

let metricsTimer: ReturnType<typeof setInterval> | null = null;

export function startMetricsPoll(ms = 200): void {
  if (metricsTimer) return;
  metricsTimer = setInterval(() => {
    const m = currentMetrics;
    for (const cb of metricsSubs) {
      try { cb(m); } catch { /* ignore */ }
    }
  }, ms);
}

export function stopMetricsPoll(): void {
  if (metricsTimer) { clearInterval(metricsTimer); metricsTimer = null; }
}
