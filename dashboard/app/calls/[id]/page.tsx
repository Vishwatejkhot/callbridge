'use client';

import { use, useEffect, useState } from 'react';
import { api, type Turn } from '@/lib/api';

type Detail = {
  live: boolean; summary: string; transcript: Turn[]; outcome?: string; failure_reasons?: string[];
  confidence?: number; flags?: string[]; latency_ms?: number[];
};

export default function CallDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [call, setCall] = useState<Detail | null>(null);
  const [note, setNote] = useState('');
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const load = () => api<Detail>(`/api/calls/${encodeURIComponent(id)}`).then(setCall).catch((e) => setError(e.message));
    load();
    const t = setInterval(() => { if (call?.live !== false) load(); }, 2000);
    return () => clearInterval(t);
  }, [id, call?.live]);

  async function takeover() {
    await api(`/api/calls/${encodeURIComponent(id)}/takeover`, { method: 'POST' });
  }

  async function correct(e: React.FormEvent) {
    e.preventDefault();
    await api(`/api/calls/${encodeURIComponent(id)}/correction`, { method: 'POST', body: JSON.stringify({ note }) });
    setSaved(true);
  }

  if (error) return <p className="error">{error}</p>;
  if (!call) return <p className="muted">Loading…</p>;
  return (
    <>
      <h1>{call.live ? 'Live call' : 'Call'} <span className="pill">{call.outcome ?? 'in progress'}</span></h1>
      <div className="panel">
        <strong>Summary</strong>
        <p>{call.summary}</p>
        {call.live && <button className="primary" onClick={takeover}>Take over this call</button>}
        {!!call.failure_reasons?.length && <p>Needs review: {call.failure_reasons.map((r) => <span key={r} className="pill warn">{r}</span>)}</p>}
      </div>
      <div className="panel">
        <strong>Transcript</strong>
        <div className="transcript">
          {call.transcript.map((t, i) => <div key={i} className={`bubble ${t.role}`}>{t.text}</div>)}
        </div>
      </div>
      {!call.live && (
        <form className="panel" onSubmit={correct} style={{ display: 'grid', gap: 8 }}>
          <strong>Did the AI get something wrong?</strong>
          <span className="muted">Your note turns this call into a test case so the system learns from it.</span>
          <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. she meant next week's Tuesday, not tomorrow" />
          <button disabled={!note || saved}>{saved ? 'Thanks, noted' : 'Flag for improvement'}</button>
        </form>
      )}
    </>
  );
}
