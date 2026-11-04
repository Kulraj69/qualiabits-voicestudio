"""Dubbing on the hosted control plane: the route, the order, the two meters.

A dub is the one route in this plane that makes **two** engine calls, so most of
what these tests hold down is that the two calls happen in the right order and
that neither is metered as the other:

1. The voice is checked *before* the recording is heard. A voice the plane would
   never offer must cost no transcription — so ``engine.transcriptions`` stays
   empty when it is refused.
2. A recording with no words in it is refused rather than spoken, and the speech
   engine is never asked to say nothing — so ``engine.payloads`` stays empty.
3. Speech in and speech out stay separate counters. A dub moves both, by exactly
   what each half cost, and nothing mixed.

The stub engine answers with a fixed transcript and a WAV of a known duration, so
what is checked here is what the plane did with the requests and the answers —
not whether a speech model heard anything. Recognition against real weights is the
browser run's job (``hosted/web/e2e/dashboard.mjs``), and that distinction is not
blurred.
"""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import inspect as sa_inspect, select, text

from conftest import (
    app_client,
    dub,
    fetch_dub_audio,
    login,
    make_wav,
    mint_key,
    signup,
    usage,
)

from hosted.control_plane import dubbing
from hosted.control_plane.app import INPUT_MAX_CHARS
from hosted.control_plane.db import Database, UsageEvent


def test_a_recording_goes_in_and_the_same_words_come_back_in_another_voice(client, engine):
    signup(client)
    key = mint_key(client, login(client))
    audio = make_wav(2.5)

    response = dub(client, key, audio, voice="hf_alpha", language="hi")

    assert response.status_code == 201, response.text
    body = response.json()
    record = body["dub"]
    # The transcript is the engine's own, unedited.
    assert record["transcript"] == engine.transcript
    assert record["voice"] == "hf_alpha"
    assert record["audio_url"] == f"/v1/dubs/{record['id']}/audio"
    assert record["source_bytes"] == len(audio)
    assert record["source_sha256"] == hashlib.sha256(audio).hexdigest()

    # What crossed the wire, in order: the recording first, then the transcript.
    heard = engine.transcriptions[-1]
    assert heard["body"] == audio
    assert heard["filename"] == "clip.wav"
    said = engine.payloads[-1]
    assert said == {"input": engine.transcript, "voice": "hf_alpha", "response_format": "wav"}

    # The dubbed audio is downloadable and is the bytes the engine produced.
    download = fetch_dub_audio(client, key, record["id"])
    assert download.status_code == 200
    assert download.content == engine.wav
    assert download.headers["content-type"].startswith("audio/wav")
    assert download.headers["X-Qualiabits-Dub"] == str(record["id"])


def test_the_voice_is_checked_before_the_recording_is_ever_heard(client, engine):
    """A voice the plane would never offer must not cost a transcription."""
    signup(client)
    key = mint_key(client, login(client))

    response = dub(client, key, make_wav(2.5), voice="definitely-not-a-voice")

    assert response.status_code == 400
    assert "does not offer a voice" in response.json()["detail"]
    assert engine.transcriptions == []  # never heard — the point of the ordering
    assert engine.payloads == []        # and of course never spoken
    meter = usage(client, key)
    assert meter["failed_requests"] == 1
    assert meter["transcript_characters"] == 0
    assert meter["characters"] == 0


def test_a_recording_with_no_words_is_refused_rather_than_spoken(client, engine):
    """The engine heard it; it said nothing; so there is nothing to say back."""
    signup(client)
    key = mint_key(client, login(client))
    engine.transcript = ""

    response = dub(client, key, make_wav(2.5))

    assert response.status_code == 422
    assert "heard no words" in response.json()["detail"]
    assert len(engine.transcriptions) == 1  # it was heard
    assert engine.payloads == []            # but never spoken
    meter = usage(client, key)
    assert meter["transcript_characters"] == 0
    assert meter["characters"] == 0
    # The recording's own length is still true and still measured: we were sent
    # the bytes, whatever the engine made of them.
    assert meter["audio_seconds"] == pytest.approx(2.5, abs=1e-3)


def test_a_transcript_past_the_ceiling_is_refused_rather_than_spoken(client, engine):
    signup(client)
    key = mint_key(client, login(client))
    engine.transcript = "word " * (dubbing.SPEAK_MAX_CHARS // 5 + 10)

    response = dub(client, key, make_wav(2.5))

    assert response.status_code == 413
    assert str(dubbing.SPEAK_MAX_CHARS) in response.json()["detail"]
    assert len(engine.transcriptions) == 1
    assert engine.payloads == []  # a manuscript is not spoken through this route
    # The transcript the engine returned is counted even though it was refused:
    # the transcription really happened, and the meter says so.
    assert usage(client, key)["transcript_characters"] == len(engine.transcript)


def test_the_ear_and_the_mouth_are_metered_separately(client, engine):
    """One dub moves both counters, by exactly what each half cost."""
    signup(client)
    key = mint_key(client, login(client))
    before = usage(client, key)

    assert dub(client, key, make_wav(2.5), voice="hf_alpha").status_code == 201

    after = usage(client, key)
    assert after["requests"] == before["requests"] + 2  # two engine calls, two rows
    assert after["characters"] == before["characters"] + len(engine.transcript)
    assert after["transcript_characters"] == (
        before["transcript_characters"] + len(engine.transcript)
    )
    # And the two requests really are on the two different routes in the ledger.
    settings = client.app.state.hosted.settings
    database = Database(settings.database_url)
    try:
        with database.session() as session:
            routes = [
                row.route
                for row in session.execute(
                    select(UsageEvent).order_by(UsageEvent.id)
                ).scalars().all()
            ]
    finally:
        database.dispose()
    assert routes == ["/v1/audio/transcriptions", "/v1/audio/speech"]


def test_the_dub_ceiling_is_the_speech_routes_own_ceiling():
    """A copy that cannot go stale in silence: the two numbers must be one."""
    assert dubbing.SPEAK_MAX_CHARS == INPUT_MAX_CHARS


def test_the_voice_check_only_refuses_on_a_catalogue_it_could_read():
    """An unreadable or empty catalogue says yes; the engine answers for itself."""
    assert dubbing.voice_is_known("anything", None) is True
    assert dubbing.voice_is_known("anything", {}) is True
    assert dubbing.voice_is_known("anything", {"voices": []}) is True
    catalogue = {"voices": [{"id": "hf_alpha"}, {"id": "alloy"}]}
    assert dubbing.voice_is_known("hf_alpha", catalogue) is True
    assert dubbing.voice_is_known("alloy", catalogue) is True
    assert dubbing.voice_is_known("nope", catalogue) is False


def test_nothing_a_caller_sends_can_reach_another_workspace(client, engine):
    signup(client, "a@example.com")
    signup(client, "b@example.com")
    a_key = mint_key(client, login(client, "a@example.com"))
    b_key = mint_key(client, login(client, "b@example.com"))

    made = dub(client, a_key, make_wav(1.25))
    assert made.status_code == 201, made.text
    dub_id = made.json()["dub"]["id"]

    # B cannot read A's dub, or its audio, and cannot learn it exists: 404, the
    # same answer a made-up id gets.
    assert client.get(
        f"/v1/dubs/{dub_id}", headers={"Authorization": f"Bearer {b_key}"}
    ).status_code == 404
    assert fetch_dub_audio(client, b_key, dub_id).status_code == 404
    assert client.get(
        "/v1/dubs", headers={"Authorization": f"Bearer {b_key}"}
    ).json()["dubs"] == []
    # And A can.
    assert client.get(
        f"/v1/dubs/{dub_id}", headers={"Authorization": f"Bearer {a_key}"}
    ).status_code == 200


def test_listing_dubs_is_newest_first_and_scoped_to_the_workspace(client, engine):
    signup(client)
    key = mint_key(client, login(client))
    first = dub(client, key, make_wav(1.25), voice="hf_alpha").json()["dub"]
    second = dub(client, key, make_wav(1.25), voice="hm_omega").json()["dub"]

    listed = client.get("/v1/dubs", headers={"Authorization": f"Bearer {key}"}).json()

    assert listed["workspace_total"] == 2
    assert [row["id"] for row in listed["dubs"]] == [second["id"], first["id"]]
    assert listed["dubs"][0]["voice"] == "hm_omega"


def test_an_upload_over_the_ceiling_is_refused_before_the_engine_hears_it(tmp_path, engine):
    import dataclasses

    with app_client(tmp_path, engine) as client:
        signup(client)
        key = mint_key(client, login(client))
        assert dub(client, key, b"x" * 200).status_code == 201
        engine.transcriptions.clear()
        engine.payloads.clear()

        state = client.app.state.hosted
        state.settings = dataclasses.replace(state.settings, transcribe_max_bytes=1024)
        over = dub(client, key, b"x" * 4096)

        assert over.status_code == 413
        assert "1024 bytes" in over.json()["detail"]
        assert engine.transcriptions == [] and engine.payloads == []


def test_a_declared_length_over_the_ceiling_is_refused_without_parsing_the_body(tmp_path, engine):
    import dataclasses

    with app_client(tmp_path, engine) as client:
        signup(client)
        key = mint_key(client, login(client))
        state = client.app.state.hosted
        state.settings = dataclasses.replace(state.settings, transcribe_max_bytes=1024)

        response = client.post(
            "/v1/dubs",
            content=b"x" * 4096,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "multipart/form-data; boundary=zz",
            },
        )

        assert response.status_code == 413
        assert engine.transcriptions == [] and engine.payloads == []


def test_a_request_with_no_file_part_is_refused_and_metered(client, engine):
    signup(client)
    key = mint_key(client, login(client))

    response = client.post(
        "/v1/dubs", data={"voice": "hf_alpha"}, headers={"Authorization": f"Bearer {key}"}
    )

    assert response.status_code == 400
    assert "'file' part" in response.json()["detail"]
    assert engine.transcriptions == [] and engine.payloads == []


def test_a_request_with_no_voice_is_refused_before_anything_is_heard(client, engine):
    signup(client)
    key = mint_key(client, login(client))

    response = client.post(
        "/v1/dubs",
        files={"file": ("clip.wav", make_wav(1.0), "audio/wav")},
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 400
    assert "'voice' must name" in response.json()["detail"]
    assert engine.transcriptions == [] and engine.payloads == []


def test_an_empty_file_part_is_refused(client, engine):
    signup(client)
    key = mint_key(client, login(client))

    response = dub(client, key, b"")

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]
    assert engine.transcriptions == [] and engine.payloads == []


def test_a_format_other_than_wav_is_refused(client, engine):
    signup(client)
    key = mint_key(client, login(client))

    response = dub(client, key, make_wav(1.0), response_format="mp3")

    assert response.status_code == 400
    assert "'wav'" in response.json()["detail"]
    assert engine.transcriptions == [] and engine.payloads == []


def test_dubbing_needs_a_key_and_refuses_a_session(client, engine):
    signup(client)
    session = login(client)

    no_credential = client.post(
        "/v1/dubs", files={"file": ("clip.wav", make_wav(1.0), "audio/wav")}, data={"voice": "hf_alpha"}
    )
    assert no_credential.status_code == 401

    with_session = dub(client, session, make_wav(1.0))
    assert with_session.status_code == 401
    assert "API key" in with_session.json()["detail"]
    assert engine.transcriptions == [] and engine.payloads == []


def test_the_migration_adds_the_dubs_table_to_an_older_database_without_losing_rows(tmp_path):
    """A slice-D database is brought forward, and its ledger keeps its history.

    The old schema is written out as the DDL that slice actually created — a
    ``schema_meta`` stamp at ``m2d1`` and no ``dubs`` table — because that is the
    only shape this migration will meet in the wild, and building it from today's
    models would have quietly included the table under test.
    """
    from hosted.control_plane.db import Database
    from hosted.control_plane.migrations import migrate

    path = tmp_path / "slice-d.db"
    database = Database(f"sqlite:///{path}")
    try:
        with database.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE schema_meta (key VARCHAR(40) PRIMARY KEY, "
                "value VARCHAR(80) NOT NULL)"
            ))
            connection.execute(text(
                "CREATE TABLE workspaces (id INTEGER PRIMARY KEY, email VARCHAR(320) "
                "NOT NULL UNIQUE, display_name VARCHAR(120) NOT NULL, password_salt "
                "VARCHAR(64) NOT NULL, password_hash VARCHAR(128) NOT NULL, "
                "password_params VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL)"
            ))
            connection.execute(text(
                "CREATE TABLE usage_events (id INTEGER PRIMARY KEY, workspace_id INTEGER "
                "NOT NULL, api_key_id INTEGER, route VARCHAR(80) NOT NULL, status_code "
                "INTEGER NOT NULL, characters INTEGER NOT NULL, transcript_characters "
                "INTEGER NOT NULL DEFAULT 0, audio_seconds FLOAT, audio_bytes INTEGER, "
                "engine_seconds FLOAT, error VARCHAR(200) NOT NULL, created_at DATETIME "
                "NOT NULL)"
            ))
            connection.execute(text(
                "INSERT INTO workspaces VALUES (1, 'old@example.com', '', 'salt', 'hash', "
                "'params', '2026-01-01 00:00:00')"
            ))
            connection.execute(text(
                "INSERT INTO usage_events VALUES (1, 1, NULL, '/v1/audio/speech', 200, 12, "
                "0, 1.5, 100, 3.0, '', '2026-01-01 00:00:00')"
            ))
            connection.execute(text(
                "INSERT INTO schema_meta VALUES ('schema_version', 'm2d1')"
            ))

        assert not sa_inspect(database.engine).has_table("dubs")
        outcome = migrate(database, expected_version="m2f1")

        assert outcome == "upgraded:m2d1->m2f1"
        assert sa_inspect(database.engine).has_table("dubs")
        with database.engine.begin() as connection:
            old = connection.execute(text(
                "SELECT characters FROM usage_events WHERE id = 1"
            )).scalar_one()
            stamp = connection.execute(text(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            )).scalar_one()
            # The columns the dubs table needs are really there.
            columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(dubs)"))
            }
        assert old == 12  # the row that existed before kept its meaning
        assert stamp == "m2f1"
        assert {"transcript", "object_key", "transcribe_seconds", "speech_seconds"} <= columns
        # Running again changes nothing, which is what makes a crash before the
        # stamp safe.
        assert migrate(database, expected_version="m2f1") == "current"
    finally:
        database.dispose()