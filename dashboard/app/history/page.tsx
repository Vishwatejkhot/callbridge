'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { api, when } from '@/lib/api';

type C = { id: string; started_at: string; caller_phone: string | null; outcome: string | null; summary: string | null; failure_reasons: string[] };

export default function History() {
  const [rows, setRows] = useState<C[]>([]);
  const [error, setError] = useState('');
  useEffect(() => { api<C[]>('/api/calls?limit=100').then(setRows).catch((e) => setError(e.message)); }, []);

  return (
    <>
      <h1>Call history</h1>
      {error && <p className="error">{error}</p>}
      <div className="panel table-wrap">
        <table>
          <thead><tr><th>When</th><th>Caller</th><th>Outcome</th><th>Summary</th><th /></tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id}>
                <td>{when(c.started_at)}</td>
                <td>{c.caller_phone ?? '—'}</td>
                <td>
                  <span className="pill">{c.outcome ?? 'in progress'}</span>{' '}
                  {c.failure_reasons?.length ? <span className="pill warn">review</span> : null}
                </td>
                <td className="muted">{c.summary}</td>
                <td><Link href={`/calls/${encodeURIComponent(c.id)}`}>Open</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
