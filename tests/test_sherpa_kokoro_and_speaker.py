"""Kokoro-format sherpa-onnx models, and reaching their voices over the API.

Found while running M1 with a real Kokoro model (``kokoro-int8-multi-lang-v1_0``,
54 voices, four Hindi):

* The adapter loaded every sherpa-onnx model through the VITS config. Kokoro is
  not VITS-layout: without ``voices.bin``/lexicon/dict it aborts the process with
  "Not a model using characters as modeling unit", and with a lexicon alone it
  drops every Devanagari character as an OOV token and returns an EMPTY sample
  list — a 200 with no audio, or an opaque 500 further down.
* ``generate`` read only ``speaker_id``, but ``/v1/audio/speech`` forwards the
  caller's ``voice`` string and never sends ``speaker_id``. Every request against
  a multi-speaker model silently spoke with voice 0 (Kokoro's ``af_alloy``), so
  the Hindi voices were unreachable from the product.

These tests cover the layout detection, the config it selects, the name→id
lookup, the range check, and the empty-audio guard. They use a stubbed
``sherpa_onnx`` (the real runtime loads a multi-hundred-MB graph).
"""
import os
import sys
import types

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _tts():
    import importlib

    return importlib.import_module("services.tts_backend")


def _proto_string(value: str) -> bytes:
    raw = value.encode("utf-8")
    assert len(raw) < 128, "test fixtures keep values under one varint byte"
    return bytes([len(raw)]) + raw


def _metadata_block(entries: dict) -> bytes:
    """A faithful ``metadata_props`` blob: field 14, then key/value pairs."""
    body = b"".join(b"\x0a" + _proto_string(k) + b"\x12" + _proto_string(v)
                    for k, v in entries.items())
    assert len(body) < 128
    return b"\x72" + bytes([len(body)]) + body


def _write_model(path, entries: dict, prefix: bytes = b""):
    path.write_bytes(prefix + _metadata_block(entries))
    return str(path)


@pytest.fixture(autouse=True)
def _stub_sherpa(monkeypatch):
    """A recording stand-in for the sherpa_onnx runtime; returns its captures."""
    captured: dict = {}

    class _Vits:
        def __init__(self, **kw):
            captured["vits"] = kw

    class _Kokoro:
        def __init__(self, **kw):
            captured["kokoro"] = kw

    class _ModelConfig:
        def __init__(self, **kw):
            captured["model"] = kw

    class _Tts:
        def __init__(self, config):
            captured["config"] = config
            self.num_speakers = 0
            self.sample_rate = 24000

    module = types.ModuleType("sherpa_onnx")
    module.__dict__.update({
        "OfflineTtsConfig": lambda **kw: ("OfflineTtsConfig", kw),
        "OfflineTtsModelConfig": _ModelConfig,
        "OfflineTtsVitsModelConfig": _Vits,
        "OfflineTtsKokoroModelConfig": _Kokoro,
        "OfflineTts": _Tts,
    })
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    return captured


def _model_dir(tmp_path, *, kokoro: bool, metadata: dict):
    """A model directory shaped like a real sherpa-onnx release."""
    d = tmp_path / ("kokoro-int8-multi-lang-v1_0" if kokoro
                    else "vits-piper-en_US-lessac-medium")
    d.mkdir()
    _write_model(d / "model.onnx", metadata)
    (d / "tokens.txt").write_text("a 1\nb 2\n")
    (d / "espeak-ng-data").mkdir()
    if kokoro:
        (d / "voices.bin").write_bytes(b"\x00" * 16)
        (d / "dict").mkdir()
        (d / "lexicon-us-en.txt").write_text("hello hh\n")
        (d / "lexicon-zh.txt").write_text("ni n i\n")
    return d


# ── metadata, layout detection, config selection ───────────────────────────

def test_metadata_is_read_from_the_tail_window(tmp_path):
    path = tmp_path / "model.onnx"
    # A decoy marker in the weights, outside the tail window the reader scans.
    decoy = b'{"model_type\x12' + bytes([4]) + b"fake"
    _write_model(path, {"model_type": "kokoro", "n_speakers": "54"},
                 prefix=decoy + b"\x00" * (3 * 1024 * 1024))
    metadata = _tts()._sherpa_onnx_string_metadata(str(path))
    assert metadata["model_type"] == "kokoro"
    assert metadata["n_speakers"] == "54"


def test_metadata_missing_file_is_not_fatal(tmp_path):
    assert _tts()._sherpa_onnx_string_metadata(str(tmp_path / "nope.onnx")) == {}


def test_speaker_table_maps_names_to_ids():
    tts = _tts()
    ids = tts._sherpa_speaker_ids({"id2speaker": "0->af_alloy,1->af_aoede,31->hf_alpha"})
    assert ids == {"af_alloy": 0, "af_aoede": 1, "hf_alpha": 31}
    # A bare speaker_names list uses its own order as the index.
    assert tts._sherpa_speaker_ids({"speaker_names": "af_alloy,af_aoede"}) == {
        "af_alloy": 0, "af_aoede": 1}


def test_kokoro_layout_is_detected_by_voices_bin(tmp_path):
    tts = _tts()
    vits_dir = _model_dir(tmp_path, kokoro=False, metadata={"model_type": "vits"})
    assert tts._sherpa_kokoro_assets(
        str(vits_dir), str(vits_dir / "model.onnx"), str(vits_dir / "tokens.txt")) is None

    kokoro_dir = _model_dir(tmp_path, kokoro=True, metadata={"model_type": "kokoro"})
    assets = tts._sherpa_kokoro_assets(str(kokoro_dir), str(kokoro_dir / "model.onnx"),
                                       str(kokoro_dir / "tokens.txt"))
    assert assets is not None
    assert assets["voices"].endswith("voices.bin")
    assert assets["data_dir"].endswith("espeak-ng-data")
    assert assets["dict_dir"].endswith("dict")
    # Both lexicons are offered, comma-joined, the way sherpa-onnx expects.
    assert assets["lexicon"].endswith(
        "lexicon-us-en.txt," + str(kokoro_dir / "lexicon-zh.txt"))


def test_kokoro_model_is_loaded_with_the_kokoro_config(tmp_path, monkeypatch, _stub_sherpa):
    tts = _tts()
    d = _model_dir(tmp_path, kokoro=True,
                   metadata={"model_type": "kokoro", "n_speakers": "54",
                             "id2speaker": "0->af_alloy,31->hf_alpha"})
    monkeypatch.setenv("OMNIVOICE_SHERPA_MODEL", str(d))
    backend = tts.SherpaOnnxBackend()
    backend._ensure_loaded()
    assert backend._metadata["model_type"] == "kokoro"
    assert backend._speaker_ids["hf_alpha"] == 31
    assert "kokoro" in _stub_sherpa and "vits" not in _stub_sherpa
    assert set(_stub_sherpa["kokoro"]) == {"model", "tokens", "voices", "lexicon",
                                           "data_dir", "dict_dir"}


def test_vits_model_still_uses_the_vits_config(tmp_path, monkeypatch, _stub_sherpa):
    tts = _tts()
    d = _model_dir(tmp_path, kokoro=False, metadata={"model_type": "vits"})
    monkeypatch.setenv("OMNIVOICE_SHERPA_MODEL", str(d))
    backend = tts.SherpaOnnxBackend()
    backend._ensure_loaded()
    assert backend._speaker_ids == {}
    assert "vits" in _stub_sherpa and "kokoro" not in _stub_sherpa


def test_missing_tokens_is_a_clear_error(tmp_path, monkeypatch):
    tts = _tts()
    d = tmp_path / "m"
    d.mkdir()
    _write_model(d / "model.onnx", {"model_type": "vits"})
    monkeypatch.setenv("OMNIVOICE_SHERPA_MODEL", str(d))
    with pytest.raises(RuntimeError, match="tokens.txt is missing"):
        tts.SherpaOnnxBackend()._ensure_loaded()


# ── voice selection over the API ───────────────────────────────────────────

class _GeneratingTts:
    def __init__(self, samples=None, num_speakers=54):
        self.samples = [0.1, -0.2] if samples is None else samples
        self.num_speakers = num_speakers
        self.calls = []

    def generate(self, text, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(samples=self.samples, sample_rate=24000)


def _backend_with(voices: dict, samples=None, num_speakers=54):
    backend = _tts().SherpaOnnxBackend()
    backend._model_dir = "/does/not/matter"
    backend._metadata = {"id2speaker": ",".join(f"{i}->{n}" for n, i in voices.items())}
    backend._speaker_ids = dict(voices)
    backend._tts = _GeneratingTts(samples=samples, num_speakers=num_speakers)
    return backend, backend._tts


def test_voice_name_selects_that_speaker():
    backend, engine = _backend_with({"af_alloy": 0, "hf_alpha": 31})
    backend.generate("नमस्ते", voice="hf_alpha")
    assert engine.calls[-1]["sid"] == 31


def test_voice_name_is_case_insensitive_and_numeric_ids_work():
    backend, engine = _backend_with({"af_alloy": 0, "hm_omega": 33})
    backend.generate("hi", voice="HM_OMEGA")
    backend.generate("hi", voice="33")
    assert [call["sid"] for call in engine.calls] == [33, 33]


def test_speaker_id_kwarg_still_wins_and_is_validated():
    backend, engine = _backend_with({"af_alloy": 0, "hf_alpha": 31})
    backend.generate("hi", speaker_id=31, voice="af_alloy")
    assert engine.calls[-1]["sid"] == 31
    with pytest.raises(_tts().TTSInputError, match="must be a number"):
        backend.generate("hi", speaker_id="hf_alpha")


def test_unknown_voice_lists_what_the_model_offers():
    backend, engine = _backend_with({"af_alloy": 0, "hf_alpha": 31})
    with pytest.raises(_tts().TTSInputError) as excinfo:
        backend.generate("hi", voice="expr-voice-2-f")
    assert "af_alloy" in str(excinfo.value) and "hf_alpha" in str(excinfo.value)
    assert engine.calls == []  # nothing was synthesized with the wrong voice


def test_out_of_range_speaker_is_rejected():
    backend, _ = _backend_with({"af_alloy": 0, "hf_alpha": 31})
    with pytest.raises(_tts().TTSInputError, match="out of range"):
        backend.generate("hi", speaker_id=54)
    with pytest.raises(_tts().TTSInputError, match="out of range"):
        backend.generate("hi", speaker_id=-1)


def test_single_speaker_model_ignores_an_engine_preset_voice():
    backend, engine = _backend_with({}, num_speakers=1)
    backend.generate("hi", voice="expr-voice-2-f")
    assert engine.calls[-1]["sid"] == 0


def test_empty_audio_becomes_a_caller_error():
    backend, _ = _backend_with({"hf_alpha": 31}, samples=[])
    with pytest.raises(_tts().TTSInputError, match="produced no audio"):
        backend.generate("hi", voice="hf_alpha")


def test_returned_audio_keeps_its_shape():
    backend, _ = _backend_with({"hf_alpha": 31})
    wav = backend.generate("hi", voice="hf_alpha")
    assert tuple(wav.shape) == (1, 2)