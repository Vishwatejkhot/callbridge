'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { api, when } from '@/lib/api';

type B = { id: number; patient: string; practitioner: string; start: string; status: string; call_id: string | null; updated_at: string };

export default function Bookings() {
  const [rows, setRows] = useState<B[]>([]);
  const [error, setError] = useState('');
  useEffect(() => { api<B[]>('/api/bookings').then(setRows).catch((e) => setError(e.message)); }, []);

  return (
    <>
      <h1>Bookings changed by the AI (last 14 days)</h1>
      {error && <p className="error">{error}</p>}
      <div className="panel table-wrap">
        <table>
          <thead><tr><th>Patient</th><th>With</th><th>Appointment</th><th>Status</th><th>When</th><th>Call</th></tr></thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.id}>
                <td>{b.patient}</td><td>{b.practitioner}</td><td>{when(b.start)}</td>
                <td><span className={`pill ${b.status === 'cancelled' ? 'warn' : 'ok'}`}>{b.status}</span></td>
                <td className="muted">{when(b.updated_at)}</td>
                <td>{b.call_id && <Link href={`/calls/${encodeURIComponent(b.call_id)}`}>view</Link>}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={6} className="muted">No AI bookings yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
