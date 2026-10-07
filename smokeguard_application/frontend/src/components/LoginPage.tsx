/**
 * Login page — the only auth surface in the UI.  Admins sign in with the
 * backend credentials; there is deliberately no password-reset flow here
 * (reset is API-only, see POST /api/auth/reset-password).
 */

import { useState } from 'react';
import type { FormEvent } from 'react';
import type { Session } from '../session';

interface Props {
  onLogin: (session: Session) => void;
}

export default function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (res.status === 401) {
        setError('Invalid username or password');
        return;
      }
      if (!res.ok) {
        setError(`Login failed (HTTP ${res.status})`);
        return;
      }
      const session = (await res.json()) as Session;
      onLogin(session);
    } catch {
      setError('Backend unreachable');
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={handleSubmit}>
        <img className="login-logo" src="/smokeguard_logo.png" alt="SmokeGuard" />
        <div className="login-title">Admin sign in</div>

        <label className="login-field">
          <span>Username</span>
          <input
            className="login-input"
            type="text"
            autoComplete="username"
            value={username}
            onChange={e => setUsername(e.target.value)}
            autoFocus
          />
        </label>

        <label className="login-field">
          <span>Password</span>
          <input
            className="login-input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
          />
        </label>

        {error && <div className="login-error">{error}</div>}

        <button
          className="btn-login"
          type="submit"
          disabled={pending || username === '' || password === ''}
        >
          {pending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
