"""Metering, isolation and pass-through on the hosted control plane.

The stub engine returns a WAV of a known duration, so the seconds in the meter
can be checked against the audio rather than against a number this test made up.
"""
from __future__ import annotations

import httpx
import pytest

from conftest import app_client, login, make_wav, mint_key, signup, speak, usage

HINDI = "यह एक हिन्दी वाक्य है।"
ENGLISH = "VoiceStudio is generating this sentence on a CPU with no GPU."


def test_the_engine_answers_and_the_meter_records_what_was_delivered(client, engine):
    signup(client)
    key = mint_key(client, login(client))

    first = speak(client, key, ENGLISH)
    assert first.status_code == 200
    assert first.content == engine.wav  # byte for byte, no re-encoding
    assert first.headers["content-type"] == "audio/wav"
    assert float(first.headers["X-Qualiabits-Audio-Seconds"]) == pytest.approx(
        engine.audio_seconds, abs=1e-6
    )

    second = speak(client, key, HINDI)
    assert second.status_code == 200

    meter = usage(client, key)
    assert meter["requests"] == 2
    assert meter["characters"] == len(ENGLISH) + len(HINDI)
    assert meter["audio_seconds"] == pytest.approx(2 * engine.audio_seconds, abs=1e-3)
    assert meter["audio_bytes"] == 2 * len(engine.wav)
    assert meter["failed_requests"] == 0
    assert meter["by_status"] == {"200": 2}
    assert meter["first_request_at"] and meter["last_request_at"]
    assert "measured from the returned WAV header" in meter["audio_seconds_note"]


def test_the_seconds_come_from_the_audio_not_from_a_guess(client, engine):
    engine.set_audio_seconds(0.25)
    signup(client)
    key = mint_key(client, login(client))
    response = speak(client, key, "one")  # one character, a quarter second of audio
    assert response.status_code == 200
    meter = usage(client, key)
    assert meter["characters"] == 3
    assert meter["audio_seconds"] == pytest.approx(0.25, abs=1e-3)


def test_each_workspace_sees_only_its_own_numbers(client):
    signup(client, "a@example.com")
    signup(client, "b@example.com")
    a_key = mint_key(client, login(client, "a@example.com"))
    b_key = mint_key(client, login(client, "b@example.com"))

    for text in (ENGLISH, HINDI):
        assert speak(client, a_key, text).status_code == 200
    assert speak(client, b_key, "b").status_code == 200

    a_meter, b_meter = usage(client, a_key), usage(client, b_key)
    assert a_meter["requests"] == 2 and b_meter["requests"] == 1
    assert a_meter["characters"] == len(ENGLISH) + len(HINDI)
    assert b_meter["characters"] == 1
    assert a_meter["audio_seconds"] > b_meter["audio_seconds"]


def test_no_query_parameter_can_point_the_meter_at_another_workspace(client):
    """There is no workspace id in any route, and a stray one changes nothing."""
    signup(client, "a@example.com")
    signup(client, "b@example.com")
    a_key = mint_key(client, login(client, "a@example.com"))
    b_key = mint_key(client, login(client, "b@example.com"))
    assert speak(client, a_key, ENGLISH).status_code == 200

    peeked = client.get(
        "/v1/usage?workspace_id=1", headers={"Authorization": f"Bearer {b_key}"}
    )
    assert peeked.status_code == 200
    assert peeked.json()["usage"]["workspace_id"] != 1
    assert peeked.json()["usage"]["requests"] == 0


def test_an_engine_refusal_is_passed_through_and_metered_as_a_failure(client, engine):
    signup(client)
    key = mint_key(client, login(client))
    assert speak(client, key, ENGLISH).status_code == 200
    before = usage(client, key)

    engine.mode = "refuse"
    refused = speak(client, key, HINDI)
    assert refused.status_code == 400
    assert refused.json() == engine.refusal

    after = usage(client, key)
    assert after["requests"] == before["requests"] + 1
    assert after["failed_requests"] == before["failed_requests"] + 1
    assert after["characters"] == before["characters"] + len(HINDI)
    # No audio arrived, so no audio is billed.
    assert after["audio_seconds"] == pytest.approx(before["audio_seconds"], abs=1e-6)
    assert after["by_status"] == {"200": 1, "400": 1}


def test_input_that_never_reaches_the_engine_still_lands_in_the_ledger(client, engine):
    signup(client)
    key = mint_key(client, login(client))
    for body in ({}, {"input": ""}, {"input": "   "}, {"input": 7}):
        assert client.post(
            "/v1/audio/speech", json=body, headers={"Authorization": f"Bearer {key}"}
        ).status_code in (400, 422)
    assert client.post(
        "/v1/audio/speech", json={"input": "x" * 5001},
        headers={"Authorization": f"Bearer {key}"},
    ).status_code == 413
    assert client.post(
        "/v1/audio/speech", content=b"not json",
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
    ).status_code == 400
    assert engine.payloads == []

    meter = usage(client, key)
    assert meter["requests"] == 6
    assert meter["failed_requests"] == 6
    assert meter["characters"] == 0  # nothing was sent to the engine
    assert meter["audio_seconds"] == 0


def test_a_dead_engine_is_502_and_the_customer_is_told_why(tmp_path, engine):
    """A closed loopback port is a real connection refusal, not a mocked one."""
    with app_client(
        tmp_path, engine, name="dead.db", upstream_base="http://127.0.0.1:1"
    ) as dead:
        signup(dead)
        key = mint_key(dead, login(dead))
        response = speak(dead, key, ENGLISH)
        assert response.status_code == 502
        assert response.json()["reason"] == "upstream_unreachable"
        meter = usage(dead, key)
        assert meter["failed_requests"] == 1
        assert meter["characters"] == len(ENGLISH)
        assert meter["audio_seconds"] == 0

        health = dead.get("/healthz/upstream")
        assert health.json()["reachable"] is False
        assert health.json()["reason"] in {"ConnectError", "ConnectTimeout"}


def test_the_engine_marker_header_survives_the_proxy(client, engine):
    """A customer asking "which engine spoke?" gets the engine's own answer."""
    signup(client)
    key = mint_key(client, login(client))
    response = speak(client, key, ENGLISH)
    assert response.status_code == 200
    assert response.headers["x-omnivoice-backend"] == "sherpa-onnx"


def test_the_engine_receives_the_body_unchanged(client, engine):
    signup(client)
    key = mint_key(client, login(client))
    body = {
        "input": ENGLISH,
        "voice": "hm_omega",
        "response_format": "wav",
        "speed": 1.0,
        "studio_note": "an unknown field the engine may ignore",
    }
    response = client.post(
        "/v1/audio/speech", json=body, headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code == 200
    assert engine.payloads == [body]


def test_a_second_key_from_the_same_workspace_spends_the_same_meter(client):
    signup(client)
    token = login(client)
    first, second = mint_key(client, token, "one"), mint_key(client, token, "two")
    assert speak(client, first, ENGLISH).status_code == 200
    assert speak(client, second, HINDI).status_code == 200
    meter = usage(client, second)
    assert meter["requests"] == 2
    assert meter["characters"] == len(ENGLISH) + len(HINDI)
    listed = client.get("/v1/api-keys", headers={"Authorization": f"Bearer {token}"}).json()
    used = [row["last_used_at"] is not None for row in listed["api_keys"]]
    assert used == [True, True]


def test_an_mp3_response_is_counted_but_its_seconds_are_not_invented(client, engine):
    """Only a WAV header carries a duration we can read. MP3 minutes are not guessed."""
    signup(client)
    key = mint_key(client, login(client))
    engine.wav = b"ID3\x03\x00\x00\x00" + b"\x00" * 500
    response = speak(client, key, ENGLISH, response_format="mp3")
    assert response.status_code == 200
    meter = usage(client, key)
    assert meter["requests"] == 1
    assert meter["characters"] == len(ENGLISH)
    assert meter["audio_seconds"] == 0
    assert meter["requests_without_audio_seconds"] == 1


def test_a_timeout_from_the_engine_is_504(tmp_path, engine):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with app_client(
        tmp_path, engine, name="slow.db",
        transport=httpx.MockTransport(handler),
    ) as slow:
        signup(slow)
        key = mint_key(slow, login(slow))
        response = speak(slow, key, ENGLISH)
        assert response.status_code == 504
        assert response.json()["reason"] == "upstream_timeout"
        assert usage(slow, key)["failed_requests"] == 1