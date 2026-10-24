'use client';

import { useEffect, useState } from 'react';
import { api, when } from '@/lib/api';

type Msg = { id: number; caller_name: string | null; caller_phone: string | null; body: string; urgent: boolean; created_at: string };

export default function Messages() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [error, setError] = useState('');
  const load = () => api<Msg[]>('/api/messages').then(setMsgs).catch((e) => setError(e.message));
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);

  async function done(id: number) {
    await api(`/api/messages/${id}/handled`, { method: 'POST' });
    load();
  }

  return (
    <>
      <h1>Messages to call back</h1>
      {error && <p className="error">{error}</p>}
      {msgs.length === 0 && <div className="panel muted">Nothing waiting.</div>}
      {msgs.map((m) => (
        <div key={m.id} className={`panel ${m.urgent ? 'urgent' : ''}`}>
          <div className="row">
            {m.urgent && <span className="pill danger">urgent</span>}
            <strong>{m.caller_name ?? 'Unknown caller'}</strong>
            {m.caller_phone && <a href={`tel:${m.caller_phone}`}>{m.caller_phone}</a>}
            <span className="muted">{when(m.created_at)}</span>
            <span style={{ flex: 1 }} />
            <button onClick={() => done(m.id)}>Mark called back</button>
          </div>
          <p>{m.body}</p>
        </div>
      ))}
    </>
  );
}
