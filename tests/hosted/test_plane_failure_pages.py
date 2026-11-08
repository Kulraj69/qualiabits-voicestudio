"""A page may show an empty state only when the plane answered, and said empty.

M4 slice B. The defect was eight server-rendered reads of the control plane that
folded "the read failed" into "the list is empty" — `/keys` telling a customer
their keys were gone, `/voices` saying the plane reported no voices under a
heading saying it had not answered, and the home page printing four zeros for a
meter it never read — and a plane that was not there at all turning every page
into Next's own "Application error".

The fix is one reader (`hosted/web/lib/planeRead.js`) and one notice
(`hosted/web/components/PlaneFailed.jsx`), and this suite checks the *run* rather
than the source, because a source-level assertion cannot tell a page that renders
the notice from a page that renders it and its empty state underneath:

  * the run the acceptance page cites recorded a failing-plane pass, in both
    modes, and every page it drove came back clean;
  * the notice the 500-mode page showed is the stub's own sentence, so a reader
    can see the dashboard quoted the plane rather than paraphrasing it, and the
    closed-port mode says the plane is not answering;
  * the one page that reads nothing while it renders kept its form and showed no
    notice at all;
  * every page whose source reads the plane through the reader is one the run
    drove, so a ninth page cannot adopt the partial fix unnoticed.

Nothing here is deployed: the run is loopback, with no GPU, no model for this
pass and no real object store, and the stub is named as a stub in the run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "hosted" / "web"
PAGE = REPO_ROOT / "hosted" / "M4-ACCEPTANCE.md"
EVIDENCE_RE = re.compile(r"hermes-reports/(M4-slot\d+-web-e2e(?:-stub)?-r\d+)/result\.json")

#: The nine pages this slice is about, and the file that renders each one. The
#: routes are the run's own.
ROUTE_FILES = {
    "/": "app/page.jsx",
    "/speak": "app/speak/page.jsx",
    "/voices": "app/voices/page.jsx",
    "/transcribe": "app/transcribe/page.jsx",
    "/dub": "app/dub/page.jsx",
    "/jobs": "app/jobs/page.jsx",
    "/projects": "app/projects/page.jsx",
    "/keys": "app/keys/page.jsx",
    "/usage": "app/usage/page.jsx",
}

#: The sentence the stub answers with, and where it is written down in the harness.
STUB_DETAIL = "The plane could not read its own storage."
MODE_500 = "the plane answers 500"
MODE_GONE = "the plane is not there"
HONEST_GONE = "is not answering"


def cited_run() -> dict:
    """The browser run the M4 acceptance page names, read as a whole."""
    assert PAGE.is_file(), "the M4 acceptance page is gone"
    matches = EVIDENCE_RE.findall(PAGE.read_text())
    assert matches, "the M4 acceptance page cites no browser run"
    name = sorted(set(matches))[0]
    path = REPO_ROOT / "hermes-reports" / name / "result.json"
    assert path.is_file(), f"the cited run does not exist: {path}"
    return json.loads(path.read_text())


def rows() -> list[dict]:
    run = cited_run()
    assert "failed_plane" in run, (
        "the cited run records no failing-plane pass, so the pages' behaviour when "
        "the plane refuses is not evidence anywhere"
    )
    assert run["failed_plane"]["stub_detail"] == STUB_DETAIL, (
        "the run's stub answered with a different sentence than this guard knows: "
        f"{run['failed_plane']['stub_detail']!r}"
    )
    return run["failed_plane"]["pages"]


def test_the_run_drove_every_page_in_both_modes():
    pages = rows()
    modes = {row["mode"] for row in pages}
    assert modes == {MODE_500, MODE_GONE}, f"the run drove these modes: {sorted(modes)}"
    for route in ROUTE_FILES:
        for mode in (MODE_500, MODE_GONE):
            found = [row for row in pages if row["route"] == route and row["mode"] == mode]
            assert len(found) == 1, f"{route} was not driven in mode {mode!r}"
    assert len(pages) == 2 * len(ROUTE_FILES), (
        f"the pass records {len(pages)} rows for {len(ROUTE_FILES)} pages in two modes"
    )


def test_no_page_was_left_unclean_in_either_mode():
    problems = {f"{row['route']} [{row['mode']}]": row["problems"] for row in rows() if row["problems"]}
    assert problems == {}, f"the failing-plane pass recorded problems: {problems}"


def test_every_reading_page_named_the_failure_it_got():
    """The notice is the failure it actually was, not one sentence for everything.

    In the 500 mode the dashboard has the plane's own detail and shows it; in the
    closed-port mode there is no body to quote, so the sentence says the plane is
    not answering. A page that printed the same string for both would pass a
    weaker check and tell a customer nothing about which one happened.
    """
    for row in rows():
        if not row["reads"]:
            continue
        notice = row["notice"]
        assert notice, f"{row['route']} showed no notice in mode {row['mode']!r}"
        if row["mode"] == MODE_500:
            assert notice == STUB_DETAIL, (
                f"{row['route']} showed {notice!r} in the 500 mode, and the plane's own "
                f"sentence is {STUB_DETAIL!r}"
            )
        else:
            assert HONEST_GONE in notice, (
                f"{row['route']} showed {notice!r} with no plane listening, which does "
                "not say the plane could not be reached"
            )
        assert row["status"] == 200, f"{row['route']} answered {row['status']}"
        assert not re.search(r"Application error|server-side exception", notice), (
            f"{row['route']} served Next's own error page and the notice still appeared"
        )


def test_the_page_that_reads_nothing_kept_its_form():
    """/transcribe fetches nothing while it renders, so a plane in trouble owns it.

    Its form is the whole page, and hiding it would take the product down over a
    failure it never made a request for.
    """
    row = [item for item in rows() if item["route"] == "/transcribe"]
    assert len(row) == 2, "the transcribe page was not driven in both modes"
    for item in row:
        assert item["reads"] is False, "the run now claims /transcribe reads the plane"
        assert item["notice"] == "", f"/transcribe showed a notice: {item['notice']!r}"


def test_every_page_that_reads_the_plane_is_one_the_run_drove():
    """The partial fix is not allowed to leave a ninth page behind.

    A page reaches the plane through `readPlane`; if one is added, or an existing
    page goes back to `cpJson`, it will not be in the run's list and this fails.
    """
    run_routes = {row["route"] for row in rows()}
    reading = []
    for route, relative in ROUTE_FILES.items():
        source = (WEB / relative).read_text()
        if "readPlane(" in source:
            reading.append(route)
        assert "lib/controlplane.js" not in source, (
            f"{relative} imports the plane's client directly instead of through "
            "lib/planeRead.js"
        )
    assert set(reading) == run_routes - {"/transcribe"}, (
        f"pages that read the plane while they render: {sorted(reading)}; "
        f"pages the run drove as reading ones: {sorted(run_routes - {'/transcribe'})}"
    )


def test_the_run_has_a_check_per_page_and_they_all_passed():
    run = cited_run()
    names = [check["name"] for check in run["checks"]]
    for route in ROUTE_FILES:
        matching = [name for name in names if name.startswith("with the plane refusing to answer, ") and f" {route} " in name]
        assert len(matching) == 1, f"the run has {len(matching)} failing-plane checks for {route}"
    failed = [name for name in names if name.startswith("with the plane refusing to answer, ") and
              [check for check in run["checks"] if check["name"] == name and not check["ok"]]]
    assert failed == [], f"failing-plane checks that did not pass: {failed}"