/**
 * Header badge + refresh control for the backend's internet/NTP probe.
 *
 * `internet_ntp_state` is the backend's own connectivity check (can this
 * machine reach an NTP server); the firmware's `ntp_synced` flag is a
 * separate receiver-clock indicator and is shown only in the tooltip.
 */

import { useState } from 'react';
import type {
  InternetNtpState,
  NtpStatusResponse,
  WsStatus,
} from '../types';
import { authFetch } from '../session';

interface NtpBadgeProps {
  status: WsStatus | null;
}

interface NtpView {
  state: InternetNtpState;
  server: string | null;
  offsetS: number | null;
  rttS: number | null;
  checkedAt: number | null;
  error: string | null;
}

const STATE_LABEL: Record<InternetNtpState, string> = {
  online: 'NTP Active',
  offline: 'NTP Connection Error',
  checking: 'NTP Checking…',
  unknown: 'NTP Unknown',
};

const STATE_CLASS: Record<InternetNtpState, string> = {
  online: 'badge-ok',
  offline: 'badge-err',
  checking: 'badge-warn',
  unknown: 'badge-warn',
};

function viewFromWs(status: WsStatus | null): NtpView {
  return {
    state: status?.internet_ntp_state ?? 'unknown',
    server: status?.internet_ntp_server ?? null,
    offsetS: status?.internet_ntp_offset_s ?? null,
    rttS: status?.internet_ntp_rtt_s ?? null,
    checkedAt: status?.internet_ntp_last_checked_at ?? null,
    error: status?.internet_ntp_error ?? null,
  };
}

function viewFromResponse(res: NtpStatusResponse): NtpView {
  return {
    state: res.internet_ntp_state,
    server: res.internet_ntp_server,
    offsetS: res.internet_ntp_offset_s,
    rttS: res.internet_ntp_rtt_s,
    checkedAt: res.internet_ntp_last_checked_at,
    error: res.internet_ntp_error,
  };
}

function formatCheckedAt(ts: number | null): string {
  return ts === null ? 'never' : new Date(ts * 1000).toLocaleString();
}

function formatMs(value: number | null): string {
  return value === null ? 'n/a' : `${(value * 1000).toFixed(1)} ms`;
}

export default function NtpBadge({ status }: NtpBadgeProps) {
  const [result, setResult] = useState<NtpStatusResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);

  const wsView = viewFromWs(status);
  const postView = result ? viewFromResponse(result) : null;
  // Prefer the fresh POST result until a newer WS status arrives.
  const view =
    postView && (postView.checkedAt ?? 0) >= (wsView.checkedAt ?? 0)
      ? postView
      : wsView;

  const state: InternetNtpState = busy ? 'checking' : view.state;
  const label = busy ? 'NTP Checking…' : STATE_LABEL[state];
  const cls = STATE_CLASS[state];

  const receiverClock =
    status?.ntp_synced === true ? 'synced'
      : status?.ntp_synced === false ? 'not synced'
        : 'unknown';

  const tooltip = [
    `Internet NTP: ${state}`,
    `Server: ${view.server ?? 'n/a'}`,
    `Last checked: ${formatCheckedAt(view.checkedAt)}`,
    `Clock offset (server − host): ${formatMs(view.offsetS)}`,
    `Round trip: ${formatMs(view.rttS)}`,
    `Receiver clock: ${receiverClock}`,
    `Timestamps corrected: ${status?.timestamps_corrected ?? 0}`,
    view.error ? `Error: ${view.error}` : '',
    requestError ? `Refresh error: ${requestError}` : '',
  ].filter(Boolean).join('\n');

  async function refresh() {
    setBusy(true);
    setRequestError(null);
    try {
      const res = await authFetch('/api/ntp/check', { method: 'POST' });
      if (!res.ok) {
        setRequestError(`HTTP ${res.status}`);
        return;
      }
      setResult(await res.json() as NtpStatusResponse);
    } catch (err) {
      setRequestError(err instanceof Error ? err.message : 'request failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <span className={`badge ntp-badge ${cls}`} title={tooltip} role="status">
        <span className="dot" />{label}
      </span>
      <button
        className="btn-ntp-refresh"
        onClick={refresh}
        disabled={busy}
        title="Re-check internet/NTP connectivity"
        aria-label="Re-check internet/NTP connectivity"
      >
        <span className={busy ? 'ntp-spin' : undefined}>↻</span>
      </button>
    </>
  );
}
