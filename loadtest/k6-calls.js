// k6 alternative to ramp.py: k6 run -e BASE=https://staging.example loadtest/k6-calls.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const turnLatency = new Trend('turn_latency_ms', true);
const errors = new Rate('turn_errors');
const BASE = __ENV.BASE || 'http://localhost:8000';

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-vus',
      stages: [
        { duration: '1m', target: 10 }, { duration: '2m', target: 10 },
        { duration: '1m', target: 50 }, { duration: '2m', target: 50 },
        { duration: '1m', target: 100 }, { duration: '2m', target: 100 },
      ],
    },
  },
  thresholds: { turn_latency_ms: ['p(95)<800'], turn_errors: ['rate<0.01'] },
};

const SCRIPTS = [
  ["Hi, I'd like to book a physio appointment", "It's Alex Smith, 3rd of May 1980", 'Thursday afternoon', 'the first one', 'yes please', 'no thanks, bye'],
  ['What time do you open on Saturday?', 'Is there parking?', "no that's all, thanks"],
];

export function setup() {
  const r = http.post(`${BASE}/api/auth/login`, JSON.stringify({ email: __ENV.EMAIL || 'demo@harbourside.example', password: __ENV.PASSWORD || 'demo-password' }), { headers: { 'Content-Type': 'application/json' } });
  return { token: r.json('token') };
}

export default function (data) {
  const params = { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${data.token}` } };
  const start = http.post(`${BASE}/api/sim/calls`, JSON.stringify({ caller_phone: `+447700900${100 + Math.floor(Math.random() * 899)}` }), params);
  if (!check(start, { started: (r) => r.status === 200 })) { errors.add(1); return; }
  const id = start.json('call_id');
  for (const line of SCRIPTS[Math.floor(Math.random() * SCRIPTS.length)]) {
    sleep(0.5 + Math.random());
    const r = http.post(`${BASE}/api/sim/calls/${id}/turn`, JSON.stringify({ text: line }), params);
    turnLatency.add(r.timings.duration);
    errors.add(r.status !== 200);
    if (r.status !== 200 || r.json('ended')) return;
  }
  http.post(`${BASE}/api/sim/calls/${id}/end`, null, params);
}
