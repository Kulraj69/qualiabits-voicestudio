"""An unsupported language is caller error: /v1/audio/speech must answer 400.

Before this fix the refusal raised a bare ``ValueError``. The native /generate
route maps that to 400, but the OpenAI-compatible route only maps typed
failures (``_typed_speech_http_error``) and fell through to its generic 500 —
so an OpenAI client asking an English-only engine for Hindi got ``server_error``
instead of the actionable message the desktop UI shows (#2104).

The probe run that found it (real HTTP, unchanged backend, empty state):
``scripts/m1_speech_probe.py`` → English 200 + audio, Hindi 500 + traceback.
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from api.routers.openai_compat import _typed_speech_http_error  # noqa: E402
from services.tts_backend import KittenTTSBackend, TTSInputError  # noqa: E402


def test_language_refusal_is_typed_and_still_a_value_error():
    engine = KittenTTSBackend()
    assert engine.supported_languages == ["en"]
    with pytest.raises(TTSInputError) as caught:
        engine._check_language("hi")
    # /generate's ValueError→400 mapping must keep working unchanged.
    assert isinstance(caught.value, ValueError)
    assert not isinstance(caught.value, (TypeError, RuntimeError))
    message = str(caught.value)
    assert "KittenTTS" in message and "'hi'" in message and "'en'" in message


def test_the_compat_route_maps_that_refusal_to_400():
    engine = KittenTTSBackend()
    with pytest.raises(TTSInputError) as caught:
        engine._check_language("hi")
    http = _typed_speech_http_error(caught.value)
    assert http is not None, "a bare fall-through here is the 500 this fixes"
    assert http.status_code == 400
    assert http.param == "input"


def test_a_supported_language_and_auto_are_not_refused():
    engine = KittenTTSBackend()
    assert engine._check_language("en") == "en"
    assert engine._check_language("auto") is None
    assert engine._check_language(None) is None
    assert engine._check_language("") is None