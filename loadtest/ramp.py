"""Concurrent-call load test against the text simulator API (staging only).

Ramps 10 -> 50 -> 100 simultaneous calls, each a realistic multi-turn conversation, and reports
per-turn latency (p50/p95/p99) and error rate at each step, so you can find the breaking point.
This exercises everything except audio (STT/TTS run at the providers and scale separately).

  python loadtest/ramp.py --base https://staging.example --steps 10 50 100 --email ... --password ..."""

import argparse
import asyncio
import random
import statistics
import time

import httpx

SCRIPTS = [
    ["Hi, I'd like to book a physio appointment", "It's Alex Smith, 3rd of May 1980", "Thursday afternoon",
     "the first one", "yes please", "no thanks, bye"],
    ["What time do you open on Saturday?", "Is there parking?", "no that's all, thanks"],
    ["I need to cancel my appointment", "12th March 1985", "yes", "no thanks, bye"],
    ["Can I leave a message for Tom?", "Please ask him to call me about my exercises", "no, bye"],
]


async def one_call(client: httpx.AsyncClient, headers: dict, lat: list[float], errors: list[str]) -> None:
    script = random.choice(SCRIPTS)
    phone = f"+447700900{random.randint(100, 999)}"
    try:
        r = await client.post("/api/sim/calls", json={"caller_phone": phone}, headers=headers)
        r.raise_for_status()
        call_id = r.json()["call_id"]
        for line in script:
            await asyncio.sleep(random.uniform(0.5, 1.5))  # caller speaking time
            t0 = time.perf_counter()
            r = await client.post(f"/api/sim/calls/{call_id}/turn", json={"text": line}, headers=headers)
            lat.append((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                errors.append(f"{r.status_code}")
                return
            if r.json()["ended"]:
                return
        await client.post(f"/api/sim/calls/{call_id}/end", headers=headers)
    except Exception as e:  # noqa: BLE001 - record and continue
        errors.append(type(e).__name__)


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * (len(xs) - 1)))]


async def step(base: str, headers: dict, concurrent: int, duration_s: int) -> dict:
    lat: list[float] = []
    errors: list[str] = []
    calls = 0
    deadline = time.monotonic() + duration_s
    limits = httpx.Limits(max_connections=concurrent * 2)
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=limits) as client:
        async def worker():
            nonlocal calls
            while time.monotonic() < deadline:
                await one_call(client, headers, lat, errors)
                calls += 1

        await asyncio.gather(*(worker() for _ in range(concurrent)))
    return {"concurrent_calls": concurrent, "calls": calls, "turns": len(lat), "errors": len(errors),
            "error_rate": round(len(errors) / max(1, len(lat)), 4), "p50_ms": round(statistics.median(lat), 1) if lat else None,
            "p95_ms": round(pct(lat, 0.95), 1), "p99_ms": round(pct(lat, 0.99), 1)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", default="demo@harbourside.example")
    ap.add_argument("--password", default="demo-password")
    ap.add_argument("--steps", type=int, nargs="+", default=[10, 50, 100])
    ap.add_argument("--duration", type=int, default=60, help="seconds per step")
    a = ap.parse_args()
    async with httpx.AsyncClient(base_url=a.base) as c:
        token = (await c.post("/api/auth/login", json={"email": a.email, "password": a.password})).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("concurrent  calls  turns  errors  p50ms  p95ms  p99ms")
    for n in a.steps:
        r = await step(a.base, headers, n, a.duration)
        print(f"{r['concurrent_calls']:>10}  {r['calls']:>5}  {r['turns']:>5}  {r['errors']:>6}  {r['p50_ms']:>5}  {r['p95_ms']:>5}  {r['p99_ms']:>5}")
        if r["error_rate"] > 0.01:
            print(f"breaking point: error rate {r['error_rate']:.1%} at {n} concurrent calls")
            break


if __name__ == "__main__":
    asyncio.run(main())
