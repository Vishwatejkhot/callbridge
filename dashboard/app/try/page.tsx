'use client';

import { useEffect, useRef, useState } from 'react';
import { api, token } from '@/lib/api';

/* Demo phone: talk to the real Callbridge brain from the browser.
   Speech-to-text and text-to-speech are the browser's own (Web Speech API, best in Chrome/Edge),
   standing in for Deepgram + Cartesia on a real phone line. Everything else (understanding,
   safety checker, bookings, SMS log, live dashboard) is the production code path via /api/sim. */

type Line = { role: 'caller' | 'assistant'; text: string; ms?: number };

const CALLERS = [
  { label: 'Sarah Jones (returning patient, DOB 12 March 1985)', phone: '+447700900123' },
  { label: 'David Okafor (returning patient, DOB 2 November 1972)', phone: '+447700900456' },
  { label: 'New caller', phone: '+447700900777' },
  { label: 'Withheld number', phone: '' },
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Recognition = any;

export default function TryCall() {
  const [callId, setCallId] = useState<string | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [caller, setCaller] = useState(CALLERS[0].phone);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [typed, setTyped] = useState('');
  const [interim, setInterim] = useState('');
  const [handsFree, setHandsFree] = useState(true);
  const [error, setError] = useState('');
  const recRef = useRef<Recognition>(null);
  const callRef = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const handsFreeRef = useRef(handsFree);
  handsFreeRef.current = handsFree;

  const speechSupported = typeof window !== 'undefined' &&
    ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window);

  useEffect(() => { if (!token()) location.href = '/login'; }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [lines, interim]);
  useEffect(() => () => { recRef.current?.abort(); speechSynthesis.cancel(); }, []);

  function speak(text: string, after?: () => void) {
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    const voices = speechSynthesis.getVoices();
    u.voice = voices.find((v) => v.lang === 'en-GB' && /female|libby|sonia|hazel|serena/i.test(v.name))
      ?? voices.find((v) => v.lang === 'en-GB') ?? null;
    u.lang = 'en-GB';
    u.rate = 1.05;
    u.onstart = () => setSpeaking(true);
    u.onend = () => { setSpeaking(false); after?.(); };
    u.onerror = () => { setSpeaking(false); after?.(); };
    speechSynthesis.speak(u);
  }

  function listen() {
    if (!speechSupported || !callRef.current) return;
    speechSynthesis.cancel(); // barge-in: talking over the AI stops it
    const Ctor = (window as Recognition).SpeechRecognition || (window as Recognition).webkitSpeechRecognition;
    const rec: Recognition = new Ctor();
    rec.lang = 'en-GB';
    rec.interimResults = true;
    rec.continuous = false;
    rec.onresult = (e: Recognition) => {
      let finalText = '';
      let partial = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript;
        else partial += e.results[i][0].transcript;
      }
      setInterim(partial);
      if (finalText.trim()) { setInterim(''); send(finalText.trim()); }
    };
    rec.onerror = (e: Recognition) => { if (e.error !== 'no-speech' && e.error !== 'aborted') setError(`Microphone: ${e.error}`); };
    rec.onend = () => setListening(false);
    recRef.current = rec;
    setListening(true);
    setError('');
    rec.start();
  }

  async function start() {
    setError('');
    setLines([]);
    try {
      const r = await api<{ call_id: string; reply: string }>('/api/sim/calls', {
        method: 'POST', body: JSON.stringify({ caller_phone: caller || null }),
      });
      setCallId(r.call_id);
      callRef.current = r.call_id;
      setLines([{ role: 'assistant', text: r.reply }]);
      speak(r.reply, () => handsFreeRef.current && listen());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function send(text: string) {
    const id = callRef.current;
    if (!id || !text) return;
    setLines((l) => [...l, { role: 'caller', text }]);
    setBusy(true);
    try {
      const r = await api<{ reply: string; ended: boolean; latency_ms: number }>(`/api/sim/calls/${id}/turn`, {
        method: 'POST', body: JSON.stringify({ text }),
      });
      setLines((l) => [...l, { role: 'assistant', text: r.reply, ms: r.latency_ms }]);
      if (r.ended) {
        callRef.current = null;
        setCallId(null);
        speak(r.reply);
      } else {
        speak(r.reply, () => handsFreeRef.current && listen());
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function hangUp() {
    recRef.current?.abort();
    speechSynthesis.cancel();
    const id = callRef.current;
    callRef.current = null;
    setCallId(null);
    if (id) await api(`/api/sim/calls/${id}/end`, { method: 'POST' }).catch(() => undefined);
  }

  const status = !callId ? 'Not in a call' : speaking ? 'AI speaking…' : busy ? 'Thinking…' : listening ? 'Listening… speak now' : 'Your turn';

  return (
    <>
      <h1>Try a call</h1>
      <p className="muted">
        Talk to the AI receptionist like a patient would. Open <a href="/" target="_blank">Live</a> in another
        window to watch the call from the staff side, take it over, and see the bookings and messages it creates.
      </p>

      {!speechSupported && (
        <div className="panel alert">Voice needs Chrome or Edge. You can still type below.</div>
      )}

      <div className="panel">
        <div className="row">
          <label className="row" style={{ gap: 6 }}>
            Calling as
            <select value={caller} onChange={(e) => setCaller(e.target.value)} disabled={!!callId}
              style={{ font: 'inherit', padding: 6, borderRadius: 8, background: 'var(--panel)', color: 'var(--text)', border: '1px solid var(--line)' }}>
              {CALLERS.map((c) => <option key={c.label} value={c.phone}>{c.label}</option>)}
            </select>
          </label>
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={handsFree} onChange={(e) => setHandsFree(e.target.checked)} style={{ width: 'auto' }} />
            Hands-free
          </label>
          <span style={{ flex: 1 }} />
          {!callId
            ? <button className="primary" onClick={start}>📞 Call Harbourside Physio</button>
            : <button onClick={hangUp} style={{ borderColor: 'var(--danger)', color: 'var(--danger)' }}>Hang up</button>}
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>
          <span className={`pill ${listening ? 'ok' : speaking ? 'warn' : ''}`}>{status}</span>
        </p>
      </div>

      <div className="panel">
        <div className="transcript" style={{ maxHeight: 460, minHeight: 200 }}>
          {lines.map((l, i) => (
            <div key={i} className={`bubble ${l.role}`}>
              {l.text}
              {l.ms !== undefined && <div style={{ fontSize: 11, opacity: 0.7 }}>{Math.round(l.ms)} ms</div>}
            </div>
          ))}
          {interim && <div className="bubble caller" style={{ opacity: 0.6 }}>{interim}…</div>}
          <div ref={endRef} />
        </div>
        {callId && (
          <form className="row" style={{ marginTop: 12 }} onSubmit={(e) => { e.preventDefault(); const t = typed; setTyped(''); send(t); }}>
            {speechSupported && (
              <button type="button" className={listening ? '' : 'primary'} onClick={listening ? () => recRef.current?.stop() : listen} disabled={busy}>
                {listening ? '■ Stop' : '🎤 Speak'}
              </button>
            )}
            <input style={{ flex: 1, width: 'auto' }} placeholder="…or type what the caller says" value={typed}
              onChange={(e) => setTyped(e.target.value)} disabled={busy} />
            <button disabled={busy || !typed.trim()}>Send</button>
          </form>
        )}
        {error && <p className="error">{error}</p>}
      </div>

      <div className="panel muted">
        <strong>Things to try:</strong> “I need to move my appointment” · “Is there parking?” · “Can I book a sports
        massage with Jake on Friday at 3pm?” · “What’s David Okafor’s phone number?” · “I’ve had chest pain all day”
        · “Can I speak to a real person?” · “Please delete my data”
      </div>
    </>
  );
}
