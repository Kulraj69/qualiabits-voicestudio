"""Fixtures for the hosted control-plane tests.

The control plane is exercised as the real app: its real database on a temporary
file, its real routes behind Starlette's test client, real password hashing and
real key derivation. The only substitution is the engine, and it is named as such
everywhere: a stub ASGI app that answers ``/v1/audio/speech`` with a WAV of a
known duration. The end-to-end run against the real engine is
``hosted/scripts/hosted_e2e.py``, not this.
"""
from __future__ import annotations

import io
import struct
import sys
import wave
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from fastapi import FastAPI, Request, Response  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.datastructures import UploadFile  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from hosted.control_plane.app import create_app  # noqa: E402
from hosted.control_plane.config import Settings  # noqa: E402
from hosted.control_plane.upstream import UpstreamClient  # noqa: E402

SAMPLE_RATE = 24000


def make_wav(seconds: float, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    """A real 16-bit mono WAV of exactly ``seconds`` at a known rate.

    Made with the stdlib ``wave`` module rather than hand-rolled bytes, so the
    duration the metering code reads out of the header is the duration a real
    encoder wrote.
    """
    frames = int(round(seconds * sample_rate))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(struct.pack("<h", 1200) * frames)
    return buffer.getvalue()


class StubEngine:
    """A stand-in for the engine. Records what it was asked, answers like the real one."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.mode = "ok"  # ok | refuse | boom
        self.audio_seconds = 1.25
        self.refusal = {"detail": "engine says no", "param": "voice", "code": "unknown_voice"}
        self.wav = make_wav(self.audio_seconds)
        # ── the STT half of the same stub ─────────────────────────────────────
        #: What the engine "heard". The real engine's transcript of the same
        #: audio is a real ASR output; this is a fixed string, and tests assert
        #: on what came back rather than pretending it is recognition quality.
        self.transcript = "यह एक हिन्दी वाक्य है।"
        #: Every transcription the stub was asked for, in order.
        self.transcriptions: list[dict] = []
        self.stt_refusal = {
            "error": {"message": "No ASR model is loaded on the engine.",
                      "type": "invalid_request_error", "code": "asr_model_missing"},
            "detail": "No ASR model is loaded on the engine.",
        }
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        stub = FastAPI()
        state = self

        @stub.get("/health")
        async def health() -> dict:
            return {"status": "ok", "device": "cpu", "version": "stub"}

        @stub.get("/v1/audio/voices")
        async def voices() -> dict:
            # The shape the real engine answers with: OpenAI aliases and saved
            # profiles, and none of the ids a model declares in its own file.
            return {
                "voices": [
                    {"voice_id": "alloy", "name": "Alloy", "type": "openai_alias"},
                    {"voice_id": "echo", "name": "Echo", "type": "openai_alias"},
                    {"voice_id": "profile-1", "name": "Studio read", "type": "profile",
                     "language": "hi"},
                ],
                "engines": [{"id": "sherpa-onnx", "available": True}],
            }

        @stub.get("/engines/tts")
        async def engines_tts() -> dict:
            return {
                "active": "sherpa-onnx",
                "backends": [
                    {"id": "sherpa-onnx", "display_name": "sherpa-onnx",
                     "available": True,
                     "preset_voices": ["af_alloy", "hf_alpha", "hf_beta", "hm_omega"],
                     "default_voice": "af_alloy"},
                    {"id": "kittentts", "display_name": "KittenTTS", "available": True,
                     "preset_voices": ["expr-voice-2-f"], "default_voice": "expr-voice-2-f"},
                ],
            }

        @stub.post("/v1/audio/speech")
        async def speech(payload: dict) -> Response:
            state.payloads.append(payload)
            if state.mode == "refuse":
                return JSONResponse(status_code=400, content=state.refusal)
            if state.mode == "boom":
                raise RuntimeError("stub engine exploded")
            return Response(
                content=state.wav,
                media_type="audio/wav",
                headers={"x-omnivoice-backend": "sherpa-onnx"},
            )

        @stub.post("/v1/audio/transcriptions")
        async def transcribe(request: Request) -> Response:
            """The engine's OpenAI-compatible STT route, in the shape it answers.

            Parsed with the same multipart reader the real engine uses (Starlette),
            so a test can assert what actually crossed the wire: the filename, the
            content type, the option fields the plane forwarded, and the bytes.
            """
            form = await request.form()
            upload = form.get("file")
            raw, filename, content_type = b"", None, None
            if isinstance(upload, UploadFile):
                raw = await upload.read()
                filename, content_type = upload.filename, upload.content_type
            state.transcriptions.append({
                "filename": filename,
                "content_type": content_type,
                "bytes": len(raw),
                "body": raw,
                "fields": {k: v for k, v in form.multi_items() if k != "file"},
            })
            if state.mode == "refuse":
                return JSONResponse(status_code=409, content=state.stt_refusal)
            if state.mode == "boom":
                raise RuntimeError("stub engine exploded")
            requested = str(form.get("response_format") or "json")
            language = str(form.get("language") or "hi")
            if requested == "text":
                return Response(content=state.transcript, media_type="text/plain")
            if requested == "srt":
                return Response(
                    content=f"1\n00:00:00,000 --> 00:00:02,000\n{state.transcript}\n",
                    media_type="text/plain",
                )
            payload = {
                "task": "transcribe",
                "language": language,
                "duration": state.audio_seconds,
                "text": state.transcript,
                "segments": [
                    {"id": 0, "seek": 0, "start": 0.0, "end": state.audio_seconds,
                     "text": f" {state.transcript}", "tokens": [],
                     "temperature": 0.0, "avg_logprob": 0.0, "compression_ratio": 0.0,
                     "no_speech_prob": 0.0},
                ],
            }
            return JSONResponse(
                content=payload, headers={"x-omnivoice-backend": "faster-whisper"}
            )

        return stub

    def reset(self) -> None:
        self.payloads.clear()
        self.transcriptions.clear()
        self.mode = "ok"

    def set_audio_seconds(self, seconds: float) -> None:
        self.audio_seconds = seconds
        self.wav = make_wav(seconds)


@pytest.fixture
def engine() -> StubEngine:
    return StubEngine()


@contextmanager
def app_client(
    tmp_path: Path,
    engine: StubEngine,
    *,
    name: str = "control-plane.db",
    ttl: int = 3600,
    upstream_base: str = "http://engine",
    transport: httpx.AsyncBaseTransport | None = None,
    storage_backend: str = "local",
    s3_bucket: str = "",
    storage_dir: Path | None = None,
    job_lease_seconds: int = 900,
    job_max_attempts: int = 3,
):
    """A real control plane, its own database, its own object store, stub engine."""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / name}",
        upstream_base_url=upstream_base,
        upstream_timeout_seconds=30.0,
        session_ttl_seconds=ttl,
        storage_backend=storage_backend,
        storage_dir=str(storage_dir if storage_dir is not None else tmp_path / "objects"),
        s3_bucket=s3_bucket,
        job_lease_seconds=job_lease_seconds,
        job_max_attempts=job_max_attempts,
    )
    # The stub is reached in-process only when the test asked for the stub's own
    # URL. A test that points at a real address (a closed loopback port, say) gets
    # the ordinary transport, so its failure is a real connection failure.
    if transport is None and upstream_base == "http://engine":
        transport = httpx.ASGITransport(app=engine.app)
    client = httpx.AsyncClient(transport=transport, base_url=upstream_base)
    upstream = UpstreamClient(upstream_base, 30.0, client=client)
    with TestClient(create_app(settings, upstream)) as test_client:
        yield test_client


@pytest.fixture
def client(tmp_path: Path, engine: StubEngine):
    with app_client(tmp_path, engine) as test_client:
        yield test_client


# ── the worker, in process ────────────────────────────────────────────────────

def worker_for(test_client: TestClient, engine: StubEngine, *, worker_id: str = "test-worker"):
    """A real worker process's logic, pointed at the same database and store.

    The only substitution is again the engine: the worker talks to the stub over
    the same ASGI transport the plane uses. Everything else — the queue table, the
    object store, the lease, the usage ledger — is the real thing.
    """
    from hosted.control_plane.db import Database
    from hosted.worker.runner import Worker

    settings = test_client.app.state.hosted.settings
    store = test_client.app.state.hosted.store
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=engine.app),
        base_url=settings.upstream_base_url,
    )
    upstream = UpstreamClient(settings.upstream_base_url, 30.0, client=client)
    return Worker(
        settings,
        worker_id,
        db=Database(settings.database_url),
        store=store,
        upstream=upstream,
        log=lambda line: None,
    )


def drain(worker, *, max_jobs: int = 0):
    """Run the worker until the queue is empty, the way ``run_worker.py --once`` does."""
    import asyncio

    return asyncio.run(worker.drain(max_jobs=max_jobs, idle_polls_before_exit=1))


# ── small helpers every test file uses ────────────────────────────────────────

PASSWORD = "correct horse battery"


def signup(client: TestClient, email: str = "buyer@example.com", password: str = PASSWORD) -> dict:
    response = client.post("/v1/workspaces", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()["workspace"]


def login(client: TestClient, email: str = "buyer@example.com", password: str = PASSWORD) -> str:
    response = client.post("/v1/auth/token", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def mint_key(client: TestClient, session_token: str, label: str = "test") -> str:
    response = client.post(
        "/v1/api-keys",
        json={"label": label},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["api_key_record"]["api_key"]


def usage(client: TestClient, credential: str) -> dict:
    response = client.get("/v1/usage", headers={"Authorization": f"Bearer {credential}"})
    assert response.status_code == 200, response.text
    return response.json()["usage"]


def speak(client: TestClient, credential: str, text: str, **extra):
    body = {"input": text, "voice": "hf_alpha", "response_format": "wav", **extra}
    return client.post(
        "/v1/audio/speech", json=body, headers={"Authorization": f"Bearer {credential}"}
    )


def queue_job(client: TestClient, credential: str, text: str, **extra):
    body = {"input": text, "voice": "hf_alpha", "response_format": "wav", **extra}
    return client.post(
        "/v1/jobs", json=body, headers={"Authorization": f"Bearer {credential}"}
    )


def transcribe(
    client: TestClient,
    credential: str,
    content: bytes | None = None,
    *,
    filename: str = "clip.wav",
    content_type: str = "audio/wav",
    **fields,
):
    """One call to the plane's metered transcription route.

    ``content=None`` sends a real WAV of the stub engine's duration, because a
    test that measures seconds needs bytes with a header to measure. Passing
    ``b""`` sends an empty part on purpose — that is a case worth testing, so it
    is not swallowed by the default.
    """
    payload = make_wav(1.25) if content is None else content
    data = {key: str(value) for key, value in fields.items()}
    return client.post(
        "/v1/audio/transcriptions",
        files={"file": (filename, payload, content_type)},
        data=data,
        headers={"Authorization": f"Bearer {credential}"},
    )


def dub(
    client: TestClient,
    credential: str,
    content: bytes | None = None,
    *,
    voice: str = "hf_alpha",
    filename: str = "clip.wav",
    content_type: str = "audio/wav",
    **fields,
):
    """One call to the plane's metered dubbing route.

    ``content=None`` sends a real WAV, because a test that measures the upload
    needs bytes with a header; ``b""`` sends an empty part on purpose.
    """
    payload = make_wav(1.25) if content is None else content
    data = {"voice": voice, **{key: str(value) for key, value in fields.items()}}
    return client.post(
        "/v1/dubs",
        files={"file": (filename, payload, content_type)},
        data=data,
        headers={"Authorization": f"Bearer {credential}"},
    )


def fetch_dub_audio(client: TestClient, credential: str, dub_id: int):
    return client.get(
        f"/v1/dubs/{dub_id}/audio", headers={"Authorization": f"Bearer {credential}"}
    )


def create_job(client: TestClient, credential: str, text: str, **extra) -> dict:
    response = queue_job(client, credential, text, **extra)
    assert response.status_code == 202, response.text
    return response.json()["job"]


def get_job(client: TestClient, credential: str, job_id: int) -> dict:
    response = client.get(
        f"/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {credential}"}
    )
    assert response.status_code == 200, response.text
    return response.json()["job"]


def list_jobs(client: TestClient, credential: str, **params) -> dict:
    response = client.get(
        "/v1/jobs", headers={"Authorization": f"Bearer {credential}"}, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def fetch_job_audio(client: TestClient, credential: str, job_id: int):
    return client.get(
        f"/v1/jobs/{job_id}/audio", headers={"Authorization": f"Bearer {credential}"}
    )