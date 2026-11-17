/**
 * Stacking toast notifications for detected smoking activity.
 *
 * Fixed top-right; natural flex-column order means the newest toast stacks
 * BELOW the previous ones. Each toast has an × close button (auto-dismiss
 * after 8 s in alertStore).
 */

import { useEffect, useState } from 'react';
import { getToasts, onAlerts, removeAlert, type AlertToast } from '../store/alertStore';
import type { DetectionLabel } from '../types';

const TOAST_META: Record<DetectionLabel, { title: string; className: string }> = {
  fusion: { title: 'Smoking activity detected', className: '' },
  smoke_only: {
    title: 'Possible smoking activity (not backed by CSI)',
    className: ' toast-possible',
  },
  csi_only: {
    title: 'Possible smoking activity (not backed by air-quality data)',
    className: ' toast-csi-only',
  },
};

export default function ToastStack() {
  const [toasts, setToasts] = useState<AlertToast[]>(() => [...getToasts()]);

  useEffect(() => onAlerts(() => setToasts([...getToasts()])), []);

  if (toasts.length === 0) return null;

  return (
    <div className="toast-stack">
      {toasts.map(a => (
        <div
          key={a.key}
          className={`toast toast-in${TOAST_META[a.label].className}`}
          role="status"
        >
          <span className="toast-icon" aria-hidden="true" />
          <div className="toast-body">
            <div className="toast-title">{TOAST_META[a.label].title}</div>
            <div className="toast-time">
              {new Date(a.detectedAt * 1000).toLocaleTimeString()} · event #{a.id}
            </div>
          </div>
          <button
            className="toast-close"
            aria-label="Dismiss notification"
            onClick={() => removeAlert(a.key)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
