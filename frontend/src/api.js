const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export async function apiGet(path, params = {}) {
  const url = new URL(`${API_BASE}${path}`);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, v);
  });
  const resp = await fetch(url.toString());
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(err.detail || 'Request failed');
  }
  return resp.json();
}

export async function apiPost(path, body = null, params = {}) {
  const url = new URL(`${API_BASE}${path}`);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, v);
  });
  const resp = await fetch(url.toString(), {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(err.detail || 'Request failed');
  }
  return resp.json();
}

export async function apiPatch(path, body = null, params = {}) {
  const url = new URL(`${API_BASE}${path}`);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, v);
  });
  const resp = await fetch(url.toString(), {
    method: 'PATCH',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(err.detail || 'Request failed');
  }
  return resp.json();
}

export async function apiDelete(path, params = {}) {
  const url = new URL(`${API_BASE}${path}`);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, v);
  });
  const resp = await fetch(url.toString(), { method: 'DELETE' });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(err.detail || 'Request failed');
  }
  return resp.json();
}

// --- Strava token management ---

const TOKEN_KEY = 'pace_prophet_strava';

export function getStoredToken() {
  try {
    const raw = localStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    // Check expiry (with 5 min buffer)
    if (data.expires_at && data.expires_at < Date.now() / 1000 + 300) {
      return { ...data, expired: true };
    }
    return data;
  } catch {
    return null;
  }
}

export function storeToken(data) {
  localStorage.setItem(TOKEN_KEY, JSON.stringify(data));
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export async function ensureValidToken() {
  const stored = getStoredToken();
  if (!stored) return null;

  if (stored.expired && stored.refresh_token) {
    try {
      const refreshed = await apiPost('/api/strava/refresh', null, {
        refresh_token: stored.refresh_token,
      });
      const updated = { ...stored, ...refreshed, expired: false };
      storeToken(updated);
      return updated;
    } catch {
      clearToken();
      return null;
    }
  }
  return stored;
}