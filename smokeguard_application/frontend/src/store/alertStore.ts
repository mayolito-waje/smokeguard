/**
 * Detection-alert store — module-level singleton for "smoking detected"
 * alerts (same pattern as csiStore/smokeStore). Alerts arrive over the
 * WebSocket, stack in a toast list, and auto-dismiss after a short delay.
 */

import type { DetectionLabel, WsAlert } from '../types';

// ---------------------------------------------------------------------------
// Toast state
// ---------------------------------------------------------------------------

export interface AlertToast {
  key: number;                 // unique key for the toast list (X-button dismiss)
  id: number;                  // detection event id in the SQLite store
  detectedAt: number;          // epoch seconds
  label: DetectionLabel;       // 'csi_only' | 'smoke_only' | 'fusion'
  pSensor: number | null;
}

// ---------------------------------------------------------------------------
// Alarm sound (public/alert_sound.mp3)
// ---------------------------------------------------------------------------

const ALARM_SRC = '/alert_sound.mp3';
let alarm: HTMLAudioElement | null = null;

function playAlarm(): void {
  try {
    if (!alarm) {
      alarm = new Audio(ALARM_SRC);
      alarm.preload = 'auto';
    }
    alarm.currentTime = 0;
    void alarm.play().catch(() => {
      // Autoplay can be blocked before any user gesture — the toast is the
      // primary notification, so silently ignore the audio failure.
    });
  } catch {
    /* audio unsupported — ignore */
  }
}

/** Toasts auto-dismiss after this many ms (X button is the primary dismiss). */
const AUTO_DISMISS_MS = 8_000;

/** Keep the stack bounded so a burst of alerts never floods the screen. */
export const MAX_TOASTS = 5;

const toasts: AlertToast[] = [];
let nextKey = 1;

// ---------------------------------------------------------------------------
// Subscribers
// ---------------------------------------------------------------------------

type AlertSub = () => void;

const subs = new Set<AlertSub>();

function notify(): void {
  for (const cb of subs) {
    try { cb(); } catch { /* ignore */ }
  }
}

// ---------------------------------------------------------------------------
// Push / remove (push called from WebSocket onmessage)
// ---------------------------------------------------------------------------

export function pushAlert(msg: WsAlert): void {
  const toast: AlertToast = {
    key: nextKey++,
    id: msg.id,
    detectedAt: msg.detected_at,
    label: msg.label ?? 'fusion',
    pSensor: msg.p_sensor,
  };
  toasts.push(toast);
  if (toasts.length > MAX_TOASTS) toasts.shift();
  notify();
  playAlarm();

  window.setTimeout(() => removeAlert(toast.key), AUTO_DISMISS_MS);
}

export function removeAlert(key: number): void {
  const idx = toasts.findIndex(a => a.key === key);
  if (idx === -1) return;
  toasts.splice(idx, 1);
  notify();
}

// ---------------------------------------------------------------------------
// Read API
// ---------------------------------------------------------------------------

export function getToasts(): readonly AlertToast[] {
  return toasts;
}

// ---------------------------------------------------------------------------
// Subscribe
// ---------------------------------------------------------------------------

export function onAlerts(cb: AlertSub): () => void {
  subs.add(cb);
  return () => { subs.delete(cb); };
}
