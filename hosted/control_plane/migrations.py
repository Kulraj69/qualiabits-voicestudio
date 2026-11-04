"""Bringing the control plane's own database forward, one ordered step at a time.

Slice A refused to start on a database stamped with anything but its own version,
because it had no migration path. That was the right refusal then and the wrong
answer now: slice B adds the queue table, so every slice-A database in existence
(old local runs, and any customer the moment this ships) would have been refused
instead of upgraded. This module is that migration path.

How it works, and why it is this small. The plane owns exactly one schema, applied
linearly; there is no branching and no second consumer, which is the shape
``alembic`` is built for but not the shape we need yet. So the versions live in a
list, each step is a function that receives one transaction, and a database is
stamped with the last version it has actually been brought to, inside the same
transaction as that step's DDL. A step interrupted half way therefore leaves the
stamp alone and the next start retries it, which is why every step must be
idempotent (``checkfirst=True`` and friends, never ``DROP``). When the schema
stops being linear — a step that needs data from a table a later step drops, or
two people shipping steps at once — that is the moment to switch to alembic and
turn these functions into a baseline revision; doing it before then would be
paying for machinery this code does not use.

``KNOWN_VERSIONS`` is deliberately exhaustive. A database stamped with something
that is not in that list is not "probably fine": it is somebody else's schema, and
the honest answer stays a refusal that names both versions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .db import Base, Database, Dub, Job, Project, ProjectChapter

#: Oldest first. A database is stamped with the last entry it has been brought to.
#: m2a1 — slice A (workspaces, api_keys, session_tokens, usage_events).
#: m2b1 — slice B (adds the jobs queue).
#: m2c1 — slice C (adds usage_events.transcript_characters).
#: m2d1 — slice D (adds projects and project_chapters).
#: m2f1 — slice F (adds dubs). Slice E added no table, so it has no step here.
KNOWN_VERSIONS: tuple[str, ...] = ("m2a1", "m2b1", "m2c1", "m2d1", "m2f1")

MIGRATION_VERSION = KNOWN_VERSIONS[-1]


def _ensure_directory(url: str) -> None:
    # The directory is made here, not on import, so importing the app
    # (``uvicorn hosted.control_plane.app:app`` resolves the module before the
    # lifespan runs) still writes nothing to disk.
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)


def _stamp(connection: Connection, version: str) -> None:
    connection.execute(
        text("UPDATE schema_meta SET value = :v WHERE key = 'schema_version'"),
        {"v": version},
    )


# ── the steps ─────────────────────────────────────────────────────────────────


def _add_jobs_table(connection: Connection) -> None:
    """m2b1: the queue.

    ``CREATE TABLE IF NOT EXISTS`` semantics via ``checkfirst``: a start that
    crashed after the DDL but before the stamp re-runs this and changes nothing.
    """
    Job.__table__.create(connection, checkfirst=True)


#: The column m2c1 adds, with the table it belongs to.
_TRANSCRIPT_CHARACTERS = ("usage_events", "transcript_characters")


def _add_transcript_characters(connection: Connection) -> None:
    """m2c1: a third counter on the ledger, for speech that came *in*.

    SQLite can add a NOT NULL column with a constant default in place, so the
    existing rows keep their meaning (zero transcribed characters) and no data
    is rewritten. ``ADD COLUMN`` has no ``IF NOT EXISTS`` in the SQL SQLite and
    older PostgreSQL both accept, so idempotence comes from asking the schema
    first — the same "re-run changes nothing" property the step above gets from
    ``checkfirst``. Nothing here drops or recreates a table: a crash between the
    DDL and the stamp must leave the ledger exactly where it was.
    """
    table, column = _TRANSCRIPT_CHARACTERS
    present = {col["name"] for col in inspect(connection).get_columns(table)}
    if column in present:
        return
    connection.execute(
        text(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
        )
    )


def _add_projects(connection: Connection) -> None:
    """m2d1: long-form work — a manuscript and the chapters it was split into.

    Two new tables, no change to anything that already exists: the chapters are
    ordinary jobs, so this step adds no column to ``jobs`` and an older plane's
    rows keep their meaning untouched. ``checkfirst=True`` gives the same
    "re-running changes nothing" property the two steps above have.
    """
    Project.__table__.create(connection, checkfirst=True)
    ProjectChapter.__table__.create(connection, checkfirst=True)


def _add_dubs(connection: Connection) -> None:
    """m2f1: dubbing — a recording, and the same words in another voice.

    One new table and no change to anything that already exists. A dub writes
    two ordinary usage events (a transcription and a speech request), so the
    ledger grows no new column, exactly as the projects step added none.
    ``checkfirst=True`` gives the same "re-running changes nothing" property the
    steps above have.
    """
    Dub.__table__.create(connection, checkfirst=True)


#: (version this step produces, work to do). Order matters; the stamp is written
#: in the same transaction as the work.
MIGRATIONS: tuple[tuple[str, Callable[[Connection], None]], ...] = (
    ("m2b1", _add_jobs_table),
    ("m2c1", _add_transcript_characters),
    ("m2d1", _add_projects),
    ("m2f1", _add_dubs),
)


def _order(version: str) -> int:
    return KNOWN_VERSIONS.index(version)


def migrate(db: Database, *, expected_version: str) -> str:
    """Create a fresh schema, or bring an older one forward.

    Returns what happened — ``"created"``, ``"current"`` or ``"upgraded:m2a1->m2b1"``
    — so the plane can say it in ``/healthz`` and the tests can assert on it
    instead of on a side effect.
    """
    _ensure_directory(db.url)
    if expected_version not in KNOWN_VERSIONS:
        raise RuntimeError(
            f"this build claims schema {expected_version!r}, which is not in "
            f"KNOWN_VERSIONS {KNOWN_VERSIONS!r}; add it to migrations.py before shipping"
        )
    present = (
        db.schema_version() if inspect(db.engine).has_table("schema_meta") else None
    )
    if present is None:
        Base.metadata.create_all(db.engine)
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO schema_meta (key, value) "
                    "VALUES ('schema_version', :v)"
                ),
                {"v": expected_version},
            )
        return "created"
    if present not in KNOWN_VERSIONS:
        raise RuntimeError(
            f"control-plane database holds schema {present!r}, and this build "
            f"knows {KNOWN_VERSIONS!r}; refusing to guess what that schema is. "
            "Point QUALI_DATABASE_URL at the right database, or write the "
            "migration that brings the old one forward."
        )
    if _order(present) > _order(expected_version):
        # An older build against a newer database: refuse rather than run
        # queries against columns it does not know about.
        raise RuntimeError(
            f"control-plane database holds schema {present!r}, newer than this "
            f"build's {expected_version!r}; run the newer build against it instead"
        )
    applied: list[str] = []
    for target, step in MIGRATIONS:
        if _order(target) <= _order(present):
            continue
        if _order(target) > _order(expected_version):
            # A step this build does not claim to know. Stopping here keeps the
            # stamp honest: without it, a build that says it is ``m2c1`` would apply
            # the ``m2d1`` step too and then stamp the database ``m2c1``, leaving a
            # schema that has tables the stamp denies and the next start of the
            # newer build with nothing to do about them.
            break
        with db.engine.begin() as connection:
            step(connection)
            _stamp(connection, target)
        applied.append(target)
    if not applied:
        return "current"
    return f"upgraded:{present}->{applied[-1]}"