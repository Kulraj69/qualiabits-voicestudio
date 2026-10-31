#!/usr/bin/env python3
"""Run the hosted control plane against the real engine, and prove it end to end.

    .venv/bin/python hosted/scripts/hosted_e2e.py --output /path/to/new-evidence-dir \\
        --model /workspace/m1-models/kokoro-int8-multi-lang-v1_0

Two real processes, one reserved loopback socket each, no mocks:

1. the fork's own backend (``backend.main:app``) with the permissive CPU engine
   (``sherpa-onnx`` + the operator-supplied Kokoro model) — the same engine and
   model the M1 probe uses;
2. the control plane (``hosted.control_plane.app:app``) pointed at it.

Then it signs up two workspaces, mints keys, speaks Hindi and English through the
control plane, and checks the meter against the audio: characters from the text,
seconds from the returned WAV's own header.

What a pass means: a signed-up customer, holding a key we minted, gets real audio
from the real engine through the hosted layer, and the meter attributes it to that
customer and to nobody else. What it does not mean: nothing is deployed, nothing
is public, no GPU, no listening check by a human, and M1 is still open. Those are
recorded as false in ``result.json`` on purpose.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import m1_speech_probe as m1  # noqa: E402  (env building, readiness, WAV measuring)

ROOT = Path(__file__).resolve().parents[2]
ENGINE = "sherpa-onnx"
HINDI = "यह एक हिन्दी वाक्य है।"
#: What the job section speaks: a paragraph rather than a sentence, which is the
#: shape of work a queue is really for. It is not near the 20,000-character
#: ceiling — on this CPU engine every second of speech costs several seconds of
#: compute, so a full-length job would add minutes to a run that is already
#: minutes long. The ceiling itself is asserted in the unit suite, not here.
HINDI_LONG = (
    "नमस्ते। यह एक लंबा हिन्दी वाक्य है जो कतार से बोला जाता है। "
    "इसमें एक सौ से अधिक अक्षर हैं, ताकि यह सीधे अनुरोध से बड़ा काम लगे। "
    "यह साबित करता है कि लंबा काम कतार में जाता है और ऑब्जेक्ट स्टोर में रखा जाता है।"
)
ENGLISH = "This sentence is spoken through the hosted control plane."
HINDI_VOICE = "hf_alpha"
HINDI_VOICE_2 = "hm_omega"
PASSWORD = "correct horse battery staple"


class Failure(Exception):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise Failure(message)


def http(base: str, path: str, payload: dict | None = None, *,
         method: str = "POST", token: str | None = None,
         timeout: float = 300.0) -> tuple[int, bytes, dict]:
    """One request. Keeps its own headers; does not require the engine's marker."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        status = response.status
        body = response.read()
        seen = {k.lower(): v for k, v in response.headers.items()}
    assert isinstance(status, int), "response had no status"
    return status, body, seen


def json_body(raw: bytes) -> dict:
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise Failure("expected JSON, got something else") from None
    require(isinstance(parsed, dict), "expected a JSON object")
    return parsed


def free_port() -> tuple[socket.socket, str]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener, f"http://127.0.0.1:{listener.getsockname()[1]}"


def start(command: list[str], env: dict, log: Path, listener: socket.socket,
          handles: dict) -> subprocess.Popen:
    handle = log.open("w")
    process = subprocess.Popen(
        command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, pass_fds=(listener.fileno(),),
    )
    handles[process.pid] = handle  # held open until stop() closes it
    return process


def stop(process: subprocess.Popen | None, log: Path, handles: dict) -> dict:
    """Terminate, wait, and report whether the app shut itself down cleanly."""
    if process is None:
        return {"started": False}
    alive_before = process.poll() is None
    process.terminate()
    forced = False
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        forced = True
    handle = handles.pop(process.pid, None)
    if handle is not None:
        handle.close()
    text = log.read_text(errors="replace")
    return {
        "started": True,
        "exit_code": process.returncode,
        "forced": forced,
        "clean": bool(
            alive_before and not forced
            and process.returncode in (0, -signal.SIGTERM)
            and "Application shutdown complete." in text
        ),
    }


def wait_for(base: str, path: str, process: subprocess.Popen, what: str,
             seconds: float = 240.0) -> dict:
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        require(process.poll() is None, f"{what} exited during startup")
        try:
            status, body, _ = http(base, path, None, method="GET", timeout=5.0)
            if status == 200:
                return json_body(body)
            last = status
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            last = "unreachable"
        time.sleep(0.25)
    raise Failure(f"{what} was not ready within {seconds:.0f}s (last: {last})")


def speech_payload(text: str, voice: str, language: str) -> dict:
    """The body the engine's own probe sends.

    ``model`` and ``language`` are part of it. Leaving ``model`` out is what the
    engine reads as "use the default engine", and the default here is the gated
    OmniVoice one — the first version of this harness did that and got its own
    refusal back from the engine, which is how the field was found.
    """
    return {"model": ENGINE, "voice": voice, "input": text, "language": language,
            "response_format": "wav"}


def held_out() -> dict:
    """The engine's refusal for a voice the model does not have."""
    return speech_payload(ENGLISH, "definitely-not-a-voice", "en")


def run(output: Path, model: Path) -> int:
    require(not output.exists(), "Evidence directory already exists")
    require(model.is_dir(), f"No model directory at {model}")
    require((model / "tokens.txt").is_file() and (model / "LICENSE").is_file(),
            "The model directory has no tokens.txt/LICENSE; point --model at the unpacked bundle")
    licence = [
        line.strip() for line in
        (model / "LICENSE").read_text(errors="replace").splitlines() if line.strip()
    ]
    output.mkdir(parents=True)
    audio_dir = output / "audio"
    audio_dir.mkdir()

    result: dict[str, Any] = {
        "run": "hosted control plane end to end",
        "engine": ENGINE,
        "model_dir": str(model),
        "model_licence_first_line": licence[0] if licence else "",
        "checks": [],
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "m1_complete": False,
        "gpu_verified": False,
        "listening_verified": False,
        "deployed": False,
        "public_url": None,
    }

    engine_proc = plane_proc = None
    handles: dict = {}
    with tempfile.TemporaryDirectory(prefix="hosted-e2e-") as state:
        engine_listener, engine_base = free_port()
        plane_listener, plane_base = free_port()
        engine_env = m1.build_env(state, ENGINE, None)
        engine_env["OMNIVOICE_SHERPA_MODEL"] = str(model)
        plane_env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": state,
            "TMPDIR": state,
            "QUALI_STATE_DIR": state,
            "QUALI_DATABASE_URL": f"sqlite:///{Path(state) / 'control-plane.db'}",
            "QUALI_UPSTREAM_BASE_URL": engine_base,
            "QUALI_UPSTREAM_TIMEOUT_SECONDS": "300",
            "PYTHONPATH": str(ROOT),
        }
        started = time.monotonic()
        try:
            engine_proc = start(
                [sys.executable, "-m", "uvicorn", "backend.main:app", "--fd",
                 str(engine_listener.fileno()), "--no-proxy-headers"],
                engine_env, output / "engine.log", engine_listener, handles,
            )
            health = wait_for(engine_base, "/health", engine_proc, "the engine")
            require(health.get("device") == "cpu", "This run expects CPU mode")
            result["engine_ready_seconds"] = round(time.monotonic() - started, 3)
            result["engine_health"] = health

            plane_proc = start(
                [sys.executable, "-m", "uvicorn", "hosted.control_plane.app:app", "--fd",
                 str(plane_listener.fileno()), "--no-proxy-headers"],
                plane_env, output / "control-plane.log", plane_listener, handles,
            )
            plane_health = wait_for(plane_base, "/healthz", plane_proc, "the control plane")
            result["control_plane_health"] = plane_health
            result["plane_ready_seconds"] = round(time.monotonic() - started, 3)
            upstream_health = json_body(http(plane_base, "/healthz/upstream", None,
                                             method="GET")[1])
            require(upstream_health.get("reachable") is True,
                    "The control plane cannot see the engine")
            result["control_plane_sees_engine"] = upstream_health
            result["checks"].append("control_plane_and_engine_both_up")

            # ── two customers ────────────────────────────────────────────────
            customers = {}
            for name, email in (("a", "first@example.com"), ("b", "second@example.com")):
                status, body, _ = http(plane_base, "/v1/workspaces",
                                       {"email": email, "password": PASSWORD,
                                        "display_name": name})
                require(status == 201, f"signup for {email} returned {status}")
                workspace = json_body(body)["workspace"]
                status, body, _ = http(plane_base, "/v1/auth/token",
                                       {"email": email, "password": PASSWORD})
                require(status == 200, f"login for {email} returned {status}")
                token = json_body(body)["token"]
                status, body, _ = http(plane_base, "/v1/api-keys", {"label": name}, token=token)
                require(status == 201, f"key mint for {email} returned {status}")
                key = json_body(body)["api_key_record"]["api_key"]
                require(key.startswith("qbv_"), "minted key has the wrong shape")
                customers[name] = {"email": email, "workspace": workspace,
                                   "session": token, "key": key}
            result["workspaces"] = {n: {"id": c["workspace"]["id"], "email": c["email"]}
                                    for n, c in customers.items()}
            result["checks"].append("two_workspaces_signed_up_with_keys")

            # ── real speech through the hosted layer ─────────────────────────
            a_key = customers["a"]["key"]
            spoken = []
            for label, text, voice, language in (
                ("hindi_hf_alpha", HINDI, HINDI_VOICE, "hi"),
                ("hindi_hm_omega", HINDI, HINDI_VOICE_2, "hi"),
                ("english", ENGLISH, HINDI_VOICE, "en"),
            ):
                status, raw, headers = http(plane_base, "/v1/audio/speech",
                                            speech_payload(text, voice, language),
                                            token=a_key)
                require(status == 200, f"{label} through the control plane returned {status}: "
                                       f"{raw[:200]!r}")
                wav = m1.describe_wav(raw)
                (audio_dir / f"{label}.wav").write_bytes(raw)
                entry = {
                    "label": label, "voice": voice, "language": language,
                    "characters": len(text),
                    "status": status, "bytes": len(raw),
                    "seconds": round(wav["frames"] / wav["sample_rate"], 3),
                    "sample_rate": wav["sample_rate"], "channels": wav["channels"],
                    "peak": wav["peak"],
                    "engine_seconds_header": float(headers.get("x-qualiabits-engine-seconds", 0)),
                    "audio_seconds_header": float(headers.get("x-qualiabits-audio-seconds", 0)),
                    # The engine marks its own responses; this is that header's
                    # value (the VoiceStudio build number), not a claim by us.
                    "engine_marker_header": headers.get("x-omnivoice-backend"),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
                require(entry["sample_rate"] == 24000 and entry["channels"] == 1,
                        f"{label} is not 24 kHz mono")
                require(entry["peak"] > 0.001, f"{label} is silence")
                require(abs(entry["audio_seconds_header"] - entry["seconds"]) < 0.01,
                        f"{label}: the metered seconds do not match the WAV's own duration")
                spoken.append(entry)
            result["speech"] = spoken
            result["checks"].append("hindi_and_english_audio_through_the_control_plane")

            # ── the same request, straight at the engine, for comparison ────
            status, raw, _ = http(engine_base, "/v1/audio/speech",
                                  speech_payload(ENGLISH, HINDI_VOICE, "en"))
            require(status == 200, f"the direct engine call returned {status}")
            direct = m1.describe_wav(raw)
            via_plane = [entry for entry in spoken if entry["label"] == "english"][0]
            result["direct_vs_proxied"] = {
                "direct_seconds": round(direct["frames"] / direct["sample_rate"], 3),
                "proxied_seconds": via_plane["seconds"],
                "direct_bytes": len(raw), "proxied_bytes": via_plane["bytes"],
                "note": ("Same request, both paths, same engine. Byte identity is not "
                         "asserted: the engine samples, so two calls need not be "
                         "identical. Equality of the bytes the proxy returned with the "
                         "bytes the engine sent is proven in the unit suite against a "
                         "deterministic stub."),
            }
            require(abs(result["direct_vs_proxied"]["direct_seconds"]
                        - via_plane["seconds"]) < 1.0,
                    "The proxied English audio has a very different length than the direct call")

            # ── refusals ────────────────────────────────────────────────────
            status, raw, _ = http(plane_base, "/v1/audio/speech", held_out(), token=a_key)
            require(status == 400, f"an unknown voice returned {status}, not 400")
            result["unknown_voice_refusal"] = json_body(raw)
            result["checks"].append("engine_refusal_passed_through_as_400")

            # Byte identity against the real engine, on the one response that is
            # deterministic: an error body. A 200 body cannot be compared this
            # way, because the engine samples and two calls legitimately differ.
            status, direct_raw, _ = http(engine_base, "/v1/audio/speech", held_out())
            require(status == 400, f"the direct engine call returned {status}, not 400")
            result["refusal_passthrough"] = {
                "proxied_sha256": hashlib.sha256(raw).hexdigest(),
                "direct_sha256": hashlib.sha256(direct_raw).hexdigest(),
                "identical": raw == direct_raw,
                "bytes": len(raw),
            }
            require(raw == direct_raw,
                    "the plane did not return the engine's refusal bytes unchanged")
            result["checks"].append("refusal_bytes_identical_to_the_real_engine")

            status, _raw, _ = http(plane_base, "/v1/audio/speech",
                                   speech_payload(ENGLISH, HINDI_VOICE, "en"),
                                   token="qbv_" + "0" * 16 + "_nope")
            require(status == 401, "an unknown key was not refused")
            status, _raw, _ = http(plane_base, "/v1/audio/speech",
                                   speech_payload(ENGLISH, HINDI_VOICE, "en"))
            require(status == 401, "a keyless request was not refused")
            result["checks"].append("key_refusals_are_401")

            # ── the meter ───────────────────────────────────────────────────
            meter_a = json_body(http(plane_base, "/v1/usage", None, method="GET",
                                     token=a_key)[1])["usage"]
            meter_b = json_body(http(plane_base, "/v1/usage", None, method="GET",
                                     token=customers["b"]["key"])[1])["usage"]
            result["usage_workspace_a"] = meter_a
            result["usage_workspace_b"] = meter_b
            expected_characters = sum(entry["characters"] for entry in spoken) + len(ENGLISH)
            require(meter_a["requests"] == 4, f"workspace A should have 4 requests, has {meter_a['requests']}")
            require(meter_a["characters"] == expected_characters,
                    f"characters: expected {expected_characters}, got {meter_a['characters']}")
            require(meter_a["failed_requests"] == 1,
                    f"workspace A should have 1 failed attempt, has {meter_a['failed_requests']}")
            expected_seconds = sum(entry["seconds"] for entry in spoken)
            require(abs(meter_a["audio_seconds"] - expected_seconds) < 0.05,
                    f"audio seconds: expected about {expected_seconds:.2f}, "
                    f"got {meter_a['audio_seconds']}")
            require(meter_b["requests"] == 0 and meter_b["audio_seconds"] == 0,
                    "workspace B has usage it never made")
            require(meter_a["workspace_id"] != meter_b["workspace_id"],
                    "the two workspaces share an id")
            result["checks"].append("meter_matches_the_audio_and_is_per_workspace")

            # ── jobs: queued through the plane, worked by a real process ────
            # The same real engine, but the work goes through the queue: the
            # worker is started as its own process (``hosted/scripts/run_worker.py
            # --once``), exactly as a deployment runs it, and it claims the job
            # over the same database the plane is serving.
            storage_root = Path(state) / "objects"
            worker_env = dict(plane_env)
            worker_env["QUALI_STORAGE_DIR"] = str(storage_root)

            def run_worker_once(tag: str) -> dict:
                stats_file = output / f"worker-stats-{tag}.json"
                completed = subprocess.run(
                    [sys.executable, "hosted/scripts/run_worker.py", "--once",
                     "--worker-id", f"e2e-{tag}", "--stats-file", str(stats_file)],
                    cwd=ROOT, env=worker_env, capture_output=True, timeout=1800,
                )
                (output / f"worker-{tag}.log").write_bytes(
                    completed.stdout + completed.stderr
                )
                require(completed.returncode == 0,
                        f"the worker exited {completed.returncode}: {completed.stderr[-400:]!r}")
                parsed = json.loads(stats_file.read_text())
                require(parsed["claimed"] == parsed["succeeded"] + parsed["failed"]
                        + parsed["requeued"],
                        "the worker's counters do not add up")
                return parsed

            def usage_now(token: str) -> dict:
                return json_body(http(plane_base, "/v1/usage", None, method="GET",
                                      token=token)[1])["usage"]

            job_text = HINDI_LONG
            meter_before_job = usage_now(a_key)
            status, body, _ = http(plane_base, "/v1/jobs",
                                   speech_payload(job_text, HINDI_VOICE_2, "hi"), token=a_key)
            require(status == 202, f"queueing a job returned {status}: {body[:200]!r}")
            job = json_body(body)["job"]
            require(job["status"] == "queued" and job["attempts"] == 0,
                    f"a queued job came back as {job['status']!r}")
            require(job["audio"] is None and job["characters"] == len(job_text),
                    "a queued job reports audio it does not have, or the wrong length")
            # Queueing is not speaking: the meter must be untouched until the
            # worker has actually produced audio.
            require(usage_now(a_key) == meter_before_job,
                    "queueing a job moved the meter before anything was spoken")

            stats = run_worker_once("first")
            require(stats["claimed"] == 1 and stats["succeeded"] == 1,
                    f"the worker did not speak the queued job: {stats}")
            status, body, _ = http(plane_base, f"/v1/jobs/{job['id']}", None,
                                   method="GET", token=a_key)
            require(status == 200, f"reading the job back returned {status}")
            done = json_body(body)["job"]
            require(done["status"] == "succeeded" and done["attempts"] == 1,
                    f"the job finished as {done['status']!r} after {done['attempts']} attempts")

            status, raw, headers = http(plane_base, f"/v1/jobs/{job['id']}/audio", None,
                                        method="GET", token=a_key)
            require(status == 200, f"downloading the job's audio returned {status}")
            require(headers.get("x-qualiabits-object-sha256") == hashlib.sha256(raw).hexdigest(),
                    "the audio served does not hash to the digest recorded on the job")
            wav = m1.describe_wav(raw)
            require(wav["sample_rate"] == 24000 and wav["channels"] == 1,
                    "the job's audio is not 24 kHz mono")
            require(wav["peak"] > 0.001, "the job's audio is silence")
            job_seconds = round(wav["frames"] / wav["sample_rate"], 3)
            require(abs(job_seconds - done["audio"]["seconds"]) < 0.01,
                    f"the job records {done['audio']['seconds']} s, the WAV is {job_seconds} s")
            (audio_dir / "job_hindi_long.wav").write_bytes(raw)
            result["job"] = {
                "id": job["id"], "voice": HINDI_VOICE_2, "characters": job["characters"],
                "seconds": job_seconds, "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "object_sha256": done["audio"]["sha256"],
                "engine_seconds": done["audio"]["engine_seconds"],
                "worker_stats": stats,
            }
            # The object is on disk where the plane said it would be, and hashes to
            # the name it was stored under.
            object_path = storage_root / done["audio"]["sha256"][:2] / done["audio"]["sha256"][2:4] \
                / f"{done['audio']['sha256']}.wav"
            require(object_path.is_file(), f"the worker's object is not at {object_path}")
            stored_bytes = object_path.read_bytes()
            require(hashlib.sha256(stored_bytes).hexdigest() == done["audio"]["sha256"]
                    == hashlib.sha256(raw).hexdigest(),
                    "the object on disk, the job receipt and the download disagree")
            result["job"]["object_path"] = str(object_path)

            meter_after_job = usage_now(a_key)
            require(meter_after_job["requests"] == meter_before_job["requests"] + 1,
                    "the finished job is not in the meter's request count")
            require(meter_after_job["characters"]
                    == meter_before_job["characters"] + len(job_text),
                    "the finished job's characters are not counted once")
            require(abs(meter_after_job["audio_seconds"] - meter_before_job["audio_seconds"]
                        - job_seconds) < 0.05,
                    "the finished job's audio seconds are not in the meter")
            by_key = meter_after_job["by_key"][0]
            require(by_key["requests"] == meter_after_job["requests"],
                    "the job was not attributed to the key that queued it")
            result["checks"].append("a_queued_job_is_spoken_by_a_real_worker_and_metered_once")

            # A job the engine refuses fails with the engine's own words, once.
            status, body, _ = http(plane_base, "/v1/jobs",
                                   speech_payload(HINDI, "no_such_voice_here", "hi"), token=a_key)
            require(status == 202, f"queueing the refused job returned {status}")
            refused_job = json_body(body)["job"]
            refused_stats = run_worker_once("refused")
            require(refused_stats["failed"] == 1 and refused_stats["requeued"] == 0,
                    f"a deterministic refusal was retried: {refused_stats}")
            status, body, _ = http(plane_base, f"/v1/jobs/{refused_job['id']}", None,
                                   method="GET", token=a_key)
            failed = json_body(body)["job"]
            require(failed["status"] == "failed" and failed["status_code"] == 400,
                    f"the refused job ended as {failed['status']!r}/{failed['status_code']}")
            require(failed["attempts"] == 1, "a refused job was attempted more than once")
            # The engine's refusal names the voice it was asked for, so the wording
            # cannot be compared to the held-out refusal above. Compare it to the
            # engine's own answer for exactly this voice, taken straight from the
            # engine: the job's ledger line must be the engine's message (our
            # ledger truncates it at 200 characters, so it must be a prefix).
            status, direct_refusal, _ = http(engine_base, "/v1/audio/speech",
                                             speech_payload(HINDI, "no_such_voice_here", "hi"))
            require(status == 400, f"the engine answered {status} for the unknown voice")
            engine_words = json_body(direct_refusal).get("detail", "")
            require(isinstance(engine_words, str) and engine_words
                    and engine_words.startswith(failed["error"]),
                    "the failed job does not carry the engine's own refusal message")
            require("no_such_voice_here" in failed["error"],
                    "the job's failure does not name the voice it was refused for")
            require(http(plane_base, f"/v1/jobs/{refused_job['id']}/audio", None,
                         method="GET", token=a_key)[0] == 409,
                    "a failed job served audio")
            result["job_refused"] = {
                "id": refused_job["id"], "status": failed["status"],
                "status_code": failed["status_code"], "attempts": failed["attempts"],
                "error": failed["error"],
                "engine_detail_for_this_voice": engine_words,
                "engine_detail_was_truncated_by_the_ledger": len(engine_words) > len(failed["error"]),
                "worker_stats": refused_stats,
            }
            result["checks"].append("a_refused_job_fails_once_with_the_engine_s_own_message")

            # A cancelled job is never spoken: nothing reaches the engine, so
            # nothing is metered, and the worker finds nothing to claim.
            meter_before_cancel = usage_now(a_key)
            status, body, _ = http(plane_base, "/v1/jobs",
                                   speech_payload(HINDI, HINDI_VOICE, "hi"), token=a_key)
            require(status == 202, f"queueing the cancellable job returned {status}")
            cancellable = json_body(body)["job"]
            status, body, _ = http(plane_base, f"/v1/jobs/{cancellable['id']}", None,
                                   method="DELETE", token=a_key)
            require(status == 200 and json_body(body)["job"]["status"] == "cancelled",
                    "cancelling a queued job did not work")
            cancel_stats = run_worker_once("after-cancel")
            require(cancel_stats["claimed"] == 0,
                    f"a cancelled job was claimed: {cancel_stats}")
            require(usage_now(a_key)["requests"] == meter_before_cancel["requests"],
                    "a cancelled job was billed something")
            result["job_cancelled"] = {"id": cancellable["id"], "worker_stats": cancel_stats}
            result["checks"].append("a_cancelled_job_is_never_spoken_or_metered")

            # ── a revoked key stops immediately ─────────────────────────────
            status, body, _ = http(plane_base, "/v1/api-keys", None, method="GET",
                                   token=customers["a"]["session"])
            require(status == 200, "listing keys failed")
            key_rows = json_body(body)["api_keys"]
            require(len(key_rows) == 1 and "api_key" not in key_rows[0],
                    "the key listing must not return key material")
            status, _body, _ = http(plane_base, f"/v1/api-keys/{key_rows[0]['id']}", None,
                                    method="DELETE", token=customers["a"]["session"])
            require(status == 200, "revoking the key failed")
            status, _raw, _ = http(plane_base, "/v1/audio/speech",
                                   speech_payload(ENGLISH, HINDI_VOICE, "en"),
                                   token=a_key)
            require(status == 401, "a revoked key still works")
            require(json_body(http(plane_base, "/v1/usage", None, method="GET",
                                   token=customers["b"]["key"])[1])["usage"]["requests"] == 0,
                    "revoking A's key changed B's meter")
            result["checks"].append("revoked_key_refused_and_other_workspace_untouched")

        except Exception as exc:  # noqa: BLE001 - a failed run still writes evidence
            result["failure_type"] = type(exc).__name__
            result["failure"] = str(exc)
        finally:
            result["engine_shutdown"] = stop(engine_proc, output / "engine.log", handles)
            result["control_plane_shutdown"] = stop(
                plane_proc, output / "control-plane.log", handles
            )

    result["model_files"] = {
        name: hashlib.sha256((model / name).read_bytes()).hexdigest()
        for name in ("tokens.txt", "voices.bin", "LICENSE")
        if (model / name).is_file()
    }
    result["revision"] = m1.record_revision(ROOT, output)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    passed = (
        "failure" not in result
        and result["engine_shutdown"].get("clean")
        and result["control_plane_shutdown"].get("clean")
    )
    result["passed"] = passed
    result["what_this_proves"] = (
        "A signed-up workspace, holding a key minted by the control plane, got real "
        "24 kHz WAV speech in Hindi and English from the fork's own engine through the "
        "hosted layer, and the usage meter matched the audio's own duration and stayed "
        "inside that workspace. A longer Hindi paragraph was queued and spoken by a "
        "separate worker process; the audio was fetched back out of the object store "
        "and hashed to the digest on the job. A refusal failed once with the engine's "
        "own message, and a cancelled job was never spoken or billed."
    )
    result["what_this_does_not_prove"] = (
        "Not deployed, no public URL, no HTTPS, no GPU, no human listening check, no S3 "
        "bucket or credentials, no email delivery, no billing, and no claim that the "
        "voice is good enough to sell. The store is the local filesystem backend; the "
        "S3 backend is written and refuses when misconfigured but has never been run "
        "against a bucket. M1 stays open until the owner's GPU run and listening check."
    )
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in
                      ("passed", "checks", "usage_workspace_a", "usage_workspace_b",
                       "failure", "failure_type") if k in result}, indent=2))
    return 0 if passed else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New evidence directory (must not exist)")
    parser.add_argument("--model", type=Path, required=True,
                        help="The unpacked sherpa-onnx Kokoro model directory")
    args = parser.parse_args(argv)
    return run(args.output.resolve(), args.model.resolve())


if __name__ == "__main__":
    raise SystemExit(main())