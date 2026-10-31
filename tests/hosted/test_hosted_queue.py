"""What the queue promises: one worker per job, leases that expire, bounded retries.

These tests poke the queue directly, on a real SQLite file, without the HTTP layer.
That is where the concurrency lives, and a claim race is not something an HTTP test
can produce reliably — it needs real threads writing to one database.

The three failure modes they are built around are the ones that cost money in
production: two workers speaking the same job (double the cost, two files, one
receipt), a job nobody can ever claim (a customer waiting for a file that will never
exist), and a job that retries forever (a queue that never empties).
"""
from __future__ import annotations

import os
import socket
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from hosted.control_plane import jobs, security
from hosted.control_plane.config import Settings
from hosted.control_plane.db import ApiKey, Database, Job, UsageEvent, Workspace, utcnow
from hosted.control_plane.storage import LocalObjectStore
from hosted.control_plane.upstream import UpstreamClient
from hosted.worker.runner import Worker


def free_closed_port() -> int:
    """A port nothing is listening on: a real connection refusal, not a mock."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def world(tmp_path: Path):
    """A plane's database, one workspace with a key, and a store. No HTTP."""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'queue.db'}",
        upstream_base_url=f"http://127.0.0.1:{free_closed_port()}",
        upstream_timeout_seconds=5.0,
        storage_dir=str(tmp_path / "objects"),
        s3_bucket="",
        job_lease_seconds=900,
        job_max_attempts=3,
    )
    db = Database(settings.database_url)
    assert db.init_schema(settings.schema_version) == "created"
    salt, digest, params = security.hash_password("a real password here")
    with db.session() as session:
        workspace = Workspace(
            email="queue@example.com",
            password_salt=salt,
            password_hash=digest,
            password_params=params,
        )
        session.add(workspace)
        session.flush()
        key = ApiKey(workspace_id=workspace.id, key_id="testkeyid", key_digest="x" * 64)
        session.add(key)
        session.flush()
        workspace_id, api_key_id = workspace.id, key.id

    class World:
        pass

    bag = World()
    bag.settings = settings
    bag.db = db
    bag.store = LocalObjectStore(settings.storage_dir)
    bag.workspace_id = workspace_id
    bag.api_key_id = api_key_id

    def queue(text: str = "एक वाक्य", **kwargs) -> int:
        with db.session() as session:
            job = jobs.enqueue(
                session,
                workspace_id=bag.workspace_id,
                api_key_id=bag.api_key_id,
                text=text,
                voice="hf_alpha",
                max_attempts=kwargs.pop("max_attempts", settings.job_max_attempts),
                **kwargs,
            )
            session.commit()
            return job.id

    def row(job_id: int) -> Job:
        with db.session() as session:
            return session.get(Job, job_id)

    bag.queue = queue
    bag.row = row
    yield bag
    db.dispose()


def usage_rows(world) -> list[UsageEvent]:
    with world.db.session() as session:
        return session.query(UsageEvent).order_by(UsageEvent.id).all()


# ── one worker per job ────────────────────────────────────────────────────────


def test_four_workers_racing_claim_every_job_exactly_once(world):
    """The claim is a compare-and-swap, so a race cannot hand one job to two workers."""
    job_ids = [world.queue(f"काम {index}") for index in range(12)]
    claimed: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    start = threading.Barrier(4)

    def take(worker: str) -> None:
        try:
            start.wait(timeout=30)
            for _ in range(60):
                job = jobs.claim_one(world.db, worker_id=worker, lease_seconds=900)
                if job is None:
                    return
                with lock:
                    claimed.append(job.id)
        except BaseException as exc:  # noqa: BLE001 - a thread's failure is the test's
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=take, args=(f"w{index}",)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert sorted(claimed) == job_ids, "every queued job was claimed"
    assert len(claimed) == len(set(claimed)), f"a job was claimed twice: {claimed}"
    for job_id in job_ids:
        assert world.row(job_id).attempts == 1
    assert jobs.claim_one(world.db, worker_id="late", lease_seconds=900) is None


# ── leases ────────────────────────────────────────────────────────────────────


def test_a_job_with_a_live_lease_is_not_claimable_by_another_worker(world):
    job_id = world.queue()
    first = jobs.claim_one(world.db, worker_id="w1", lease_seconds=900)

    assert first.id == job_id
    assert first.worker_id == "w1"
    assert jobs.claim_one(world.db, worker_id="w2", lease_seconds=900) is None


def test_a_lease_that_expires_is_reclaimed_by_another_worker(world):
    job_id = world.queue()
    jobs.claim_one(world.db, worker_id="w1", lease_seconds=900)
    # The worker died here: its lease is now in the past.
    with world.db.session() as session:
        session.get(Job, job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        session.commit()

    second = jobs.claim_one(world.db, worker_id="w2", lease_seconds=900)

    assert second is not None and second.id == job_id
    assert second.worker_id == "w2"
    assert second.attempts == 2
    # The row now belongs to w2: w1 can no longer renew it.
    assert jobs.heartbeat(world.db, job_id=job_id, worker_id="w1", lease_seconds=900) is False
    assert jobs.heartbeat(world.db, job_id=job_id, worker_id="w2", lease_seconds=900) is True


def test_a_job_whose_lease_expired_on_the_last_attempt_is_failed_not_left_running(world):
    job_id = world.queue(max_attempts=1)
    jobs.claim_one(world.db, worker_id="w1", lease_seconds=900)
    with world.db.session() as session:
        session.get(Job, job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        session.commit()

    # Nothing else will ever claim it (the claim needs an attempt in hand), so the
    # next claim pass has to settle it. Otherwise it is running forever.
    assert jobs.claim_one(world.db, worker_id="w2", lease_seconds=900) is None

    settled = world.row(job_id)
    assert settled.status == "failed"
    assert settled.status_code == 504
    assert "lease expired" in settled.error
    # One job, one usage event, even though it never produced audio.
    rows = usage_rows(world)
    assert len(rows) == 1
    assert rows[0].status_code == 504
    assert rows[0].characters == settled.characters
    assert rows[0].audio_seconds is None
    # And the sweep does not do it twice.
    assert jobs.claim_one(world.db, worker_id="w3", lease_seconds=900) is None
    assert len(usage_rows(world)) == 1


def test_a_worker_that_lost_its_lease_cannot_write_its_result_over_the_new_owner(world):
    job_id = world.queue()
    jobs.claim_one(world.db, worker_id="w1", lease_seconds=1)
    with world.db.session() as session:
        session.get(Job, job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    assert jobs.claim_one(world.db, worker_id="w2", lease_seconds=900) is not None

    stored = world.store.put(b"RIFF a stale result", "audio/wav")
    written = jobs.succeed(
        world.db,
        job_id=job_id,
        worker_id="w1",
        stored=stored,
        store=world.store,
        engine_seconds=1.0,
    )

    assert written is None, "the old worker must not settle a job it no longer holds"
    assert world.row(job_id).worker_id == "w2"
    assert world.row(job_id).status == "running"
    assert usage_rows(world) == []


# ── retries, bounded ──────────────────────────────────────────────────────────


def test_a_retryable_failure_requeues_the_job_and_counts_nothing_yet(world):
    """The engine being unreachable is our problem, so the job goes back in line."""
    job_id = world.queue("दोबारा कोशिश")
    worker = Worker(world.settings, "w1", db=world.db, store=world.store)

    import asyncio

    # One pass only: with a broken engine, a full drain would spend all three
    # attempts here and the point of this test is the first one.
    asyncio.run(worker.drain(max_jobs=1, idle_polls_before_exit=1))

    row = world.row(job_id)
    assert row.status == "queued", "a 502 is retried, not billed as a failure"
    assert row.attempts == 1
    assert row.error == "upstream_unreachable"
    assert row.lease_expires_at is None
    assert usage_rows(world) == []
    # The wasted attempt is recorded as wasted, and separately from delivered work.
    assert row.wasted_engine_seconds > 0
    assert row.engine_seconds is None


def test_a_job_that_exhausts_its_attempts_fails_once_world(world):
    """Bounded: two attempts, one terminal failure, one usage event, characters once."""
    settings = Settings(
        database_url=world.settings.database_url,
        upstream_base_url=world.settings.upstream_base_url,
        upstream_timeout_seconds=5.0,
        storage_dir=world.settings.storage_dir,
        job_lease_seconds=900,
        job_max_attempts=2,
        job_poll_seconds=0.01,
    )
    job_id = world.queue("बार-बार नहीं", max_attempts=2)
    worker = Worker(settings, "w1", db=world.db, store=world.store)

    import asyncio

    stats = asyncio.run(worker.drain(idle_polls_before_exit=1))

    row = world.row(job_id)
    assert stats.claimed == 2, "two attempts, and not a third"
    assert stats.failed == 1 and stats.requeued == 1
    assert row.status == "failed"
    assert row.attempts == 2
    assert row.status_code == 502
    rows = usage_rows(world)
    assert len(rows) == 1
    assert rows[0].characters == row.characters, "a retry is not a second order"
    assert rows[0].status_code == 502


def test_the_worker_stores_the_audio_and_the_meter_measures_the_stored_bytes(world):
    """End of the queue, without any HTTP: claim, "speak", store, settle."""
    job_id = world.queue("सीधे कतार से")
    claim = jobs.claim_one(world.db, worker_id="w1", lease_seconds=900)
    assert claim.id == job_id

    stored = world.store.put(b"RIFF" + b"\x00" * 40, "audio/wav")
    settled = jobs.succeed(
        world.db,
        job_id=job_id,
        worker_id="w1",
        stored=stored,
        store=world.store,
        engine_seconds=2.0,
    )

    assert settled.status == "succeeded"
    assert settled.object_key == stored.key
    assert settled.audio_bytes == stored.size
    assert settled.engine_seconds == 2.0
    with world.db.session() as session:
        assert session.get(Job, job_id).status == "succeeded"
    # Not a WAV with a readable header: the meter records the bytes and leaves the
    # duration unknown rather than inventing one.
    rows = usage_rows(world)
    assert len(rows) == 1
    assert rows[0].audio_seconds is None
    assert rows[0].audio_bytes == stored.size
    assert world.store.get(stored.key) == b"RIFF" + b"\x00" * 40


def test_an_unreachable_engine_is_a_real_connection_refusal(world):
    """Guards the tests above: the closed port really refuses connections."""
    import asyncio

    client = UpstreamClient(world.settings.upstream_base_url, 5.0)
    try:
        result = asyncio.run(client.speech({"input": "hello"}))
    finally:
        asyncio.run(client.aclose())
    assert result.status == 502
    assert result.delivered is False
    assert result.error == "upstream_unreachable"