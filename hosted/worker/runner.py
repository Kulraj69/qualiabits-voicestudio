"""The worker: claims queued jobs, asks the engine, stores what comes back.

A second process, on purpose. The plane answers HTTP and must never spend ten
minutes synthesising; the worker spends minutes per job and must never hold an HTTP
request open. They share one database file and one object store, which is exactly
why the claim is a compare-and-swap and the claim is a lease (see ``jobs``).

What this process does with a claimed job:

    claim -> heartbeat while the engine works -> POST /v1/audio/speech
          -> delivered: store the bytes, settle the job and its one usage event
          -> refused: settle it failed (the engine's own refusal is deterministic)
          -> our failure (timeout, connection): requeue it, up to the attempt limit

Two things it deliberately does not do. It does not retry a refusal — the engine
saying "I do not have that voice" will say it again, and spending two more minutes
of CPU to confirm it is not diligence. And it does not write a result for a job it
no longer holds: if the lease expired while the engine was working and another
worker took over, ``succeed``/``fail`` return None and this worker drops its result
and logs that it did.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field

from ..control_plane import jobs, storage
from ..control_plane.config import Settings
from ..control_plane.db import Database
from ..control_plane.upstream import UpstreamClient


def error_from(result) -> str:
    """A short, honest reason from an engine response. Never a stack trace."""
    body = result.body or b""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:200]
        if detail is not None:
            return json.dumps(detail)[:200]
    if result.error:
        return result.error
    return f"engine answered {result.status}"


def payload_for(job) -> dict:
    """What the engine is asked. An empty voice means the engine's own default."""
    payload: dict = {"input": job.input_text}
    if job.voice:
        payload["voice"] = job.voice
    if job.response_format:
        payload["response_format"] = job.response_format
    return payload


@dataclass
class WorkerStats:
    claimed: int = 0
    succeeded: int = 0
    failed: int = 0
    requeued: int = 0
    lost_lease: int = 0
    idle_polls: int = 0
    engine_seconds: float = field(default=0.0)

    def as_dict(self) -> dict:
        return {
            "claimed": self.claimed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "requeued": self.requeued,
            "lost_lease": self.lost_lease,
            "idle_polls": self.idle_polls,
            "engine_seconds": round(self.engine_seconds, 3),
        }


class Worker:
    """One worker's worth of state. ``db``/``store``/``upstream`` are injectable."""

    def __init__(
        self,
        settings: Settings,
        worker_id: str,
        *,
        db: Database | None = None,
        store: storage.ObjectStore | None = None,
        upstream: UpstreamClient | None = None,
        log=None,
    ) -> None:
        self.settings = settings
        self.worker_id = worker_id
        self.db = db or Database(settings.database_url)
        self.store = store or storage.build_store(settings)
        self.upstream = upstream or UpstreamClient(
            settings.upstream_base_url, settings.upstream_timeout_seconds
        )
        self.stats = WorkerStats()
        self.log = log or (lambda line: print(line, file=sys.stderr, flush=True))

    async def aclose(self) -> None:
        await self.upstream.aclose()
        self.db.dispose()

    async def _heartbeat_until(self, job_id: int, done: asyncio.Event) -> None:
        """Keep the lease fresh while the engine works, until ``done`` is set.

        The interval is a third of the lease, so two heartbeats can fail before the
        lease is at risk. The database call is synchronous; it runs in a thread so
        the heartbeat does not block the await on the engine.
        """
        interval = max(1.0, self.settings.job_lease_seconds / 3)
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if done.is_set():
                return
            try:
                held = await asyncio.to_thread(
                    jobs.heartbeat,
                    self.db,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.settings.job_lease_seconds,
                )
            except Exception as exc:  # noqa: BLE001 - a heartbeat must not kill the job
                self.log(f"worker {self.worker_id}: heartbeat failed: {type(exc).__name__}")
                continue
            if not held:
                self.log(
                    f"worker {self.worker_id}: job {job_id} is no longer ours; "
                    "we are not renewing its lease"
                )
                return

    async def process_one(self) -> str:
        """Claim and finish one job. Returns "worked", "idle" or "lost"."""
        job = await asyncio.to_thread(
            jobs.claim_one,
            self.db,
            worker_id=self.worker_id,
            lease_seconds=self.settings.job_lease_seconds,
        )
        if job is None:
            self.stats.idle_polls += 1
            return "idle"
        self.stats.claimed += 1
        self.log(
            f"worker {self.worker_id}: claimed job {job.id} "
            f"({job.characters} characters, attempt {job.attempts}/{job.max_attempts})"
        )
        done = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat_until(job.id, done))
        try:
            result = await self.upstream.speech(payload_for(job))
        finally:
            done.set()
            await heartbeat

        self.stats.engine_seconds += result.elapsed_seconds
        if result.delivered:
            assert result.body is not None  # ``delivered`` means 2xx with a body
            try:
                stored = await asyncio.to_thread(
                    self.store.put, result.body, result.media_type or "audio/wav"
                )
            except storage.StorageError as exc:
                # Our storage refused the audio. Retryable: the job is not the
                # customer's mistake, and the next attempt may find storage well.
                settled = await asyncio.to_thread(
                    jobs.fail,
                    self.db,
                    job_id=job.id,
                    worker_id=self.worker_id,
                    status_code=500,
                    error=f"storage refused the object: {exc}",
                    engine_seconds=result.elapsed_seconds,
                    retryable=True,
                )
                self._log_settle(job.id, settled, "storage refused")
                return "worked"
            settled = await asyncio.to_thread(
                jobs.succeed,
                self.db,
                job_id=job.id,
                worker_id=self.worker_id,
                stored=stored,
                store=self.store,
                engine_seconds=result.elapsed_seconds,
                status_code=result.status,
            )
            self._log_settle(job.id, settled, f"stored {stored.key}")
            return "worked" if settled is not None else "lost"

        settled = await asyncio.to_thread(
            jobs.fail,
            self.db,
            job_id=job.id,
            worker_id=self.worker_id,
            status_code=result.status,
            error=error_from(result),
            engine_seconds=result.elapsed_seconds,
        )
        self._log_settle(job.id, settled, f"engine answered {result.status}")
        return "worked" if settled is not None else "lost"

    def _log_settle(self, job_id: int, settled, what: str) -> None:
        if settled is None:
            self.stats.lost_lease += 1
            self.log(
                f"worker {self.worker_id}: job {job_id} was reclaimed by another "
                f"worker while we were working; discarding our result ({what})"
            )
        elif settled.status == jobs.SUCCEEDED:
            self.stats.succeeded += 1
            self.log(
                f"worker {self.worker_id}: job {job_id} succeeded, "
                f"{settled.audio_seconds} s of audio ({what})"
            )
        elif settled.status == jobs.QUEUED:
            self.stats.requeued += 1
            self.log(
                f"worker {self.worker_id}: job {job_id} requeued for another "
                f"attempt ({what}: {settled.error})"
            )
        else:
            self.stats.failed += 1
            self.log(
                f"worker {self.worker_id}: job {job_id} failed, {settled.status_code} "
                f"({what}: {settled.error})"
            )

    async def drain(
        self, *, max_jobs: int = 0, idle_polls_before_exit: int = 1, poll_seconds: float | None = None
    ) -> WorkerStats:
        """Work until the queue is empty (or a limit is reached), then stop.

        ``idle_polls_before_exit`` is what makes this usable from a test and from
        the end-to-end script: an idle poll means a claim found nothing claimable,
        which for a single-writer queue is the end of the work in front of it.
        """
        poll = self.settings.job_poll_seconds if poll_seconds is None else poll_seconds
        idle = 0
        while True:
            outcome = await self.process_one()
            if max_jobs and self.stats.claimed >= max_jobs:
                break
            if outcome == "idle":
                idle += 1
                if idle >= idle_polls_before_exit:
                    break
                if poll:
                    await asyncio.sleep(poll)
            else:
                idle = 0
        return self.stats

    async def serve(
        self, *, poll_seconds: float | None = None, idle_exit: bool = False, max_jobs: int = 0
    ) -> WorkerStats:
        """Keep polling forever, unless ``idle_exit`` says one empty pass is the end."""
        if idle_exit:
            return await self.drain(
                max_jobs=max_jobs, idle_polls_before_exit=1, poll_seconds=poll_seconds
            )
        poll = self.settings.job_poll_seconds if poll_seconds is None else poll_seconds
        while True:
            await self.process_one()
            if max_jobs and self.stats.claimed >= max_jobs:
                break
            await asyncio.sleep(poll)
        return self.stats