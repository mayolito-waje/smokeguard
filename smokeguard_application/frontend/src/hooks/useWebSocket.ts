/**
 * WebSocket hook — connect, reconnect, heartbeat, dispatch to store.
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import type { WsMessage } from '../types';
import {
  pushFrame,
  startMetricsPoll,
  stopMetricsPoll,
} from '../store/csiStore';

// ---------------------------------------------------------------------------
// URL resolution
// ---------------------------------------------------------------------------

function wsUrl(): string {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
  const api = import.meta.env.VITE_API_BASE;
  if (api) return api.replace(/^http/, 'ws') + '/ws';
  return '/ws';
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_BACKOFF = 15_000;
const HEARTBEAT_MS = 25_000;

export type ConnState = 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface UseWsResult {
  state: ConnState;
  reconnect: () => void;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useWebSocket(): UseWsResult {
  const [state, setState] = useState<ConnState>('connecting');
  const wsRef = useRef<WebSocket | null>(null);
  const backoffRef = useRef(1000);
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTO = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const clearTimers = useCallback(() => {
    if (heartbeatRef.current) { clearInterval(heartbeatRef.current); heartbeatRef.current = null; }
    if (reconnectTO.current) { clearTimeout(reconnectTO.current); reconnectTO.current = null; }
  }, []);

  const connect = useCallback(() => {
    clearTimers();
    const ws = new WebSocket(wsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      backoffRef.current = 1000;
      setState('open');
      startMetricsPoll(200);

      heartbeatRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('{"type":"ping"}');
      }, HEARTBEAT_MS);
    };

    ws.onmessage = (ev: MessageEvent<string>) => {
      if (!mountedRef.current) return;
      try {
        const msg: WsMessage = JSON.parse(ev.data);
        if (msg.type === 'csi') pushFrame(msg);
      } catch { /* ignore malformed */ }
    };

    ws.onclose = (ev: CloseEvent) => {
      if (!mountedRef.current) return;
      clearTimers();
      stopMetricsPoll();

      if (!ev.wasClean) {
        setState('reconnecting');
        const d = backoffRef.current;
        backoffRef.current = Math.min(d * 2, MAX_BACKOFF);
        reconnectTO.current = setTimeout(connect, d);
      } else {
        setState('closed');
      }
    };

    ws.onerror = () => { /* onclose fires next */ };
  }, [clearTimers]);

  const reconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    clearTimers();
    backoffRef.current = 1000;
    setState('connecting');
    connect();
  }, [connect, clearTimers]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimers();
      stopMetricsPoll();
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [connect, clearTimers]);

  return { state, reconnect };
}
