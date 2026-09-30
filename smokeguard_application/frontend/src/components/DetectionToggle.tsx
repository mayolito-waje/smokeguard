/**
 * Sidebar card for the smoking-activity detection mechanism.
 *
 * "Enabled" is persisted in the backend's SQLite store and defaults to off.
 * "Simulate detection" fires the dummy detector once and works even while
 * the mechanism is disabled (it is a manual test override).
 */

import { useEffect, useState } from 'react';
import type { DetectionConfig } from '../types';

export default function DetectionToggle() {
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load the persisted config on mount (idempotent — StrictMode-safe)
  useEffect(() => {
    let cancelled = false;
    fetch('/api/detection/config')
      .then(r => r.json())
      .then((cfg: DetectionConfig) => { if (!cancelled) setEnabled(cfg.enabled); })
      .catch(() => { if (!cancelled) setError('Detection backend unreachable'); });
    return () => { cancelled = true; };
  }, []);

  const toggle = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch('/api/detection/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !enabled }),
      });
      if (res.ok) {
        const cfg: DetectionConfig = await res.json();
        setEnabled(cfg.enabled);
      } else {
        setError('Failed to update detection state');
      }
    } catch {
      setError('Detection backend unreachable');
    } finally {
      setBusy(false);
    }
  };

  const simulate = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch('/api/detection/simulate', { method: 'POST' });
      if (res.status === 409) {
        setError('No CSI data buffered yet');
      } else if (!res.ok) {
        setError('Simulate failed');
      }
    } catch {
      setError('Detection backend unreachable');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card detection-card">
      <div className="card-header">Smoking Detection</div>

      <label className="detection-toggle">
        <input
          type="checkbox"
          checked={enabled}
          disabled={busy}
          onChange={toggle}
        />
        <span>Enabled</span>
      </label>

      <button className="btn-simulate" disabled={busy} onClick={simulate}>
        Simulate detection
      </button>

      <p className="detection-note">
        Random dummy trigger every 30–120 s when enabled.
        Simulate and alert detected activity even when smoking detection is disabled.
      </p>

      {error && <div className="detection-error">{error}</div>}
    </div>
  );
}
