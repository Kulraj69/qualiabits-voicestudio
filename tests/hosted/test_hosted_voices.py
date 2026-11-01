"""The voice list a picker is built from.

Two answers are joined here, because neither one is the whole truth: the
engine's voice catalogue knows the OpenAI aliases and the saved profiles but not
the ids a model declares in its own file, and the engine inventory knows those
ids and nothing else. A dashboard that offered only the catalogue could not name
a single Hindi voice while the engine happily spoke one.
"""
from __future__ import annotations

import json

from conftest import app_client, login, mint_key, signup

from hosted.control_plane.voices import compose


def voices(client, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get("/v1/voices", headers=headers)


def test_the_engine_s_own_voices_come_first_and_the_aliases_are_labelled(client):
    signup(client)
    token = login(client)
    response = voices(client, token)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["engine"]["id"] == "sherpa-onnx"
    assert body["engine"]["display_name"] == "sherpa-onnx"
    assert body["engine"]["default_voice"] == "af_alloy"
    assert body["engine"]["reachable"] is True

    kinds = [voice["kind"] for voice in body["voices"]]
    assert kinds[:4] == ["engine"] * 4, kinds
    assert body["voices"][0]["id"] == "af_alloy"
    assert body["voices"][1]["id"] == "hf_alpha"
    assert [voice["id"] for voice in body["voices"]][-3:] == ["alloy", "echo", "profile-1"]

    by_id = {voice["id"]: voice for voice in body["voices"]}
    assert by_id["hf_alpha"]["kind"] == "engine"
    assert by_id["hf_alpha"]["default"] is False
    assert by_id["af_alloy"]["default"] is True
    assert by_id["alloy"]["kind"] == "alias"
    assert by_id["profile-1"]["kind"] == "profile"
    assert by_id["profile-1"]["language"] == "hi"

    assert body["count"] == len(body["voices"]) == 7
    assert body["kinds"] == {"engine": 4, "alias": 2, "profile": 1}


def test_only_the_active_engine_s_voices_are_offered(client):
    """A voice the plane would refuse to speak is worse than no voice at all."""
    signup(client)
    ids = [voice["id"] for voice in voices(client, login(client)).json()["voices"]]
    assert "expr-voice-2-f" not in ids  # kittentts' voice, not the active engine's


def test_a_voice_list_is_not_public(client):
    signup(client)
    anonymous = voices(client)
    assert anonymous.status_code == 401
    assert "hf_alpha" not in anonymous.text


def test_api_key_may_read_it_but_the_reading_is_attributed(client):
    signup(client)
    session = login(client)
    key = mint_key(client, session)
    response = client.get("/v1/voices", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 7
    # A catalogue read is not synthesis: it must not show up in the meter.
    meter = client.get("/v1/usage", headers={"Authorization": f"Bearer {key}"}).json()
    assert meter["usage"]["requests"] == 0
    assert meter["usage"]["characters"] == 0


def test_an_unreachable_engine_is_reported_rather_than_faked(client, tmp_path, engine):
    """No engine answer: say so, do not invent a list."""
    with app_client(tmp_path, engine, name="no-engine.db",
                    upstream_base="http://127.0.0.1:9") as offline:
        signup(offline)
        body = voices(offline, login(offline)).json()
    assert body["engine"] == {
        "id": None, "display_name": None, "default_voice": None, "reachable": False,
    }
    assert body["voices"] == []
    assert body["count"] == 0


# ── the composition itself, without a server in the way ───────────────────────

def test_a_duplicate_id_a_profile_shares_with_a_model_voice_appears_once():
    catalogue = {"voices": [
        {"voice_id": "hf_alpha", "name": "hf_alpha", "type": "profile"},
        {"voice_id": "alloy", "name": "Alloy", "type": "openai_alias"},
    ]}
    engines = {"active": "sherpa-onnx", "backends": [
        {"id": "sherpa-onnx", "display_name": "sherpa-onnx",
         "preset_voices": ["hf_alpha", "hm_omega"], "default_voice": "hf_alpha"},
    ]}
    composed = compose(catalogue, engines)
    assert [voice["id"] for voice in composed["voices"]] == ["hf_alpha", "hm_omega", "alloy"]
    assert composed["voices"][0]["kind"] == "engine"
    assert composed["count"] == 3


def test_a_default_voice_the_model_does_not_declare_is_not_marked(client):
    """The marker is the engine's own default, never the first entry."""
    composed = compose(
        {"voices": []},
        {"active": "sherpa-onnx", "backends": [
            {"id": "sherpa-onnx", "display_name": "sherpa-onnx",
             "preset_voices": ["a", "b"], "default_voice": "c"},
        ]},
    )
    assert [voice["default"] for voice in composed["voices"]] == [False, False]
    assert composed["engine"]["default_voice"] == "c"


def test_a_catalogue_with_no_engine_inventory_still_lists_the_aliases():
    composed = compose({"voices": [{"voice_id": "alloy", "name": "Alloy",
                                    "type": "openai_alias"}]}, None)
    assert composed["engine"]["reachable"] is False
    assert [voice["id"] for voice in composed["voices"]] == ["alloy"]


def test_an_entry_with_no_id_is_dropped_rather_than_listed_as_empty():
    composed = compose({"voices": [
        {"name": "nameless"}, {"voice_id": "", "name": "empty"},
        {"id": "by-id", "name": "By id"},
    ]}, None)
    assert [voice["id"] for voice in composed["voices"]] == ["by-id"]
    assert json.dumps(composed)  # the shape survives serialisation