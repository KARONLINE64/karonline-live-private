from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request
from array import array
from fractions import Fraction

import av
import sounddevice as sd
from aiortc import AudioStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from PySide6.QtCore import QObject, Signal


class _MicrophoneTrack(AudioStreamTrack):
    kind = "audio"

    def __init__(self, audio_queue: asyncio.Queue):
        super().__init__()
        self._audio_queue = audio_queue
        self._timestamp = 0

    async def recv(self):
        pcm_bytes = await self._audio_queue.get()
        frame = av.AudioFrame(
            format="s16", layout="mono", samples=len(pcm_bytes) // 2
        )
        frame.planes[0].update(pcm_bytes)
        frame.sample_rate = 48000
        frame.pts = self._timestamp
        frame.time_base = Fraction(1, 48000)
        self._timestamp += len(pcm_bytes) // 2
        return frame


class DuoAudioLink(QObject):
    """Lien WebRTC audio desktop-a-desktop pour une session DUO.

    Le karaoke reste lu localement sur chaque poste; Windows mixe cette sortie
    avec le retour micro local et le flux Opus recu de l'autre desktop.
    """

    status_changed = Signal(str)
    error = Signal(str)

    SAMPLE_RATE = 48000
    BLOCK_SIZE = 960

    def __init__(self, central_base: str, token_provider,
                 input_device_name_provider=None, parent=None):
        super().__init__(parent)
        self._central_base = central_base.rstrip("/")
        self._token_provider = token_provider
        self._input_device_name_provider = input_device_name_provider
        self._thread = None
        self._loop = None
        self._stop_event = None
        self._code = ""
        self._is_host = False
        self._input_stream = None
        self._output_stream = None
        self.running = False

    def start(self, code: str, is_host: bool):
        if self.running:
            return
        self._code = code
        self._is_host = is_host
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self._loop = None
        self._stop_event = None

    def _run(self):
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            self.error.emit(f"Audio DUO indisponible : {exc}")
        finally:
            self.running = False

    def _headers(self, content_type=False):
        token = self._token_provider() or ""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
            "Authorization": f"Bearer {token}",
        }
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def _request_json(self, path: str, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self._central_base}{path}", data=body,
            headers=self._headers(payload is not None),
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))

    async def _request(self, path: str, payload=None):
        return await asyncio.get_running_loop().run_in_executor(
            None, self._request_json, path, payload
        )

    async def _run_async(self):
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        credentials = await self._request(f"/duo/turn-credentials?code={self._code}")
        ice_servers = [
            RTCIceServer(urls="stun:stun.l.google.com:19302"),
            RTCIceServer(
                urls=credentials["urls"],
                username=credentials["username"],
                credential=credentials["credential"],
            ),
        ]
        peer = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        audio_queue: asyncio.Queue = asyncio.Queue(maxsize=12)

        def capture(indata, frames, time_info, status):
            if status:
                return
            pcm_bytes = bytes(indata)
            def enqueue():
                if not audio_queue.full():
                    audio_queue.put_nowait(pcm_bytes)
            self._loop.call_soon_threadsafe(enqueue)

        try:
            input_device = self._selected_input_device()
            self._input_stream = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE, channels=1, dtype="int16",
                blocksize=self.BLOCK_SIZE, callback=capture,
                device=input_device,
            )
            self._output_stream = sd.RawOutputStream(
                samplerate=self.SAMPLE_RATE, channels=1, dtype="int16",
                blocksize=self.BLOCK_SIZE,
            )
            self._input_stream.start()
            self._output_stream.start()
            peer.addTrack(_MicrophoneTrack(audio_queue))

            @peer.on("connectionstatechange")
            async def on_connectionstatechange():
                state = peer.connectionState
                self.status_changed.emit(f"Audio DUO WebRTC : {state}")
                if state in {"failed", "disconnected"}:
                    self.error.emit(
                        f"Audio DUO WebRTC interrompu ({state}). "
                        "Vérifiez la connexion Internet et les pare-feu des deux PC."
                    )

            @peer.on("track")
            def on_track(track):
                if track.kind == "audio":
                    asyncio.create_task(self._play_remote(track))

            if self._is_host:
                offer = await peer.createOffer()
                await peer.setLocalDescription(offer)
                await self._wait_for_ice(peer)
                await self._request("/duo/signal", {"code": self._code, "type": "offer", "sdp": peer.localDescription.sdp})
                self.status_changed.emit("Audio DUO : attente de la connexion invité")
                answer = await self._wait_signal("answer")
                await peer.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))
            else:
                self.status_changed.emit("Audio DUO : connexion au mix hôte")
                offer = await self._wait_signal("offer")
                await peer.setRemoteDescription(RTCSessionDescription(sdp=offer, type="offer"))
                answer = await peer.createAnswer()
                await peer.setLocalDescription(answer)
                await self._wait_for_ice(peer)
                await self._request("/duo/signal", {"code": self._code, "type": "answer", "sdp": peer.localDescription.sdp})

            self.status_changed.emit("Audio DUO connecté : mix micro distant actif")
            await self._stop_event.wait()
        finally:
            await peer.close()
            for stream_name in ("_input_stream", "_output_stream"):
                stream = getattr(self, stream_name, None)
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                    setattr(self, stream_name, None)
            self.status_changed.emit("Audio DUO arrêté")

    def _selected_input_device(self):
        """Résout le choix MICRO/CASQUE vers un périphérique sounddevice."""
        if self._input_device_name_provider is None:
            return None
        wanted = str(self._input_device_name_provider() or "").strip()
        if not wanted:
            return None
        wanted = wanted.removeprefix("🎤").strip().casefold()
        try:
            for index, device in enumerate(sd.query_devices()):
                name = str(device.get("name", "")).casefold()
                if device.get("max_input_channels", 0) and (
                    wanted in name or name in wanted
                ):
                    return index
        except Exception:
            pass
        return None

    async def _wait_signal(self, signal_type: str):
        while not self._stop_event.is_set():
            data = await self._request(f"/duo/signal?code={self._code}")
            if data.get("type") == signal_type and data.get("sdp"):
                return data["sdp"]
            await asyncio.sleep(0.2)
        raise RuntimeError("Session DUO fermée")

    async def _wait_for_ice(self, peer):
        while peer.iceGatheringState != "complete":
            await asyncio.sleep(0.1)

    async def _play_remote(self, track):
        resampler = av.AudioResampler(format="s16", layout="mono", rate=self.SAMPLE_RATE)
        while self.running and not self._stop_event.is_set():
            frame = await track.recv()
            if self._output_stream:
                for pcm_frame in resampler.resample(frame):
                    self._output_stream.write(bytes(pcm_frame.planes[0]))
