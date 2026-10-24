"""Pre-rendered greeting audio, so the first thing a caller hears has no TTS round trip.
Rendered once per clinic + text via Cartesia's HTTP API and kept in memory."""

import logging
import threading

import httpx

from callbridge.config import get_settings

log = logging.getLogger(__name__)
_cache: dict[tuple[int, str], bytes] = {}
_lock = threading.Lock()


def get(clinic_id: int, text: str) -> bytes | None:
    key = (clinic_id, text)
    with _lock:
        if key in _cache:
            return _cache[key]
    st = get_settings()
    if not st.cartesia_api_key:
        return None
    try:
        r = httpx.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={"X-API-Key": st.cartesia_api_key, "Cartesia-Version": "2025-04-16"},
            json={"model_id": "sonic-2", "transcript": text, "voice": {"mode": "id", "id": st.cartesia_voice_id},
                  "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 8000},
                  "language": "en"},
            timeout=5.0,
        )
        r.raise_for_status()
    except Exception:
        log.warning("greeting pre-render failed; will synthesise live", exc_info=True)
        return None
    with _lock:
        _cache[key] = r.content
    return r.content
