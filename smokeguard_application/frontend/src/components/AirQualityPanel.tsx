/**
 * AirQualityPanel — bottom-right panel: PMS5003 particle data as numbers
 * only, no chart (mirrors real-world AQ monitors, where PM values are
 * headline tiles). Hero tiles for PM1.0/PM2.5/PM10, a per-0.1 L particle
 * count grid, and the sensor RSSI. Tiles update via the store's
 * rAF-coalesced subscription (~1 Hz) and dim when the sensor goes offline.
 */

import { useEffect, useState } from 'react';
import {
  getLatestSmokeSample,
  onSmokeSample,
  STALE_AFTER_MS,
  type StoredSmokeSample,
} from '../store/smokeStore';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtCount(v: number | undefined): string {
  return v === undefined ? '—' : v.toLocaleString('en-US');
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AirQualityPanel() {
  const [sample, setSample] = useState<StoredSmokeSample | null>(null);
  const [stale, setStale] = useState(false);
  const [ageSec, setAgeSec] = useState<number | null>(null);

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

  return (
    <section className={`aq-panel${stale ? ' stale' : ''}`}>
      <div className="aq-toolbar">
        <span className="aq-title">Air Quality</span>
        {stale && sample && <span className="aq-offline-badge">SENSOR OFFLINE</span>}
      </div>
      <div className="aq-body">
        <div className="aq-hero">
          <div className="aq-hero-tile">
            <span className="aq-hero-label">PM1.0</span>
            <span className="aq-hero-value">
              {sample ? sample.pm1_0.toLocaleString('en-US') : '—'}
              <small> µg/m³</small>
            </span>
          </div>
          <div className="aq-hero-tile">
            <span className="aq-hero-label">PM2.5</span>
            <span className="aq-hero-value">
              {sample ? sample.pm2_5.toLocaleString('en-US') : '—'}
              <small> µg/m³</small>
            </span>
          </div>
          <div className="aq-hero-tile">
            <span className="aq-hero-label">PM10</span>
            <span className="aq-hero-value">
              {sample ? sample.pm10.toLocaleString('en-US') : '—'}
              <small> µg/m³</small>
            </span>
          </div>
        </div>
        <div className="aq-section-label">Particles / 0.1 L</div>
        <div className="aq-stats-grid">
          <div className="aq-stat-cell">
            <span className="aq-stat-label">0.3 µm</span>
            <span className="aq-stat-value">{fmtCount(sample?.cnt0_3)}</span>
          </div>
          <div className="aq-stat-cell">
            <span className="aq-stat-label">0.5 µm</span>
            <span className="aq-stat-value">{fmtCount(sample?.cnt0_5)}</span>
          </div>
          <div className="aq-stat-cell">
            <span className="aq-stat-label">1.0 µm</span>
            <span className="aq-stat-value">{fmtCount(sample?.cnt1_0)}</span>
          </div>
          <div className="aq-stat-cell">
            <span className="aq-stat-label">2.5 µm</span>
            <span className="aq-stat-value">{fmtCount(sample?.cnt2_5)}</span>
          </div>
          <div className="aq-stat-cell">
            <span className="aq-stat-label">5.0 µm</span>
            <span className="aq-stat-value">{fmtCount(sample?.cnt5_0)}</span>
          </div>
          <div className="aq-stat-cell">
            <span className="aq-stat-label">10 µm</span>
            <span className="aq-stat-value">{fmtCount(sample?.cnt10)}</span>
          </div>
          <div className="aq-stat-cell">
            <span className="aq-stat-label">RSSI</span>
            <span className="aq-stat-value">{sample ? `${sample.rssi} dBm` : '—'}</span>
          </div>
        </div>
        <div className="aq-stale-note">
          {ageSec === null ? 'No data yet' : `Last sample ${ageSec}s ago`}
        </div>
      </div>
    </section>
  );
}
