/**
 * Auth gate — sits above App so the dashboard (and its WebSocket) only
 * mounts once a valid, non-expired session exists.  The UI supports only
 * login and logout; password resets are API-only by design.
 */

import { useEffect, useState } from 'react';
import App from '../App';
import LoginPage from './LoginPage';
import { clearSession, isSessionValid, saveSession } from '../session';
import type { Session } from '../session';
import { applyTheme, resolveTheme } from '../theme';

export default function AuthGate() {
  const [authed, setAuthed] = useState(() => isSessionValid());

  // Theme the login page too (App's effect only runs once it mounts)
  useEffect(() => {
    applyTheme(resolveTheme());
  }, []);

  const handleLogin = (session: Session) => {
    saveSession(session);
    setAuthed(true);
  };

  const handleLogout = () => {
    clearSession();
    setAuthed(false);
  };

  return authed
    ? <App onLogout={handleLogout} />
    : <LoginPage onLogin={handleLogin} />;
}
