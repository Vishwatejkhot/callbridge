import './globals.css';
import type { Metadata } from 'next';
import Nav from '@/components/Nav';

export const metadata: Metadata = { title: 'Callbridge', description: 'Live calls, messages and bookings from your AI receptionist' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-GB">
      <body>
        <Nav />
        <main>{children}</main>
      </body>
    </html>
  );
}
