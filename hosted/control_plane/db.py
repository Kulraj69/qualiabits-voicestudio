"""The control plane's own database: workspaces, keys, sessions, usage.

This database is separate from the engine's. Nothing in ``backend/`` opens it and
nothing here opens the engine's, which is why an engine upgrade cannot change how
a customer authenticates.

SQLite by default (a single box, one control plane), any SQLAlchemy URL via
``QUALI_DATABASE_URL`` otherwise. The schema is created on startup with
``create_all`` and its version is written into ``schema_meta``; there is no
migration tool for the control plane yet. That is fine while the schema is a
week old and nothing is deployed — it stops being fine the moment a real
customer's data lives in it, and slice B carries the alembic setup.
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
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    """Aware UTC. Stored naive-UTC by SQLite, read back as UTC here."""
    return datetime.now(timezone.utc)


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
    audio_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engine_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class SchemaMeta(Base):
    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(80))


class Database:
    """Engine + session factory, owned by the app and closed on shutdown."""

    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            url, echo=echo, future=True, connect_args=connect_args
        )
        if url.startswith("sqlite"):
            # SQLite ignores ON DELETE unless it is asked to enforce it, and the
            # cascade above is only real if this runs on every connection.
            event.listen(
                self.engine,
                "connect",
                lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"),
            )
        self.session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    def init_schema(self, version: str) -> None:
        # The directory is made here rather than in __init__ so that importing the
        # app (``uvicorn hosted.control_plane.app:app`` resolves the module before
        # the lifespan runs) writes nothing to disk.
        if self.url.startswith("sqlite:///"):
            Path(self.url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self.engine)
        with self.session() as session:
            row = session.get(SchemaMeta, "schema_version")
            if row is None:
                session.add(SchemaMeta(key="schema_version", value=version))
            elif row.value != version:
                # Refusing is the honest move: this plane has no migrations, so a
                # version mismatch means somebody else's schema is underneath.
                raise RuntimeError(
                    f"control-plane database holds schema {row.value!r}, "
                    f"this build expects {version!r}; there is no migration path yet"
                )

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