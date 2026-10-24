'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { api, setToken, token, type Me } from '@/lib/api';

const LINKS = [
  ['/', 'Live'], ['/try', 'Try a call'], ['/messages', 'Messages'], ['/bookings', 'Bookings'], ['/history', 'Call history'], ['/reports', 'Reports'],
] as const;

export default function Nav() {
  const path = usePathname();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (path === '/login' || !token()) return;
    api<Me>('/api/me').then(setMe).catch(() => setMe(null));
  }, [path]);

  if (path === '/login') return null;
  return (
    <nav>
      <span className="brand">Callbridge</span>
      {LINKS.filter(([href]) => href !== '/reports' || me?.role === 'owner').map(([href, label]) => (
        <Link key={href} href={href} className={path === href ? 'active' : ''}>{label}</Link>
      ))}
      <span className="spacer" />
      {me && (
        <span className="muted">
          {me.clinic.name}{' '}
          <span className={`pill ${me.clinic.ai_enabled ? 'ok' : 'danger'}`}>AI {me.clinic.ai_enabled ? 'on' : 'off'}</span>
        </span>
      )}
      <button onClick={() => { setToken(null); location.href = '/login'; }}>Log out</button>
    </nav>
  );
}
