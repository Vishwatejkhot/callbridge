'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import { API, api, token, type LiveCall } from '@/lib/api';

export default function Live() {
  const [calls, setCalls] = useState<LiveCall[]>([]);
  const [error, setError] = useState('');
  const [taken, setTaken] = useState<Record<string, string>>({});
  const alerted = useRef<Set<string>>(new Set());

  const refresh = useCallback(() => {
    api<LiveCall[]>('/api/calls/live').then((c) => { setCalls(c); setError(''); }).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!token()) { location.href = '/login'; return; }
    refresh();
    const es = new EventSource(`${API}/api/events?token=${encodeURIComponent(token() ?? '')}`);
    es.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      if (data.alert && !alerted.current.has(data.call_id)) {
        alerted.current.add(data.call_id);
        try { new Audio('data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=').play(); } catch { /* no audio */ }
      }
      refresh();
    };
    const poll = setInterval(refresh, 5000); // fallback if SSE is blocked by a proxy
    return () => { es.close(); clearInterval(poll); };
  }, [refresh]);

  async function takeover(id: string) {
    const r = await api<{ transferred: boolean; summary: string }>(`/api/calls/${id}/takeover`, { method: 'POST' });
    setTaken((t) => ({ ...t, [id]: r.summary + (r.transferred ? '' : ' (no transfer number set: call the patient back)') }));
    refresh();
  }

  return (
    <>
      <h1>Live calls</h1>
      {error && <p className="error">{error}</p>}
      {Object.entries(taken).map(([id, summary]) => (
        <div key={id} className="panel"><strong>You took over a call.</strong> Summary so the patient doesn't repeat themselves:<p>{summary}</p></div>
      ))}
      {calls.length === 0 && <div className="panel muted">No calls in progress. The AI is answering when your team can't.</div>}
      {calls.map((c) => (
        <div key={c.call_id} className={`panel ${c.flags.includes('redflag') ? 'urgent' : c.alert ? 'alert' : ''}`}>
          <div className="row">
            <strong>{c.caller_phone ?? 'Withheld number'}</strong>
            {c.task && <span className="pill">{c.task}</span>}
            {c.flags.map((f) => <span key={f} className={`pill ${f === 'redflag' ? 'danger' : 'warn'}`}>{f.replace('_', ' ')}</span>)}
            <span className="spacer" style={{ flex: 1 }} />
            <span className="muted">AI confidence</span>
            <span className="bar" title={`${Math.round(c.confidence * 100)}%`}><span style={{ width: `${Math.round(c.confidence * 100)}%` }} /></span>
            <button className="primary" onClick={() => takeover(c.call_id)}>Take over</button>
            <Link href={`/calls/${encodeURIComponent(c.call_id)}`}>Open</Link>
          </div>
          <p>{c.summary}</p>
          {c.transcript.length > 0 && (
            <p className="muted">Last said: “{c.transcript[c.transcript.length - 1].text}”</p>
          )}
        </div>
      ))}
    </>
  );
}
