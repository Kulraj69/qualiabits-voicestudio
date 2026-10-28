"""Unit failure injection for the smoke harness; no speech engine is mocked live."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "headless_baseline", Path(__file__).resolve().parents[1] / "scripts/headless_baseline.py"
)
assert spec is not None and spec.loader is not None
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def responses(base, path, payload=None, **kwargs):
    if path == "/startup/progress":
        return 200, {"status": "ready"}
    if path == "/openapi.json":
        return 200, {"paths": {p: {} for p in (
            "/health", "/models", "/engines", "/v1/audio/speech", "/v1/audio/transcriptions"
        )}}
    if path == "/models":
        return 200, {"models": [{"repo_id": "fixture/model", "installed": False}]}
    if path == "/engines":
        return 200, {k: {"backends": [{"id": "fixture"}]} for k in ("tts", "asr", "llm")}
    if path == "/v1/audio/speech":
        assert payload is not None
        if "input" not in payload:
            return 400, {"error": {"code": "invalid_value"}}
        return 403, {"error": {"code": "model_licence_required"}}
    raise AssertionError(path)


def test_refusal_is_not_speech_evidence(monkeypatch):
    monkeypatch.setattr(baseline, "request", responses)
    result = baseline.inspect_api("unused")
    assert result["speech_verified"] is False
    assert result["gpu_verified"] is False
    assert len(result["checks"]) == 7
    assert [r["language"] for r in result["speech_attempts"]] == ["en", "hi"]
    assert all(r["audio_generated"] is False for r in result["speech_attempts"])


@pytest.mark.parametrize("path,status,body", [
    ("/startup/progress", 200, {"status": "failed"}),
    ("/openapi.json", 200, {"paths": {}}),
    ("/models", 200, {"models": []}),
    ("/models", 200, {"models": [{"installed": True}]}),
    ("/models", 200, {"models": [{}]}),
    ("/models", 200, {"models": [{"installed": False}]}),
    ("/engines", 200, {"tts": {}}),
    ("/engines", 200, {k: {"backends": [{}]} for k in ("tts", "asr", "llm")}),
    ("/v1/audio/speech", 200, {"audio": "not-evidence"}),
    ("/v1/audio/speech", 500, {"error": {"code": "model_licence_required"}}),
    ("/v1/audio/speech", 403, {"error": {"code": "wrong"}}),
])
def test_bad_contract_fails(monkeypatch, path, status, body):
    def fake(base, requested, payload=None, **kwargs):
        return (status, body) if requested == path else responses(base, requested, payload)
    monkeypatch.setattr(baseline, "request", fake)
    with pytest.raises(baseline.BaselineFailure):
        baseline.inspect_api("unused")


def test_dead_process_fails_before_http():
    class Dead:
        def poll(self):
            return 1
    with pytest.raises(baseline.BaselineFailure, match="exited"):
        baseline.wait_ready("unused", Dead())


def test_starting_is_retried(monkeypatch):
    class Running:
        def poll(self):
            return None
    replies = iter([(503, {"status": "starting"}), (200, {"status": "ok"})])
    monkeypatch.setattr(baseline, "request", lambda *a, **k: next(replies))
    monkeypatch.setattr(baseline.time, "sleep", lambda _: None)
    assert baseline.wait_ready("unused", Running()) == {"status": "ok"}


def test_wait_has_deadline(monkeypatch):
    class Running:
        def poll(self):
            return None
    ticks = iter([0, 121])
    monkeypatch.setattr(baseline.time, "monotonic", lambda: next(ticks))
    with pytest.raises(baseline.BaselineFailure, match="deadline"):
        baseline.wait_ready("unused", Running())


def test_no_evidence_overwrite(tmp_path):
    with pytest.raises(FileExistsError):
        baseline.run(tmp_path)


def test_redirects_are_rejected():
    with pytest.raises(baseline.BaselineFailure, match="redirect"):
        baseline.NoRedirect().redirect_request(None, None, 302, None, None, "https://example.com")


@pytest.mark.parametrize("code,forced,text,expected", [
    (-15, False, "Application shutdown complete.\nFinished server process", True),
    (0, False, "Application shutdown complete.\nFinished server process", True),
    (-15, False, "", False),
    (-9, True, "Application shutdown complete.\nFinished server process", False),
    (1, False, "Application shutdown complete.\nFinished server process", False),
    (0, False, "Application shutdown complete.", False),
])
def test_shutdown_requires_both_markers(code, forced, text, expected):
    assert baseline.clean_shutdown(code, forced, text) is expected


def test_parent_dotenv_is_rejected_without_reading(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    (tmp_path / ".env").write_text("NOT_A_SECRET=test-fixture\n")
    with pytest.raises(baseline.BaselineFailure, match="clean checkout"):
        baseline.require_clean_checkout(root)


def test_empty_sentinel_shields_ancestor(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    (tmp_path / ".env").write_text("NOT_A_SECRET=test-fixture\n")
    (root / ".env").touch()
    baseline.require_clean_checkout(root)


def test_backend_sentinel_does_not_hide_project_env(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/.env").touch()
    (tmp_path / ".env").write_text("NOT_A_SECRET=test-fixture\n")
    with pytest.raises(baseline.BaselineFailure, match="clean checkout"):
        baseline.require_clean_checkout(tmp_path)


@pytest.mark.parametrize("failure", ["startup", "spawn", "interrupt", "exit"])
def test_startup_failure_keeps_explicit_acceptance_flags(tmp_path, monkeypatch, failure):
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    for name in ("backend/main.py", "uv.lock", "scripts/headless_baseline.py",
                 "tests/test_headless_baseline.py"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("test fixture\n")
    (root / ".env").touch()
    monkeypatch.setattr(baseline, "__file__", str(root / "scripts/headless_baseline.py"))

    terminated = []

    class Process:
        returncode = -15

        def poll(self):
            return None

        def terminate(self):
            terminated.append(True)

        def wait(self, timeout):
            return self.returncode

    def spawn(*args, **kwargs):
        if failure == "spawn":
            raise OSError("test process creation failure")
        return Process()

    monkeypatch.setattr(baseline.subprocess, "Popen", spawn)

    def fail(*args):
        if failure == "interrupt":
            raise KeyboardInterrupt
        if failure == "exit":
            raise SystemExit(2)
        raise baseline.BaselineFailure("Startup failed")

    monkeypatch.setattr(baseline, "wait_ready", fail)
    output = tmp_path / "evidence"
    if failure in ("interrupt", "exit"):
        with pytest.raises(KeyboardInterrupt if failure == "interrupt" else SystemExit):
            baseline.run(output)
    else:
        assert baseline.run(output) == 1
    assert terminated == ([] if failure == "spawn" else [True])
    result = baseline.json.loads((output / "result.json").read_text())
    assert result["scope"] == "offline_cpu_api_contract_only"
    assert result["m1_complete"] is False
    assert result["speech_verified"] is False
    assert result["gpu_verified"] is False
    assert result["api_contract_passed"] is False
    assert result["production_readiness_verified"] is False


@pytest.mark.parametrize("location", [".env", "backend/.env"])
def test_dotenv_symlink_rejected(tmp_path, location):
    (tmp_path / "backend").mkdir()
    (tmp_path / location).symlink_to(tmp_path / "absent")
    with pytest.raises(baseline.BaselineFailure, match="symlinks"):
        baseline.require_clean_checkout(tmp_path)
