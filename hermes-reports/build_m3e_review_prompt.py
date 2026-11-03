#!/usr/bin/env python3
"""Build the cheap+ judge prompt for M3 slice E (the light theme) from real artifacts.

Same rules as the slice A builder one file over: the judge gets the artifacts
themselves — the pages, the file list, the two browser runs' own assertions, the
suite logs and the commits — and not a summary of them. The heredoc-free helpers
(show, e2e_summary, milestone, …) are imported from `build_m3_review_prompt.py`
so the two packs cannot describe the same repository differently.

    python hermes-reports/build_m3e_review_prompt.py [round-label] > prompt.txt

A missing artifact refuses the bundle rather than leaving it out, because a judge
scoring an incomplete bundle scores the wrong thing.
"""
from __future__ import annotations

import sys
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

SLICE = "M3-slot72"


def review_files(limit: int = 3) -> str:
    found = sorted(REPORTS.glob(f"{SLICE}-review*.txt"))
    if not found:
        return "no judge pass is committed yet; this is the first"
    return "\n\n".join(
        f"### {p.name}\n" + p.read_text(encoding="utf-8", errors="replace") for p in found[-limit:]
    )


def since_text() -> str:
    files = sorted(REPORTS.glob(f"{SLICE}-since-review*.txt"))
    if not files:
        return "nothing: this is the first pass on the slice"
    return "\n\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "round1"
    evidence = REPORTS / f"{SLICE}-web-e2e-r1" / "result.json"
    stub = REPORTS / f"{SLICE}-web-e2e-stub-r1" / "result.json"
    tests = REPORTS / "V0-slot72-tests-r1.log"
    unit = REPORTS / "V0-slot72-web-unit-r1.log"

    parts = [
        f"# Judge pass {label} on M3 slice E (the light theme), cheap+ tier\n",
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
        "D1 dark/light, from the milestone's own words (\"responsive, fast, "
        "dark/light\"): a reader whose system asks for light gets a light dashboard, "
        "a reader who never expressed a preference keeps the shipped dark one, and a "
        "remembered choice outranks the system preference.\n"
        "D2 no flash of the wrong theme: the resolved theme is on <html> before the "
        "page's own markup and before hydration, applied by a script in the served "
        "HTML.\n"
        "D3 the toggle: in the top bar, reachable by keyboard, named for the theme it "
        "switches TO, remembers the choice across reloads, and does not break if the "
        "browser refuses localStorage.\n"
        "D4 every colour is a token, and the two light blocks (the data-theme one and "
        "the no-script prefers-color-scheme one) declare the same tokens with the same "
        "values — checked by a guard that bites on a tampered copy.\n"
        "D5 contrast is measured, not eyeballed: WCAG AA (4.5:1) for body, muted and "
        "heading text in both themes, on every page in light.\n"
        "D6 the card grid fills its trailing row — the hole a judge pass on slice D "
        "pointed at and slot 69 deferred — measured as geometry in the browser, not "
        "asserted in prose.\n"
        "D7 docs: hosted/M3-ACCEPTANCE.md gains slice E and moves \"a light theme\" out "
        "of its not-built list; both READMEs say how to see the theme and what the "
        "checks are; the counts in them are read back out of the committed runs.\n"
        "D8 two cheap+ judge rounds on the real artifacts, every fixable flaw fixed, "
        "unfixable ones written down; originality is expected to stay below 4 because "
        "a theme is not a novel idea and the differentiator is the owner's decision.\n",
        "## The README a reviewer reads first (hosted/web/README.md, full)\n",
        show(WEB / "README.md"), "\n",
        "## The hosted layer's README (hosted/README.md, first 140 lines)\n",
        show(REPO / "hosted" / "README.md", head=140), "\n",
        "## The app's files (excluding node_modules, .next, .evidence)\n",
        file_list(WEB), "\n",
        "## The theme module the page and the tests both read (lib/theme.js, full)\n",
        show(WEB / "lib" / "theme.js"), "\n",
        "## The toggle (components/ThemeToggle.jsx, full)\n",
        show(WEB / "components" / "ThemeToggle.jsx"), "\n",
        "## The light palette and the token guard (app/globals.css, first 200 lines)\n",
        show(WEB / "app" / "globals.css", head=200), "\n",
        "## The acceptance table this slice owes a row in (hosted/M3-ACCEPTANCE.md)\n",
        show(REPO / "hosted" / "M3-ACCEPTANCE.md"), "\n",
        "## The end-to-end, real engine, this revision (--keep)\n",
        e2e_summary(evidence), "\n",
        "## The end-to-end, stub engine (the default smoke path)\n",
        stub_summary(stub), "\n",
        "## Suite results (tests/hosted, from a clean clone of the pushed revision)\n",
        show(tests) if tests.exists() else "missing clean-clone suite log",
        "\n",
        "## Web unit tests (node --test)\n",
        show(unit) if unit.exists() else "missing web unit log",
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
        "deployment/bucket decision and the next M3 page are the owner's. The "
        "no-script fallback (a browser that runs no JavaScript) is asserted by the "
        "CSS guard and by the served stylesheet, not by a JavaScript-disabled "
        "browser run. The contrast numbers are measured against the page background "
        "token, not against the soft accent glow painted over it.\n",
        "## Output format\n",
        "For each of the seven criteria: score and two or three sentences of evidence "
        "from the artifacts. Then the five most damaging flaws with file or line "
        "references. Then one sentence on what would make it win. Say plainly which D "
        "items are demonstrated, partly demonstrated, or not.\n",
    ]
    sys.stdout.write("\n".join(parts))


if __name__ == "__main__":
    main()