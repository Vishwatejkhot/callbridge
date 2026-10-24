export const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export type Me = { email: string; role: 'staff' | 'owner'; clinic: { id: number; name: string; ai_enabled: boolean } };
export type Turn = { role: 'caller' | 'assistant'; text: string; at: string };
export type LiveCall = {
  call_id: string; caller_phone: string | null; summary: string; confidence: number; alert: boolean;
  status: string; task: string | null; transcript: Turn[]; flags: string[];
};

export function token(): string | null {
  try { return localStorage.getItem('cb_token'); } catch { return null; }
}

export function setToken(t: string | null) {
  try { t ? localStorage.setItem('cb_token', t) : localStorage.removeItem('cb_token'); } catch { /* private mode */ }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(token() ? { Authorization: `Bearer ${token()}` } : {}), ...init.headers },
  });
  if (res.status === 401) {
    setToken(null);
    if (typeof window !== 'undefined' && !location.pathname.startsWith('/login')) location.href = '/login';
    throw new Error('Please log in');
  }
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json() as Promise<T>;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return '';
  return new Date(iso).toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
