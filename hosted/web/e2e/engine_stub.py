#!/usr/bin/env python3
"""A stand-in for the engine, for the browser end-to-end.

The browser suite drives real Chromium against a real control plane and a real
web server; the one substitution is the engine, and it is named as such here.
This is the same shape as the stub the Python suite uses
(``tests/hosted/conftest.py``): it answers ``/health``, the voice catalogue, the
engine inventory and ``/v1/audio/speech``, and it refuses a voice the model does
not have with its own sentence — so the page can be checked against the engine's
refusal, not a rewritten one.

Point the suite at the real engine instead with ``QB_E2E_ENGINE_CMD``; this file
is then unused.

    python hosted/web/e2e/engine_stub.py --port 39100
"""
from __future__ import annotations

import argparse
import io
import struct
import wave

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

#: The ids the active engine declares. `hf_alpha` is one a dashboard picks; the
#: Hindi ids make the list look like the Kokoro bundle's own.
PRESET_VOICES = ["af_alloy", "hf_alpha", "hm_omega"]
DEFAULT_VOICE = "af_alloy"
SAMPLE_RATE = 24000
AUDIO_SECONDS = 1.5


def make_wav(seconds: float) -> bytes:
    frames = int(round(seconds * SAMPLE_RATE))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(struct.pack("<h", 1200) * frames)
    return buffer.getvalue()


def refusal(voice: str) -> dict:
    """The engine's own sentence for a voice it does not have."""
    return {
        "detail": (
            f"Voice '{voice}' is not one of the model's voices: "
            + ", ".join(PRESET_VOICES)
            + "."
        ),
        "param": "voice",
        "code": "unknown_voice",
    }


app = FastAPI(title="engine stub")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "device": "cpu", "version": "engine-stub"}


@app.get("/v1/audio/voices")
async def voices() -> dict:
    # The catalogue the real engine answers with: aliases and profiles, and none
    # of the ids a model declares in its own file.
    return {
        "voices": [
            {"voice_id": "alloy", "name": "Alloy", "type": "openai_alias"},
            {"voice_id": "echo", "name": "Echo", "type": "openai_alias"},
            {"voice_id": "profile-1", "name": "Studio read", "type": "profile", "language": "hi"},
        ],
        "engines": [{"id": "sherpa-onnx", "available": True}],
    }


@app.get("/engines/tts")
async def engines_tts() -> dict:
    return {
        "active": "sherpa-onnx",
        "backends": [
            {
                "id": "sherpa-onnx",
                "display_name": "sherpa-onnx",
                "available": True,
                "preset_voices": PRESET_VOICES,
                "default_voice": DEFAULT_VOICE,
            }
        ],
    }


@app.post("/v1/audio/speech")
async def speech(payload: dict) -> Response:
    voice = str(payload.get("voice") or "")
    if voice not in PRESET_VOICES:
        return JSONResponse(status_code=400, content=refusal(voice))
    return Response(
        content=make_wav(AUDIO_SECONDS),
        media_type="audio/wav",
        headers={"x-omnivoice-backend": "sherpa-onnx"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="engine stub for the browser end-to-end")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()