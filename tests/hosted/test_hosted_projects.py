"""Long-form work through the real plane: a manuscript in, one audiobook out.

The splitter and the assembler are tested on their own next door. What is checked
here is the whole path: the manuscript becomes ordinary queued jobs, the real worker
speaks each one and stores it, the assembled file is what those chapters add up to,
and the meter counts chapters rather than projects. The only substitution is the
engine, and it is the same stub the rest of the suite uses.
"""
from __future__ import annotations

import hashlib
import io
import struct
import wave

import pytest

from conftest import (
    PASSWORD,
    app_client,
    drain,
    login,
    mint_key,
    signup,
    usage,
    worker_for,
)
from hosted.control_plane import projects
from hosted.control_plane.db import Job

AUTHORIZATION = "Authorization"


def create_project(client, credential: str, input: str, **extra):
    body = {"input": input, "voice": "hf_alpha", "response_format": "wav", **extra}
    return client.post(
        "/v1/projects", json=body, headers={AUTHORIZATION: f"Bearer {credential}"}
    )


def project(client, credential: str, project_id: int):
    return client.get(
        f"/v1/projects/{project_id}", headers={AUTHORIZATION: f"Bearer {credential}"}
    )


def project_audio(client, credential: str, project_id: int):
    return client.get(
        f"/v1/projects/{project_id}/audio",
        headers={AUTHORIZATION: f"Bearer {credential}"},
    )


def long_paragraph(marker: str, *, repeats: int = 40) -> str:
    """A paragraph of roughly 1,100 characters: two of them cannot share a chapter."""
    return marker + " " + " ".join(["यह वाक्य इस अध्याय का है।"] * repeats)


#: Three chapters' worth, written the way a manuscript is written: paragraphs of
#: their own, separated by blank lines. Each is past half a chapter, so the split
#: lands on the paragraph boundaries rather than somewhere inside a sentence.
MANUSCRIPT = "\n\n".join(
    [
        long_paragraph("पहला पैराग्राफ:"),
        long_paragraph("दूसरा पैराग्राफ:"),
        long_paragraph("तीसरा पैराग्राफ:"),
    ]
)


def test_a_manuscript_becomes_one_queued_job_per_chapter(client):
    token = login(client, signup(client)["email"])
    response = create_project(client, token, MANUSCRIPT, title="किताब")
    assert response.status_code == 202, response.text
    body = response.json()
    chapters = body["project"]["chapters"]
    assert body["project"]["title"] == "किताब"
    assert body["project"]["status"] == "queued"
    assert body["project"]["chapter_count"] == len(chapters) == 3
    assert [chapter["position"] for chapter in chapters] == [1, 2, 3]
    assert all(chapter["status"] == "queued" for chapter in chapters)
    assert len({chapter["job_id"] for chapter in chapters}) == 3, "one job per chapter"
    # The chapters are the manuscript's paragraphs, not a copy of the whole thing.
    assert "पहला पैराग्राफ" in chapters[0]["preview"]
    assert "तीसरा पैराग्राफ" in chapters[2]["preview"]
    assert sum(chapter["characters"] for chapter in chapters) == sum(
        len(chapter_text)
        for chapter_text in projects.split_chapters(MANUSCRIPT)
    )
    # And the queue agrees: three jobs are waiting, and nothing has been spoken.
    assert client.get("/healthz").json()["jobs"] == {"queued": 3}
    assert usage(client, token)["requests"] == 0


def test_the_projects_own_ceiling_is_refused_with_the_number(client):
    token = login(client, signup(client)["email"])
    response = create_project(client, token, "शब्द " * projects.PROJECT_MAX_CHARS)
    assert response.status_code == 413, response.text
    assert str(projects.PROJECT_MAX_CHARS) in response.json()["detail"]


def test_an_empty_manuscript_is_refused(client):
    token = login(client, signup(client)["email"])
    response = create_project(client, token, "   \n  ")
    assert response.status_code == 400
    assert "non-empty" in response.json()["detail"]


def test_a_splitter_refusal_reaches_the_caller_in_the_splitters_own_words(client, monkeypatch):
    """The route does not rewrite the splitter's sentence or swallow it.

    Forced here rather than written out as a 200,000-character fixture: the
    splitter's own refusals are tested next door, and what this covers is the route
    turning one into a 400 that carries the sentence.
    """
    token = login(client, signup(client)["email"])

    def refuse(_text, **_kwargs):
        raise projects.SplitError("a single word is 9000 characters long")

    monkeypatch.setattr(projects, "split_chapters", refuse)
    response = create_project(client, token, "कुछ भी")
    assert response.status_code == 400
    assert response.json()["detail"] == "a single word is 9000 characters long"
    # Nothing was queued by the failed request.
    assert client.get("/healthz").json().get("jobs", {}) == {}


def test_a_long_manuscript_splits_over_the_api_and_every_chapter_fits_the_queue(client):
    token = login(client, signup(client)["email"])
    paragraphs = ["यह वाक्य इस अध्याय में दोहराया गया है। " * 40] * 25
    response = create_project(client, token, "\n\n".join(paragraphs))
    assert response.status_code == 202, response.text
    built = response.json()["project"]
    assert built["chapter_count"] > 1
    assert all(
        chapter <= projects.CHAPTER_MAX_CHARS for chapter in built["chapter_characters"]
    )
    assert built["chapter_max_chars"] == projects.CHAPTER_MAX_CHARS
    assert client.get("/healthz").json()["jobs"] == {"queued": built["chapter_count"]}


def test_chapter_audio_is_a_conflict_until_every_chapter_has_been_spoken(client):
    token = login(client, signup(client)["email"])
    project_id = create_project(client, token, MANUSCRIPT).json()["project"]["id"]

    response = project_audio(client, token, project_id)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["project_status"] == "queued"
    assert detail["chapter_count"] == 3 and detail["spoken_chapters"] == 0
    assert [row["chapter"] for row in detail["waiting"]] == [1, 2, 3]
    assert all(row["status"] == "queued" for row in detail["waiting"])
    # The per-chapter audio of a job that has not been spoken is a conflict too.
    chapters = project(client, token, project_id).json()["project"]["chapters"]
    assert client.get(
        f"/v1/jobs/{chapters[0]['job_id']}/audio",
        headers={AUTHORIZATION: f"Bearer {token}"},
    ).status_code == 409


def test_the_worker_speaks_every_chapter_and_the_audiobook_is_those_chapters_joined(
    client, engine
):
    token = login(client, signup(client)["email"])
    created = create_project(client, token, MANUSCRIPT).json()["project"]
    engine.set_audio_seconds(1.25)

    stats = drain(worker_for(client, engine))
    assert stats.succeeded == 3, stats.as_dict()

    built = project(client, token, created["id"]).json()["project"]
    assert built["status"] == "ready"
    assert built["spoken_chapters"] == 3
    assert built["statuses"] == {"succeeded": 3}
    assert built["spoken_seconds"] == pytest.approx(3.75, abs=1e-3)

    response = project_audio(client, token, created["id"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["X-Qualiabits-Project"] == str(created["id"])
    assert response.headers["X-Qualiabits-Project-Chapters"] == "3"
    audio = response.content
    # The header is the joined file's own, and the bytes hash to what the header says.
    assert audio[:4] == b"RIFF"
    assert response.headers["X-Qualiabits-Object-Sha256"] == hashlib.sha256(audio).hexdigest()
    with wave.open(io.BytesIO(audio), "rb") as handle:
        assert handle.getnframes() == 3 * 30000, "three 1.25 s chapters at 24 kHz"
        frames = handle.readframes(handle.getnframes())
    assert len(frames) == 3 * 30000 * 2
    assert response.headers["X-Qualiabits-Audio-Seconds"].startswith("3.75")
    # The assembled file is the chapters in reading order: every chapter's own
    # audio appears in it, so nothing was dropped or re-ordered.
    assert frames.count(struct.pack("<h", 1200)) == 3 * 30000


def test_the_meter_counts_chapters_not_projects(client, engine):
    token = login(client, signup(client)["email"])
    create_project(client, token, MANUSCRIPT)
    before = usage(client, token)
    assert before["requests"] == 0, "queueing a chapter is not speaking it"

    drain(worker_for(client, engine))
    after = usage(client, token)
    assert after["requests"] == 3, "one request per chapter, not one per project"
    assert after["characters"] == sum(
        len(chapter) for chapter in projects.split_chapters(MANUSCRIPT)
    )
    assert after["audio_seconds"] == pytest.approx(3.75, abs=1e-3)
    # No project counter was invented: the ledger's shape is the same as before.
    assert "projects" not in after and "project_count" not in after


def test_a_project_queued_with_a_key_is_attributed_to_that_key(client, engine):
    token = login(client, signup(client)["email"])
    key = mint_key(client, token)
    create_project(client, key, "\n\n".join([long_paragraph("पहला:"), long_paragraph("दूसरा:")]))
    drain(worker_for(client, engine))
    ledger = usage(client, token)
    assert ledger["requests"] == 2
    assert len(ledger["by_key"]) == 1 and ledger["by_key"][0]["requests"] == 2


def test_another_workspace_cannot_see_a_project_or_its_audio(client, engine):
    owner = signup(client, "author@example.com")
    owner_token = login(client, owner["email"])
    created = create_project(client, owner_token, MANUSCRIPT).json()["project"]
    drain(worker_for(client, engine))

    stranger = signup(client, "stranger@example.com")
    stranger_token = login(client, stranger["email"])
    # A project id that is real but not theirs answers exactly like one that is not.
    for path in (f"/v1/projects/{created['id']}", f"/v1/projects/{created['id']}/audio", "/v1/projects/999999"):
        response = client.get(path, headers={AUTHORIZATION: f"Bearer {stranger_token}"})
        assert response.status_code == 404, path
        assert response.json()["detail"] == "No such project."
    assert client.delete(
        f"/v1/projects/{created['id']}", headers={AUTHORIZATION: f"Bearer {stranger_token}"}
    ).status_code == 404
    listed = client.get("/v1/projects", headers={AUTHORIZATION: f"Bearer {stranger_token}"}).json()
    assert listed["projects"] == [] and listed["workspace_total"] == 0


def test_cancelling_a_project_cancels_only_the_chapters_still_queued(client, engine):
    token = login(client, signup(client)["email"])
    created = create_project(client, token, MANUSCRIPT).json()["project"]
    worker = worker_for(client, engine)
    # Speak the first chapter only, by stopping the worker after one job.
    stats = drain(worker, max_jobs=1, )
    assert stats.succeeded == 1

    response = client.delete(
        f"/v1/projects/{created['id']}", headers={AUTHORIZATION: f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["cancelled_jobs"]) == 2, "the two unspoken chapters were cancelled"
    assert body["project"]["status"] == "cancelled"
    assert body["project"]["spoken_chapters"] == 1
    # Nothing the customer cancelled was spoken, so the meter keeps only the one.
    assert usage(client, token)["requests"] == 1
    assert drain(worker_for(client, engine)).claimed == 0
    assert project_audio(client, token, created["id"]).status_code == 409


def test_chapters_that_disagree_on_their_format_are_refused_with_the_reason(client, engine):
    """The join is refused rather than written wrong, and the sentence says why.

    Repointed on purpose: object keys are content-addressed and reads verify the
    hash, so a chapter cannot simply be corrupted in place. This stands in for the
    real case — a chapter spoken before a rate or channel change on the engine —
    by storing a 48 kHz file and pointing one chapter's job at it.
    """
    token = login(client, signup(client)["email"])
    created = create_project(client, token, MANUSCRIPT).json()["project"]
    drain(worker_for(client, engine))

    state = client.app.state.hosted
    other = state.store.put(engine.wav, "audio/wav")  # same 24 kHz stub audio
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(struct.pack("<h", 1200) * 48000)
    resampled = state.store.put(buffer.getvalue(), "audio/wav")

    second_chapter = created["chapters"][1]["job_id"]
    with state.db.session() as session:
        job = session.get(Job, second_chapter)
        job.object_key = resampled.key
        job.object_sha256 = resampled.sha256
        session.commit()
    assert other.key != resampled.key

    response = project_audio(client, token, created["id"])
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "cannot be joined" in detail["detail"]
    assert "chapter 2" in detail["detail"] and "48000 Hz" in detail["detail"]
    assert "resampling" in detail["detail"]
    # Every chapter is still succeeded; the refusal is about the file, not the queue.
    assert detail["project_status"] == "ready"


def test_a_second_manuscript_is_listed_with_the_first(client, engine):
    token = login(client, signup(client)["email"])
    first = create_project(client, token, MANUSCRIPT, title="पहली").json()["project"]
    drain(worker_for(client, engine))
    second = create_project(client, token, "एक अध्याय।", title="दूसरी").json()["project"]

    listed = client.get("/v1/projects", headers={AUTHORIZATION: f"Bearer {token}"}).json()
    assert listed["workspace_total"] == 2 and listed["returned"] == 2
    assert [row["id"] for row in listed["projects"]] == [second["id"], first["id"]]
    assert listed["projects"][0]["status"] == "queued"
    assert listed["projects"][1]["status"] == "ready"
    assert listed["projects"][1]["spoken_chapters"] == 3


def test_the_health_route_publishes_the_long_form_limits(client):
    health = client.get("/healthz").json()
    assert health["project_max_chars"] == projects.PROJECT_MAX_CHARS
    assert health["chapter_target_chars"] == projects.CHAPTER_TARGET_CHARS
    assert health["chapter_max_chars"] == projects.CHAPTER_MAX_CHARS
    assert health["chapter_max_count"] == projects.CHAPTER_MAX_COUNT


def test_a_project_queued_on_an_old_database_still_works(tmp_path, engine):
    """The same path, against a database that was brought forward by migration."""
    from conftest import app_client as make_client

    with make_client(tmp_path, engine) as first:
        token = login(first, signup(first)["email"])
        create_project(first, token, MANUSCRIPT)

    # A second plane start finds the database already at the current version and
    # serves the same project, which is what a restart in production looks like.
    with make_client(tmp_path, engine) as second:
        token = login(second, "buyer@example.com", PASSWORD)
        listed = second.get("/v1/projects", headers={AUTHORIZATION: f"Bearer {token}"}).json()
        assert listed["workspace_total"] == 1
        assert listed["projects"][0]["chapter_count"] == 3
        assert second.get("/healthz").json()["schema_state"] == "current"