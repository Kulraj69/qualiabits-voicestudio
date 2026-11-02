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

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

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


#: What this stub "hears". It cannot recognise anything — that is what the real
#: engine run is for — so the browser suite's stub pass checks the wiring and the
#: measurement, and the real-engine pass checks the words. The sentence is
#: deliberately *not* the one the suite speaks, so a stub answer can never be
#: mistaken for a real transcript in an evidence file.
STUB_TRANSCRIPT = "यह स्टब का जवाब है।"


def wav_seconds(body: bytes) -> float | None:
    """Duration from a RIFF/WAVE payload's own header, or None for anything else.

    The same reader the plane uses, on the bytes the stub was actually sent: a
    stub that echoes a duration it was told would prove nothing about whether the
    browser really uploaded a file.
    """
    if len(body) < 44 or body[0:4] != b"RIFF" or body[8:12] != b"WAVE":
        return None
    offset, byte_rate, data_size = 12, None, None
    while offset + 8 <= len(body):
        chunk_id = body[offset : offset + 4]
        (chunk_size,) = struct.unpack_from("<I", body, offset + 4)
        payload_at = offset + 8
        if chunk_id == b"fmt " and chunk_size >= 16 and payload_at + 16 <= len(body):
            (byte_rate,) = struct.unpack_from("<I", body, payload_at + 8)
        elif chunk_id == b"data":
            data_size = min(chunk_size, max(0, len(body) - payload_at))
            break
        offset = payload_at + chunk_size + (chunk_size % 2)
    if not byte_rate or data_size is None:
        return None
    return round(data_size / byte_rate, 6)


@app.post("/v1/audio/transcriptions")
async def transcribe(request: Request) -> Response:
    form = await request.form()
    upload = form.get("file")
    raw = b""
    if isinstance(upload, UploadFile):
        raw = await upload.read()
    seconds = wav_seconds(raw)
    if str(form.get("response_format") or "json") == "text":
        return Response(content=STUB_TRANSCRIPT, media_type="text/plain")
    payload = {
        "task": "transcribe",
        "language": str(form.get("language") or "hi"),
        "duration": seconds,
        "text": STUB_TRANSCRIPT,
        "segments": [
            {"id": 0, "seek": 0, "start": 0.0, "end": seconds or 0.0,
             "text": f" {STUB_TRANSCRIPT}", "tokens": [], "temperature": 0.0,
             "avg_logprob": 0.0, "compression_ratio": 0.0, "no_speech_prob": 0.0}
        ],
        "uploaded_bytes": len(raw),
    }
    return JSONResponse(content=payload, headers={"x-omnivoice-backend": "engine-stub"})


def main() -> None:
    parser = argparse.ArgumentParser(description="engine stub for the browser end-to-end")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()