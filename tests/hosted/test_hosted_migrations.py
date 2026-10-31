"""Bringing an older control-plane database forward.

The DDL in ``slice_a_ddl`` is slice A's schema, copied here on purpose rather than
generated from the current models. A migration test that builds its "old" database
out of today's models tests nothing: the moment the models move, the fixture moves
with them and the test keeps passing while real slice-A databases stop upgrading.

The last two tests open a slice-A database through the real app, so what is checked
is not that a function returns a string but that a customer who signed up before the
change can still log in and use their ledger after it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect

from conftest import PASSWORD, login, usage
from hosted.control_plane import jobs, security
from hosted.control_plane.config import SCHEMA_VERSION, Settings
from hosted.control_plane.db import Database, Job, Workspace
from hosted.control_plane.migrations import KNOWN_VERSIONS, MIGRATIONS, migrate

#: Slice A's tables, exactly as that build wrote them (schema version ``m2a1``).
#: No ``jobs`` table: that is what slice B adds.
SLICE_A_DDL = """
CREATE TABLE workspaces (
    id INTEGER NOT NULL PRIMARY KEY,
    email VARCHAR(320) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    password_salt VARCHAR(64) NOT NULL,
    password_hash VARCHAR(128) NOT NULL,
    password_params VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE UNIQUE INDEX ix_workspaces_email ON workspaces (email);
CREATE TABLE api_keys (
    id INTEGER NOT NULL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    key_id VARCHAR(32) NOT NULL,
    key_digest VARCHAR(64) NOT NULL,
    label VARCHAR(120) NOT NULL,
    created_at DATETIME NOT NULL,
    last_used_at DATETIME,
    revoked_at DATETIME
);
CREATE UNIQUE INDEX ix_api_keys_key_id ON api_keys (key_id);
CREATE TABLE session_tokens (
    id INTEGER NOT NULL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    token_digest VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME
);
CREATE UNIQUE INDEX ix_session_tokens_token_digest ON session_tokens (token_digest);
CREATE TABLE usage_events (
    id INTEGER NOT NULL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    api_key_id INTEGER REFERENCES api_keys (id) ON DELETE SET NULL,
    route VARCHAR(80) NOT NULL,
    status_code INTEGER NOT NULL,
    characters INTEGER NOT NULL,
    audio_seconds FLOAT,
    audio_bytes INTEGER,
    engine_seconds FLOAT,
    error VARCHAR(200) NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE schema_meta (
    key VARCHAR(40) NOT NULL PRIMARY KEY,
    value VARCHAR(80) NOT NULL
);
INSERT INTO schema_meta (key, value) VALUES ('schema_version', 'm2a1');
"""


def write_slice_a_database(path: Path, *, email: str = "old@example.com") -> None:
    """A real slice-A database with one customer, one key and one metered request."""
    salt, digest, params = security.hash_password(PASSWORD)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SLICE_A_DDL)
        connection.execute(
            "INSERT INTO workspaces (email, display_name, password_salt, password_hash, "
            "password_params, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (email, "Old Customer", salt, digest, params, "2026-10-31 06:00:00.000000"),
        )
        connection.execute(
            "INSERT INTO api_keys (workspace_id, key_id, key_digest, label, created_at) "
            "VALUES (1, 'oldsessionkey', ?, 'laptop', '2026-10-31 06:01:00.000000')",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO usage_events (workspace_id, api_key_id, route, status_code, "
            "characters, audio_seconds, audio_bytes, engine_seconds, error, created_at) "
            "VALUES (1, 1, '/v1/audio/speech', 200, 42, 1.5, 70000, 3.0, '', "
            "'2026-10-31 06:02:00.000000')"
        )
        connection.commit()
    finally:
        connection.close()


def test_a_fresh_database_is_created_at_the_current_version(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'fresh.db'}")
    assert migrate(db, expected_version=SCHEMA_VERSION) == "created"
    assert db.schema_version() == SCHEMA_VERSION == "m2b1"
    assert inspect(db.engine).has_table("jobs")


def test_a_slice_a_database_is_brought_forward_and_keeps_every_row(tmp_path):
    path = tmp_path / "old.db"
    write_slice_a_database(path)
    db = Database(f"sqlite:///{path}")

    assert db.schema_version() == "m2a1"
    assert migrate(db, expected_version=SCHEMA_VERSION) == "upgraded:m2a1->m2b1"
    assert db.schema_version() == "m2b1"
    assert inspect(db.engine).has_table("jobs")

    with db.session() as session:
        workspace = session.query(Workspace).one()
        assert workspace.email == "old@example.com"
        assert workspace.display_name == "Old Customer"
        assert security.verify_password(
            PASSWORD, workspace.password_salt, workspace.password_hash, workspace.password_params
        ), "the password stored by the old build still verifies"
        # The new table is usable on the old database.
        job = jobs.enqueue(
            session,
            workspace_id=workspace.id,
            api_key_id=1,
            text="नया काम पुराने डेटाबेस पर",
            max_attempts=3,
        )
        session.commit()
        assert session.get(Job, job.id).status == "queued"


def test_upgrading_twice_changes_nothing_the_second_time(tmp_path):
    path = tmp_path / "twice.db"
    write_slice_a_database(path)
    db = Database(f"sqlite:///{path}")

    assert migrate(db, expected_version=SCHEMA_VERSION) == "upgraded:m2a1->m2b1"
    assert migrate(db, expected_version=SCHEMA_VERSION) == "current"
    assert migrate(db, expected_version=SCHEMA_VERSION) == "current"
    with db.session() as session:
        assert session.query(Workspace).count() == 1


def test_a_step_can_be_run_again_without_failing(tmp_path):
    """What makes a crash between the DDL and the stamp survivable."""
    db = Database(f"sqlite:///{tmp_path / 'again.db'}")
    migrate(db, expected_version=SCHEMA_VERSION)
    step = dict(MIGRATIONS)["m2b1"]

    with db.engine.begin() as connection:
        step(connection)  # the tables are already there
    with db.engine.begin() as connection:
        step(connection)
    assert inspect(db.engine).has_table("jobs")


def test_a_database_from_an_unknown_schema_is_refused_with_both_versions_named(tmp_path):
    path = tmp_path / "alien.db"
    write_slice_a_database(path)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE schema_meta SET value = 'm9z9'")
    connection.commit()
    connection.close()

    db = Database(f"sqlite:///{path}")
    with pytest.raises(RuntimeError) as error:
        migrate(db, expected_version=SCHEMA_VERSION)
    message = str(error.value)
    assert "m9z9" in message and SCHEMA_VERSION in message
    assert "refusing to guess" in message


def test_a_newer_database_is_refused_by_an_older_build(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'newer.db'}")
    migrate(db, expected_version="m2b1")

    with pytest.raises(RuntimeError) as error:
        migrate(db, expected_version="m2a1")
    assert "newer than this build" in str(error.value)


def test_a_build_claiming_a_version_nobody_knows_is_refused(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'unknown.db'}")
    with pytest.raises(RuntimeError) as error:
        migrate(db, expected_version="m2z9")
    assert "not in" in str(error.value)
    assert "m2z9" in str(error.value)


def test_the_known_versions_are_in_the_order_the_steps_are_applied():
    assert KNOWN_VERSIONS[-1] == SCHEMA_VERSION
    produced = [version for version, _ in MIGRATIONS]
    assert produced == list(KNOWN_VERSIONS[1:]), (
        "every version after the first must be produced by exactly one step, in order"
    )


# ── through the real app ──────────────────────────────────────────────────────


def test_the_app_upgrades_a_slice_a_database_in_place_and_the_customer_still_logs_in(
    tmp_path, engine
):
    from conftest import app_client

    write_slice_a_database(tmp_path / "control-plane.db")
    with app_client(tmp_path, engine) as client:
        health = client.get("/healthz").json()
        assert health["schema_state"] == "upgraded:m2a1->m2b1"
        assert health["schema_version"] == SCHEMA_VERSION

        # The customer who signed up under slice A signs in with the same password.
        token = login(client, "old@example.com")
        ledger = usage(client, token)
        assert ledger["requests"] == 1
        assert ledger["characters"] == 42
        assert ledger["audio_seconds"] == 1.5
        assert ledger["by_key"][0]["label"] == "laptop"

        # And the new feature works on the old database.
        queued = client.post(
            "/v1/jobs",
            json={"input": "पुराने ग्राहक का नया काम", "voice": "hf_alpha"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert queued.status_code == 202
        assert client.get("/healthz").json()["jobs"] == {"queued": 1}


def test_the_health_route_says_created_for_a_fresh_database(client):
    health = client.get("/healthz").json()
    assert health["schema_state"] == "created"
    assert health["schema_version"] == SCHEMA_VERSION


def test_an_unknown_schema_stops_the_app_from_starting(tmp_path, engine):
    """Refusing to start beats running queries against a schema we do not know."""
    from conftest import app_client

    write_slice_a_database(tmp_path / "control-plane.db")
    connection = sqlite3.connect(tmp_path / "control-plane.db")
    connection.execute("UPDATE schema_meta SET value = 'm5x5'")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError) as error:
        with app_client(tmp_path, engine):
            pass
    assert "m5x5" in str(error.value)


def test_settings_defaults_point_at_a_real_storage_directory(tmp_path):
    """A plane started with no configuration at all still has somewhere to write."""
    import os

    previous = {name: os.environ.get(name) for name in ("QUALI_STATE_DIR", "QUALI_STORAGE_DIR")}
    try:
        os.environ["QUALI_STATE_DIR"] = str(tmp_path / "state")
        os.environ.pop("QUALI_STORAGE_DIR", None)
        settings = Settings.from_env()
        assert settings.storage_backend == "local"
        assert settings.storage_dir == str(tmp_path / "state" / "objects")
        assert settings.job_max_attempts == 3
        assert settings.job_lease_seconds == 900
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value