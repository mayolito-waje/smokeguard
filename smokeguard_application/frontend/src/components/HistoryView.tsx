/**
 * Detection history — pick a month/year, list smoking-detection events
 * from the backend SQLite store, open one for the detail view.
 */

import { useEffect, useState } from 'react';
import type { DetectionEventsResponse, DetectionLabel } from '../types';

interface Props {
  onOpen: (id: number) => void;
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function fmtProb(p: number | null): string {
  return p === null || p === undefined ? '—' : p.toFixed(2);
}

const LABEL_TEXT: Record<DetectionLabel, string> = {
  fusion: 'CSI + smoke sensor',
  smoke_only: 'Sensor only',
  csi_only: 'CSI only',
};

const EMPTY_TEXT: Record<DetectionLabel, string> = {
  fusion: 'CSI + smoke sensor',
  smoke_only: 'sensor-only',
  csi_only: 'CSI-only',
};

export default function HistoryView({ onOpen }: Props) {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1); // 1-based
  const [label, setLabel] = useState<'' | DetectionLabel>('');
  const [data, setData] = useState<DetectionEventsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  // Year range covers the sample replay data (captured Sep 2026)
  const years: number[] = [];
  for (let y = now.getFullYear() - 3; y <= now.getFullYear() + 1; y++) years.push(y);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const labelParam = label ? `&label=${label}` : '';
    fetch(`/api/detection/events?year=${year}&month=${month}${labelParam}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((res: DetectionEventsResponse) => {
        if (!cancelled) setData(res);
      })
      .catch(() => {
        if (!cancelled) setError('Failed to load detection history');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [year, month, label]);

  async function downloadReport(): Promise<void> {
    setReportLoading(true);
    setReportError(null);
    try {
      const res = await fetch(
        `/api/detection/report?year=${year}&month=${month}`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `smokeguard-detection-report-${year}-${String(month).padStart(2, '0')}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setReportError('Failed to generate the PDF report');
    } finally {
      setReportLoading(false);
    }
  }

  return (
    <div className="history-view">
      <div className="history-toolbar">
        <h2 className="history-title">Detection History</h2>
        <div className="history-selects">
          <select
            className="history-select"
            value={month}
            onChange={e => setMonth(Number(e.target.value))}
            aria-label="Month"
          >
            {MONTH_NAMES.map((name, i) => (
              <option key={name} value={i + 1}>{name}</option>
            ))}
          </select>
          <select
            className="history-select"
            value={year}
            onChange={e => setYear(Number(e.target.value))}
            aria-label="Year"
          >
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          <select
            className="history-select"
            value={label}
            onChange={e => setLabel(e.target.value as '' | DetectionLabel)}
            aria-label="Detection class"
          >
            <option value="">All detections</option>
            <option value="fusion">CSI + smoke sensor (highest probability)</option>
            <option value="smoke_only">Sensor only (possible)</option>
            <option value="csi_only">CSI only (experimental)</option>
          </select>
          <button
            className="btn-report"
            onClick={downloadReport}
            disabled={reportLoading}
            title="Downloads the full monthly report (all detection classes)"
          >
            {reportLoading ? 'Preparing PDF…' : 'Download PDF report'}
          </button>
        </div>
      </div>

      {loading && <div className="history-status">Loading…</div>}
      {error && <div className="history-status history-error">{error}</div>}
      {reportError && (
        <div className="history-status history-error">{reportError}</div>
      )}

      {!loading && !error && data && data.count === 0 && (
        <div className="history-status">
          No {label ? `${EMPTY_TEXT[label]} ` : ''}
          detections recorded for {MONTH_NAMES[month - 1]} {year}
        </div>
      )}

      {!loading && !error && data && data.count > 0 && (
        <div className="history-list">
          {data.events.map(ev => (
            <div key={ev.id} className="event-row">
              <div className="event-body">
                <div className="event-time">
                  {new Date(ev.detected_at * 1000).toLocaleString()}
                  <span className={`event-label label-${ev.label}`}>
                    {LABEL_TEXT[ev.label]}
                  </span>
                </div>
                <div className="event-meta">
                  #{ev.id} · {ev.csi_frames} CSI frames · {ev.smoke_samples} smoke samples
                  {' '}· CSI {fmtProb(ev.p_csi)} · Sensor {fmtProb(ev.p_sensor)}
                  {' '}· Score {fmtProb(ev.fused_score)}
                </div>
              </div>
              <button className="event-open" onClick={() => onOpen(ev.id)}>
                View →
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
