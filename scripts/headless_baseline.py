"""Exercise the real, unmodified API with empty state; never approve model terms.

POSIX-only process harness. A passing baseline is NOT speech/GPU acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


class BaselineFailure(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BaselineFailure("Unexpected HTTP redirect")


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
MAX_BODY = 8 * 1024 * 1024


def request(base, path, payload=None, timeout: float = 15):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        response = OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise BaselineFailure("Response exceeded baseline size limit")
        if not response.headers.get("x-omnivoice-backend"):
            raise BaselineFailure("Response is not marked as VoiceStudio")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise BaselineFailure("Expected JSON, not audio or HTML") from None
        if not isinstance(body, dict):
            raise BaselineFailure("Expected a JSON object")
        return response.status, body


def require(condition, message):
    if not condition:
        raise BaselineFailure(message)


def clean_shutdown(returncode, forced, log_text):
    # Uvicorn re-raises captured SIGTERM after running lifespan shutdown.
    return (not forced and returncode in (0, -signal.SIGTERM)
            and "Application shutdown complete." in log_text
            and "Finished server process" in log_text)


def require_clean_checkout(root):
    # An empty regular sentinel stops dotenv's search before ancestor secrets.
    # Inspect metadata only, never read existing environment-file contents.
    project_env = root / ".env"  # main.py also loads this explicitly.
    require(not project_env.is_symlink(), "Use a clean checkout without .env symlinks")
    if project_env.exists():
        require(project_env.is_file() and project_env.stat().st_size == 0,
                "Use a clean checkout without nonempty .env files")
    for directory in (root / "backend", root, *root.parents):
        candidate = directory / ".env"
        if candidate.is_symlink():
            raise BaselineFailure("Use a clean checkout without .env symlinks")
        if candidate.exists():
            require(candidate.is_file() and candidate.stat().st_size == 0,
                    "Use a clean checkout without nonempty .env files")
            return


def wait_ready(base, process, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        require(process.poll() is None, "Backend exited during startup")
        try:
            status, body = request(base, "/health", timeout=min(2, max(.01, deadline - time.monotonic())))
            if status == 200:
                require(body.get("status") == "ok", "Unexpected health response")
                return body
            require(status == 503 and body.get("status") == "starting", "Unexpected startup response")
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(min(.2, max(0, deadline - time.monotonic())))
    raise BaselineFailure("Backend did not become ready within startup deadline")


def inspect_api(base):
    result = {"scope": "offline_cpu_api_contract_only", "m1_complete": False,
              "speech_verified": False, "gpu_verified": False, "checks": []}
    status, progress = request(base, "/startup/progress")
    require(status == 200 and progress.get("status") == "ready", "Startup is not ready")
    result["checks"].append("startup_ready")
    status, schema = request(base, "/openapi.json")
    paths = schema.get("paths", {})
    require(status == 200 and all(p in paths for p in (
        "/health", "/models", "/engines", "/v1/audio/speech", "/v1/audio/transcriptions"
    )), "Required API routes missing")
    result["checks"].append("api_routes")
    status, models = request(base, "/models")
    rows = models.get("models")
    if status != 200 or not isinstance(rows, list) or not rows:
        raise BaselineFailure("Model catalog missing")
    require(all(isinstance(row, dict) and row.get("installed") is False
                and isinstance(row.get("repo_id"), str) and row["repo_id"] for row in rows),
            "Baseline requires an empty model cache")
    require(len({row["repo_id"] for row in rows}) == len(rows), "Duplicate model identities")
    result["model_count"] = len(rows)
    result["installed_model_count"] = 0
    result["checks"].append("empty_model_catalog")
    status, engines = request(base, "/engines")
    require(status == 200 and all(isinstance(engines.get(k), dict) and
            isinstance(engines[k].get("backends"), list) and engines[k]["backends"]
            for k in ("tts", "asr", "llm")), "Engine catalogs missing")
    for family in ("tts", "asr", "llm"):
        entries = engines[family]["backends"]
        require(all(isinstance(entry, dict) and isinstance(entry.get("id"), str)
                    and entry["id"] for entry in entries), "Engine identity missing")
        require(len({entry["id"] for entry in entries}) == len(entries), "Duplicate engine identities")
    result["engine_counts"] = {k: len(engines[k]["backends"]) for k in ("tts", "asr", "llm")}
    result["checks"].append("engine_catalogs")
    result["speech_attempts"] = []
    for language, text in (("en", "This is a local speech check."), ("hi", "यह स्थानीय आवाज़ की जाँच है।")):
        status, body = request(base, "/v1/audio/speech", {
            "model": "omnivoice", "voice": "alloy", "input": text,
            "language": language, "response_format": "wav",
        })
        error = body.get("error", {})
        require(status == 403 and error.get("code") == "model_licence_required",
                "Expected unaccepted-model refusal; baseline does not authorize synthesis")
        result["speech_attempts"].append({"language": language, "http_status": status,
                                          "error_code": error["code"], "audio_generated": False})
        result["checks"].append(f"{language}_license_refusal")
    status, body = request(base, "/v1/audio/speech", {"model": "omnivoice"})
    require(status == 400 and body.get("error", {}).get("code") == "invalid_value",
            "Missing input did not return a validation error")
    result["checks"].append("invalid_input_rejected")
    return result


def run(output):
    require(os.name == "posix", "This process harness requires POSIX pass_fds")
    # No clobber of an earlier evidence run, no parent environment credentials.
    if output.exists():
        raise FileExistsError("Evidence directory already exists")
    root = Path(__file__).resolve().parents[1]
    require_clean_checkout(root)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="voicestudio-baseline-") as state:
        env = {
            "PATH": os.environ.get("PATH", os.defpath), "HOME": state,
            "TMPDIR": state, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
            "OMNIVOICE_DATA_DIR": str(Path(state) / "data"),
            "OMNIVOICE_ENV_FILE": str(Path(state) / "user-env"),
            "OMNIVOICE_CACHE_DIR": str(Path(state) / "cache"),
        }
        with socket.socket() as listener, (output / "server.log").open("w") as log:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            base = f"http://127.0.0.1:{listener.getsockname()[1]}"
            result: dict[str, Any] = {
                "scope": "offline_cpu_api_contract_only", "m1_complete": False,
                "api_contract_passed": False, "speech_verified": False,
                "gpu_verified": False, "production_readiness_verified": False,
            }
            process = None
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "backend.main:app", "--fd",
                     str(listener.fileno()), "--no-proxy-headers"],
                    cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, pass_fds=(listener.fileno(),),
                )
                health = wait_ready(base, process)
                result["ready_seconds"] = round(time.monotonic() - started, 3)
                result["startup_deadline_seconds"] = 120
                require(health.get("device") == "cpu", "This diagnostic requires CPU mode")
                result.update(inspect_api(base))
                result["health"] = health
                result["api_contract_passed"] = True
            except Exception as exc:  # noqa: BLE001 - report failure and always stop owned backend
                # Never copy response bodies, request payloads or arbitrary exception text.
                result["failure_type"] = type(exc).__name__
                if isinstance(exc, BaselineFailure):
                    result["failure"] = str(exc)
            finally:
                alive_before_stop = process is not None and process.poll() is None
                if process is not None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        result["forced_shutdown"] = True
                        result["api_contract_passed"] = False
                result["server_exit_code"] = process.returncode if process is not None else None
                log.flush()
                shutdown_log = (output / "server.log").read_text(errors="replace")
                result["startup_stall_warning"] = "Event loop has not run" in shutdown_log
                result["production_readiness_verified"] = False
                result["shutdown_verified"] = alive_before_stop and clean_shutdown(
                    result["server_exit_code"], result.get("forced_shutdown", False), shutdown_log
                )
                if not result["shutdown_verified"]:
                    result["api_contract_passed"] = False
                result["source_sha256"] = {
                    name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                    for name in ("backend/main.py", "uv.lock", "scripts/headless_baseline.py",
                                 "tests/test_headless_baseline.py")
                }
                result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["api_contract_passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory")
    args = parser.parse_args()
    raise SystemExit(run(args.output.resolve()))
