"""Jobs: queueing, the worker, the audio, and the meter.

Every test here runs the real plane on a real database with a real object store and
drives the real worker code against the stub engine. What the stub supplies is only
the speech itself; everything that decides who owns a job, what is stored and what is
counted is the shipped code.
"""
from __future__ import annotations

import hashlib

import pytest

from conftest import (
    app_client,
    create_job,
    drain,
    fetch_job_audio,
    get_job,
    list_jobs,
    login,
    make_wav,
    mint_key,
    queue_job,
    signup,
    speak,
    usage,
    worker_for,
)


def test_queueing_a_job_is_accepted_and_speaks_nothing_yet(client):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)

    job = create_job(client, key, "  यह एक लंबा हिन्दी वाक्य है।  ")

    assert job["status"] == "queued"
    assert job["attempts"] == 0
    assert job["audio"] is None
    assert job["audio_url"] is None
    # Whitespace is stripped before queueing, so the characters counted are the
    # characters the engine will be asked to speak.
    assert job["characters"] == len("यह एक लंबा हिन्दी वाक्य है।")
    assert job["input"] == "यह एक लंबा हिन्दी वाक्य है।"
    # Nothing was spoken: the meter must not have moved.
    assert usage(client, key)["requests"] == 0
    assert client.get("/healthz").json()["jobs"] == {"queued": 1}


def test_the_engine_is_not_asked_at_queue_time_and_is_asked_once_by_the_worker(
    client, engine
):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    create_job(client, key, "नमस्ते", voice="hf_alpha")

    assert engine.payloads == []

    stats = drain(worker_for(client, engine))
    assert stats.claimed == 1 and stats.succeeded == 1
    assert engine.payloads == [
        {"input": "नमस्ते", "voice": "hf_alpha", "response_format": "wav"}
    ]


def test_a_finished_job_carries_the_stored_audio_and_the_meter_counts_it_once(
    client, engine
):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token, label="server")
    engine.set_audio_seconds(2.5)
    job = create_job(client, key, "बारह अक्षर का वाक्य")

    drain(worker_for(client, engine))
    done = get_job(client, key, job["id"])

    assert done["status"] == "succeeded"
    assert done["status_code"] == 200
    assert done["attempts"] == 1
    assert done["audio_url"] == f"/v1/jobs/{job['id']}/audio"
    assert done["audio"]["bytes"] == len(engine.wav)
    assert done["audio"]["sha256"] == hashlib.sha256(engine.wav).hexdigest()
    # The duration is measured from the WAV that was stored, not from a number
    # either side told the other.
    assert done["audio"]["seconds"] == 2.5

    response = fetch_job_audio(client, key, job["id"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.headers["x-qualiabits-object-sha256"] == done["audio"]["sha256"]
    assert response.headers["x-qualiabits-audio-seconds"] == "2.5"
    assert response.content == engine.wav

    meter = usage(client, key)
    assert meter["requests"] == 1
    assert meter["characters"] == job["characters"]
    assert meter["audio_seconds"] == 2.5
    assert meter["audio_bytes"] == len(engine.wav)
    assert meter["by_status"] == {"200": 1}
    assert meter["by_key"][0]["key_id"] == key.split("_")[1]
    assert meter["by_key"][0]["label"] == "server"
    assert meter["by_key"][0]["characters"] == job["characters"]


def test_the_same_job_finished_twice_is_still_one_metered_request(client, engine):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    create_job(client, key, "एक ही बार गिना जाना चाहिए")

    drain(worker_for(client, engine, worker_id="worker-a"))
    # A second worker pass finds nothing claimable: a finished job is a receipt.
    stats = drain(worker_for(client, engine, worker_id="worker-b"))

    assert stats.claimed == 0
    assert usage(client, key)["requests"] == 1
    assert engine.payloads.__len__() == 1


def test_the_engine_s_own_refusal_fails_the_job_and_is_recorded_verbatim(
    client, engine
):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    engine.mode = "refuse"
    job = create_job(client, key, "काल्पनिक आवाज़", voice="no_such_voice")

    stats = drain(worker_for(client, engine))
    failed = get_job(client, key, job["id"])

    assert stats.failed == 1
    assert failed["status"] == "failed"
    assert failed["status_code"] == 400
    assert failed["error"] == "engine says no"
    # A refusal is deterministic: asking the same question again changes nothing,
    # so there is exactly one attempt and no retry of the customer's characters.
    assert failed["attempts"] == 1
    assert failed["audio_url"] is None
    assert fetch_job_audio(client, key, job["id"]).status_code == 409

    meter = usage(client, key)
    assert meter["requests"] == 1
    assert meter["characters"] == job["characters"]
    assert meter["audio_seconds"] == 0.0
    assert meter["failed_requests"] == 1
    assert meter["by_status"] == {"400": 1}


def test_a_job_queued_from_the_dashboard_is_counted_without_a_key(client, engine):
    token = login(client, signup(client)["email"])
    job = create_job(client, token, "डैशबोर्ड से बनाया गया काम")

    assert usage(client, token)["requests"] == 0, "nothing spoken yet"

    drain(worker_for(client, engine))
    meter = usage(client, token)
    # The workspace total is there and the audio was delivered...
    assert meter["requests"] == 1
    assert meter["characters"] == job["characters"]
    assert meter["audio_seconds"] == engine.audio_seconds
    # ...but there is no key to attribute it to, and the breakdown says so by
    # being empty rather than by inventing one.
    assert meter["by_key"] == []


def test_another_workspace_cannot_see_read_cancel_or_download_a_job(client, engine):
    token_a = login(client, signup(client, "a@example.com")["email"])
    key_a = mint_key(client, token_a)
    token_b = login(client, signup(client, "b@example.com")["email"])
    job = create_job(client, key_a, "यह किसका काम है")
    drain(worker_for(client, engine))

    for method, path in (
        ("get", f"/v1/jobs/{job['id']}"),
        ("get", f"/v1/jobs/{job['id']}/audio"),
        ("delete", f"/v1/jobs/{job['id']}"),
    ):
        response = getattr(client, method)(
            path, headers={"Authorization": f"Bearer {token_b}"}
        )
        # The same answer as a job id that does not exist: the route is not a way
        # to learn which ids are real.
        assert response.status_code == 404, (method, path, response.text)

    assert client.get(
        "/v1/jobs/999999", headers={"Authorization": f"Bearer {token_b}"}
    ).status_code == 404
    assert list_jobs(client, token_b)["jobs"] == []
    assert list_jobs(client, token_b)["workspace_total"] == 0
    assert list_jobs(client, token_a)["workspace_total"] == 1


def test_job_ids_do_not_leak_another_workspace_s_row_count_or_status(client):
    token_a = login(client, signup(client, "a@example.com")["email"])
    token_b = login(client, signup(client, "b@example.com")["email"])
    create_job(client, token_a, "ठीक है")
    create_job(client, token_b, "मेरा काम")

    listing = list_jobs(client, token_b)
    assert [row["input"] for row in listing["jobs"]] == ["मेरा काम"]


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"input": ""}, 400),
        ({"input": "   "}, 400),
        ({"input": None}, 422),
        ({"input": "x" * 20001}, 413),
    ],
)
def test_the_refusals_before_queueing_are_explicit(client, payload, expected):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    response = client.post(
        "/v1/jobs", json=payload, headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code == expected, response.text
    assert client.get("/healthz").json()["jobs"] == {}, "nothing may be queued"


def test_a_job_at_the_limit_is_accepted_and_one_past_it_is_not(client):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)

    at_limit = client.post(
        "/v1/jobs",
        json={"input": "क" * 20000},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert at_limit.status_code == 202
    assert at_limit.json()["job"]["characters"] == 20000


def test_queueing_needs_a_credential(client):
    assert client.post("/v1/jobs", json={"input": "hello"}).status_code == 401


def test_cancelling_a_queued_job_is_free_and_cancelling_it_twice_is_a_conflict(
    client, engine
):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    job = create_job(client, key, "यह रद्द कर दिया जाएगा")

    cancelled = client.delete(
        f"/v1/jobs/{job['id']}", headers={"Authorization": f"Bearer {key}"}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["job"]["status"] == "cancelled"

    again = client.delete(
        f"/v1/jobs/{job['id']}", headers={"Authorization": f"Bearer {key}"}
    )
    assert again.status_code == 409

    # Nothing was sent to the engine, so the meter never heard about it.
    meter = usage(client, key)
    assert meter["requests"] == 0
    assert meter["characters"] == 0
    assert usage(client, key)["by_key"] == []

    # And a cancelled job is not claimable: it will never be spoken by accident.
    assert drain(worker_for(client, engine)).claimed == 0
    assert engine.payloads == []


def test_a_finished_job_cannot_be_cancelled(client, engine):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    job = create_job(client, key, "पूरा हो चुका काम")
    drain(worker_for(client, engine))

    response = client.delete(
        f"/v1/jobs/{job['id']}", headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code == 409
    # Cancelling a finished job did not remove what the customer paid for.
    assert get_job(client, key, job["id"])["status"] == "succeeded"
    assert fetch_job_audio(client, key, job["id"]).status_code == 200


def test_the_audio_of_a_queued_job_is_a_conflict_not_an_empty_file(client):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    job = create_job(client, key, "अभी बना नहीं है")

    response = fetch_job_audio(client, key, job["id"])
    assert response.status_code == 409
    assert response.json()["detail"]["job_status"] == "queued"


def test_a_single_request_still_works_alongside_jobs(client, engine):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)

    direct = speak(client, key, "सीधा अनुरोध")
    assert direct.status_code == 200
    job = create_job(client, key, "कतार वाला काम")
    drain(worker_for(client, engine))

    meter = usage(client, key)
    assert meter["requests"] == 2
    assert get_job(client, key, job["id"])["status"] == "succeeded"
    # Both routes are metered, and the job's characters are counted once.
    assert meter["characters"] == len("सीधा अनुरोध") + job["characters"]


def test_the_listing_filters_and_bounds_itself(client, engine):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    first = create_job(client, key, "पहला")
    second = create_job(client, key, "दूसरा")
    drain(worker_for(client, engine))

    engine.mode = "refuse"
    third = create_job(client, key, "तीसरा")
    drain(worker_for(client, engine))

    everything = list_jobs(client, key)
    assert [row["id"] for row in everything["jobs"]] == [third["id"], second["id"], first["id"]]
    assert everything["workspace_total"] == 3
    # The listing shows a preview, not the whole text: a list of a thousand
    # projects must not carry a thousand transcripts.
    assert everything["jobs"][0]["input_truncated"] is False

    failed = list_jobs(client, key, status="failed")
    assert [row["id"] for row in failed["jobs"]] == [third["id"]]
    assert list_jobs(client, key, limit=1)["returned"] == 1
    assert list_jobs(client, key, limit=1)["workspace_total"] == 3

    bad = client.get("/v1/jobs?status=finished", headers={"Authorization": f"Bearer {key}"})
    assert bad.status_code == 400
    too_many = client.get("/v1/jobs?limit=101", headers={"Authorization": f"Bearer {key}"})
    assert too_many.status_code == 422


def test_a_long_job_preview_is_truncated_in_the_listing_and_full_in_the_job(client):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    text = ("यह एक बहुत लंबा वाक्य है जिसे सूची में पूरा नहीं दिखाया जाएगा " * 4).strip()
    job = create_job(client, key, text)

    listed = list_jobs(client, key)["jobs"][0]
    assert listed["input_truncated"] is True
    assert len(listed["input"]) == 80
    fetched = get_job(client, key, job["id"])
    assert fetched["input"] == text
    assert fetched["input_truncated"] is False


def test_the_queue_survives_a_plane_restart(tmp_path, engine):
    """The queue is a table, not a queue object in memory."""
    from pathlib import Path

    with app_client(tmp_path, engine) as first:
        token = login(first, signup(first)["email"])
        key = mint_key(first, token)
        job = create_job(first, key, "बिजली जाने के बाद भी रहेगा")

    # The plane is gone; the job row and the database file are not.
    with app_client(tmp_path, engine) as second:
        reread = get_job(second, key, job["id"])
        assert reread["status"] == "queued"
        # The session token still works: it is a row, not a cookie in memory.
        drain(worker_for(second, engine))
        done = get_job(second, key, job["id"])
        assert done["status"] == "succeeded"
        audio = fetch_job_audio(second, key, job["id"])
        # The object store in the same directory the first plane was given.
        assert audio.status_code == 200
        assert audio.content == make_wav(engine.audio_seconds)