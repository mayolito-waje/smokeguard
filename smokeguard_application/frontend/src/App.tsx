import { useState, useEffect, useCallback } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import { useSmokingFacts } from './hooks/useSmokingFacts';
import { onMetrics, getMetrics } from './store/csiStore';
import type { MetricsSnapshot } from './types';
import type { Theme } from './renderers/drawStripChart';
import StripChart from './components/WaterfallChart';
import VocPanel from './components/VocPanel';
import AirQualityPanel from './components/AirQualityPanel';
import './App.css';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function wsStatusClass(state: string): string {
  if (state === 'open') return 'badge-ok';
  if (state === 'reconnecting' || state === 'connecting') return 'badge-warn';
  return 'badge-err';
}

function resolveTheme(): Theme {
  try {
    const saved = localStorage.getItem('smokeguard-theme');
    if (saved === 'light' || saved === 'dark') return saved;
  } catch { /* localStorage unavailable */ }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  const { state, reconnect } = useWebSocket();
  const [metrics, setMetrics] = useState<MetricsSnapshot>(getMetrics());
  const [theme, setTheme] = useState<Theme>(resolveTheme);
  const [factIdx, setFactIdx] = useState(0);

  const facts = useSmokingFacts();

  // ---- Apply theme attribute on <html> ----
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem('smokeguard-theme', theme); } catch { /* noop */ }
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

  return (
    <div className="app">
      {/* ================================================================ */}
      {/* Header                                                          */}
      {/* ================================================================ */}
      <header className="header">
        <div className="header-brand">
          <span className="brand-icon">&#x1F525;</span>
          <span className="brand-name">SmokeGuard</span>
          <span className="brand-sub">CSI Monitor</span>
        </div>

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
        </div>
      </header>

      {/* ================================================================ */}
      {/* Body (sidebar + main chart)                                     */}
      {/* ================================================================ */}
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
