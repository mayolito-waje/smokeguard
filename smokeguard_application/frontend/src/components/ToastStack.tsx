/**
 * Stacking toast notifications for detected smoking activity.
 *
 * Fixed top-right; natural flex-column order means the newest toast stacks
 * BELOW the previous ones. Each toast has an × close button (auto-dismiss
 * after 8 s in alertStore).
 */

import { useEffect, useState } from 'react';
import { getToasts, onAlerts, removeAlert, type AlertToast } from '../store/alertStore';

export default function ToastStack() {
  const [toasts, setToasts] = useState<AlertToast[]>(() => [...getToasts()]);

  useEffect(() => onAlerts(() => setToasts([...getToasts()])), []);

  if (toasts.length === 0) return null;

  return (
    <div className="toast-stack">
      {toasts.map(a => (
        <div key={a.key} className="toast toast-in" role="status">
          <span className="toast-icon" aria-hidden="true" />
          <div className="toast-body">
            <div className="toast-title">Smoking activity detected</div>
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
