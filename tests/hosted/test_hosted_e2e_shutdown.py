"""How long the harness lets a process shut down, and what it records about it.

This suite exists because of a real failure inside this slot's own evidence run.
The first end-to-end run of slot 55 was force-killed: the engine's lifespan
teardown (a 20 s cancellation of its background tasks, the watermark pool drain,
the model unload) outran the harness's 30 s budget on a two-CPU box, so
``result.json`` recorded ``passed: false`` and the run proved nothing about
shutdown — while the *page* went on printing "every process it started: clean
shutdown", a sentence that did not come from the run file at all. A second run of
the same code on the same box shut down cleanly, so the 30 s number was measuring
the box's load rather than the app's behaviour.

Two things are tested here instead of trusted:

  * ``stop()`` really reports a clean shutdown for a process that says goodbye and
    really reports a forced kill for one that ignores the signal, and it records
    how long it waited against the budget it used;
  * the page's shutdown lines are rendered from the run file, so a forced kill
    cannot be described as a clean one — including the case where the run did not
    record a timing at all, which must not be given a borrowed one.

The children are real processes and the signal is a real SIGTERM; the only small
thing about the test is the budget the stubborn child is given.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

from hosted.scripts import hosted_e2e  # noqa: E402
from hosted.scripts.render_measured import shutdown_lines  # noqa: E402

CLEAN_CHILD = """
import signal
import sys
import time


def goodbye(signum, frame):
    print("Application shutdown complete.", flush=True)
    sys.exit(0)


signal.signal(signal.SIGTERM, goodbye)
print("serving", flush=True)
while True:
    time.sleep(0.05)
"""

STUBBORN_CHILD = """
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("serving", flush=True)
while True:
    time.sleep(0.05)
"""


def spawn(source: str, log: Path) -> subprocess.Popen:
    child = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=REPO_ROOT,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    # Wait until the child says it is serving, so the SIGTERM cannot race the
    # child's own signal setup: a child that dies before it installs its handler
    # would make these tests measure start-up latency rather than shutdown.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if "serving" in log.read_text(errors="replace"):
            return child
        if child.poll() is not None:
            raise AssertionError(f"the child exited before it served: {log.read_text()}")
        time.sleep(0.02)
    child.kill()
    raise AssertionError("the child never reported that it was serving")


def test_a_process_that_says_goodbye_is_recorded_as_clean(tmp_path):
    log = tmp_path / "clean.log"
    child = spawn(CLEAN_CHILD, log)
    try:
        record = hosted_e2e.stop(child, log, {})
    finally:
        if child.poll() is None:
            child.kill()
    assert record["clean"] is True
    assert record["forced"] is False
    assert record["waited_seconds"] < hosted_e2e.TERMINATION_GRACE_SECONDS
    assert record["grace_seconds"] == hosted_e2e.TERMINATION_GRACE_SECONDS
    assert "Application shutdown complete." in log.read_text()


def test_a_process_that_ignores_the_signal_is_forced_and_never_called_clean(tmp_path, monkeypatch):
    # One second rather than the real budget: the point is that a process which
    # outlasts the budget is killed and reported as such, not that the budget is
    # long. The budget itself is asserted against the constant in the other tests.
    monkeypatch.setattr(hosted_e2e, "TERMINATION_GRACE_SECONDS", 1.0)
    log = tmp_path / "stubborn.log"
    child = spawn(STUBBORN_CHILD, log)
    try:
        record = hosted_e2e.stop(child, log, {})
    finally:
        if child.poll() is None:
            child.kill()
    assert record["forced"] is True
    assert record["clean"] is False
    assert record["waited_seconds"] >= 1.0
    assert record["grace_seconds"] == 1.0


def test_a_run_that_started_no_process_records_no_shutdown():
    record = hosted_e2e.stop(None, Path("/dev/null"), {})
    assert record == {"started": False}


def test_the_page_takes_its_shutdown_lines_from_the_run_file():
    source = (REPO_ROOT / "hosted" / "scripts" / "render_measured.py").read_text()
    assert "every process it started: clean shutdown" not in source, (
        "the page's shutdown claim is hardcoded again; it has to come from the run"
    )
    assert "shutdown_lines(result)" in source, (
        "render_section no longer renders the run's own shutdown record"
    )


def clean_record(seconds: float, budget: float = 60.0) -> dict:
    return {
        "started": True,
        "exit_code": 0,
        "forced": False,
        "waited_seconds": seconds,
        "grace_seconds": budget,
        "clean": True,
    }


def test_a_clean_shutdown_is_rendered_with_what_was_waited():
    result = {
        "engine_shutdown": clean_record(2.145),
        "control_plane_shutdown": clean_record(1.02),
    }
    rendered = shutdown_lines(result)
    assert rendered.count("clean, the app said so") == 2
    assert "2.145 s of a 60.0 s budget" in rendered
    assert "1.020 s of a 60.0 s budget" in rendered


def test_a_forced_kill_cannot_be_rendered_as_a_clean_shutdown():
    forced = clean_record(60.0)
    forced.update({"forced": True, "clean": False, "exit_code": -9})
    result = {"engine_shutdown": forced, "control_plane_shutdown": clean_record(1.02)}
    rendered = shutdown_lines(result)
    assert "killed after the budget" in rendered
    assert rendered.count("clean, the app said so") == 1, (
        "the forced kill was rendered as if the app had shut itself down"
    )


def test_a_run_without_a_recorded_timing_is_not_given_one():
    silent = {"started": True, "exit_code": 0, "forced": False, "clean": True}
    rendered = shutdown_lines({"engine_shutdown": silent, "control_plane_shutdown": silent})
    assert "timing not recorded by this run" in rendered
    assert "0.000 s" not in rendered