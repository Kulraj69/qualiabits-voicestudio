"""The control plane's own database: workspaces, keys, sessions, usage.

This database is separate from the engine's. Nothing in ``backend/`` opens it and
nothing here opens the engine's, which is why an engine upgrade cannot change how
a customer authenticates.

SQLite by default (a single box, one control plane), any SQLAlchemy URL via
``QUALI_DATABASE_URL`` otherwise. The schema is created on startup with
``create_all``, and a database that already exists is brought forward one ordered
step at a time by ``migrations.py``; the version it has been brought to lives in
``schema_meta``. The engine's tables are not here and nothing in ``backend/``
opens this database.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    """Aware UTC. Stored naive-UTC by SQLite, read back as UTC here."""
    return datetime.now(timezone.utc)


def _sqlite_pragmas(connection, _record) -> None:
    """Per-connection SQLite setup. Runs on every connection of the engine."""
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    """One customer. One workspace per signup, on purpose: the queue's V0 plan
    says "one workspace per customer", and seats/invites are not in slice A."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    password_salt: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(128))
    password_params: Mapped[str] = mapped_column(String(64))
    #: Slice G. Characters this workspace may ask the engine to speak in one UTC
    #: calendar month. ``0`` means no ceiling, which is the default because a
    #: limit nobody asked for is a policy, not a safety net. The signup default
    #: can be set by the operator (``QUALI_DEFAULT_CHARACTER_CEILING``) and every
    #: workspace can change its own from the Usage page.
    character_ceiling: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    #: Slice G. Requests one API key of this workspace may make per fixed
    #: one-minute window. ``0`` means no rate limit.
    requests_per_minute: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ApiKey(Base):
    """A customer's key. The digest is what we store; the key itself is not."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    key_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    key_digest: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SessionToken(Base):
    """A dashboard login. Expires, and can be revoked."""

    __tablename__ = "session_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UsageEvent(Base):
    """One metered request.

    ``characters`` is attempt-based: the text we asked the engine to speak, for
    every request that got past input validation. ``audio_seconds`` is
    delivery-based: measured from the audio bytes the engine actually returned,
    so a failed call adds a failure and no minutes.

    ``transcript_characters`` is the third counter, added by slice C, and it is
    deliberately not the first one. Speech out and speech in are different
    products; a workspace that transcribes 40,000 characters of its own
    recordings has not asked us to speak 40,000 characters, and a single blended
    number would let a customer's own invoice read as if it had.
    """

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    route: Mapped[str] = mapped_column(String(80))
    status_code: Mapped[int] = mapped_column(Integer)
    characters: Mapped[int] = mapped_column(Integer, default=0)
    #: Added by m2c1. ``server_default="0"`` on purpose: an existing row has no
    #: transcript, and a migration that left the column NULL would make every
    #: older request indistinguishable from a request we failed to measure.
    transcript_characters: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    audio_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engine_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class QuotaPeriod(Base):
    """Slice G: the reservation counter for one workspace and one UTC month.

    Deliberately *not* a second ledger. The ledger (``usage_events``) stays the
    only place a character is charged; this row exists to close the race between
    reading spend and acting on it, and it is released in the same transaction as
    the usage event that replaces it. A workspace with no ceiling never gets a
    row.

    ``UniqueConstraint(workspace_id, period_start)`` is what makes the
    compare-and-swap safe: there is exactly one counter per workspace per month,
    so the conditional UPDATE either takes the characters or refuses them.

    Both halves of the month live *in this one row*, which is the point.
    ``committed_characters`` is a projection of the ledger — every character
    charged lands here in the same transaction as its usage event — and
    ``reserved_characters`` is what requests in flight have taken. The gate is
    ``committed + reserved + n <= ceiling``, evaluated inside one conditional
    UPDATE, so no request can ever compare against a settled figure that a
    concurrent settle has already moved: a first attempt that read the ledger and
    then acted on it had exactly that hole, and a thread-race test in
    ``tests/hosted/test_hosted_limits.py`` is what made it visible.
    """

    __tablename__ = "quota_periods"
    __table_args__ = (
        UniqueConstraint("workspace_id", "period_start", name="uq_quota_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: A projection of ``usage_events.characters`` for this period, maintained only
    #: by ``limits.settle`` (which writes it beside the event that justifies it) and
    #: seeded from the ledger when the row is first created, so an upgraded
    #: database's existing spend is counted rather than forgotten.
    committed_characters: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    reserved_characters: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )


class RateWindow(Base):
    """Slice G: how many requests one key made in one fixed minute.

    One row per key per minute, so the counter is a conditional UPDATE and not a
    scan of every request ever made. Nothing prunes these rows yet, and that is
    stated rather than hidden: a key making a request a minute leaves 1,440 rows
    a day, and a sweep can be added when a real deployment makes the size matter.
    """

    __tablename__ = "rate_windows"
    __table_args__ = (
        UniqueConstraint("api_key_id", "window_start", name="uq_rate_window"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    api_key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), index=True
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class SchemaMeta(Base):
    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(80))


class Job(Base):
    """One unit of queued speech.

    A job exists because a real request does not fit in one HTTP call: a
    paragraph of Hindi takes minutes on CPU, the engine can be down, and the
    caller needs a record of what was asked for and what came out. The row is
    the queue entry, the lease and the receipt at once.

    ``attempts`` counts the tries this plane made, not the orders the customer
    placed: a retry after our own 503 is one job. ``wasted_engine_seconds`` is
    the time burned on attempts that were thrown away, kept apart from the
    ``engine_seconds`` of the attempt whose audio was actually delivered, so
    nobody reads a blended number as work we did for the customer.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    input_text: Mapped[str] = mapped_column(Text)
    voice: Mapped[str] = mapped_column(String(120), default="")
    response_format: Mapped[str] = mapped_column(String(16), default="wav")
    characters: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    worker_id: Mapped[str] = mapped_column(String(64), default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(String(200), default="")
    object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engine_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    wasted_engine_seconds: Mapped[float] = mapped_column(Float, default=0.0)

    @property
    def terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "cancelled")


class Project(Base):
    """A manuscript, and the settings every one of its chapters is spoken with.

    A project owns no audio of its own: the chapters are ordinary ``Job`` rows, and
    the audiobook is assembled from what those jobs stored. That is deliberate —
    the queue's claim, lease and retry rules are the hard part of long-form work,
    and a second copy of them here would be a second set of bugs. What the project
    adds is the order of the chapters and the fact that they were one manuscript.
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(120), default="")
    voice: Mapped[str] = mapped_column(String(120), default="")
    response_format: Mapped[str] = mapped_column(String(16), default="wav")
    source_characters: Mapped[int] = mapped_column(Integer, default=0)
    chapter_count: Mapped[int] = mapped_column(Integer, default=0)
    #: The ceiling the splitter was given, kept so a reader can tell why a
    #: manuscript became this many chapters rather than guessing.
    chapter_max_chars: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ProjectChapter(Base):
    """One chapter: a position in a project, and the job that speaks it.

    The chapter's text lives on the job (``Job.input_text``), not here. Two copies
    of the same paragraph could disagree, and the one the engine was actually asked
    to speak is the job's.
    """

    __tablename__ = "project_chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    #: Zero-based, and the order the audiobook is assembled in.
    chapter_index: Mapped[int] = mapped_column(Integer)
    characters: Mapped[int] = mapped_column(Integer, default=0)


class Dub(Base):
    """One recording heard, and the same words said back in another voice.

    A dub is the only receipt in this database that two engine calls produced.
    Both are kept apart rather than blended: ``transcribe_seconds`` is what the
    ear cost and ``speech_seconds`` is what the mouth cost, because a customer
    reading "3.1 s of engine time" cannot tell which half to optimise. The
    transcript that was actually spoken is stored here, not re-derived later —
    the page shows what the file says, and the file is not kept as text.

    ``object_key`` points at the dubbed WAV in the object store; when a dub
    succeeds it is always set, and a dub is only written after the speech call
    delivered bytes, so there is no half-done row that reads like a success.
    """

    __tablename__ = "dubs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    voice: Mapped[str] = mapped_column(String(120), default="")
    response_format: Mapped[str] = mapped_column(String(16), default="wav")
    #: The recording as it arrived, described by its own bytes.
    source_bytes: Mapped[int] = mapped_column(Integer, default=0)
    source_sha256: Mapped[str] = mapped_column(String(64), default="")
    #: What the engine heard, unedited, and the same string's length.
    transcript: Mapped[str] = mapped_column(Text, default="")
    transcript_characters: Mapped[int] = mapped_column(Integer, default=0)
    spoken_characters: Mapped[int] = mapped_column(Integer, default=0)
    object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcribe_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    speech_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Database:
    """Engine + session factory, owned by the app and closed on shutdown."""

    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        connect_args = (
            # ``timeout`` is the sqlite3 busy timeout: two worker processes on
            # one database file is the normal case now (the plane serves, the
            # worker drains), and the default of five seconds turns a busy
            # moment into "database is locked".
            {"check_same_thread": False, "timeout": 30.0}
            if url.startswith("sqlite")
            else {}
        )
        self.engine: Engine = create_engine(
            url, echo=echo, future=True, connect_args=connect_args
        )
        if url.startswith("sqlite"):
            # SQLite ignores ON DELETE unless it is asked to enforce it, and the
            # cascade above is only real if this runs on every connection. WAL
            # is what makes a reader (the plane answering GET /v1/jobs) not
            # block behind the writer (the worker claiming a job).
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self.session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    def init_schema(self, version: str) -> str:
        """Create the schema, or bring an older one forward. See ``migrations``."""
        from .migrations import migrate  # local import: keeps this module leaf-level

        return migrate(self, expected_version=version)

    def schema_version(self) -> str | None:
        with self.session() as session:
            row = session.execute(
                select(SchemaMeta).where(SchemaMeta.key == "schema_version")
            ).scalar_one_or_none()
            return row.value if row else None

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Commit on success, roll back on any exception, always close."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def state_dir() -> Path:
    return Path(os.environ.get("QUALI_STATE_DIR", "") or Path.home() / ".qualiabits-voice")