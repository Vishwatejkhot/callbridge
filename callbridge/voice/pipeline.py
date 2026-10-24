"""Phone audio pipeline (Pipecat):

  Twilio media stream -> Silero VAD -> Deepgram STT (streaming) -> smart turn detection
     -> CallbridgeAgent (our LangGraph brain) -> Cartesia TTS -> Twilio

Barge-in: when the caller starts talking, the turn processor broadcasts an interruption and Pipecat
cuts the bot's audio. Smart turn detection (Smart Turn v3, a small local model that listens to how a
sentence ends) decides when the caller has *finished their thought*, not just paused.
While the caller is still talking, each final transcript chunk is understood speculatively
(CallSession.prefetch); if they then say something different, the draft is simply discarded.

Never silent: if a speech service fails, Twilio's own TTS takes over (see api.twilio_routes.fallback)."""

import asyncio
import logging
import time

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame, EndFrame, ErrorFrame, Frame, OutputAudioRawFrame, TranscriptionFrame, TTSSpeakFrame,
    UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from callbridge.agent.session import CallSession
from callbridge.calls import registry
from callbridge.config import get_settings
from callbridge.improver import rollout
from callbridge.observability import VOICE_LATENCY
from callbridge.voice import greeting_cache, telephony

log = logging.getLogger(__name__)
SAMPLE_RATE = 8000  # Twilio media streams are 8kHz mu-law


class CallbridgeAgent(FrameProcessor):
    """Bridges Pipecat frames to CallSession turns."""

    def __init__(self, session: CallSession, **kwargs):
        super().__init__(**kwargs)
        self.session = session
        self._chunks: list[str] = []
        self._user_stopped_at: float | None = None
        self._busy = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._chunks.append(frame.text.strip())
            text_so_far = " ".join(self._chunks)
            asyncio.get_running_loop().run_in_executor(None, self.session.prefetch, text_so_far)
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._user_stopped_at = None
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_stopped_at = time.perf_counter()
            await self._respond()
        elif isinstance(frame, BotStartedSpeakingFrame) and self._user_stopped_at is not None:
            VOICE_LATENCY.observe(time.perf_counter() - self._user_stopped_at)
            self._user_stopped_at = None
        elif isinstance(frame, ErrorFrame):
            log.error("pipeline error: %s", frame.error)

        await self.push_frame(frame, direction)

    async def _respond(self) -> None:
        if not self._chunks:
            await asyncio.sleep(0.4)  # the final transcript can land just after the turn ends
        if not self._chunks or self._busy:
            return
        text = " ".join(self._chunks)
        self._chunks.clear()
        self._busy = True
        try:
            result = await asyncio.to_thread(self.session.turn, text)
        finally:
            self._busy = False
        if result.reply:
            await self.push_frame(TTSSpeakFrame(result.reply))
        if result.transfer:
            await asyncio.to_thread(telephony.transfer_to_staff, self.session.call_id, self.session.clinic_id)
        elif result.ended:
            await self.push_frame(EndFrame())


async def run_twilio_call(websocket) -> None:
    st = get_settings()
    _, call_data = await parse_telephony_websocket(websocket)
    body = call_data.get("body", {}) or {}
    call_sid = call_data["call_id"]
    clinic_id = int(body["clinic_id"])
    caller = body.get("from") or None

    config, arm, shadow = rollout.choose(call_sid)
    session = CallSession(call_sid, clinic_id, caller, config=config, rollout_arm=arm, shadow=shadow)
    greeting = await asyncio.to_thread(session.start)
    registry.on_takeover(call_sid, lambda: telephony.transfer_to_staff(call_sid, clinic_id))

    serializer = TwilioFrameSerializer(stream_sid=call_data["stream_id"], call_sid=call_sid,
                                       account_sid=st.twilio_account_sid, auth_token=st.twilio_auth_token)
    transport = FastAPIWebsocketTransport(websocket, FastAPIWebsocketParams(
        audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False, serializer=serializer))
    stt = DeepgramSTTService(api_key=st.deepgram_api_key, settings=DeepgramSTTService.Settings(
        model="nova-3", language=Language.EN_GB, smart_format=True, interim_results=True, punctuate=True,
        keyterm=[p["first"] for p in session.practitioners]))
    tts = CartesiaTTSService(api_key=st.cartesia_api_key, settings=CartesiaTTSService.Settings(voice=st.cartesia_voice_id))
    agent = CallbridgeAgent(session)

    pipeline = Pipeline([
        transport.input(),
        VADProcessor(vad_analyzer=SileroVADAnalyzer()),
        stt,
        UserTurnProcessor(user_turn_strategies=UserTurnStrategies()),  # VAD start + Smart Turn v3 stop
        agent,
        tts,
        transport.output(),
    ])
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE, enable_metrics=True,
        enable_usage_metrics=True))

    @transport.event_handler("on_client_connected")
    async def _connected(_transport, _client):
        audio = await asyncio.to_thread(greeting_cache.get, clinic_id, greeting)
        if audio:  # pre-rendered: the caller hears us immediately
            await task.queue_frame(OutputAudioRawFrame(audio=audio, sample_rate=SAMPLE_RATE, num_channels=1))
        else:
            await task.queue_frame(TTSSpeakFrame(greeting))
        asyncio.get_running_loop().run_in_executor(None, telephony.start_recording, call_sid, clinic_id)

    @transport.event_handler("on_client_disconnected")
    async def _disconnected(_transport, _client):
        await task.cancel()

    try:
        await PipelineRunner(handle_sigint=False).run(task)
    finally:
        reason = "completed" if (session.state or {}).get("status") in ("ended", "handoff") else "hangup"
        telephony.mark_finished(call_sid, normally=reason == "completed" or session.taken_over)
        await asyncio.to_thread(session.end, reason)
