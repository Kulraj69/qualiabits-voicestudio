#!/usr/bin/env python3
"""Build the cheap+ judge prompt for M3 slice H (the width) from real artifacts.

Same rules as the builders beside it: the judge gets the artifacts themselves — the
stylesheet and its rules, the harness's own width section, the tests, the pages, the
two browser runs' own measurements, the suite logs and the commits — and not a
summary of them. The helpers are imported from `build_m3_review_prompt.py` so every
slice's bundle describes the same repository the same way, and a missing artifact
refuses the bundle rather than leaving a silent gap.

    python hermes-reports/build_m3h_review_prompt.py [round-label] > prompt.txt
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/workspace/qualiabits-voicestudio/hermes-reports")

from build_m3_review_prompt import (  # noqa: E402
    REPORTS,
    REPO,
    RUBRIC,
    WEB,
    file_list,
    git_log,
    milestone,
    show,
    stub_summary,
)

SLICE = "M3-slot88"
#: Slice H's earlier label. The first cheap+ pass on this slice was archived under it by
#: the run that opened the slice, and D7 asks for every pass on the slice rather than
#: every pass since the last rename, so both are read back for the judge.
EARLIER_SLABEL = "M3-slot81"
REVIEW_GLOBS = (f"{EARLIER_SLABEL}-review*.txt", f"{SLICE}-review*.txt")

#: The judge prompt goes to the model as one command-line argument and Linux refuses
#: an argument longer than 128 KB, so the big artifacts are excerpts with their real
#: line numbers: a judge can then see what it was not given.
CSS_RANGES = (
    (108, 150),      # the palette tokens (both themes), so a width block is read in context
    (985, 1090),     # the width section itself: the comment, the 900px block, the 640px block
)
HARNESS_RANGES = (
    (620, 762),      # the width section: the measurement and the checks up to the
    #                  screenshots; the last two checks are named in the result file below
)
TEST_RANGES = (
    (1, 40),         # what the file is for
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


def _controls(controls: list) -> dict:
    """What the two top-bar checks are about, from every control at once."""
    if not controls:
        return {"count": 0}
    return {
        "count": len(controls),
        "leftmost": min(c.get("left") or 0 for c in controls),
        "rightmost": max(c.get("right") or 0 for c in controls),
        "narrowest": min((c.get("width") or 0 for c in controls), default=0),
        "shortest": min((c.get("height") or 0 for c in controls), default=0),
        "names": sorted({(c.get("name") or "").strip() for c in controls}),
    }


def width_measurements(path: Path) -> str:
    """What the run measured at the three widths, and nothing else.

    The per-page screenshot records, the palette dumps and the check list would be
    most of a 65 KB file and would not survive the shell's argument limit. What the
    judge needs is the provenance block, the engine's own answers and every width
    measurement the run took, page by page.
    """
    if not path.exists():
        raise SystemExit(f"missing end-to-end result: {path}")
    data = json.loads(path.read_text())
    keep = {
        k: data.get(k)
        for k in ("run", "revision", "engine", "engine_is_stub", "engine_health",
                  "engine_active", "engine_voice_count", "engine_model",
                  "transcription_model_pinned", "browser", "deployed", "public_url",
                  "gpu", "real_s3", "passed", "failed", "ok")
    }
    lines = [
        f"{path.name} (provenance, engine answers and the width measurements; the "
        f"per-page screenshot records and the check list are omitted from this prompt, "
        f"not from the file):",
        json.dumps(keep, indent=2, ensure_ascii=False),
    ]
    for entry in data.get("widths", []):
        slim = {
            "id": entry.get("id"),
            "width": entry.get("width"),
            "height": entry.get("height"),
            "screenshots": len(entry.get("screenshots") or []),
            "screenshots_expected": entry.get("screenshots_expected"),
            "pages": [
                {
                    "route": page.get("route"),
                    "inner_width": page.get("inner_width"),
                    "document_scroll_width": page.get("document_scroll_width"),
                    "horizontal_overflow": page.get("horizontal_overflow"),
                    "offender_count": page.get("offender_count"),
                    "outside": page.get("outside"),
                    "nav_row_tops": page.get("nav_row_tops"),
                    "topbar_rows": page.get("topbar_rows"),
                    "topbar_height": page.get("topbar_height"),
                    "heading_visible": page.get("heading_visible"),
                    # The bar's controls are summarised, not listed one per page:
                    # twenty-seven pages of them is most of a 148 KB prompt and the
                    # shell refuses an argument over 128 KB. The count, the outermost
                    # edges and the smallest control are what the checks below are
                    # about; the full lists are in the archived result.json.
                    "topbar_controls": _controls(page.get("topbar_controls", [])),
                }
                for page in entry.get("pages", [])
            ],
        }
        lines.append(json.dumps(slim, indent=2, ensure_ascii=False))
    names = [check["name"] for check in data.get("checks", [])]
    width_checks = [n for n in names if ":" in n or "screenshot" in n]
    lines.append(
        f"\n{len(names)} checks, each with its own verdict, are in the result file; "
        f"the width ones the judge is being asked about are: {json.dumps(width_checks, ensure_ascii=False)}"
    )
    return "\n".join(lines)


def review_files(limit: int = 4) -> str:
    """The cheap+ passes already run on this slice, verbatim, if any completed."""
    usable = []
    for pattern in REVIEW_GLOBS:
        for path in sorted(REPORTS.glob(pattern)):
            text = path.read_text(encoding="utf-8", errors="replace")
            if text.rstrip().endswith("exit=0"):
                usable.append((path, text))
    if not usable:
        return "no completed judge pass is committed yet; this is the first"
    usable.sort(key=lambda item: item[0].name)
    return "\n\n".join(f"### {p.name}\n" + text for p, text in usable[-limit:])


def since_text() -> str:
    files = sorted(
        path
        for pattern in (f"{EARLIER_SLABEL}-since-review*.txt", f"{SLICE}-since-review*.txt")
        for path in REPORTS.glob(pattern)
    )
    if not files:
        return "nothing: this is the first pass on the slice"
    return "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def newest(pattern: str) -> Path:
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
    tests = newest("V0-slot8*-tests-*.log")
    unit = newest("V0-slot8*-web-unit-*.log")

    parts = [
        f"# Judge pass {label} on M3 slice H (the width: a responsive dashboard), cheap+ tier\n",
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
        "## What the slice claims to be (D1-D7, written before any code)\n",
        "D1 the nine pages render at three widths — 390x844 (phone), 768x1024 "
        "(tablet), 1440x900 (desktop) — with no sideways scroll and no element wider "
        "than the window on any page at any of them, asserted element by element in a "
        "real browser and not by eye.\n"
        "D2 the top bar's own controls (brand, nav, theme switch, session) stay inside "
        "the window and remain clickable at phone width.\n"
        "D3 the desktop appearance is unchanged — the same width, and the checks that "
        "existed before this slice still pass at the viewport they use. What backs this "
        "now: a guard compares the stylesheet against the same file as it stood at "
        "bd6c51bba (committed at tests/hosted/fixtures/globals-before-slice-h.css with "
        "its sha256 pinned) and fails if any rule outside the two width blocks changed; "
        "the run's own desktop evidence is four geometry facts, and no pixel comparison "
        "is claimed anywhere.\n"
        "D4 screenshots of every page at both narrow widths are committed as the "
        "artefact.\n"
        "D5 any pure geometry rule worth keeping without a browser gets a unit test.\n"
        "D6 hosted/M3-ACCEPTANCE.md, hosted/README.md, hosted/web/README.md and "
        "hosted/DESIGN.md carry the new rows and guards read them back.\n"
        "D7 two cheap+ judge rounds with fixes after each, no honesty flaw in the "
        "last. Originality is expected to stay under 4: a responsive layout is not a "
        "novel idea, and the product differentiator is the owner's decision.\n",
        "## The README a reviewer reads first (hosted/web/README.md, first 130 lines of the file)\n",
        show(WEB / "README.md", head=70), "\n",
        "## The hosted layer's README (hosted/README.md, first 130 lines)\n",
        show(REPO / "hosted" / "README.md", head=45), "\n",
        "## The design notes for this slice (hosted/DESIGN.md, the width section)\n",
        excerpt(REPO / "hosted" / "DESIGN.md", ((252, 293),)), "\n",
        "## The stylesheet (hosted/web/app/globals.css, the palette and the width blocks)\n",
        excerpt(WEB / "app" / "globals.css", CSS_RANGES), "\n",
        "## The harness's width section (hosted/web/e2e/dashboard.mjs, the measurement and the five checks)\n",
        excerpt(WEB / "e2e" / "dashboard.mjs", HARNESS_RANGES), "\n",
        "## The top bar (hosted/web/components/SiteHeader.jsx)\n",
        show(WEB / "components" / "SiteHeader.jsx"), "\n",
        "## The tests this slice owes\n",
        "### tests/hosted/test_dashboard_width_rules.py (the whole file)\n",
        show(REPO / "tests" / "hosted" / "test_dashboard_width_rules.py"), "\n",
        "### the parts of tests/hosted/test_web_pages_claims.py that read the two runs\n",
        excerpt(REPO / "tests" / "hosted" / "test_web_pages_claims.py", ((40, 70), (89, 120), (169, 205))), "\n",
        "### the parts of tests/hosted/test_m3_acceptance_matrix.py that read the table\n",
        excerpt(REPO / "tests" / "hosted" / "test_m3_acceptance_matrix.py", ((43, 60), (99, 130))), "\n",
        "## The acceptance page this slice owes rows in (hosted/M3-ACCEPTANCE.md, the slice list and the width rows)\n",
        excerpt(REPO / "hosted" / "M3-ACCEPTANCE.md", ((1, 20), (58, 120), (196, 222))), "\n",
        "## The end-to-end, real engine, this revision (--keep): the width measurements\n",
        width_measurements(evidence), "\n",
        "## The end-to-end, stub engine (the default smoke path)\n",
        stub_summary(stub), "\n",
        "## Suite results (tests/hosted, the newest transcript beside these runs)\n",
        stamped(tests), "\n",
        "## Web unit tests (node --test, the newest transcript, first 15 lines)\n",
        "\n".join(stamped(unit).splitlines()[:15]),
        "\n",
        "## The app's files (excluding node_modules, .next, .evidence)\n",
        file_list(WEB), "\n",
        "## Commits in this slice\n", git_log(12), "\n",
        "## The judge passes already run on this slice (D7)\n",
        "These are the independent cheap+ passes on this slice, verbatim, so that D7's "
        "'two rounds, every fixable flaw fixed' can be checked rather than believed.\n\n",
        review_files(), "\n",
        "## What changed after the pass above\n",
        since_text(), "\n",
        "## What is NOT claimed\n",
        "Nothing is deployed and there is no public URL (deployed=false, public_url=null "
        "in the run). There is no real S3 bucket, only the local object store. There is "
        "no GPU. No human has listened to the audio. M1 (a scoped GPU run and a "
        "listening check), a product differentiator, a deployment/bucket decision and "
        "the next M3 page are the owner's. Voice Clone and Voice Design are not built. "
        "The width work is three viewports in one browser on loopback: no real phone, "
        "no touch device, no Safari or Firefox pass, and no claim about a width between "
        "the three.\n",
        "## Output format\n",
        "For each of the seven criteria: score and two or three sentences of evidence "
        "from the artifacts. Then the five most damaging flaws with file or line "
        "references. Then one sentence on what would make it win. Say plainly which D "
        "items are demonstrated, partly demonstrated, or not.\n",
    ]
    prompt = "\n".join(parts)
    #: Linux refuses a single command-line argument over 128 KiB, and this prompt goes
    #: to the judge as exactly that. A first pass on this slice was handed the shell's
    #: exit=126 stub instead of a completed review because the bundle had grown past the
    #: limit - so the builder refuses to print one that would not fit, rather than
    #: letting the caller discover it as a mystery.
    limit = 120_000
    size = len(prompt.encode())
    if size > limit:
        raise SystemExit(
            f"the prompt is {size} bytes and this builder allows {limit}: trim an "
            "excerpt range, do not let the shell refuse the argument"
        )
    print(f"[{size} bytes, limit {limit}]", file=sys.stderr)
    sys.stdout.write(prompt)


if __name__ == "__main__":
    main()