"""Accounts, sessions and API keys on the hosted control plane.

These are the tests that would fail if authentication broke: every one of them
either mints a real credential through the real route or presents a credential
that must be refused.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time

from conftest import PASSWORD, mint_key, signup, login, usage


def test_signup_creates_one_workspace_per_email(client):
    first = signup(client, "buyer@example.com")
    assert first["email"] == "buyer@example.com"
    assert first["id"] > 0

    duplicate = client.post(
        "/v1/workspaces", json={"email": "BUYER@example.com ", "password": PASSWORD}
    )
    assert duplicate.status_code == 409

    other = signup(client, "other@example.com")
    assert other["id"] != first["id"]


def test_signup_refuses_a_bad_email_and_a_short_password(client):
    bad_email = client.post(
        "/v1/workspaces", json={"email": "not-an-email", "password": PASSWORD}
    )
    # Our own field check says 400. A password below the schema minimum is
    # refused by field validation before the route runs, and that is 422.
    assert bad_email.status_code == 400
    short = client.post(
        "/v1/workspaces", json={"email": "buyer@example.com", "password": "short"}
    )
    assert short.status_code == 422
    # Nothing was created by either refusal.
    assert client.post(
        "/v1/auth/token", json={"email": "buyer@example.com", "password": PASSWORD}
    ).status_code == 401


def test_a_key_works_even_when_its_secret_contains_an_underscore(client):
    """The key alphabet includes ``_``; parsing must not care.

    This is the regression test for a real bug: the key was split on every
    underscore, so any key whose secret happened to contain one was refused as
    malformed. Minting until one appears is deterministic enough (about half the
    keys qualify) and needs no seeding.
    """
    signup(client)
    token = login(client)
    keys = [mint_key(client, token, f"key {i}") for i in range(20)]
    assert any("_" in key.split("_", 2)[2] for key in keys), "test found no underscore"
    for key in keys:
        assert client.post(
            "/v1/audio/speech", json={"input": "hello"},
            headers={"Authorization": f"Bearer {key}"},
        ).status_code == 200, key
    assert usage(client, keys[0])["requests"] == 20


def test_login_issues_a_session_that_the_other_routes_accept(client):
    signup(client)
    token = login(client)
    listed = client.get("/v1/api-keys", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert listed.json()["api_keys"] == []


def test_login_refuses_wrong_password_unknown_email_and_junk_tokens(client):
    signup(client)
    assert client.post(
        "/v1/auth/token", json={"email": "buyer@example.com", "password": "wrong horse"}
    ).status_code == 401
    assert client.post(
        "/v1/auth/token", json={"email": "nobody@example.com", "password": PASSWORD}
    ).status_code == 401
    for header in ("Bearer nonsense", "Basic abc", "Bearer"):
        assert client.get("/v1/api-keys", headers={"Authorization": header}).status_code == 401
    assert client.get("/v1/api-keys").status_code == 401


def test_a_session_expires(tmp_path, engine):
    from conftest import app_client

    with app_client(tmp_path, engine, name="short-ttl.db", ttl=1) as short:
        signup(short)
        token = login(short)
        assert short.get(
            "/v1/api-keys", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200
        time.sleep(1.2)
        expired = short.get(
            "/v1/api-keys", headers={"Authorization": f"Bearer {token}"}
        )
        assert expired.status_code == 401
        assert "expired" in expired.json()["detail"]


def test_a_key_is_returned_once_and_only_a_digest_is_stored(client, tmp_path):
    signup(client)
    token = login(client)
    created = client.post(
        "/v1/api-keys",
        json={"label": "phone test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201
    record = created.json()["api_key_record"]
    key = record["api_key"]
    assert key.startswith("qbv_") and record["key_id"] in key
    assert "only time" in record["shown_once"]

    listed = client.get("/v1/api-keys", headers={"Authorization": f"Bearer {token}"}).json()
    assert [row["key_id"] for row in listed["api_keys"]] == [record["key_id"]]
    assert "api_key" not in listed["api_keys"][0]

    # The database on disk, not the response: the key must not be in there.
    raw = (tmp_path / "control-plane.db").read_bytes()
    assert key.encode() not in raw
    assert PASSWORD.encode() not in raw
    con = sqlite3.connect(tmp_path / "control-plane.db")
    try:
        stored = con.execute("select key_digest from api_keys").fetchall()
        salt, digest = con.execute(
            "select password_salt, password_hash from workspaces"
        ).fetchone()
    finally:
        con.close()
    assert stored == [(hashlib.sha256(key.encode()).hexdigest(),)]
    assert salt and len(salt) == 32 and digest and len(digest) == 64


def test_speech_refuses_every_kind_of_bad_key(client, engine):
    signup(client)
    session_token = login(client)
    responses = {
        "missing": client.post("/v1/audio/speech", json={"input": "hi"}),
        "malformed": client.post(
            "/v1/audio/speech", json={"input": "hi"},
            headers={"Authorization": "Bearer qbv_not-a-key"},
        ),
        "unknown_but_shaped": client.post(
            "/v1/audio/speech", json={"input": "hi"},
            headers={"Authorization": f"Bearer qbv_{'a' * 16}_{'b' * 40}"},
        ),
        "session_token": client.post(
            "/v1/audio/speech", json={"input": "hi"},
            headers={"Authorization": f"Bearer {session_token}"},
        ),
    }
    for name, response in responses.items():
        assert response.status_code == 401, name
    assert engine.payloads == [], "a refused request must never reach the engine"


def test_a_session_cannot_be_used_where_a_key_is_needed_and_the_reverse(client):
    signup(client)
    token = login(client)
    key = mint_key(client, token)
    assert client.post(
        "/v1/api-keys", json={"label": "nope"}, headers={"Authorization": f"Bearer {key}"}
    ).status_code == 401


def test_revoking_a_key_stops_it(client):
    """The revoked state must be visible to the next request, with no sleep.

    This is the assertion the end-to-end run failed: the DELETE answered 200 and
    the next request was still accepted, because the write was committed on
    dependency teardown rather than inside the request.
    """
    signup(client)
    token = login(client)
    created = client.post(
        "/v1/api-keys", json={"label": "temp"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["api_key_record"]
    key = created["api_key"]
    assert usage(client, key)["requests"] == 0

    revoked = client.delete(
        f"/v1/api-keys/{created['id']}", headers={"Authorization": f"Bearer {token}"}
    )
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True
    assert client.get(
        "/v1/usage", headers={"Authorization": f"Bearer {key}"}
    ).status_code == 401
    second = client.delete(
        f"/v1/api-keys/{created['id']}", headers={"Authorization": f"Bearer {token}"}
    )
    assert second.status_code == 200  # revoking twice is not an error, it is done


def test_one_workspace_cannot_revoke_the_other_workspace_key(client):
    signup(client, "a@example.com")
    signup(client, "b@example.com")
    a_token, b_token = login(client, "a@example.com"), login(client, "b@example.com")
    a_key_record = client.post(
        "/v1/api-keys", json={"label": "a"}, headers={"Authorization": f"Bearer {a_token}"}
    ).json()["api_key_record"]

    forbidden = client.delete(
        f"/v1/api-keys/{a_key_record['id']}",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert forbidden.status_code == 404

    b_list = client.get(
        "/v1/api-keys", headers={"Authorization": f"Bearer {b_token}"}
    ).json()
    assert b_list["api_keys"] == []
    # A's key still works, so B's attempt changed nothing.
    assert usage(client, a_key_record["api_key"])["requests"] == 0


def test_health_routes_need_no_credential(client):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["schema_version"] == "m2a1"

    upstream = client.get("/healthz/upstream")
    assert upstream.status_code == 200
    assert upstream.json()["reachable"] is True
    assert upstream.json()["device"] == "cpu"