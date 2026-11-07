#!/usr/bin/env python3
"""Build the cheap+ judge prompt for M4 slice A (accessibility) from real artifacts.

Same rules as the builders beside it: the judge gets the artifacts themselves — the
acceptance page, the harness's own axe and keyboard sections, the tests, the three
browser runs' own numbers (the fix, the stub path and the run taken before the fix)
and the suite transcripts — and not a summary of them. The helpers are imported from
`build_m3_review_prompt.py` so every slice's bundle describes the same repository the
same way, and a missing artifact refuses the bundle rather than leaving a silent gap.

    python hermes-reports/build_m4a_review_prompt.py [round-label] > prompt.txt

The bundle is refused if it would exceed the 128 KB a Linux command-line argument
allows: a pass whose prompt cannot be delivered is not a pass, and the shell's
`exit=126` must never be read as one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/workspace/qualiabits-voicestudio/hermes-reports")

from build_m3_review_prompt import (  # noqa: E402
    REPORTS,
    REPO,
    RUBRIC,
    WEB,
    e2e_summary,
    git_log,
    milestone,
    show,
    stub_summary,
)

SLICE = "M4-slot99"
#: Linux refuses a command-line argument over 128 KB. Refuse to print a bundle that
#: would be handed to the shell as an argument it cannot take.
MAX_BUNDLE_BYTES = 120_000

#: The harness's two sections this slice added: the axe pass and the per-control
#: keyboard walk, including the bound that was the slice's own second finding.
HARNESS_RANGES = (
    (793, 825),    # what the section is for, and the pinned tool
    (903, 925),    # the axe checks
    (947, 1000),   # the keyboard walk, its bound, and the toggle
)


def excerpt(path: Path, ranges) -> str:
    """Named line ranges of one file, each headed by the lines it really occupies."""
    if not path.exists():
        raise SystemExit(f"missing artifact: {path}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for first, last in ranges:
        if last > len(lines):
            raise SystemExit(f"{path} has {len(lines)} lines; range {first}-{last} does not")
        out.append(f"--- {path.relative_to(REPO)}, lines {first}-{last} ---")
        out.extend(f"{n:>5}| {lines[n - 1]}" for n in range(first, last + 1))
        out.append("")
    return "\n".join(out)


def a11y_summary(path: Path) -> str:
    """The run's accessibility block, as numbers rather than as a 60 KB dump."""
    if not path.exists():
        raise SystemExit(f"missing end-to-end result: {path}")
    data = json.loads(path.read_text())
    a11y = data.get("a11y")
    if not a11y:
        raise SystemExit(f"{path} has no a11y block")
    lines = [
        f"{path.parent.name}/result.json: {data.get('passed')} passed, {data.get('failed')} failed, "
        f"engine_is_stub={data.get('engine_is_stub')}, checks={len(data.get('checks', []))}",
        f"revision: {json.dumps(data.get('revision'))}",
        f"tool: {json.dumps(a11y.get('tool'))}",
        f"tags: {json.dumps(a11y.get('tags'))}",
        f"runs: {len(a11y.get('runs') or [])} "
        f"(pages={a11y.get('pages')}, viewports={json.dumps(a11y.get('viewports'))})",
        f"violations_total: {a11y.get('violations_total')}, rules_run_min: {a11y.get('rules_run_min')}",
        f"keyboard_controls: {a11y.get('keyboard_controls')}, "
        f"keyboard_toggle: {json.dumps(a11y.get('keyboard_toggle'))}",
        "keyboard rows (route, controls, reached, truncated):",
    ]
    for row in a11y.get("keyboard") or []:
        lines.append(f"  {row['route']}: {row['controls']}, {row['reached']}, {row.get('truncated')}"
                     + (f"  missed={json.dumps(row.get('missed'))}" if row.get("missed") else ""))
    violations = [v for run in (a11y.get("runs") or []) for v in run.get("violations") or []]
    lines.append(f"violations recorded: {len(violations)}")
    for v in violations[:10]:
        lines.append(f"  {v['id']} ({v['impact']}, {v['nodes']} nodes) e.g. {v['targets'][:2]}")
    return "\n".join(lines)


def newest(pattern: str) -> Path:
    found = sorted(REPORTS.glob(pattern))
    if not found:
        raise SystemExit(f"missing transcript: {pattern}")
    return found[-1]


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "round1"
    real = REPORTS / f"{SLICE}-web-e2e-r1" / "result.json"
    stub = REPORTS / f"{SLICE}-web-e2e-stub-r1" / "result.json"
    before = REPORTS / "M4-slot99-web-e2e-before-real" / "result.json"
    tests = newest("V0-slot99-tests-*.log")
    unit = REPORTS / "V0-slot99-web-unit-r1.log"
    pins = REPORTS / "V0-slot99-pins-r1.log"
    cold = REPORTS / "V0-slot99-coldclone-r1.log"

    parts = [
        f"# Judge pass {label} on M4 slice A (accessibility: what a screen reader and a "
        "keyboard get), cheap+ tier\n",
        "You are a strict hackathon judge. Score the seven criteria of the rubric "
        "below, 1 to 5 each, and pass only if every score is 4 or more with no "
        "honesty flaw. The artifacts are supplied; the builder's own round log is "
        "NOT supplied and must not be asked for — judge what is here.\n",
        "## Rubric\n", show(RUBRIC), "\n",
        "## The milestone this is measured against (criteria fit)\n",
        "There is no hackathon rules file for a product milestone, so criterion 5 is "
        "measured against the owner's own milestone below, quoted from the queue this "
        "work comes from. M4's own text is \"Fix bugs found in M1-M3; add tests "
        "(backend + Playwright E2E for every page)\", and this slice is its first one: "
        "the class of defect a real tool finds that neither the width pass nor the "
        "theme pass looked for. Score criteria fit against the milestone, and say "
        "which of its items have no evidence here.\n\n",
        milestone(), "\n",
        "## What the slice claims to be (D1-D8, written before any code)\n",
        "D1 axe-core is a dev dependency pinned to an exact version, its version and "
        "the sha256 of the file the suite actually injects are asserted by a test, and "
        "the licence is named.\n"
        "D2 the browser suite runs axe (WCAG 2.1 level A and AA) over all nine pages "
        "at 390x844, 768x1024 and 1440x900, records per page every violation with its "
        "rule id, impact, node count and first target, writes them into result.json, "
        "and fails the run on any violation.\n"
        "D3 every violation that is the dashboard's own markup or stylesheet is "
        "fixed; anything that is not ours is recorded with its rule id and the reason "
        "rather than suppressed.\n"
        "D4 contrast stops being an opinion: a test reads the two theme blocks out of "
        "app/globals.css, computes the WCAG ratio for the text-on-surface pairs the "
        "design declares, and fails under 4.5.\n"
        "D5 the keyboard path is asserted per page per control: every control on "
        "every page is reachable by Tab, with the count of controls in the run.\n"
        "D6 hosted/M4-ACCEPTANCE.md carries claim -> code -> test -> run-evidence "
        "rows, tests/hosted/test_m4_acceptance_matrix.py reads them back, and the "
        "numbers a reader reads come out of the run's own result.json.\n"
        "D7 both browser paths re-taken at ONE revision over clean trees, the "
        "suites green, a cold clone at the pushed revision green.\n"
        "D8 two cheap+ judge passes on the real artifacts with a fix after each, and "
        "no honesty flaw in the last. Originality and criteria fit against the whole "
        "V0 product are expected to stay under 4: the product differentiator and the "
        "deployment are the owner's decisions, and this lane may not invent them.\n",
        "## The acceptance page for this slice (hosted/M4-ACCEPTANCE.md, the whole file)\n",
        show(REPO / "hosted" / "M4-ACCEPTANCE.md"), "\n",
        "## The harness's axe and keyboard sections (hosted/web/e2e/dashboard.mjs)\n",
        excerpt(WEB / "e2e" / "dashboard.mjs", HARNESS_RANGES), "\n",
        "## The tests this slice owes\n",
        "### tests/hosted/test_m4_acceptance_matrix.py (the whole file)\n",
        show(REPO / "tests" / "hosted" / "test_m4_acceptance_matrix.py"), "\n",
        "### tests/hosted/test_axe_is_pinned.py (the whole file)\n",
        show(REPO / "tests" / "hosted" / "test_axe_is_pinned.py"), "\n",
        "### the harness test added this slice (hosted/web/test/harness.test.mjs, the last 20 lines)\n",
        show(WEB / "test" / "harness.test.mjs", head=None)[-1600:], "\n",
        "### the parts of tests/hosted/test_web_pages_claims.py that read the two runs\n",
        excerpt(REPO / "tests" / "hosted" / "test_web_pages_claims.py",
                ((44, 70), (91, 99), (165, 196), (234, 294))), "\n",
        "## The run this page cites (real engine)\n", e2e_summary(real), "\n",
        "## The stub path of the same pair\n", stub_summary(stub), "\n",
        "## The run taken BEFORE the harness fix (the defect record)\n",
        f"{before.parent.name}/result.json:\n",
        a11y_summary(before), "\n",
        "## The cited run's accessibility block, as numbers\n", a11y_summary(real), "\n",
        "## The suites, from their own transcripts\n",
        f"### {tests.name}\n" + tests.read_text(encoding="utf-8", errors="replace")[-900:] + "\n",
        f"### {unit.name}\n" + unit.read_text(encoding="utf-8", errors="replace")[-500:] + "\n",
        f"### {pins.name}\n" + pins.read_text(encoding="utf-8", errors="replace")[-400:] + "\n",
        f"### {cold.name} (a clone with no .venv, no node_modules and no model)\n"
        + cold.read_text(encoding="utf-8", errors="replace") + "\n",
        "## The commits\n", git_log(6), "\n",
        "## What is asked of you\n",
        "Score each of the seven criteria 1-5, name the five most damaging flaws with "
        "the file, line or timestamp that shows each one, and say in one sentence what "
        "would make this win. If you believe a claim on the acceptance page is not "
        "backed by the artifacts in front of you, say so plainly and point at it: an "
        "honesty flaw is the one failure this repository treats as fatal.\n",
    ]
    bundle = "\n".join(parts)
    if len(bundle.encode()) > MAX_BUNDLE_BYTES:
        raise SystemExit(
            f"refusing: the bundle is {len(bundle.encode())} bytes, over the "
            f"{MAX_BUNDLE_BYTES} this builder allows (a Linux argument caps at 128 KB). "
            "Trim an excerpt range rather than letting the shell answer with exit=126."
        )
    sys.stdout.write(bundle)


if __name__ == "__main__":
    main()