"""The job queue: claim a job, do the work, settle it once.

Why a queue at all, when slice A already proxies ``POST /v1/audio/speech``. A
paragraph of Hindi on CPU is minutes of compute; an HTTP request is not where that
belongs. A caller needs to hand over the work, get an id back, and come back for
the file — and when the engine is down or the box reboots mid-synthesis, somebody
has to be able to say what happened to that work. That record is the job row.

The hard part is not the table, it is one rule: **exactly one worker does a job**.
Three mechanisms hold that up, and each is tested:

1. Claiming is an optimistic compare-and-swap. The worker selects a candidate id,
   then issues one ``UPDATE ... WHERE id = :candidate AND <still claimable>`` and
   believes only a rowcount of 1. Two workers that read the same candidate both
   write, and the loser's WHERE no longer matches. That works on SQLite, which has
   no ``SELECT ... FOR UPDATE SKIP LOCKED``.
2. A lease, not a lock. A worker that dies holding a job does not hold it forever:
   the claim is valid until ``lease_expires_at``, after which another worker may
   take it. The worker heartbeats while a slow engine call runs, so a live worker
   does not lose a job it is still doing.
3. A bounded number of attempts, and a sweep for the last one. A job whose lease
   expired after its final attempt is not claimable (the claim requires
   ``attempts < max_attempts``) and would sit ``running`` forever, so ``claim_one``
   fails it and writes the one usage event it owes.

One usage event per job, written when the job reaches a terminal state. A retry
after our own failure is not a second order, so the characters are counted once.
The engine time burned on attempts that were thrown away is recorded separately as
``wasted_engine_seconds`` rather than added to ``engine_seconds``, so nobody can
read a blended number as work done for the customer. Cancelling a queued job is
free: nothing was ever sent to the engine, so the meter never sees it.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from . import metering
from .db import ApiKey, Database, Job, utcnow
from .storage import StoredObject

#: Jobs exist for the long text the single-request route refuses; 5,000 is that
#: route's ceiling, so this is deliberately larger and still a stated limit.
MAX_JOB_CHARS = 20000
INPUT_PREVIEW_CHARS = 80

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL_STATUSES = (SUCCEEDED, FAILED, CANCELLED)

#: The engine's own refusals are deterministic: asking again changes nothing, so
#: they fail the job on the first try instead of spending two more attempts and
#: two more minutes of CPU to be told the same thing.
RETRYABLE_STATUS_CODES = (502, 503, 504)


def claimable(now) -> Any:
    return or_(
        Job.status == QUEUED,
        and_(
            Job.status == RUNNING,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at < now,
        ),
    )


def enqueue(
    session: Session,
    *,
    workspace_id: int,
    api_key_id: int | None,
    text: str,
    voice: str = "",
    response_format: str = "wav",
    max_attempts: int = 3,
) -> Job:
    """Queue one job. The caller validates and commits."""
    job = Job(
        workspace_id=workspace_id,
        api_key_id=api_key_id,
        status=QUEUED,
        input_text=text,
        voice=voice,
        response_format=response_format,
        characters=len(text),
        attempts=0,
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()
    return job


def job_dict(job: Job, *, full_input: bool = False) -> dict:
    """What a customer sees. Never another workspace's row, never a worker id."""
    text = job.input_text or ""
    out: dict = {
        "id": job.id,
        "status": job.status,
        "voice": job.voice,
        "response_format": job.response_format,
        "characters": job.characters,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "status_code": job.status_code,
        "error": job.error or "",
        "input": text if full_input else text[:INPUT_PREVIEW_CHARS],
        "input_truncated": len(text) > INPUT_PREVIEW_CHARS and not full_input,
        "audio": None,
        "audio_url": None,
    }
    if job.object_key and job.status == SUCCEEDED:
        out["audio"] = {
            "sha256": job.object_sha256,
            "bytes": job.audio_bytes,
            "seconds": job.audio_seconds,
            "engine_seconds": job.engine_seconds,
            "wasted_engine_seconds": job.wasted_engine_seconds,
        }
        out["audio_url"] = f"/v1/jobs/{job.id}/audio"
    return out


def _sweep_unclaimable_running(db: Database, now) -> int:
    """Fail the jobs that are running, out of attempts, and past their lease.

    These are the last attempt of a worker that then died. Nothing else will ever
    claim them (the claim needs an attempt in hand), so leaving them ``running``
    would be a job that is neither going to finish nor going to fail — the worst
    answer for a customer and for anyone reading the log later.
    """
    with db.session() as session:
        rows = session.execute(
            select(Job).where(
                Job.status == RUNNING,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at < now,
                Job.attempts >= Job.max_attempts,
            )
        ).scalars().all()
        for job in rows:
            job.status = FAILED
            job.status_code = 504
            job.error = "the worker's lease expired on the final attempt"
            job.finished_at = now
            metering.record(
                session,
                workspace_id=job.workspace_id,
                api_key_id=job.api_key_id,
                route=JOBS_ROUTE,
                status_code=504,
                characters=job.characters,
                error="worker lease expired on the final attempt",
            )
        session.commit()
        return len(rows)


def claim_one(
    db: Database, *, worker_id: str, lease_seconds: int, candidates: int = 64
) -> Job | None:
    """Claim one job for ``worker_id``, or return None when there is nothing to do.

    Each attempt in the loop reads one candidate and then tries the compare-and-
    swap on it. Losing means another worker got there first, so the loop reads the
    next candidate rather than the same one.
    """
    _sweep_unclaimable_running(db, utcnow())
    for _ in range(candidates):
        with db.session() as session:
            now = utcnow()
            candidate_id = session.execute(
                select(Job.id)
                .where(Job.attempts < Job.max_attempts, claimable(now))
                .order_by(Job.id)
                .limit(1)
            ).scalar_one_or_none()
            if candidate_id is None:
                return None
            result = session.execute(
                update(Job)
                .where(
                    Job.id == candidate_id,
                    Job.attempts < Job.max_attempts,
                    claimable(now),
                )
                .values(
                    status=RUNNING,
                    worker_id=worker_id,
                    attempts=Job.attempts + 1,
                    started_at=func.coalesce(Job.started_at, now),
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    finished_at=None,
                )
            )
            if result.rowcount == 1:
                session.commit()
                job = session.get(Job, candidate_id)
                return job
            # Somebody else claimed it between our SELECT and our UPDATE. Their
            # claim stands; we go round again and look at the next candidate.
            session.rollback()
    return None


def heartbeat(db: Database, *, job_id: int, worker_id: str, lease_seconds: int) -> bool:
    """Extend our own lease. Returns False when the job is no longer ours."""
    now = utcnow()
    with db.session() as session:
        result = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == RUNNING, Job.worker_id == worker_id)
            .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
        )
        session.commit()
        return result.rowcount == 1


JOBS_ROUTE = "/v1/jobs"


def _own_job(session: Session, *, job_id: int, worker_id: str) -> Job | None:
    """The job row, but only if this worker still holds it.

    A worker whose lease expired and was reclaimed by somebody else must not write
    its result over the new worker's: the row is the receipt, and the second worker
    owns it now. Returning None here means the worker drops its work on the floor
    and says so in its log.
    """
    return session.execute(
        select(Job).where(Job.id == job_id, Job.worker_id == worker_id, Job.status == RUNNING)
    ).scalar_one_or_none()


def succeed(
    db: Database,
    *,
    job_id: int,
    worker_id: str,
    stored: StoredObject,
    store,
    engine_seconds: float,
    status_code: int = 200,
) -> Job | None:
    """Record delivered audio: terminal, one usage event, audio measured for real.

    ``store`` is passed in rather than looked up so that the meter is fed the bytes
    the backend can actually hand back. If the store cannot return them, the audio
    seconds stay unknown (``None``) and the request lands in
    ``requests_without_audio_seconds`` — an invented duration would be worse.
    """
    try:
        stored_bytes = store.get(stored.key)
    except Exception:  # noqa: BLE001 - the meter must not turn a delivered job into a failure
        stored_bytes = None
    with db.session() as session:
        job = _own_job(session, job_id=job_id, worker_id=worker_id)
        if job is None:
            return None
        job.status = SUCCEEDED
        job.status_code = status_code
        job.error = ""
        job.object_key = stored.key
        job.object_sha256 = stored.sha256
        job.audio_bytes = stored.size
        job.engine_seconds = engine_seconds
        job.finished_at = utcnow()
        # The duration is measured from the stored object's own header, the same
        # way the single-request route measures the bytes it hands back.
        event = metering.record(
            session,
            workspace_id=job.workspace_id,
            api_key_id=job.api_key_id,
            route=JOBS_ROUTE,
            status_code=status_code,
            characters=job.characters,
            body=stored_bytes,
            engine_seconds=engine_seconds,
        )
        job.audio_seconds = event.audio_seconds
        _touch_key(session, job.api_key_id)
        session.commit()
        return job


def fail(
    db: Database,
    *,
    job_id: int,
    worker_id: str,
    status_code: int,
    error: str,
    engine_seconds: float = 0.0,
    retryable: bool | None = None,
) -> Job | None:
    """Record a failed attempt, and decide whether it is also the failed job.

    Retryable means our side of the wire broke — the engine did not answer. Then
    the job goes back to ``queued`` and no usage event is written, because the job
    has not reached a terminal state. Anything else, or the last allowed attempt,
    is terminal: one usage event, characters counted, no audio.
    """
    if retryable is None:
        retryable = status_code in RETRYABLE_STATUS_CODES
    with db.session() as session:
        job = _own_job(session, job_id=job_id, worker_id=worker_id)
        if job is None:
            return None
        job.wasted_engine_seconds = (job.wasted_engine_seconds or 0.0) + engine_seconds
        exhausted = job.attempts >= job.max_attempts
        if retryable and not exhausted:
            job.status = QUEUED
            job.worker_id = ""
            job.lease_expires_at = None
            job.error = error[:200]
            session.commit()
            return job
        job.status = FAILED
        job.status_code = status_code
        job.error = error[:200]
        job.finished_at = utcnow()
        metering.record(
            session,
            workspace_id=job.workspace_id,
            api_key_id=job.api_key_id,
            route=JOBS_ROUTE,
            status_code=status_code,
            characters=job.characters,
            error=error,
        )
        _touch_key(session, job.api_key_id)
        session.commit()
        return job


def cancel(session: Session, job: Job) -> bool:
    """Cancel a queued job. Nothing was sent to the engine, so the meter is untouched."""
    if job.status != QUEUED:
        return False
    job.status = CANCELLED
    job.finished_at = utcnow()
    job.error = "cancelled before it was claimed"
    return True


def _touch_key(session: Session, api_key_id: int | None) -> None:
    if api_key_id is None:
        return
    key = session.get(ApiKey, api_key_id)
    if key is not None:
        metering.touch_key(session, key)