"""Probe real Hindi speech from the unchanged API with a permissive CPU engine.

Why this exists: M1's acceptance asks for English and Hindi speech out of the
model's local API. The default (OmniVoice) model is licence-gated and its terms
are the owner's to accept; the English-only CPU engine that answered the last
round's English request refuses Hindi. This probe takes the third route: a model
whose publisher declares a permissive licence and that genuinely speaks Hindi,
run on CPU through the engine VoiceStudio already ships.

Engine: ``sherpa-onnx`` (Apache-2.0 runtime, already a dependency) loading
``kokoro-int8-multi-lang-v1_0`` — Kokoro-82M, apache-2.0, 82M parameters, 54
voices of which four are Hindi. The model is brought by the operator via
``OMNIVOICE_SHERPA_MODEL``; it is not in VoiceStudio's model catalogue, so the
catalogue's consent gate never applies to it (the probe accepts no terms for
anyone and installs no gated weights).

What a pass means, and nothing more: real WAV audio came back from the real
engine over the real HTTP API, for English and for Hindi, from two different
Hindi voices. It is NOT a listening check and NOT an intelligibility claim (no
human and no ASR judge the words), NOT GPU evidence, and NOT M1 acceptance. The
Hindi text is Devanagari and the voices are the model's Hindi voices; whether it
sounds like Hindi to an Indian listener is still the owner's to hear.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import m1_speech_probe as harness  # noqa: E402  (the shared M1 probe harness)

ENGINE = "sherpa-onnx"
HINDI_VOICES = ("hf_alpha", "hf_beta", "hm_omega", "hm_psi")
MODEL_FILES = ("model.int8.onnx", "tokens.txt", "voices.bin", "LICENSE", "README.md")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_provenance(model_dir: Path, result: dict) -> dict:
    """What the model directory actually is, hashed rather than asserted.

    The licence file is the bundle's own; the probe records it and says plainly
    that this is the publisher's declaration, not a legal clearance.
    """
    files = {}
    for name in MODEL_FILES:
        path = model_dir / name
        files[name] = ({"sha256": _sha256(path), "bytes": path.stat().st_size}
                       if path.is_file() else None)
    metadata = _read_metadata(model_dir, result)
    speakers = _read_speaker_ids(metadata, result)
    present = [name for name in HINDI_VOICES if name in speakers]
    result["model"] = {
        "directory": model_dir.name,
        "files": files,
        "onnx_metadata": metadata,
        "hindi_voices_in_table": present,
        "licence_claim": (
            "The bundle ships an Apache-2.0 LICENSE; the weights' publisher "
            "(hexgrad/Kokoro-82M) declares apache-2.0 and states the model was "
            "trained on permissive/non-copyrighted audio. Recorded as the "
            "publisher's declaration, NOT a legal clearance and NOT this run's "
            "decision to make — the production engine/model choice stays the "
            "owner's."
        ),
    }
    harness.require(len(present) >= 2,
                    "The loaded model does not declare the Hindi voices this probe needs")
    harness.require(files["LICENSE"], "The model bundle carries no licence file to record")
    return speakers


def _read_metadata(model_dir: Path, result: dict) -> dict[str, str]:
    """The model's own ONNX metadata, read with the application's own reader."""
    from services.tts_backend import _sherpa_onnx_string_metadata
    model_file = next((model_dir / name for name in
                       ("model.onnx", "model.int8.onnx") if (model_dir / name).is_file()), None)
    if model_file is None:
        raise harness.ProbeFailure("No ONNX graph found in the model directory")
    metadata = _sherpa_onnx_string_metadata(str(model_file))
    harness.require(metadata.get("model_type") == "kokoro",
                    f"Expected a kokoro-layout model, got {metadata.get('model_type')!r}")
    result["checks"].append("model_declares_kokoro_layout")
    return metadata


def _read_speaker_ids(metadata: dict[str, str], result: dict) -> dict[str, int]:
    from services.tts_backend import _sherpa_speaker_ids
    ids = _sherpa_speaker_ids(metadata)
    harness.require(ids, "The model declares no speaker table")
    return ids


def speech(base: str, result: dict, label: str, voice: str, text: str,
           language: str, expect: int = 200) -> dict:
    """One /v1/audio/speech request, measured and kept as evidence."""
    row: dict[str, Any] = {"label": label, "voice": voice, "language": language,
                           "expected_status": expect}
    result["requests"].append(row)
    started = time.monotonic()
    try:
        status, body, headers = harness.request_audio(base, "/v1/audio/speech", {
            "model": ENGINE, "voice": voice, "input": text,
            "language": language, "response_format": "wav",
        }, timeout=600.0)
    except BaseException as exc:
        row["failure_type"] = type(exc).__name__
        raise
    finally:
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
    row["http_status"] = status
    row["headers"] = headers
    if status != expect:
        try:
            error = json.loads(body).get("error", {})
            row["error_type"] = error.get("type")
            row["error_code"] = error.get("code")
            row["error_param"] = error.get("param")
            # The engine's own refusal text: it names the voices available.
            row["error_message"] = (error.get("message") or "")[:400]
        except (ValueError, UnicodeError, AttributeError):
            row["error_code"] = None
        return row
    if expect != 200:
        return row
    harness.require(headers.get("content-type", "").startswith("audio/"),
                    "Successful speech response is not audio")
    audio_path = result["_audio_dir"] / f"{label}.wav"
    audio_path.write_bytes(body)
    row["audio_sha256"] = hashlib.sha256(body).hexdigest()
    row["audio_bytes"] = len(body)
    row["audio_file"] = audio_path.name
    row["audio"] = harness.describe_wav(body)
    return row


def probe(base: str, result: dict, english: str, hindi: str, second_hindi_voice: str) -> None:
    harness.check_engine_identity(base, result, ENGINE)

    # The consent gate is a catalogue feature; this model is operator-supplied,
    # so the probe records that fact instead of claiming an acceptance.
    status, catalog = harness.request(base, "/models", timeout=180.0)
    harness.require(status == 200, "Model catalogue is not readable")
    rows = [row for row in (catalog.get("models") or [])
            if isinstance(row, dict) and ENGINE in (row.get("engines") or [])]
    result["catalogue_entries_for_engine"] = [
        {"repo_id": row.get("repo_id"), "installed": row.get("installed")} for row in rows
    ]
    result["catalogue_tts_entries"] = [
        entry for entry in result["catalogue_entries_for_engine"]
        if entry["repo_id"] and "tts" not in str(entry["repo_id"]).lower()
    ]
    result["acceptance_required"] = False
    result["model_terms_accepted_by_probe"] = 0
    result["claim_basis"] = (
        "the engine is the shipped sherpa-onnx runtime and the voice model is "
        "operator-supplied through OMNIVOICE_SHERPA_MODEL, so VoiceStudio's "
        "per-model catalogue acceptance gate does not cover it and was never "
        "invoked. The licence facts recorded here are the publisher's own "
        "declarations, not a legal clearance."
    )
    result["checks"].append("no_catalogue_acceptance_involved")

    hindi_row = speech(base, result, "hindi", "hf_alpha", hindi, "hi")
    harness.require(hindi_row.get("http_status") == 200,
                    "The Hindi request did not return audio")
    harness.require(hindi_row["audio"]["non_silent"] is True, "Hindi audio is silent")
    harness.require(hindi_row["audio"]["sample_rate"] >= 16000, "Hindi audio rate is implausible")
    result["hindi_audio"] = hindi_row["audio"]
    result["hindi_audio_produced"] = True
    result["checks"].append("hindi_audio_returned")

    second = speech(base, result, "hindi_second_voice", second_hindi_voice, hindi, "hi")
    harness.require(second.get("http_status") == 200,
                    "The second Hindi voice did not return audio")
    harness.require(second["audio"]["non_silent"] is True, "Second Hindi voice is silent")
    harness.require(second["audio_sha256"] != hindi_row["audio_sha256"],
                    "Two different voices produced identical audio")
    result["hindi_second_voice_audio"] = second["audio"]
    result["checks"].append("second_hindi_voice_is_distinct")

    english_row = speech(base, result, "english", "af_bella", english, "en")
    harness.require(english_row.get("http_status") == 200,
                    "The English request did not return audio")
    harness.require(english_row["audio"]["non_silent"] is True, "English audio is silent")
    result["english_audio"] = english_row["audio"]
    result["checks"].append("english_audio_returned")

    # Refusals must be client errors that name the model's actual voices.
    unknown = speech(base, result, "unknown_voice", "expr-voice-2-f", english, "en", expect=400)
    harness.require(unknown.get("http_status") == 400,
                    "An unknown voice was not refused with 400")
    harness.require("hf_alpha" in (unknown.get("error_message") or ""),
                    "The unknown-voice refusal does not list the model's voices")
    result["checks"].append("unknown_voice_refused_with_voice_list")

    out_of_range = speech(base, result, "out_of_range_speaker", "99", english, "en", expect=400)
    harness.require(out_of_range.get("http_status") == 400,
                    "An out-of-range speaker was not refused with 400")
    result["checks"].append("out_of_range_speaker_refused")


def run(output: Path, model_dir: Path, english: str, hindi: str,
        second_hindi_voice: str) -> int:
    harness.require(os.name == "posix", "This process harness requires POSIX pass_fds")
    harness.require(model_dir.is_dir(), f"Model directory does not exist: {model_dir}")
    harness.require(not output.exists(), "Evidence directory already exists")
    root = Path(__file__).resolve().parents[1]
    harness.require_clean_checkout(root)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result: dict[str, Any] = {
        "_audio_dir": output / "audio",
        "scope": "cpu_english_and_hindi_speech_operator_supplied_kokoro",
        "engine": ENGINE, "m1_complete": False, "gpu_verified": False,
        "hindi_audio_produced": False, "intelligibility_verified": False,
        "listening_verified": False, "checks": [], "requests": [],
        "honesty_note": (
            "hindi_audio_produced means the real engine returned non-silent audio "
            "for Devanagari text from a voice the model declares as Hindi. Nobody "
            "has listened to it and no ASR has checked the words, so no "
            "intelligibility or acceptance claim is made."
        ),
        "host": harness.host_metadata(),
    }
    result["_audio_dir"].mkdir()
    try:
        result["speaker_ids"] = model_provenance(model_dir, result)
    except harness.ProbeFailure as exc:
        result["failure_type"] = "ProbeFailure"
        result["failure"] = str(exc)
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 1

    with tempfile.TemporaryDirectory(prefix="voicestudio-hindi-probe-") as state:
        env = harness.build_env(state, ENGINE, None)
        env["OMNIVOICE_SHERPA_MODEL"] = str(model_dir)
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
                health = harness.wait_ready(base, process)
                harness.require(health.get("device") == "cpu", "This probe requires CPU mode")
                result["health"] = health
                result["ready_seconds"] = round(time.monotonic() - started, 3)
                probe(base, result, english, hindi, second_hindi_voice)
            except Exception as exc:  # noqa: BLE001 - report failure, always stop the backend
                result["failure_type"] = type(exc).__name__
                if isinstance(exc, harness.ProbeFailure):
                    result["failure"] = str(exc)
            finally:
                alive_before_stop = process is not None and process.poll() is None
                if process is not None:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        result["forced_shutdown"] = True
                result["server_exit_code"] = process.returncode if process is not None else None
                log.flush()
                shutdown_log = (output / "server.log").read_text(errors="replace")
                result["startup_stall_warning"] = "Event loop has not run" in shutdown_log
                result["shutdown_verified"] = alive_before_stop and harness.clean_shutdown(
                    result["server_exit_code"], result.get("forced_shutdown", False), shutdown_log)
                if not result["shutdown_verified"]:
                    result["hindi_audio_produced"] = False
                result["source_sha256"] = {
                    name: _sha256(root / name)
                    for name in ("backend/main.py", "backend/services/tts_backend.py",
                                 "backend/api/routers/openai_compat.py", "uv.lock",
                                 "scripts/m1_hindi_probe.py",
                                 "tests/test_sherpa_kokoro_and_speaker.py")
                }
                try:
                    result.update(harness.record_revision(root, output))
                except Exception as exc:  # noqa: BLE001 - evidence survives a git failure
                    result["revision_unavailable"] = type(exc).__name__
                result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                result.pop("_audio_dir")
                (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    passed = (result["hindi_audio_produced"]
              and "english_audio_returned" in result["checks"]
              and not result.get("failure_type"))
    return 0 if passed else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    parser.add_argument("--model-dir", type=Path, required=True,
                        help="sherpa-onnx Kokoro model directory")
    parser.add_argument("--english", default=(
        "VoiceStudio is generating this English sentence on a CPU with no GPU."))
    parser.add_argument("--hindi", default=(
        "यह एक हिन्दी वाक्य है। भारत में बहुत सारी भाषाएँ बोली जाती हैं।"))
    parser.add_argument("--second-hindi-voice", default="hm_omega",
                        choices=list(HINDI_VOICES))
    args = parser.parse_args(argv)
    return run(args.output, args.model_dir, args.english, args.hindi,
               args.second_hindi_voice)


if __name__ == "__main__":
    raise SystemExit(main())