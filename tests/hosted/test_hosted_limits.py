"""Slice G: the limits, exercised against the real plane.

Everything here runs the real app: the real database, the real routes, the real
ledger and the real compare-and-swap in ``limits.py``. The engine is the stub in
``conftest.py``, and it is used for one thing a mock cannot fake — counting how
many times it was *asked*. That count is how "refused before the engine was
asked" stops being a claim in a docstring and becomes a number in an assertion.
"""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from conftest import app_client, drain, login, make_wav, mint_key, queue_job, signup, speak, transcribe, worker_for
from hosted.control_plane import limits
from hosted.control_plane.db import Database, Workspace, utcnow
from hosted.control_plane.migrations import migrate


def set_limits(client, token: str, **body):
    return client.put(
        "/v1/limits", json=body, headers={"Authorization": f"Bearer {token}"}
    )


def read_limits(client, credential: str):
    response = client.get(
        "/v1/limits", headers={"Authorization": f"Bearer {credential}"}
    )
    assert response.status_code == 200, response.headers
    return response.json()["limits"]


# ── the default is no limit, and that is a decision ───────────────────────────


def test_a_new_workspace_starts_with_no_limits_at_all(client):
    signup(client)
    token = login(client)
    state = read_limits(client, token)
    assert state["character_ceiling"] is None
    assert state["requests_per_minute"] is None
    assert state["characters_remaining"] is None
    # And nothing is throttled: ten requests in a row, all the engine's.
    keep = mint_key(client, token)
    for _ in range(10):
        assert speak(client, keep, "hello there").status_code == 200


def test_the_operator_can_impose_a_house_default_on_new_signups(tmp_path, engine):
    """A default that exists is enforced; the option is not a decoration."""
    with app_client(tmp_path, engine) as client:
        settings = client.app.state.hosted.settings
        object.__setattr__(settings, "default_character_ceiling", 12)
        signup(client, "house@example.com")
        token = login(client, "house@example.com")
        state = read_limits(client, token)
        assert state["character_ceiling"] == 12
        keep = mint_key(client, token)
        assert speak(client, keep, "12345678901").status_code == 200  # 11, fits
        refused = speak(client, keep, "12345678901")
        assert refused.status_code == 402


# ── the ceiling ───────────────────────────────────────────────────────────────


def test_the_ceiling_refuses_before_the_engine_is_asked(client, engine):
    signup(client)
    token = login(client)
    keep = mint_key(client, token)
    assert set_limits(client, token, character_ceiling=20).status_code == 200

    assert speak(client, keep, "123456789012345").status_code == 200  # 15
    asked_after_first = len(engine.payloads)

    refused = speak(client, keep, "123456789012345")
    assert refused.status_code == 402, refused.headers
    body = refused.json()["detail"]
    assert body["error"] == "character_ceiling_exceeded"
    assert body["limit"] == 20 and body["used"] == 15 and body["remaining"] == 5
    assert body["resets_at"].startswith("2026-12-01")
    # The assertion this whole test exists for.
    assert len(engine.payloads) == asked_after_first, (
        "the engine was asked for a request the ceiling had already refused"
    )
    headers = {k.lower(): v for k, v in refused.headers.items()}
    assert headers["x-qualiabits-limit"] == "20"
    # Retry-After is deliberately absent: waiting a minute cannot help.
    assert "retry-after" not in headers


def test_a_refused_request_costs_no_characters_but_shows_up_in_the_meter(client):
    signup(client)
    token = login(client)
    keep = mint_key(client, token)
    set_limits(client, token, character_ceiling=20)
    speak(client, keep, "123456789012345")
    speak(client, keep, "123456789012345")  # refused
    speak(client, keep, "123456789012345")  # refused again

    state = read_limits(client, token)
    assert state["characters_settled"] == 15, "a refused request charged the ceiling"
    assert state["characters_used"] == 15

    ledger = client.get("/v1/usage", headers={"Authorization": f"Bearer {token}"}).json()["usage"]
    assert ledger["by_status"].get("402") == 2, "the refusals are not in the meter"
    assert ledger["characters"] == 15


def test_lowering_the_ceiling_is_not_retroactive_and_bites_the_next_request(client):
    signup(client)
    token = login(client)
    keep = mint_key(client, token)
    set_limits(client, token, character_ceiling=100)
    assert speak(client, keep, "x" * 80).status_code == 200
    set_limits(client, token, character_ceiling=50)
    state = read_limits(client, token)
    assert state["characters_used"] == 80, "the spend already made was rewritten"
    assert state["characters_remaining"] == 0
    assert speak(client, keep, "x" * 10).status_code == 402


def test_the_ceiling_does_not_apply_to_transcription(client, engine):
    """The scope sentence, made executable: speech in is bounded by the rate."""
    signup(client)
    token = login(client)
    keep = mint_key(client, token)
    set_limits(client, token, character_ceiling=1)
    assert transcribe(client, keep).status_code == 200
    assert len(engine.transcriptions) == 1
    state = read_limits(client, token)
    assert state["characters_settled"] == 0


# ── the rate ──────────────────────────────────────────────────────────────────


def test_the_rate_refuses_with_a_real_retry_after(client, engine):
    signup(client)
    token = login(client)
    keep = mint_key(client, token)
    assert set_limits(client, token, requests_per_minute=3).status_code == 200

    for _ in range(3):
        assert speak(client, keep, "one").status_code == 200
    asked = len(engine.payloads)
    refused = speak(client, keep, "one")
    assert refused.status_code == 429, refused.headers
    headers = {k.lower(): v for k, v in refused.headers.items()}
    retry = int(headers["retry-after"])
    assert 1 <= retry <= 60
    assert headers["x-qualiabits-limit"] == "3"
    assert len(engine.payloads) == asked, "the engine was asked after a rate refusal"
    rate_body = refused.json()["detail"]
    assert rate_body["error"] == "request_rate_exceeded"
    assert rate_body["limit"] == 3 and rate_body["remaining"] == 0
    assert rate_body["retry_after"] == retry
    # The refused request cost no characters.
    assert read_limits(client, token)["characters_settled"] == 9


def test_the_rate_is_per_key_not_per_workspace(client):
    signup(client)
    token = login(client)
    first = mint_key(client, token, label="laptop")
    second = mint_key(client, token, label="server")
    set_limits(client, token, requests_per_minute=2)
    for _ in range(2):
        assert speak(client, first, "one").status_code == 200
    assert speak(client, first, "one").status_code == 429
    # The other key has its own window.
    assert speak(client, second, "one").status_code == 200
    assert speak(client, second, "one").status_code == 200
    assert speak(client, second, "one").status_code == 429


def test_the_window_is_fixed_and_says_so(client):
    signup(client)
    token = login(client)
    state = read_limits(client, token)
    assert state["rate_window_kind"] == "fixed"
    assert state["rate_window_seconds"] == 60


# ── the reservation: the part a sequential test cannot prove ──────────────────


def test_concurrent_requests_cannot_both_take_the_last_of_the_ceiling(tmp_path):
    """Eight threads, a ceiling of ten characters, five characters each.

    Exactly two may pass. This is the test the conditional UPDATE exists for: a
    read-then-write would let every one of them see room and every one of them
    spend it.
    """
    db = Database(f"sqlite:///{tmp_path / 'race.db'}")
    migrate(db, expected_version="m2g1")
    with db.session() as session:
        workspace = Workspace(
            email="race@example.com",
            display_name="Race",
            password_salt="s",
            password_hash="h",
            password_params="p",
            character_ceiling=10,
        )
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id

    outcomes: list[bool] = []
    lock = threading.Lock()

    def attempt() -> None:
        with db.session() as session:
            row = session.get(Workspace, workspace_id)
            refusal = limits.reserve(session, workspace=row, characters=5)
        with lock:
            outcomes.append(refusal is None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: attempt(), range(8)))

    assert sum(outcomes) == 2, f"{sum(outcomes)} of 8 requests passed a ceiling of 10"
    with db.session() as session:
        assert (
            limits.reserved_characters(session, workspace_id, limits.utc_month_start(utcnow()))
            == 10
        )


def test_a_ceiling_row_is_created_once_under_a_race(tmp_path):
    """Two reservations in the same fresh month both try to create the row."""
    db = Database(f"sqlite:///{tmp_path / 'row-race.db'}")
    migrate(db, expected_version="m2g1")
    with db.session() as session:
        workspace = Workspace(
            email="fresh@example.com",
            display_name="Fresh",
            password_salt="s",
            password_hash="h",
            password_params="p",
            character_ceiling=100,
        )
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id

    def attempt() -> None:
        with db.session() as session:
            row = session.get(Workspace, workspace_id)
            limits.reserve(session, workspace=row, characters=1)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: attempt(), range(6)))
    with sqlite3.connect(tmp_path / "row-race.db") as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM quota_periods WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()[0]
    assert rows == 1, "a race created more than one counter for the same month"


# ── jobs and projects: reservation in, settlement out ────────────────────────


def test_a_queued_job_reserves_and_its_settlement_charges_once(client, engine):
    signup(client)
    token = login(client)
    keep = mint_key(client, token)
    set_limits(client, token, character_ceiling=100)
    response = queue_job(client, keep, "x" * 40)
    assert response.status_code == 202, response.text
    state = read_limits(client, token)
    assert state["characters_reserved"] == 40
    assert state["characters_settled"] == 0
    assert state["characters_remaining"] == 60

    drain(worker_for(client, engine))
    state = read_limits(client, token)
    assert state["characters_settled"] == 40
    assert state["characters_reserved"] == 0
    assert state["characters_used"] == 40
    assert state["counter_committed"] == 40


def test_cancelling_a_queued_job_gives_the_ceiling_back(client):
    signup(client)
    token = login(client)
    keep = mint_key(client, token)
    set_limits(client, token, character_ceiling=50)
    job_id = queue_job(client, keep, "x" * 40).json()["job"]["id"]
    assert read_limits(client, token)["characters_remaining"] == 10
    cancelled = client.delete(
        f"/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {keep}"}
    )
    assert cancelled.status_code == 200, cancelled.text
    after = read_limits(client, token)
    assert after["characters_reserved"] == 0
    assert after["characters_remaining"] == 50
    assert after["characters_settled"] == 0, "a cancelled job was charged for work nobody did"
    assert after["counter_committed"] == 0
    # And the ceiling really is usable again.
    assert queue_job(client, keep, "y" * 40).status_code == 202


def test_a_project_reserves_exactly_what_its_chapters_will_speak(client, engine):
    signup(client)
    token = login(client)
    keep = mint_key(client, token)
    set_limits(client, token, character_ceiling=4000)
    # Sized to split into exactly two chapters, the way the browser end-to-end
    # sizes its manuscript: chapter boundaries are the splitter's decision, and a
    # test that asserts "two" has to give the splitter enough text for two.
    manuscript = ("पहला अध्याय। " * 60).strip() + "\n\n" + ("दूसरा अध्याय। " * 60).strip()
    response = client.post(
        "/v1/projects",
        json={"title": "Two chapters", "input": manuscript, "voice": "hf_alpha"},
        headers={"Authorization": f"Bearer {keep}"},
    )
    assert response.status_code == 202, response.text
    chapters = response.json()["project"]["chapter_count"]
    assert chapters == 2
    # The chapters, not the manuscript: the two characters that separate them are
    # never spoken, so reserving the manuscript would strand them all month.
    reserved = read_limits(client, token)["characters_reserved"]
    assert reserved == len(manuscript) - 2, "the reservation is not what the chapters speak"

    drain(worker_for(client, engine))
    state = read_limits(client, token)
    assert state["characters_reserved"] == 0, "a settled project stranded a reservation"
    assert state["characters_settled"] == len(manuscript) - 2, (
        "the two separator characters are not spoken and must not be charged"
    )
    assert state["counter_committed"] == state["characters_settled"], "counter drifted"
    assert state["characters_remaining"] == 4000 - state["characters_settled"]


# ── the routes' contract ──────────────────────────────────────────────────────


def test_a_key_can_read_the_limits_and_cannot_change_them(client):
    signup(client)
    token = login(client)
    keep = mint_key(client, token, label="server")
    assert read_limits(client, keep)["character_ceiling"] is None
    response = client.put(
        "/v1/limits",
        json={"requests_per_minute": 5},
        headers={"Authorization": f"Bearer {keep}"},
    )
    assert response.status_code == 401, "a key raised its own limits"
    assert read_limits(client, token)["requests_per_minute"] is None


def test_an_empty_change_is_refused_and_a_partial_one_leaves_the_rest(client):
    signup(client)
    token = login(client)
    assert set_limits(client, token).status_code == 400
    set_limits(client, token, character_ceiling=100)
    set_limits(client, token, requests_per_minute=5)
    set_limits(client, token, character_ceiling=200)
    state = read_limits(client, token)
    assert state["character_ceiling"] == 200
    assert state["requests_per_minute"] == 5, "a partial change cleared the other limit"
    # null clears, and 0 means the same thing.
    set_limits(client, token, requests_per_minute=None)
    assert read_limits(client, token)["requests_per_minute"] is None
    set_limits(client, token, character_ceiling=0)
    assert read_limits(client, token)["character_ceiling"] is None


def test_an_impossible_limit_is_refused_by_the_schema(client):
    signup(client)
    token = login(client)
    assert set_limits(client, token, character_ceiling=-1).status_code == 422
    assert set_limits(client, token, requests_per_minute=10**9).status_code == 422


# ── the migration an existing plane actually takes ───────────────────────────


def test_a_slice_f_database_upgrades_to_g_and_keeps_its_ledger(tmp_path):
    """The realistic upgrade: a plane already running slice F."""
    path = tmp_path / "slice-f.db"
    db = Database(f"sqlite:///{path}")
    assert migrate(db, expected_version="m2f1") == "created"
    with db.session() as session:
        workspace = Workspace(
            email="old@example.com",
            display_name="Old",
            password_salt="s",
            password_hash="h",
            password_params="p",
        )
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id
        from hosted.control_plane import metering

        metering.record(
            session, workspace_id=workspace_id, api_key_id=None,
            route="/v1/audio/speech", status_code=200, characters=42,
        )
    db.dispose()

    db = Database(f"sqlite:///{path}")
    assert migrate(db, expected_version="m2g1") == "upgraded:m2f1->m2g1"
    with db.session() as session:
        workspace = session.get(Workspace, workspace_id)
        assert workspace.character_ceiling == 0
        assert workspace.requests_per_minute == 0
        start = limits.utc_month_start(utcnow())
        assert limits.settled_characters(session, workspace_id, start) == 42
        assert limits.reserved_characters(session, workspace_id, start) == 0
    assert migrate(db, expected_version="m2g1") == "current"