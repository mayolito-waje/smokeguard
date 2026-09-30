/**
 * Detection history — pick a month/year, list smoking-detection events
 * from the backend SQLite store, open one for the detail view.
 */

import { useEffect, useState } from 'react';
import type { DetectionEventsResponse } from '../types';

interface Props {
  onOpen: (id: number) => void;
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export default function HistoryView({ onOpen }: Props) {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1); // 1-based
  const [data, setData] = useState<DetectionEventsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Year range covers the sample replay data (captured Sep 2026)
  const years: number[] = [];
  for (let y = now.getFullYear() - 3; y <= now.getFullYear() + 1; y++) years.push(y);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/api/detection/events?year=${year}&month=${month}`)
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
  }, [year, month]);

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
        </div>
      </div>

      {loading && <div className="history-status">Loading…</div>}
      {error && <div className="history-status history-error">{error}</div>}

      {!loading && !error && data && data.count === 0 && (
        <div className="history-status">
          No detections recorded for {MONTH_NAMES[month - 1]} {year}
        </div>
      )}

      {!loading && !error && data && data.count > 0 && (
        <div className="history-list">
          {data.events.map(ev => (
            <div key={ev.id} className="event-row">
              <div className="event-body">
                <div className="event-time">
                  {new Date(ev.detected_at * 1000).toLocaleString()}
                </div>
                <div className="event-meta">
                  #{ev.id} · {ev.csi_frames} CSI frames · {ev.smoke_samples} smoke samples
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
