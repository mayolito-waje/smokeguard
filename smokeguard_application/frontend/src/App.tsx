import { useState, useEffect, useCallback } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import { useSmokingFacts } from './hooks/useSmokingFacts';
import { onMetrics, getMetrics } from './store/csiStore';
import type { MetricsSnapshot } from './types';
import type { Theme } from './renderers/drawStripChart';
import { applyTheme, resolveTheme } from './theme';
import StripChart from './components/WaterfallChart';
import VocPanel from './components/VocPanel';
import AirQualityPanel from './components/AirQualityPanel';
import DetectionToggle from './components/DetectionToggle';
import HistoryView from './components/HistoryView';
import DetectionDetailView from './components/DetectionDetailView';
import ToastStack from './components/ToastStack';
import './App.css';

// ---------------------------------------------------------------------------
// View switching (no router — the project has zero runtime deps beyond React)
// ---------------------------------------------------------------------------

type View = 'live' | 'history' | 'detail';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function wsStatusClass(state: string): string {
  if (state === 'open') return 'badge-ok';
  if (state === 'reconnecting' || state === 'connecting') return 'badge-warn';
  return 'badge-err';
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

interface AppProps {
  onLogout: () => void;
}

export default function App({ onLogout }: AppProps) {
  const { state, reconnect } = useWebSocket();
  const [metrics, setMetrics] = useState<MetricsSnapshot>(getMetrics());
  const [theme, setTheme] = useState<Theme>(resolveTheme);
  const [factIdx, setFactIdx] = useState(0);
  const [view, setView] = useState<View>('live');
  const [detailId, setDetailId] = useState<number | null>(null);

  const facts = useSmokingFacts();

  // ---- Apply theme attribute on <html> ----
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // ---- Metrics subscription (throttled in store) ----
  useEffect(() => {
    const unsub = onMetrics(setMetrics);
    return unsub;
  }, []);

  // ---- Trivia rotation (8 s) ----
  useEffect(() => {
    if (facts.length === 0) return;
    setFactIdx(0);
    const id = setInterval(() => setFactIdx(i => (i + 1) % facts.length), 8000);
    return () => clearInterval(id);
  }, [facts]);

  // ---- Theme toggle ----
  const toggleTheme = useCallback(() => {
    setTheme(t => t === 'dark' ? 'light' : 'dark');
  }, []);

  // ---- Derived labels ----
  const wsLabel = state === 'open' ? 'Connected'
    : state === 'reconnecting' ? 'Reconnecting…'
    : state === 'connecting' ? 'Connecting…'
    : 'Disconnected';

  const currentFact = facts.length > 0 ? facts[factIdx] : '';

  // ---- View navigation ----
  const openEvent = useCallback((eventId: number) => {
    setDetailId(eventId);
    setView('detail');
  }, []);

  return (
    <div className="app">
      {/* ================================================================ */}
      {/* Header                                                          */}
      {/* ================================================================ */}
      <header className="header">
        <div className="header-brand">
          <img className="brand-logo" src="/smokeguard_logo.png" alt="SmokeGuard" />
          <span className="brand-sub">CSI Monitor</span>
        </div>

        <nav className="tabs" aria-label="Views">
          <button
            className={`tab ${view === 'live' ? 'active' : ''}`}
            onClick={() => setView('live')}
          >
            Live
          </button>
          <button
            className={`tab ${view === 'history' || view === 'detail' ? 'active' : ''}`}
            onClick={() => setView('history')}
          >
            History
          </button>
        </nav>

        <div className="header-right">
          <span className={`badge ${wsStatusClass(state)}`}>
            <span className="dot" />{wsLabel}
          </span>
          {state !== 'open' && (
            <button className="btn-reconnect" onClick={reconnect}>Reconnect</button>
          )}
          <button
            className="theme-toggle"
            onClick={toggleTheme}
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            aria-label="Toggle colour theme"
          >
            {theme === 'dark' ? '☀️' : '🌙'}
          </button>
          <button className="btn-logout" onClick={onLogout} title="End the admin session">
            Log out
          </button>
        </div>
      </header>

      {/* ================================================================ */}
      {/* Body (sidebar + main chart)                                     */}
      {/* ================================================================ */}
      {view === 'live' && (
      <div className="body">
        {/* ---- Sidebar ---- */}
        <aside className="sidebar">
          {/* Live metrics */}
          <div className="card card-stats">
            <div className="card-header">Live Metrics</div>
            <div className="stat-row">
              <span className="stat-label">RSSI</span>
              <span className="stat-value">{metrics.rssi} <small>dBm</small></span>
            </div>
            <div className="stat-row">
              <span className="stat-label">Noise Floor</span>
              <span className="stat-value">{metrics.noiseFloor} <small>dBm</small></span>
            </div>
            <div className="stat-row">
              <span className="stat-label">Packet Rate</span>
              <span className="stat-value">{metrics.packetsPerSecond} <small>pkts/s</small></span>
            </div>
            <div className="stat-row">
              <span className="stat-label">Total Received</span>
              <span className="stat-value">{metrics.packetCount.toLocaleString()}</span>
            </div>
            <div className="stat-row">
              <span className="stat-label">Sequence #</span>
              <span className="stat-value">{metrics.seq}</span>
            </div>
          </div>

          {/* Trivia */}
          <div className="card card-trivia">
            <div className="card-header">
              &#x1F4A1; Did You Know?
              <span className="trivia-source">{facts.length > 30 ? 'Wikipedia + curated' : 'Curated facts'}</span>
            </div>
            <p className="trivia-text" key={factIdx}>{currentFact}</p>
          </div>

          {/* Smoking detection toggle */}
          <DetectionToggle />
        </aside>

        {/* ---- Main chart + bottom sensor panels ---- */}
        <main className="main">
          <StripChart theme={theme} />
          <div className="bottom-panels">
            <VocPanel theme={theme} />
            <AirQualityPanel />
          </div>
        </main>
      </div>
      )}

      {/* ================================================================ */}
      {/* History / Detail views                                           */}
      {/* ================================================================ */}
      {view === 'history' && (
        <HistoryView onOpen={openEvent} />
      )}
      {view === 'detail' && detailId !== null && (
        <DetectionDetailView
          key={detailId}
          id={detailId}
          theme={theme}
          onBack={() => setView('history')}
        />
      )}

      {/* Stacking alert toasts (all views) */}
      <ToastStack />

      {/* ================================================================ */}
      {/* Footer                                                          */}
      {/* ================================================================ */}
      <footer className="footer">
        <span>SmokeGuard v0.1</span>
        <span className="footer-sep">·</span>
        <span>Wi-Fi CSI Sensing</span>
        <span className="footer-sep">·</span>
        <span>Developed by Mayo, Aaron, and Maui</span>
        <span className="footer-right">Channel 11 · HT40 · 64 subcarriers</span>
      </footer>
    </div>
  );
}
