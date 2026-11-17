/**
 * Sidebar card for the smoking-activity detection mechanism.
 *
 * "Enabled" is persisted in the backend's SQLite store and defaults to off.
 * "Capture static background" stores the CSI model's (52,) static baseline —
 * capture it while the room is quiet (no smoking / movement).
 */

import { useEffect, useState } from 'react';
import { authFetch } from '../session';
import type { BaselineStatus, DetectionConfig } from '../types';

async function readDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    return typeof body?.detail === 'string' ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

export default function DetectionToggle() {
  const [enabled, setEnabled] = useState(false);
  const [csiOnly, setCsiOnly] = useState(false);
  const [baseline, setBaseline] = useState<BaselineStatus | null>(null);
  const [baselineBusy, setBaselineBusy] = useState(false);
  const [modelsReady, setModelsReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyConfig = (cfg: DetectionConfig) => {
    setEnabled(cfg.enabled);
    setCsiOnly(cfg.csi_only_enabled ?? false);
    setBaseline(cfg.baseline_status);
    setModelsReady(cfg.models_ready);
  };

  // Load the persisted config on mount (idempotent — StrictMode-safe)
  useEffect(() => {
    let cancelled = false;
    fetch('/api/detection/config')
      .then(r => r.json())
      .then((cfg: DetectionConfig) => {
        if (!cancelled) applyConfig(cfg);
      })
      .catch(() => { if (!cancelled) setError('Detection backend unreachable'); });
    return () => { cancelled = true; };
  }, []);

  const updateConfig = async (patch: Partial<Pick<DetectionConfig, 'enabled' | 'csi_only_enabled'>>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await authFetch('/api/detection/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      if (res.ok) {
        applyConfig(await res.json());
      } else {
        setError('Failed to update detection state');
      }
    } catch {
      setError('Detection backend unreachable');
    } finally {
      setBusy(false);
    }
  };

  const toggle = () => updateConfig({ enabled: !enabled });
  const toggleCsiOnly = () => updateConfig({ csi_only_enabled: !csiOnly });

  const captureBaseline = async () => {
    if (baselineBusy) return;
    setBaselineBusy(true);
    setError(null);
    try {
      const res = await authFetch('/api/detection/baseline', { method: 'POST' });
      if (res.ok) {
        const info: BaselineStatus = await res.json();
        setBaseline(info);
        setModelsReady(true);
      } else {
        setError(await readDetail(res, 'Static background capture failed'));
      }
    } catch {
      setError('Detection backend unreachable');
    } finally {
      setBaselineBusy(false);
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

      <label className="detection-toggle detection-toggle-experimental">
        <input
          type="checkbox"
          checked={csiOnly}
          disabled={busy}
          onChange={toggleCsiOnly}
        />
        <span>CSI-only detection (experimental)</span>
      </label>

      <button
        className="btn-simulate"
        disabled={baselineBusy}
        onClick={captureBaseline}
      >
        {baselineBusy ? 'Capturing…' : 'Capture static background'}
      </button>

      <p className="baseline-status">
        {baseline
          ? `Static background: ${new Date(baseline.captured_at * 1000).toLocaleTimeString()} · ${baseline.packet_count} packets`
          : 'Static background: not captured — CSI model stays offline'}
      </p>

      <p className="detection-note">
        Checks the SVM + ResNet-LSTM pipeline every 0.2 s when enabled (30 s
        cooldown between alerts). CSI + smoke sensor = smoking activity detected;
        sensor-only hits are labeled possible activity. CSI-only detection
        (weakest class, off by default) alerts on a smoking-like CSI gesture
        even when the air-quality sensor stays below its threshold — it needs
        the static background and scans once per second while the sensor is
        quiet. Capture the static background in a quiet room before enabling.
        {!modelsReady && ' Models are still loading…'}
      </p>

      {error && <div className="detection-error">{error}</div>}
    </div>
  );
}
