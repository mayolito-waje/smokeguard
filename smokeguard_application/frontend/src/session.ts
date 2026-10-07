// ---------------------------------------------------------------------------
// Admin session — JWT + expiry persisted in localStorage
// ---------------------------------------------------------------------------

export interface Session {
  token: string;
  /** JWT expiry, UNIX epoch seconds (server-provided). */
  expires_at: number;
}

const STORAGE_KEY = 'smokeguard-token';

export function saveSession(session: Session): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch { /* localStorage unavailable — session lives for this tab only */ }
}

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Session>;
    if (typeof parsed.token !== 'string' || typeof parsed.expires_at !== 'number') {
      return null;
    }
    return { token: parsed.token, expires_at: parsed.expires_at };
  } catch {
    return null;
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch { /* noop */ }
}

export function isSessionValid(): boolean {
  const session = loadSession();
  return session !== null && session.expires_at * 1000 > Date.now();
}

/** fetch wrapper for token-required routes: attaches the admin bearer token
 *  and, on 401 (stale/expired session), clears it and reloads to the login
 *  page. Read-only routes can keep using plain fetch. */
export async function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const session = loadSession();
  const headers = new Headers(init.headers);
  if (session) headers.set('Authorization', `Bearer ${session.token}`);
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    clearSession();
    window.location.reload();
  }
  return res;
}
