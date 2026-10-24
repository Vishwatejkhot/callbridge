'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

type Report = {
  days: number; calls_answered: number; bookings_by_ai: number; resolved_without_staff_pct: number | null;
  staff_minutes_saved: number; outcomes: Record<string, number>; calls_by_day: Record<string, number>;
};

export default function Reports() {
  const [r, setR] = useState<Report | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { api<Report>('/api/reports?days=30').then(setR).catch((e) => setError(e.message)); }, []);

  if (error) return <p className="error">{error}</p>;
  if (!r) return <p className="muted">Loading…</p>;
  const max = Math.max(1, ...Object.values(r.calls_by_day));
  return (
    <>
      <h1>Last {r.days} days</h1>
      <div className="stats">
        <div className="panel stat"><div className="muted">Calls answered</div><div className="n">{r.calls_answered}</div></div>
        <div className="panel stat"><div className="muted">Bookings made by AI</div><div className="n">{r.bookings_by_ai}</div></div>
        <div className="panel stat"><div className="muted">Handled without staff</div><div className="n">{r.resolved_without_staff_pct ?? '—'}{r.resolved_without_staff_pct != null && '%'}</div></div>
        <div className="panel stat"><div className="muted">Phone time saved</div><div className="n">{Math.round(r.staff_minutes_saved)} min</div></div>
      </div>
      <div className="panel">
        <strong>Calls per day</strong>
        <div style={{ display: 'grid', gap: 4, marginTop: 8 }}>
          {Object.entries(r.calls_by_day).map(([day, n]) => (
            <div key={day} className="row" style={{ gap: 8 }}>
              <span className="muted" style={{ width: 96 }}>{new Date(day).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}</span>
              <span style={{ height: 10, borderRadius: 4, background: 'var(--accent)', width: `${(n / max) * 70}%` }} />
              <span>{n}</span>
            </div>
          ))}
          {Object.keys(r.calls_by_day).length === 0 && <span className="muted">No calls yet.</span>}
        </div>
      </div>
      <div className="panel">
        <strong>Outcomes</strong>
        <p>{Object.entries(r.outcomes).map(([k, v]) => <span key={k} className="pill" style={{ marginRight: 6 }}>{k}: {v}</span>)}</p>
      </div>
    </>
  );
}
