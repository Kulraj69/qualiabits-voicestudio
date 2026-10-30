"""Failure-injection tests for the CPU speech probe; no engine is mocked live."""
import importlib.util
import math
import struct
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "m1_speech_probe", Path(__file__).resolve().parents[1] / "scripts" / "m1_speech_probe.py"
)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def wav(payload: bytes, channels: int = 1, rate: int = 24000, bits: int = 16,
        declared: int | None = None, audio_format: int = 1) -> bytes:
    block = channels * (bits // 8)
    size = len(payload) if declared is None else declared
    fmt = struct.pack("<HHIIHH", audio_format, channels, rate, rate * block, block, bits)
    return (b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", size) + payload)


def tone(seconds: float = 0.25, freq: float = 440.0, rate: int = 24000, amp: int = 12000) -> bytes:
    frames = int(seconds * rate)
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / rate)))
                    for i in range(frames))


def silence(seconds: float = 0.2, rate: int = 24000) -> bytes:
    return struct.pack("<h", 0) * int(seconds * rate)


AUDIO = (200, None, {"content-type": "audio/wav", "content-length": "1"})
REFUSAL = (403, b'{"error": {"code": "model_licence_required"}}',
           {"content-type": "application/json"})
BROKEN = (500, b'{"error": {"code": null}}', {"content-type": "application/json"})
CLIENT_REFUSAL = (400, b'{"error": {"code": "invalid_value"}}',
                  {"content-type": "application/json"})


class Wire:
    """Stands in for the network: scripted engine catalogue and speech responses."""

    def __init__(self, speech, active="kittentts", env_override=True,
                 backends=("kittentts", "omnivoice"), required=False, accepted=None):
        self.speech = speech
        self.active = active
        self.env_override = env_override
        self.backends = backends
        self.required = required
        self.accepted = (not required) if accepted is None else accepted
        self.seen: list[dict] = []

    def request(self, base, path, payload=None, **kwargs):
        if path == "/engines":
            return 200, {"tts": {
                "active": self.active, "env_override": self.env_override,
                "backends": [{"id": b} for b in self.backends],
            }}
        if path == "/models":
            return 200, {"models": [{
                "repo_id": "KittenML/kitten-tts-mini-0.8", "engines": ["kittentts"],
                "installed": True, "size_gb": 0.08,
            }]}
        if path.startswith("/models/licenses/acceptance/"):
            return 200, {"license": "apache-2.0", "category": "commercial",
                         "required": self.required, "accepted": self.accepted,
                         "state": "not_required" if not self.required else "not_accepted"}
        if path.startswith("/models/licenses/details/"):
            return 200, {"info": {"registry_version": "test-registry",
                                  "readiness": "not_verified",
                                  "blockers": ["terms_unverified"]}}
        raise AssertionError(path)

    def request_audio(self, base, path, payload: dict | None = None, **kwargs):
        assert path == "/v1/audio/speech", path
        assert isinstance(payload, dict)
        self.seen.append(payload)
        status, body, headers = self.speech[payload.get("language")]
        if body is None:
            body = wav(tone())
        return status, body, headers


def wire_up(monkeypatch, wire):
    monkeypatch.setattr(probe, "request", wire.request)
    monkeypatch.setattr(probe, "request_audio", wire.request_audio)
    return wire


def _result(tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    return {"_audio_dir": audio_dir, "requests": []}


def test_tone_is_measured_as_non_silent_audio():
    measured = probe.describe_wav(wav(tone()))
    assert measured["sample_rate"] == 24000
    assert measured["channels"] == 1
    assert measured["bits_per_sample"] == 16
    assert measured["non_silent"] is True
    assert measured["peak"] > 0.3
    assert measured["rms"] > 0.1
    assert measured["duration_seconds"] == pytest.approx(0.25, abs=0.01)
    assert measured["data_truncated"] is False


def test_silence_is_audio_but_not_speech_evidence():
    measured = probe.describe_wav(wav(silence()))
    assert measured["non_silent"] is False
    assert measured["peak"] == 0.0


def test_non_riff_payload_is_rejected():
    with pytest.raises(probe.ProbeFailure):
        probe.describe_wav(b"OggS" + bytes(64))


def test_short_header_is_rejected():
    with pytest.raises(probe.ProbeFailure):
        probe.describe_wav(b"RIFF")


def test_truncated_data_chunk_is_flagged_not_hidden():
    measured = probe.describe_wav(wav(tone(0.2), declared=10_000_000))
    assert measured["data_truncated"] is True


def test_missing_data_chunk_is_rejected():
    body = wav(tone(0.05))
    with pytest.raises(probe.ProbeFailure):
        probe.describe_wav(body[:body.index(b"data")])


def test_float32_payload_is_measured():
    frames = b"".join(struct.pack("<f", 0.5) for _ in range(1000))
    measured = probe.describe_wav(wav(frames, bits=32, audio_format=3))
    assert measured["non_silent"] is True
    assert measured["peak"] == pytest.approx(0.5, abs=0.01)


def test_licence_refusal_is_not_counted_as_audio(monkeypatch, tmp_path):
    wire_up(monkeypatch, Wire({"en": REFUSAL, "hi": REFUSAL}))
    row = probe.synthesise("http://x", _result(tmp_path), "omnivoice", "text", "en",
                           "alloy", "control")
    assert row["http_status"] == 403
    assert row["error_code"] == "model_licence_required"
    assert row["audio_generated"] is False
    assert not list((tmp_path / "audio").iterdir())


def test_a_different_active_engine_invalidates_the_attribution(monkeypatch, tmp_path):
    """Audio from some other engine is not evidence about the ungated one."""
    wire_up(monkeypatch, Wire({"en": AUDIO, "hi": AUDIO}, active="omnivoice"))
    with pytest.raises(probe.ProbeFailure):
        probe.probe("http://x", _result(tmp_path), "kittentts", "hello", None, "v")


def test_engine_must_be_active_by_the_environment_override(monkeypatch, tmp_path):
    wire_up(monkeypatch, Wire({"en": AUDIO, "hi": AUDIO}, env_override=False))
    with pytest.raises(probe.ProbeFailure):
        probe.probe("http://x", _result(tmp_path), "kittentts", "hello", None, "v")


def test_successful_response_that_is_silence_fails_the_probe(monkeypatch, tmp_path):
    wire_up(monkeypatch, Wire({"en": (200, wav(silence()), {"content-type": "audio/wav"}),
                               "hi": AUDIO}))
    result = _result(tmp_path)
    with pytest.raises(probe.ProbeFailure):
        probe.probe("http://x", result, "kittentts", "hello", None, "v")
    assert result["speech_produced"] is False


def test_real_audio_passes_but_never_claims_milestone_or_gpu(monkeypatch, tmp_path):
    wire = wire_up(monkeypatch, Wire({"en": AUDIO, "hi": AUDIO}))
    result = _result(tmp_path)
    probe.probe("http://x", result, "kittentts", "hello", "नमस्ते", "v")
    assert result["speech_produced"] is True
    assert result["english_audio"]["non_silent"] is True
    assert result["active_tts_backend"] == "kittentts"
    for flag in ("m1_complete", "gpu_verified", "hindi_verified",
                 "intelligibility_verified"):
        assert result[flag] is False
    assert result["model_terms_accepted_by_probe"] == 0
    assert [row["language"] for row in wire.seen] == ["en", "en", "hi"]
    assert result["english_warm_seconds"] <= result["english_cold_seconds"] + 1.0
    assert "warm_request_returned_audio" in result["checks"]
    assert result["hindi_attempt"]["language"] == "hi"


def test_server_error_on_the_language_attempt_is_a_probe_failure(monkeypatch, tmp_path):
    """A 5xx for an unsupported language is a defect, not an answer."""
    wire_up(monkeypatch, Wire({"en": AUDIO, "hi": BROKEN}))
    with pytest.raises(probe.ProbeFailure):
        probe.probe("http://x", _result(tmp_path), "kittentts", "hello", "नमस्ते", "v")


def test_client_error_on_the_language_attempt_is_recorded_as_a_check(monkeypatch, tmp_path):
    wire_up(monkeypatch, Wire({"en": AUDIO, "hi": CLIENT_REFUSAL}))
    result = _result(tmp_path)
    probe.probe("http://x", result, "kittentts", "hello", "नमस्ते", "v")
    assert "hindi_refused_as_client_error" in result["checks"]
    assert result["hindi_verified"] is False


def test_engine_catalogue_read_gets_a_generous_timeout(monkeypatch, tmp_path):
    """Regression: the default 15 s timed out on a slow fresh install."""
    seen = {}

    def req(base, path, payload=None, timeout=15.0):
        seen["timeout"] = timeout
        return 200, {"tts": {"active": "kittentts", "env_override": True,
                             "backends": [{"id": "kittentts"}]}}

    monkeypatch.setattr(probe, "request", req)
    result = _result(tmp_path)
    result["checks"] = []
    probe.check_engine_identity("http://x", result, "kittentts")
    assert seen["timeout"] >= 120
    assert result["checks"] == ["ungated_engine_is_active"]


def test_a_silent_warm_response_fails_the_probe(monkeypatch, tmp_path):
    """The warm path is measured too; silence there is not a pass."""
    calls = {"n": 0}

    def ra(base, path, payload=None, **kwargs):
        calls["n"] += 1
        body = wav(silence()) if calls["n"] == 2 else wav(tone())
        return 200, body, {"content-type": "audio/wav"}

    monkeypatch.setattr(probe, "request", Wire({}).request)
    monkeypatch.setattr(probe, "request_audio", ra)
    with pytest.raises(probe.ProbeFailure):
        probe.probe("http://x", _result(tmp_path), "kittentts", "hello", None, "v")


def test_build_env_sets_the_cache_it_was_given(tmp_path):
    """Regression: --hf-cache was ignored, so weights landed in ~/.cache."""
    cache = tmp_path / "given-cache"
    env = probe.build_env("/state", "kittentts", cache)
    assert env["HF_HUB_CACHE"] == str(cache)
    # backend/main.py redirects HF_HUB_CACHE from this one, so it must match.
    assert env["OMNIVOICE_CACHE_DIR"] == str(cache)
    assert "HF_HUB_OFFLINE" not in env
    assert env["OMNIVOICE_TTS_BACKEND"] == "kittentts"
    for key in ("OMNIVOICE_DATA_DIR", "OMNIVOICE_ENV_FILE", "HOME"):
        assert env[key].startswith("/state"), key


def test_build_env_without_a_cache_is_offline_but_still_hermetic():
    env = probe.build_env("/state", "kittentts", None)
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["HF_HUB_CACHE"].startswith("/state")
    assert env["OMNIVOICE_CACHE_DIR"].startswith("/state")
    assert not any(k in env for k in ("SARVAM_API_KEY", "AZURE_OPENAI_API_KEY", "VAPI_API_KEY"))


def test_host_metadata_names_the_box():
    host = probe.host_metadata()
    assert host["cpu_count"] and isinstance(host["cpu_count"], int)
    assert host["python"].startswith("3.")
    assert host["platform"]


def test_licence_provenance_is_recorded_and_gate_free(monkeypatch, tmp_path):
    wire_up(monkeypatch, Wire({"en": AUDIO, "hi": CLIENT_REFUSAL}))
    result = _result(tmp_path)
    probe.probe("http://x", result, "kittentts", "hello", "नमस्ते", "v")
    entry = result["licence_provenance"][0]
    assert entry["repo_id"] == "KittenML/kitten-tts-mini-0.8"
    assert entry["required"] is False and entry["accepted"] is True
    assert entry["category"] == "commercial"
    assert "licence_required_is_false" in result["checks"]
    assert result["host"]["cpu_count"]


def test_a_gated_model_fails_the_provenance_check(monkeypatch, tmp_path):
    wire_up(monkeypatch, Wire({"en": AUDIO, "hi": AUDIO}, required=True))
    with pytest.raises(probe.ProbeFailure):
        probe.probe("http://x", _result(tmp_path), "kittentts", "hello", None, "v")


def test_unknown_engine_is_refused_before_any_work():
    with pytest.raises(probe.ProbeFailure):
        probe.run(Path("/nonexistent-probe-output"), "omnivoice", "hi", None, "v", None)


@pytest.mark.model_licence_gate  # real enforcement; see tests/conftest.py
def test_allowlist_matches_the_application_licence_gate():
    """The probe's engine list must be exactly the registry's ungated set."""
    from services import model_acceptance

    for engine in probe.UNGATED_ENGINES:
        model_acceptance.ensure_engine_accepted(engine)  # raises if actually gated
        repos = model_acceptance.repos_for_engine(engine)
        assert repos, f"{engine} has no registered model"
        for repo in repos:
            assert model_acceptance.status(repo)["required"] is False, repo

    # The default engine is noncommercial, so the probe must never rely on it.
    with pytest.raises(model_acceptance.ModelLicenceNotAccepted):
        model_acceptance.ensure_engine_accepted("omnivoice")


def test_probe_never_accepts_terms():
    """No acceptance call may exist in the probe's code, comments excluded."""
    source = (Path(__file__).resolve().parents[1] / "scripts" / "m1_speech_probe.py").read_text()
    code = source.split('"""', 2)[2]
    code = "\n".join(line for line in code.splitlines()
                     if not line.lstrip().startswith("#"))
    for forbidden in ("ensure_accepted", "set_license_accepted", "accept(", "model_acceptance"):
        assert forbidden not in code, forbidden