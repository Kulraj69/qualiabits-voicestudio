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
from fastapi import FastAPI, Response  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
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
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        stub = FastAPI()
        state = self

        @stub.get("/health")
        async def health() -> dict:
            return {"status": "ok", "device": "cpu", "version": "stub"}

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

        return stub

    def reset(self) -> None:
        self.payloads.clear()
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
):
    """A real control plane, its own database, pointed at the stub engine."""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / name}",
        upstream_base_url=upstream_base,
        upstream_timeout_seconds=30.0,
        session_ttl_seconds=ttl,
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