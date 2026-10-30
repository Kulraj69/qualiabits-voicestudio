"""Probe real English speech from the unchanged API with an ungated CPU engine.

Why this exists: the licence gate blocks the default model. This probe selects an
engine whose catalogue licence category is already ``commercial`` (so the app
requires no acceptance), runs the real, unmodified backend on CPU with empty
state, asks ``/v1/audio/speech`` for a WAV, and measures the returned audio
structurally (RIFF/WAVE, sample rate, duration, peak, RMS).

What a pass means, and nothing more: real audio bytes came back from the real
engine. It is not intelligibility evidence (no ASR or listening here), not Hindi
evidence, not GPU evidence, and not M1 or production acceptance. The probe never
accepts model terms, installs the default weights, or copies provider
credentials. All flags stay false unless the running model genuinely produced
measurable audio.

Engine identity: ``GET /engines`` must report this engine as the active TTS
backend with ``env_override`` set, so the audio provably came from the ungated
engine. A refusal control cannot live in this run: the backend override wins over
a per-request ``model`` field, so asking for the gated default here would still be
served by the ungated engine. The gated refusal is asserted by
``scripts/headless_baseline.py`` (which boots the default backend) and by the
unit tests in ``tests/test_m1_speech_probe.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Engines whose catalogue models are declared category ``commercial`` in
# backend/config/model_licenses.json, so model_acceptance requires no acceptance.
UNGATED_ENGINES = ("kittentts", "moss-tts-nano", "cosyvoice-gguf")
MAX_AUDIO = 24 * 1024 * 1024
SILENCE_PEAK = 1e-4
# Only these response headers are copied into evidence; everything else (cookies,
# proxy headers, auth echoes) stays out of the report.
HEADER_ALLOWLIST = ("content-type", "content-length", "content-disposition")


class ProbeFailure(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeFailure("Unexpected HTTP redirect")


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def require(condition, message):
    if not condition:
        raise ProbeFailure(message)


def _fetch(base: str, path: str, payload: dict | None, timeout: float) -> tuple[int, bytes, dict]:
    """One request; never follows redirects; body capped."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        response = OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(MAX_AUDIO + 1)
        require(len(body) <= MAX_AUDIO, "Response exceeded probe size limit")
        require(response.headers.get("x-omnivoice-backend"),
                "Response is not marked as VoiceStudio")
        headers = {
            **{k: v for k, v in response.headers.items() if k.lower() in HEADER_ALLOWLIST},
            **{k: v for k, v in response.headers.items() if k.lower().startswith("x-omnivoice-")},
        }
        status = response.status
        if not isinstance(status, int):  # narrowing the stdlib's Optional[int]
            raise ProbeFailure("Response has no HTTP status")
        return status, body, headers


def request(base: str, path: str, payload: dict | None = None,
            timeout: float = 15.0) -> tuple[int, dict]:
    """One JSON request."""
    status, body, _headers = _fetch(base, path, payload, timeout)
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeError):
        raise ProbeFailure("Expected JSON, not audio or HTML") from None
    require(isinstance(parsed, dict), "Expected a JSON object")
    return status, parsed


def request_audio(base: str, path: str, payload: dict | None = None,
                  timeout: float = 300.0) -> tuple[int, bytes, dict]:
    """One audio request; returns status, bytes and allow-listed headers."""
    return _fetch(base, path, payload, timeout)


def describe_wav(raw: bytes) -> dict:
    """Structural measurement of a RIFF/WAVE stream. Raises on a malformed file."""
    require(len(raw) >= 44, "Audio is shorter than a WAV header")
    require(raw[0:4] == b"RIFF" and raw[8:12] == b"WAVE", "Audio is not a RIFF/WAVE stream")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        chunk_id = raw[pos:pos + 4]
        size = int.from_bytes(raw[pos + 4:pos + 8], "little")
        start = pos + 8
        end = min(start + size, len(raw))
        if chunk_id == b"fmt " and fmt is None:
            require(end - start >= 16, "Malformed WAV format chunk")
            fmt = {
                "audio_format": int.from_bytes(raw[start:start + 2], "little"),
                "channels": int.from_bytes(raw[start + 2:start + 4], "little"),
                "sample_rate": int.from_bytes(raw[start + 4:start + 8], "little"),
                "byte_rate": int.from_bytes(raw[start + 8:start + 12], "little"),
                "bits_per_sample": int.from_bytes(raw[start + 14:start + 16], "little"),
            }
        elif chunk_id == b"data" and data is None:
            data = (start, end, size)
        pos = start + size + (size & 1)
    if fmt is None:
        raise ProbeFailure("WAV has no format chunk")
    if data is None:
        raise ProbeFailure("WAV has no data chunk")
    require(fmt["channels"] >= 1 and fmt["sample_rate"] >= 8000, "Implausible WAV format")
    require(fmt["bits_per_sample"] in (8, 16, 24, 32), "Unsupported WAV bit depth")
    start, end, declared = data
    truncated = start + declared > len(raw)
    payload = raw[start:end]
    require(len(payload) > 0, "WAV data chunk is empty")
    frames = len(payload) // (fmt["channels"] * (fmt["bits_per_sample"] // 8))
    peak, rms = _levels(payload, fmt)
    return {
        **fmt,
        "data_bytes": len(payload),
        "data_truncated": truncated,
        "frames": frames,
        "duration_seconds": round(frames / fmt["sample_rate"], 3),
        "peak": round(peak, 6),
        "rms": round(rms, 6),
        "non_silent": peak > SILENCE_PEAK,
    }


def _levels(payload: bytes, fmt: dict) -> tuple[float, float]:
    """Peak and RMS of the sample payload, normalised to 0..1."""
    bits, channels = fmt["bits_per_sample"], fmt["channels"]
    peak = 0.0
    total = 0.0
    count = 0
    step = channels * (bits // 8)
    for offset in range(0, len(payload) - step + 1, step):
        for channel in range(channels):
            at = offset + channel * (bits // 8)
            if bits == 16:
                value = struct.unpack_from("<h", payload, at)[0] / 32768.0
            elif bits == 24:
                raw = int.from_bytes(payload[at:at + 3], "little", signed=True)
                value = raw / 8388608.0
            elif bits == 32:
                if fmt.get("audio_format") == 3:
                    value = struct.unpack_from("<f", payload, at)[0]
                    value = max(-1.0, min(1.0, value))
                else:
                    value = struct.unpack_from("<i", payload, at)[0] / 2147483648.0
            else:
                value = (payload[at] - 128) / 128.0
            peak = max(peak, abs(value))
            total += value * value
            count += 1
    return peak, math.sqrt(total / count) if count else 0.0


def require_clean_checkout(root: Path) -> None:
    """An empty .env sentinel stops dotenv from reading ancestor configuration."""
    for directory in (root, root / "backend", *root.parents):
        candidate = directory / ".env"
        if candidate.is_symlink():
            raise ProbeFailure("Use a clean checkout without .env symlinks")
        if candidate.is_file():
            require(candidate.stat().st_size == 0,
                    "Use a clean checkout without nonempty .env files")
            return
        if candidate.exists():
            raise ProbeFailure("Use a clean checkout without nonempty .env files")


def wait_ready(base: str, process: subprocess.Popen, seconds: float = 180.0) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        require(process.poll() is None, "Backend exited during startup")
        try:
            status, body = request(base, "/health",
                                   timeout=min(5.0, max(.01, deadline - time.monotonic())))
            if status == 200:
                require(body.get("status") == "ok", "Unexpected health response")
                return body
            require(status == 503 and body.get("status") == "starting",
                    "Unexpected startup response")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        time.sleep(.25)
    raise ProbeFailure("Backend did not become ready within the startup deadline")


def clean_shutdown(returncode, forced: bool, log_text: str) -> bool:
    # Uvicorn re-raises captured SIGTERM after running lifespan shutdown.
    return (not forced and returncode in (0, -signal.SIGTERM)
            and "Application shutdown complete." in log_text
            and "Finished server process" in log_text)


def host_metadata() -> dict:
    """What the timings below were measured on."""
    cpu_model = None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        cpu_model = None
    return {
        "platform": platform.platform(), "machine": platform.machine(),
        "cpu_model": cpu_model, "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }


def build_env(state: str, engine: str, hf_cache: Path | None) -> dict:
    """Minimal environment for the backend: no provider credentials, own caches.

    ``HF_HUB_CACHE`` must be set, not just inferred: the first caller of this
    probe passed ``--hf-cache`` and the model still landed in the default
    ``~/.cache/huggingface``, because the path was only used to decide the
    offline flag.
    """
    cache = str(hf_cache) if hf_cache is not None else str(Path(state) / "hf-cache")
    env = {
        "PATH": os.environ.get("PATH", os.defpath), "HOME": state,
        "TMPDIR": state, "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_CACHE": cache,
        "OMNIVOICE_DATA_DIR": str(Path(state) / "data"),
        "OMNIVOICE_ENV_FILE": str(Path(state) / "user-env"),
        "OMNIVOICE_CACHE_DIR": str(Path(state) / "cache"),
        "OMNIVOICE_TTS_BACKEND": engine,
    }
    if hf_cache is None:
        # No writable cache was offered, so the weights cannot be fetched.
        env["HF_HUB_OFFLINE"] = "1"
    return env


def check_engine_identity(base: str, result: dict, engine: str) -> None:
    """The running engine must be the ungated one, and say so itself.

    ``/engines`` probes every backend for availability and took 8.0-8.8 s on this
    2-vCPU box, so the default 15 s request timeout is not enough headroom: a
    fresh-install run timed out here (2026-10-30 21:39) and lost the whole round.
    The catalogue read gets its own generous bound.
    """
    status, engines = request(base, "/engines", timeout=180.0)
    require(status == 200, "Engine catalogue is not readable")
    tts = engines.get("tts")
    if not isinstance(tts, dict) or not isinstance(tts.get("backends"), list):
        raise ProbeFailure("TTS engine catalogue is malformed")
    result["active_tts_backend"] = tts.get("active")
    result["env_override"] = tts.get("env_override")
    require(tts.get("active") == engine,
            f"Active TTS backend is {tts.get('active')!r}, not the requested {engine!r}")
    require(tts.get("env_override") is True,
            "Engine was not selected by the environment override; the probe cannot "
            "attribute the audio to an ungated engine")
    ids = [entry.get("id") for entry in tts["backends"] if isinstance(entry, dict)]
    require(engine in ids, "Requested engine is missing from the engine catalogue")
    result["checks"].append("ungated_engine_is_active")


def check_licence_provenance(base: str, result: dict, engine: str) -> None:
    """Record why this engine needs no acceptance, from the API itself.

    The claim "no owner acceptance was needed" must travel with the evidence, not
    live only in prose. ``/models`` names the repository this engine loads;
    ``/models/licenses/acceptance/<repo>`` reports the declared licence and
    whether acceptance is required.
    """
    status, catalog = request(base, "/models", timeout=180.0)
    require(status == 200, "Model catalogue is not readable")
    rows = catalog.get("models")
    if not isinstance(rows, list) or not rows:
        raise ProbeFailure("Model catalogue is empty")
    mine = [row for row in rows
            if isinstance(row, dict) and engine in (row.get("engines") or [])]
    require(mine, f"No catalogue model is registered for engine {engine!r}")
    provenance = []
    for row in mine:
        repo = row.get("repo_id")
        if not isinstance(repo, str) or not repo:
            raise ProbeFailure("Catalogue row has no repo_id")
        entry: dict[str, Any] = {
            "repo_id": repo, "installed": row.get("installed"),
            "size_gb": row.get("size_gb"),
        }
        for key, path in (
            ("acceptance", "/models/licenses/acceptance/"),
            ("disclosure", "/models/licenses/details/"),
        ):
            code, body = request(base, path + repo, timeout=120.0)
            if code != 200 or not isinstance(body, dict):
                entry[f"{key}_readable"] = False
                continue
            entry[f"{key}_readable"] = True
            if key == "acceptance":
                entry.update({k: body.get(k) for k in
                              ("license", "category", "required", "accepted", "state")})
            else:
                raw_info = body.get("info")
                info: dict = raw_info if isinstance(raw_info, dict) else {}
                entry["registry_version"] = info.get("registry_version")
                entry["readiness"] = info.get("readiness")
                entry["blockers"] = info.get("blockers")
        provenance.append(entry)
    result["licence_provenance"] = provenance
    require(any(entry.get("required") is False for entry in provenance),
            "No model registered for this engine is free of the acceptance gate")
    for entry in provenance:
        if entry.get("required") is False:
            require(entry.get("accepted") is True,
                    "An ungated model reported itself as not accepted")
    result["checks"].append("licence_required_is_false")


def synthesise(base: str, result: dict, engine: str, text: str, language: str,
               voice: str, label: str) -> dict:
    """One measured /v1/audio/speech request, with the audio measured if returned."""
    row: dict[str, Any] = {"label": label, "engine": engine, "language": language}
    result["requests"].append(row)
    started = time.monotonic()
    try:
        status, body, headers = request_audio(base, "/v1/audio/speech", {
            "model": engine, "voice": voice, "input": text,
            "language": language, "response_format": "wav",
        })
    except BaseException as exc:
        row["failure_type"] = type(exc).__name__
        raise
    finally:
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
    row["http_status"] = status
    row["headers"] = headers
    if status != 200:
        try:
            error = json.loads(body).get("error", {})
            row["error_code"] = error.get("code")
        except (ValueError, UnicodeError, AttributeError):
            row["error_code"] = None
        row["audio_generated"] = False
        return row
    require(headers.get("content-type", "").startswith("audio/"),
            "Successful speech response is not audio")
    audio_path = result["_audio_dir"] / f"{label}.wav"
    audio_path.write_bytes(body)
    row["audio_sha256"] = hashlib.sha256(body).hexdigest()
    row["audio_bytes"] = len(body)
    row["audio_file"] = audio_path.name
    try:
        row["audio"] = describe_wav(body)
    except ProbeFailure as exc:
        row["audio_generated"] = True
        row["audio_valid"] = False
        row["audio_failure"] = str(exc)
        return row
    row["audio_generated"] = True
    row["audio_valid"] = not row["audio"]["data_truncated"]
    return row


def probe(base: str, result: dict, engine: str, english: str, hindi: str | None,
          voice: str) -> None:
    result.update({
        "scope": "cpu_english_speech_smoke_ungated_engine", "engine": engine,
        "m1_complete": False, "gpu_verified": False, "hindi_verified": False,
        "intelligibility_verified": False, "speech_produced": False, "checks": [],
        "requests": [], "model_terms_accepted_by_probe": 0,
        "host": host_metadata(),
        "timing_note": ("elapsed_seconds are client-observed, end-to-end HTTP times "
                        "for one /v1/audio/speech request. On a cold cache they "
                        "include the model download, the model load and the "
                        "AudioSeal watermark checkpoint. They are not "
                        "synthesis-only latency and not a benchmark."),
    })
    check_engine_identity(base, result, engine)
    check_licence_provenance(base, result, engine)
    row = synthesise(base, result, engine, english, "en", voice, "english")
    require(row.get("http_status") == 200, "Ungated engine did not return audio")
    require(row.get("audio_valid") is True, "Returned audio is not a valid WAV stream")
    require(row["audio"]["non_silent"] is True, "Returned audio is silent")
    result["speech_produced"] = True
    result["english_audio"] = row["audio"]
    result["checks"].append("english_audio_returned")
    result["english_cold_seconds"] = row["elapsed_seconds"]
    # A second request in the same process is the warm path: no download, no
    # first-load. Recorded separately so the cold number is never quoted as
    # synthesis speed.
    warm = synthesise(base, result, engine, english, "en", voice, "english_warm")
    require(warm.get("http_status") == 200 and warm.get("audio_valid") is True
            and warm["audio"]["non_silent"] is True,
            "Warm request did not return valid audible audio")
    result["english_warm_seconds"] = warm["elapsed_seconds"]
    result["checks"].append("warm_request_returned_audio")
    if hindi:
        row = synthesise(base, result, engine, hindi, "hi", voice, "hindi")
        result["hindi_attempt"] = row
        if row["http_status"] == 200:
            # The engine genuinely spoke Hindi; still not a listening check.
            result["checks"].append("hindi_audio_returned")
        else:
            # An engine that cannot speak the language must refuse as a CLIENT
            # error. A 5xx here is a defect, not an unsupported-language answer.
            require(row["http_status"] < 500,
                    "Unsupported language returned a server error, not a client error")
            result["checks"].append("hindi_refused_as_client_error")
    result["checks"].append("hindi_attempt_recorded")


def run(output: Path, engine: str, english: str, hindi: str | None, voice: str,
        hf_cache: Path | None) -> int:
    require(os.name == "posix", "This process harness requires POSIX pass_fds")
    require(engine in UNGATED_ENGINES, f"Engine is not a known ungated CPU engine: {engine}")
    require(not output.exists(), "Evidence directory already exists")
    root = Path(__file__).resolve().parents[1]
    require_clean_checkout(root)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result: dict[str, Any] = {"_audio_dir": output / "audio"}
    result["_audio_dir"].mkdir()
    result["hf_cache_pre_populated"] = bool(
        hf_cache is not None and hf_cache.is_dir() and any(hf_cache.iterdir()))
    with tempfile.TemporaryDirectory(prefix="voicestudio-speech-probe-") as state:
        env = build_env(state, engine, hf_cache)
        with socket.socket() as listener, (output / "server.log").open("w") as log:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            base = f"http://127.0.0.1:{listener.getsockname()[1]}"
            process = None
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "backend.main:app", "--fd",
                     str(listener.fileno()), "--no-proxy-headers"],
                    cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, pass_fds=(listener.fileno(),),
                )
                health = wait_ready(base, process)
                require(health.get("device") == "cpu", "This probe requires CPU mode")
                result["health"] = health
                result["ready_seconds"] = round(time.monotonic() - started, 3)
                probe(base, result, engine, english, hindi, voice)
            except Exception as exc:  # noqa: BLE001 - report failure, always stop the backend
                result["failure_type"] = type(exc).__name__
                if isinstance(exc, ProbeFailure):
                    result["failure"] = str(exc)
            finally:
                alive_before_stop = process is not None and process.poll() is None
                if process is not None:
                    process.terminate()
                    try:
                        # 30 s, not 15: a run that already hit a timeout leaves the
                        # catalogue probe running, and uvicorn waits for it during
                        # shutdown (a 15 s window killed the process mid-shutdown).
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        result["forced_shutdown"] = True
                result["server_exit_code"] = process.returncode if process is not None else None
                log.flush()
                shutdown_log = (output / "server.log").read_text(errors="replace")
                result["startup_stall_warning"] = "Event loop has not run" in shutdown_log
                result["shutdown_verified"] = alive_before_stop and clean_shutdown(
                    result["server_exit_code"], result.get("forced_shutdown", False), shutdown_log)
                if not result["shutdown_verified"]:
                    result["speech_produced"] = False
                result["source_sha256"] = {
                    name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                    for name in ("backend/main.py", "backend/services/tts_backend.py",
                                 "uv.lock", "scripts/m1_speech_probe.py",
                                 "tests/test_m1_speech_probe.py")
                }
                result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                result.pop("_audio_dir")
                (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["speech_produced"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--engine", default="kittentts", choices=list(UNGATED_ENGINES))
    parser.add_argument("--voice", default="expr-voice-2-f")
    parser.add_argument("--english", default="VoiceStudio is generating this sentence on a CPU with no GPU.")
    parser.add_argument("--hindi", default="यह एक हिन्दी वाक्य है।",
                        help="Pass an empty string to skip the Hindi attempt")
    parser.add_argument("--hf-cache", type=Path, default=None,
                        help="Writable HuggingFace cache; enables the one model download")
    args = parser.parse_args(argv)
    return run(args.output.resolve(), args.engine, args.english,
               args.hindi or None, args.voice, args.hf_cache)


if __name__ == "__main__":
    raise SystemExit(main())