#!/usr/bin/env python3
"""Build the cheap+ judge prompt for M3 slice G (the limits) from real artifacts.

Same rules as the builders beside it: the judge gets the artifacts themselves —
the code, the tests, the pages, the two browser runs' own assertions, the suite
logs and the commits — and not a summary of them. The helpers are imported from
`build_m3_review_prompt.py` so every slice's bundle describes the same repository
the same way, and a missing artifact refuses the bundle rather than leaving a
silent gap.

    python hermes-reports/build_m3g_review_prompt.py [round-label] > prompt.txt
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_m3_review_prompt import (  # noqa: E402
    REPORTS,
    REPO,
    RUBRIC,
    WEB,
    e2e_summary,
    file_list,
    git_log,
    milestone,
    show,
    stub_summary,
)

SLICE = "M3-slot79"

#: The judge prompt goes to the model on this box as a single command-line
#: argument, and Linux refuses one longer than 128 KB (MAX_ARG_STRLEN). Handing
#: the judge the whole of `app.py` and every document in full made a 279 KB
#: prompt that would not run at all, so the biggest artifacts are supplied as
#: named excerpts instead: the guard helper, the five metered routes where the
#: decision is made, and the first N lines of the rest, each labelled as an
#: excerpt with its real line numbers, so a judge can see what it was not given.
APP_RANGES = (
    (376, 460),    # _refuse_limits and _limits_for, the refusal and the subject
    (700, 830),    # /v1/usage, /v1/limits (get and put), /v1/jobs (guard at 799)
        (1140, 1215),  # the speech route, guard at 1188
    (1238, 1280),  # the transcription route, guard at 1260
    (1396, 1450),  # the dub route, guard at 1424
)


def excerpt(path: Path, ranges) -> str:
    """Named line ranges of one file, each headed by the lines it really occupies."""
    if not path.exists():
        raise SystemExit(f"missing artifact: {path}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out = [f"{path.name} is {len(lines)} lines; these are the ranges that matter:"]
    for start, end in ranges:
        end = min(end, len(lines))
        out.append(f"--- lines {start}-{end} ---")
        out.extend(lines[start - 1:end])
    return "\n".join(out)


def e2e_trimmed(path: Path) -> str:
    """The real run's facts, minus the per-page screenshot records, for the same
    128 KB reason the excerpts exist. The route list is in the stub summary and in
    the archives themselves; what the judge needs here is the provenance block,
    the engine's own answers and what the run measured about speech."""
    if not path.exists():
        raise SystemExit(f"missing end-to-end result: {path}")
    data = json.loads(path.read_text())
    keep = {
        k: data.get(k)
        for k in ("run", "revision", "engine", "engine_command", "engine_is_stub",
                  "engine_health", "engine_active", "engine_voice_count",
                  "engine_model", "engine_model_source", "transcription_model",
                  "transcription_model_pinned", "browser", "deployed", "public_url",
                  "gpu", "real_s3", "transcript", "dub", "audio", "limits", "usage",
                  "passed", "failed", "ok")
    }
    lines = [f"{path.name} (provenance, engine answers and measurements; the per-page "
             f"screenshot records are omitted from this prompt, not from the file):",
             json.dumps(keep, indent=2, ensure_ascii=False)]
    lines.append(f"\nchecks: {data.get('passed')} passed, {data.get('failed')} failed, "
                 f"ok={data.get('ok')}")
    named = [check["name"] for check in data.get("checks", [])]
    lines.append(f"{len(named)} checks, each with its own verdict, are listed in the "
                 f"result file; the archives beside it hold the browser screenshots. "
                 f"They are not repeated here to stay inside the shell's argument limit.")
    return "\n".join(lines)


def review_files(limit: int = 4) -> str:
    """The cheap+ passes already run on this slice, verbatim.

    A pass that never reached the model is not a pass: the first attempt at this
    slice's round 1 was refused by the shell ("Argument list too long", exit 126),
    and the first bundle handed its stub of an output to the judge as though it
    were a completed review — the judge was right to call that out, and to score
    honesty 4 for it. Only a file whose last line says exit=0 is a pass.
    """
    usable = []
    for path in sorted(REPORTS.glob(f"{SLICE}-review*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if text.rstrip().endswith("exit=0"):
            usable.append((path, text))
    if not usable:
        return "no completed judge pass is committed yet; this is the first"
    return "\n\n".join(
        f"### {p.name}\n" + text for p, text in usable[-limit:]
    )


def since_text() -> str:
    files = sorted(REPORTS.glob(f"{SLICE}-since-review*.txt"))
    if not files:
        return "nothing: this is the first pass on the slice"
    return "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def newest(pattern: str) -> Path:
    """The newest artifact matching a pattern, with its own timestamp shown."""
    found = sorted(REPORTS.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not found:
        raise SystemExit(f"missing artifact matching {pattern}")
    return found[-1]


def stamped(path: Path) -> str:
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
    return f"(written {when}, {path.stat().st_size} bytes)\n" + show(path)


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "round1"
    evidence = REPORTS / f"{SLICE}-web-e2e-r1" / "result.json"
    stub = REPORTS / f"{SLICE}-web-e2e-stub-r1" / "result.json"
    tests = newest(f"V0-slot79-tests-*.log")
    unit = newest(f"V0-slot79-web-unit-*.log")

    parts = [
        f"# Judge pass {label} on M3 slice G (limits that hold), cheap+ tier\n",
        "You are a strict hackathon judge. Score the seven criteria of the rubric "
        "below, 1 to 5 each, and pass only if every score is 4 or more with no "
        "honesty flaw. The artifacts are supplied; the builder's own round log is "
        "NOT supplied and must not be asked for — judge what is here.\n",
        "## Rubric\n", show(RUBRIC), "\n",
        "## The milestone this is measured against (criteria fit)\n",
        "There is no hackathon rules file for a product milestone, so criterion 5 is "
        "measured against the owner's own milestone below, quoted from the queue this "
        "work comes from. Score criteria fit against it, and say which of its items "
        "have no evidence here.\n\n",
        milestone(), "\n",
        "## What the slice claims to be (D1-D8, written before any code)\n",
        "D1 a per-workspace character ceiling per UTC calendar month and a request "
        "rate per API key, both decided *before* the engine is asked, and both set "
        "to 'none' for a workspace that never asked for one (a limit nobody asked "
        "for is a policy, not a safety net).\n"
        "D2 the gate is one conditional UPDATE comparing both halves of the month "
        "(committed and reserved) in the row it is about to change, so two "
        "concurrent requests cannot both take the last of the ceiling — with a "
        "thread-race test that fails against the read-then-write version this "
        "slice started with, and a second race on the row's own creation.\n"
        "D3 the ledger stays the record: the counter is a projection seeded from "
        "the ledger when it is created (so an upgraded database, or a ceiling set "
        "mid-month, counts what was already spent), advanced only beside the usage "
        "event it mirrors, and a test asserts the two agree after every kind of "
        "request. Reserve/settle/release are three distinct verbs: a cancelled job "
        "is released, never charged.\n"
        "D4 refusals are product behaviour: 402 for the ceiling with the period's "
        "reset moment, 429 for the rate with a real Retry-After, the limit numbers "
        "in the headers, a machine-readable error code in an object body, and a "
        "zero-character row in the meter — because the text never reached the "
        "engine.\n"
        "D5 the honest scope: the ceiling counts characters asked to be spoken, so "
        "a transcription is bounded by the rate and not the ceiling; the rate is a "
        "fixed window and says so; nothing prunes rate_windows yet; a request that "
        "dies mid-flight leaves its reservation standing, and the dashboard shows "
        "reserved apart from settled so that difference is visible.\n"
        "D6 the limits are readable over the API with a key and settable only with "
        "a session, and the dashboard's Usage page shows them and sets them; an "
        "empty box means no limit rather than a limit of zero; every dashboard "
        "error path now reads its message through one function, because refusals "
        "became objects and `data.detail` would otherwise have rendered as "
        "[object Object] on seven pages.\n"
        "D7 docs: hosted/M3-ACCEPTANCE.md gains slice G, a table of what the two "
        "unbuilt pages (Voice Clone, Voice Design) would need and which engine "
        "advertises each, and rows whose evidence is a check in the cited run; "
        "DESIGN.md explains the CAS, the two refusal codes and the honest limits; "
        "RUN-IT-BY-HAND.md shows a reader how to watch the ceiling hold.\n"
        "D8 two cheap+ judge rounds on the real artifacts, every fixable flaw "
        "fixed, unfixable ones written down; originality is expected to stay under "
        "4 because a rate limiter is not a novel idea and the differentiator is "
        "the owner's decision.\n",
        "## The README a reviewer reads first (hosted/web/README.md, first 120 lines of the file)\n",
        show(WEB / "README.md", head=120), "\n",
        "## The hosted layer's README (hosted/README.md, first 140 lines)\n",
        show(REPO / "hosted" / "README.md", head=140), "\n",
        "## The app's files (excluding node_modules, .next, .evidence)\n",
        file_list(WEB), "\n",
        "## The limits module (hosted/control_plane/limits.py, first 200 lines of 491)\n",
        show(REPO / "hosted" / "control_plane" / "limits.py", head=200), "\n",
        "## Where the routes decide (hosted/control_plane/app.py, excerpted: the guard helper and every metered route)\n",
        excerpt(REPO / "hosted" / "control_plane" / "app.py", APP_RANGES), "\n",
        "## The counter rows (hosted/control_plane/db.py, the slice-G models and the workspace limits, first 120 lines)\n",
        show(REPO / "hosted" / "control_plane" / "db.py", head=120), "\n",
        "## The migration (hosted/control_plane/migrations.py, first 150 lines)\n",
        show(REPO / "hosted" / "control_plane" / "migrations.py", head=150), "\n",
        "## Where a job settles (hosted/control_plane/jobs.py, first 150 lines)\n",
        show(REPO / "hosted" / "control_plane" / "jobs.py", head=150), "\n",
        "## The tests this slice owes (tests/hosted/test_hosted_limits.py, first 170 lines of 419)\n",
        show(REPO / "tests" / "hosted" / "test_hosted_limits.py", head=170), "\n",
        "## The dashboard's half (lib/quota.js, the limits API route, the panel)\n",
        show(WEB / "lib" / "quota.js"), "\n",
        show(WEB / "app" / "api" / "limits" / "route.js"), "\n",
        show(WEB / "components" / "LimitsView.jsx"), "\n",
        "## The acceptance table this slice owes rows in (hosted/M3-ACCEPTANCE.md, first 100 lines of the file)\n",
        show(REPO / "hosted" / "M3-ACCEPTANCE.md", head=100), "\n",
        "## The end-to-end, real engine, this revision (--keep)\n",
        e2e_trimmed(evidence), "\n",
        "## The end-to-end, stub engine (the default smoke path)\n",
        stub_summary(stub), "\n",
        "## Suite results (tests/hosted, the newest transcript beside these runs)\n",
        stamped(tests), "\n",
        "## Web unit tests (node --test, the newest transcript, first 15 lines)\n",
        "\n".join(stamped(unit).splitlines()[:15]),
        "\n",
        "## Commits in this slice\n", git_log(10), "\n",
        "## The judge passes already run on this slice (D8)\n",
        "These are the independent cheap+ passes on this slice, verbatim, so that "
        "D8's 'two rounds, every fixable flaw fixed' can be checked rather than "
        "believed.\n\n",
        review_files(), "\n",
        "## What changed after the pass above\n",
        since_text(), "\n",
        "## What is NOT claimed\n",
        "Nothing is deployed and there is no public URL (deployed=false, "
        "public_url=null in the run). There is no real S3 bucket, only the local "
        "object store. There is no GPU. No human has listened to the audio. M1 (the "
        "scoped GPU run and a listening check), a product differentiator, a "
        "deployment/bucket decision and the next M3 page are the owner's. Voice "
        "Clone and Voice Design are not built and are recorded with the engine "
        "evidence for why they cannot be built on this machine. The ceiling is a "
        "self-imposed spend guard, not billing: there is no invoice, no plan and no "
        "payment path anywhere in this repository. The rate window is fixed rather "
        "than sliding, and its rows are not pruned.\n",
        "## Output format\n",
        "For each of the seven criteria: score and two or three sentences of evidence "
        "from the artifacts. Then the five most damaging flaws with file or line "
        "references. Then one sentence on what would make it win. Say plainly which D "
        "items are demonstrated, partly demonstrated, or not.\n",
    ]
    sys.stdout.write("\n".join(parts))


if __name__ == "__main__":
    main()