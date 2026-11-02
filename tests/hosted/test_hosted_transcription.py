"""Transcription on the hosted control plane: the route, the ceiling, the meter.

The stub engine answers the engine's OpenAI-shaped STT route with a fixed
transcript, so these tests check what the plane did with the request and with the
answer — not whether a speech model recognised anything. Recognition is checked
against the real engine, with the real weights, by
``hosted/web/e2e/dashboard.mjs --engine-real`` and by ``hosted/scripts/`` runs; the
distinction matters and is not blurred here.

Two things these tests exist to hold down, because both are easy to get wrong:

1. Speech out and speech in are different counters. ``characters`` must not move
   when a customer transcribes, and a refused upload must move nothing at all.
2. The upload is bounded before it is parsed, and a refusal never reaches the
   engine.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text

from conftest import (
    app_client,
    login,
    make_wav,
    mint_key,
    signup,
    speak,
    transcribe,
    usage,
)


def test_a_recording_goes_in_and_the_engine_s_own_transcript_comes_back(client, engine):
    signup(client)
    key = mint_key(client, login(client))
    audio = make_wav(2.5)

    response = transcribe(client, key, audio, language="hi", response_format="verbose_json")

    assert response.status_code == 200, response.text
    assert response.json()["text"] == engine.transcript
    assert response.json()["language"] == "hi"
    assert response.headers["content-type"].startswith("application/json")
    # The engine's marker header is passed through, so a customer's bug report can
    # say which engine answered.
    assert response.headers["x-omnivoice-backend"] == "faster-whisper"

    # What actually crossed the wire to the engine: the same bytes, under the same
    # filename and content type, with the options the caller asked for.
    sent = engine.transcriptions[-1]
    assert sent["body"] == audio
    assert sent["bytes"] == len(audio)
    assert sent["filename"] == "clip.wav"
    assert sent["content_type"] == "audio/wav"
    assert sent["fields"]["language"] == "hi"
    assert sent["fields"]["response_format"] == "verbose_json"


def test_the_plane_forwards_the_engine_s_option_names_and_drops_anything_else(client, engine):
    signup(client)
    key = mint_key(client, login(client))

    response = transcribe(
        client, key, make_wav(1.0),
        language="hi", prompt="BPO call", temperature="0", model="whisper-1",
        diarize="true", workspace_id="7",
    )

    assert response.status_code == 200, response.text
    fields = engine.transcriptions[-1]["fields"]
    assert set(fields) <= {"model", "language", "prompt", "response_format", "temperature"}
    assert fields["model"] == "whisper-1" and fields["prompt"] == "BPO call"
    assert fields["temperature"] == "0"
    assert "diarize" not in fields and "workspace_id" not in fields


def test_the_meter_counts_characters_in_not_characters_out(client, engine):
    signup(client)
    key = mint_key(client, login(client))
    english = "This sentence is spoken, not transcribed."

    assert speak(client, key, english).status_code == 200
    before = usage(client, key)
    assert before["characters"] == len(english)
    assert before["transcript_characters"] == 0

    audio = make_wav(3.0)
    assert transcribe(client, key, audio, language="hi").status_code == 200

    after = usage(client, key)
    # Speech out is unchanged by a transcription: this is the whole point of the
    # third counter.
    assert after["characters"] == before["characters"]
    assert after["transcript_characters"] == len(engine.transcript)
    assert after["requests"] == before["requests"] + 1
    # The seconds are the length of the recording the customer sent, measured from
    # its own header — not a duration the plane was told.
    assert after["audio_seconds"] == pytest.approx(
        before["audio_seconds"] + 3.0, abs=1e-3
    )
    assert after["audio_bytes"] == before["audio_bytes"] + len(audio)
    assert "text the engine returned" in after["transcript_characters_note"]
    assert after["by_key"][0]["transcript_characters"] == len(engine.transcript)


def test_seconds_are_not_invented_for_a_container_the_plane_cannot_measure(client, engine):
    """An MP3 has no WAV header, so no duration is recorded — and none is guessed."""
    signup(client)
    key = mint_key(client, login(client))
    mp3ish = b"ID3\x04\x00\x00" + b"\x00" * 4096

    response = transcribe(client, key, mp3ish, filename="note.mp3", content_type="audio/mpeg")

    assert response.status_code == 200, response.text
    assert response.headers["X-Qualiabits-Upload-Bytes"] == str(len(mp3ish))
    assert "X-Qualiabits-Audio-Seconds" not in response.headers
    meter = usage(client, key)
    assert meter["audio_seconds"] == 0.0
    assert meter["requests_without_audio_seconds"] == 1
    assert meter["audio_bytes"] == len(mp3ish)
    # The upload really was sent on; the plane does not refuse a format it merely
    # cannot measure.
    assert engine.transcriptions[-1]["bytes"] == len(mp3ish)


def test_a_subtitle_answer_does_not_invent_a_character_count(client, engine):
    """``srt`` bytes are not a transcript: counting them would count timecodes."""
    signup(client)
    key = mint_key(client, login(client))

    response = transcribe(client, key, make_wav(1.0), response_format="srt")

    assert response.status_code == 200
    assert response.text.startswith("1\n00:00:00,000")
    meter = usage(client, key)
    assert meter["transcript_characters"] == 0
    assert meter["audio_seconds"] == pytest.approx(1.0, abs=1e-3)


def test_the_engine_s_refusal_is_passed_through_in_its_own_words(client, engine):
    signup(client)
    key = mint_key(client, login(client))
    engine.mode = "refuse"

    response = transcribe(client, key, make_wav(1.0), language="hi")

    assert response.status_code == 409
    assert response.json() == engine.stt_refusal
    meter = usage(client, key)
    assert meter["failed_requests"] == 1
    assert meter["transcript_characters"] == 0
    assert meter["by_status"] == {"409": 1}


def test_a_dead_engine_is_502_and_the_customer_is_told_why(tmp_path, engine):
    """A closed loopback port is a real connection refusal, not a mocked one."""
    with app_client(
        tmp_path, engine, name="dead.db", upstream_base="http://127.0.0.1:1"
    ) as dead:
        signup(dead)
        key = mint_key(dead, login(dead))

        response = transcribe(dead, key, make_wav(1.0), language="hi")

        assert response.status_code == 502
        body = response.json()
        assert body["detail"] == "The transcription engine did not answer."
        assert body["reason"] == "upstream_unreachable"
        assert "localhost" not in response.text and "127.0.0.1" not in response.text
        meter = usage(dead, key)
        assert meter["failed_requests"] == 1
        assert meter["transcript_characters"] == 0
        # We were sent the audio and measured it, so its length is still true and
        # still reported; what did not happen is transcription.
        assert meter["audio_seconds"] == pytest.approx(1.0, abs=1e-3)
        assert meter["audio_bytes"] == len(make_wav(1.0))


def test_an_upload_over_the_ceiling_is_refused_before_the_engine_sees_anything(
    tmp_path, engine
):
    import dataclasses

    with app_client(tmp_path, engine) as client:
        signup(client)
        key = mint_key(client, login(client))
        accepted = transcribe(client, key, b"x" * 200)
        assert accepted.status_code == 200
        engine.transcriptions.clear()

        state = client.app.state.hosted
        state.settings = dataclasses.replace(state.settings, transcribe_max_bytes=1024)
        over = transcribe(client, key, b"x" * 4096)

        assert over.status_code == 413
        assert "1024 bytes" in over.json()["detail"]
        assert engine.transcriptions == []  # the engine never saw it
        meter = usage(client, key)
        assert meter["failed_requests"] == 1
        assert meter["audio_bytes"] == 200  # only the accepted upload counted
        assert meter["transcript_characters"] == len(engine.transcript)


def test_a_declared_length_over_the_ceiling_is_refused_without_parsing_the_body(
    tmp_path, engine
):
    """A chunked upload declares nothing; a declared one is refused up front."""
    import dataclasses

    with app_client(tmp_path, engine) as client:
        signup(client)
        key = mint_key(client, login(client))
        state = client.app.state.hosted
        state.settings = dataclasses.replace(state.settings, transcribe_max_bytes=1024)

        response = client.post(
            "/v1/audio/transcriptions",
            content=b"x" * 4096,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "multipart/form-data; boundary=zz",
            },
        )

        assert response.status_code == 413
        assert engine.transcriptions == []
        assert usage(client, key)["failed_requests"] == 1


def test_a_request_with_no_file_part_is_refused_and_metered(client, engine):
    signup(client)
    key = mint_key(client, login(client))

    response = client.post(
        "/v1/audio/transcriptions",
        data={"language": "hi"},
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 400
    assert "'file' part" in response.json()["detail"]
    assert engine.transcriptions == []
    assert usage(client, key)["failed_requests"] == 1


def test_an_empty_file_part_is_refused(client, engine):
    signup(client)
    key = mint_key(client, login(client))

    response = transcribe(client, key, b"")

    assert response.status_code == 400
    assert "empty" in response.json()["detail"]
    assert engine.transcriptions == []
    assert usage(client, key)["failed_requests"] == 1


def test_transcription_needs_a_credential_of_some_kind(client, engine):
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", make_wav(1.0), "audio/wav")},
    )
    assert response.status_code == 401
    assert engine.transcriptions == []


def test_a_session_can_transcribe_and_the_request_is_attributed_to_the_workspace(client, engine):
    """The dashboard's path: a session token, no key, attributed to the workspace."""
    signup(client, "a@example.com")
    signup(client, "b@example.com")
    a_session, b_session = login(client, "a@example.com"), login(client, "b@example.com")

    assert transcribe(client, a_session, make_wav(1.0)).status_code == 200

    a_meter, b_meter = usage(client, a_session), usage(client, b_session)
    assert a_meter["requests"] == 1
    assert a_meter["transcript_characters"] == len(engine.transcript)
    assert a_meter["by_key"] == []  # a session is not a key, and the meter says so
    assert b_meter["requests"] == 0 and b_meter["transcript_characters"] == 0


def test_nothing_a_caller_sends_can_reach_another_workspace(client, engine):
    signup(client, "a@example.com")
    signup(client, "b@example.com")
    b_key = mint_key(client, login(client, "b@example.com"))
    a_session = login(client, "a@example.com")

    peeked = client.post(
        "/v1/audio/transcriptions?workspace_id=2",
        files={"file": ("clip.wav", make_wav(1.0), "audio/wav")},
        data={"workspace_id": "2"},
        headers={"Authorization": f"Bearer {a_session}"},
    )
    assert peeked.status_code == 200
    assert usage(client, a_session)["requests"] == 1
    assert usage(client, b_key)["requests"] == 0


def test_the_migration_adds_the_counter_to_an_older_database_without_losing_rows(tmp_path):
    """A slice-A database is brought forward, and its ledger keeps its history.

    The old schema is written out as the DDL slice A actually created — tables
    without ``transcript_characters`` and without ``jobs`` — because that is the
    only shape this migration will ever meet in the wild, and building it from
    today's models would have quietly included the column under test.
    """
    from hosted.control_plane.db import Database
    from hosted.control_plane.migrations import migrate

    path = tmp_path / "slice-a.db"
    url = f"sqlite:///{path}"
    database = Database(url)
    try:
        with database.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE workspaces (id INTEGER PRIMARY KEY, email VARCHAR(320) "
                "NOT NULL UNIQUE, display_name VARCHAR(120) NOT NULL, password_salt "
                "VARCHAR(64) NOT NULL, password_hash VARCHAR(128) NOT NULL, "
                "password_params VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL)"
            ))
            connection.execute(text(
                "CREATE TABLE api_keys (id INTEGER PRIMARY KEY, workspace_id INTEGER "
                "NOT NULL, key_id VARCHAR(32) NOT NULL UNIQUE, key_digest VARCHAR(64) "
                "NOT NULL, label VARCHAR(120) NOT NULL, created_at DATETIME NOT NULL, "
                "last_used_at DATETIME, revoked_at DATETIME)"
            ))
            connection.execute(text(
                "CREATE TABLE usage_events (id INTEGER PRIMARY KEY, workspace_id INTEGER "
                "NOT NULL, api_key_id INTEGER, route VARCHAR(80) NOT NULL, status_code "
                "INTEGER NOT NULL, characters INTEGER NOT NULL, audio_seconds FLOAT, "
                "audio_bytes INTEGER, engine_seconds FLOAT, error VARCHAR(200) NOT NULL, "
                "created_at DATETIME NOT NULL)"
            ))
            connection.execute(text(
                "CREATE TABLE schema_meta (key VARCHAR(40) PRIMARY KEY, "
                "value VARCHAR(80) NOT NULL)"
            ))
            connection.execute(text(
                "INSERT INTO workspaces VALUES (1, 'old@example.com', '', 'salt', 'hash', "
                "'params', '2026-01-01 00:00:00')"
            ))
            connection.execute(text(
                "INSERT INTO usage_events VALUES (1, 1, NULL, '/v1/audio/speech', 200, 12, "
                "1.5, 100, 3.0, '', '2026-01-01 00:00:00')"
            ))
            connection.execute(text(
                "INSERT INTO schema_meta VALUES ('schema_version', 'm2a1')"
            ))

        outcome = migrate(database, expected_version="m2c1")

        # Both steps ran, oldest first, and the plane can say so.
        assert outcome == "upgraded:m2a1->m2c1"
        with database.engine.begin() as connection:
            columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(usage_events)"))
            }
            assert "transcript_characters" in columns
            old = connection.execute(text(
                "SELECT characters, transcript_characters FROM usage_events WHERE id = 1"
            )).one()
            # The row that existed before the migration kept its meaning.
            assert old.characters == 12 and old.transcript_characters == 0
            assert connection.execute(
                text("SELECT count(*) FROM schema_meta WHERE value = 'm2c1'")
            ).scalar_one() == 1
        # Running again changes nothing, which is what makes a crash before the
        # stamp safe.
        assert migrate(database, expected_version="m2c1") == "current"
    finally:
        database.dispose()


def test_the_ledger_row_keeps_no_copy_of_a_customer_recording(client, engine):
    """The upload is measured, not stored: the row carries a size, not the audio."""
    from sqlalchemy import select

    from hosted.control_plane.db import Database, UsageEvent

    signup(client)
    key = mint_key(client, login(client))
    audio = make_wav(1.0)

    assert transcribe(client, key, audio).status_code == 200

    settings = client.app.state.hosted.settings
    database = Database(settings.database_url)
    try:
        with database.session() as session:
            row = session.execute(
                select(UsageEvent).where(UsageEvent.route == "/v1/audio/transcriptions")
            ).scalar_one()
            stored = json.dumps(
                {column.name: str(getattr(row, column.name))
                 for column in row.__table__.columns}
            )
            counted, bytes_stored = row.transcript_characters, row.audio_bytes
    finally:
        database.dispose()
    assert hashlib.sha256(audio).hexdigest() not in stored
    assert repr(audio[:64]) not in stored
    assert counted == len(engine.transcript)
    assert bytes_stored == len(audio)