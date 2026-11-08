"""The acceptance table in `hosted/M4-ACCEPTANCE.md` has to stay true.

The same reasoning as the M2 and M3 matrices, one milestone on. M4's own text is
"fix bugs found in M1-M3; add tests (backend + Playwright E2E for every page)",
and its first slice is the class of defect a real tool finds and neither the
width pass nor the theme pass looked for: what an assistive-technology user
gets. A table mapping "this slice claims" to "here is the code, the test and the
run" is what makes that checkable instead of assertable, and it rots the moment
a file moves or a check is renamed.

So this suite reads the page and checks it against the repository and against
the browser run it cites:

  * every backticked repository path in it exists,
  * every test file it names exists and is not empty,
  * every end-to-end check it names is a check in that run,
  * the run was taken over a clean working tree, at a commit that exists,
  * the code under the directories that run exercised has not changed since, and
  * the numbers the page states about the accessibility pass are the run's own:
    the controls walked, the pages and viewports covered, and the fact that no
    keyboard row was truncated by the harness rather than by the page.

The last one is why the slice exists. A page that says "every control on every
page is reachable" while the run it cites reached 120 of 150 on `/voices` is
exactly the kind of sentence this repository writes guards against.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE = REPO_ROOT / "hosted" / "M4-ACCEPTANCE.md"
#: The browser suite's own evidence file, in the fork's hermes-reports/ directory.
EVIDENCE_RE = re.compile(r"hermes-reports/(M4-slot\d+-web-e2e-r\d+)/result\.json")
ROW_RE = re.compile(r"^\|\s*(?P<cells>.+?)\s*\|\s*$", re.MULTILINE)
CELL_NAME_RE = re.compile(r"`([^`]+)`")
NO_RUN_EVIDENCE = "no run evidence"
HEADER_FIRST_CELL = "The slice claims"
#: What the browser run exercised. If one of these changes, its evidence is stale.
#: `hosted/web/e2e` is in the list because the harness is part of what the run is:
#: this slice's own finding was a bound inside that harness, so a run taken with a
#: different harness is evidence about something else.
MEASURED_SUBJECTS = (
    "hosted/control_plane",
    "hosted/worker",
    "hosted/scripts",
    "hosted/web/app",
    "hosted/web/components",
    "hosted/web/lib",
    "hosted/web/e2e",
)


def page_text() -> str:
    assert PAGE.is_file(), "the M4 acceptance page is gone"
    return PAGE.read_text()


def cited_run() -> Path:
    matches = EVIDENCE_RE.findall(page_text())
    assert len(set(matches)) == 1, (
        f"the page must cite exactly one run, it cites {sorted(set(matches))}"
    )
    path = REPO_ROOT / "hermes-reports" / matches[0] / "result.json"
    assert path.is_file(), f"the cited run does not exist: {path}"
    return path


def run_result() -> dict:
    return json.loads(cited_run().read_text())


def rows() -> list[list[str]]:
    """The table's body rows, header and separator dropped."""
    text = page_text()
    assert f"| {HEADER_FIRST_CELL} |" in text, "the acceptance table lost its header"
    out = []
    for match in ROW_RE.finditer(text):
        cells = [cell.strip() for cell in match.group("cells").split("|")]
        if len(cells) != 4 or set(cells[0]) <= set("-: "):
            continue
        if cells[0] == HEADER_FIRST_CELL:
            continue
        out.append(cells)
    return out


def backticked(cell: str) -> list[str]:
    return CELL_NAME_RE.findall(cell)


def test_the_table_has_rows_and_each_names_code_tests_and_evidence():
    table = rows()
    assert len(table) >= 8, f"the table shrank to {len(table)} rows"
    for cells in table:
        assert backticked(cells[1]), f"no code path in: {cells}"
        assert backticked(cells[2]), f"no test path in: {cells}"
        assert cells[3], f"no evidence cell in: {cells}"


def test_every_path_the_table_names_exists():
    missing = []
    for cells in rows():
        for cell in cells[1:3]:
            for path_text in backticked(cell):
                if not (REPO_ROOT / path_text).exists():
                    missing.append(f"{cells[0]!r} names {path_text}")
    assert missing == [], f"rows that name something which is not there: {missing}"


def test_every_test_file_the_table_names_has_content():
    empty = []
    for cells in rows():
        for path_text in backticked(cells[2]):
            candidate = REPO_ROOT / path_text
            if candidate.is_file() and candidate.stat().st_size == 0:
                empty.append(path_text)
    assert empty == [], f"test files named but empty: {empty}"


def test_every_check_the_table_names_is_in_the_run_it_cites():
    names = {check["name"] for check in run_result()["checks"]}
    missing = []
    for cells in rows():
        evidence = cells[3]
        if evidence.startswith(NO_RUN_EVIDENCE):
            continue
        expected_names = backticked(evidence) or [evidence]
        for expected in expected_names:
            if expected not in names:
                missing.append(expected)
    assert missing == [], f"checks named by the table that the run does not record: {missing}"


def test_the_cited_run_passed_and_was_taken_over_a_clean_tree():
    result = run_result()
    assert result["passed"] == len(result["checks"]), "the cited run has failing checks"
    not_ok = [check["name"] for check in result["checks"] if not check.get("ok")]
    assert not_ok == [], f"the cited run records these checks as not ok: {not_ok}"
    assert result["revision"]["worktree_dirty"] is False, (
        "the cited run was taken over a dirty tree, so it is not evidence about any "
        "revision"
    )
    assert result["revision"]["tested_commit"], "the cited run names no commit"


def test_the_code_the_cited_run_exercised_has_not_changed_since():
    commit = run_result()["revision"]["tested_commit"]
    changed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--name-only", commit, "HEAD", "--",
         *MEASURED_SUBJECTS],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert changed == "", (
        "the page cites a run taken before these files changed, so it describes a "
        "revision that is no longer here; re-run the browser suite:\n" + changed
    )
    dirty = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", *MEASURED_SUBJECTS],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert dirty == "", (
        "a measured directory has uncommitted changes, so the cited run no longer "
        "describes this working tree; commit them and re-run the browser suite:\n" + dirty
    )


def test_the_keyboard_claim_is_the_run_s_own_counts():
    """Every control, on every page, with the walk's own bound not in the way.

    This is the row this slice was opened for. The run before it recorded 120 of
    150 controls reached on `/voices`, because the harness stopped after a fixed
    120 Tab presses; the page may only claim what the run in front of it shows,
     so the numbers are read out of the run rather than restated from memory.
    """
    result = run_result()
    keyboard = result["a11y"]["keyboard"]
    assert keyboard, "the cited run records no keyboard walk at all"
    for row in keyboard:
        assert row["controls"] > 0, f"{row['route']} had no controls to walk"
        assert row["truncated"] is False, (
            f"the walk on {row['route']} stopped at the harness's own bound of "
            f"{row.get('max_tab_presses')} presses, so the page cannot claim the "
            "page was tabbed through"
        )
        assert row["reached"] == row["controls"], (
            f"{row['route']}: the run reached {row['reached']} of {row['controls']} "
            f"controls, and this page must not claim otherwise: {row['missed']}"
        )
    total = result["a11y"]["keyboard_controls"]
    assert total == sum(row["controls"] for row in keyboard)
    # The page is wrapped prose, so a sentence can sit across a line break; the
    # numbers are what is being read, not the layout.
    text = " ".join(page_text().split())
    assert f"{total} controls" in text, (
        f"the page does not state the {total} controls the run walked"
    )


def test_the_axe_claim_is_the_run_s_own_counts():
    """No violations, over all nine pages, at three viewports, in both themes.

    A page that says "axe found nothing" while the run it cites holds violations
    is the one sentence that would make this whole slice worthless, so the count,
    the rule-set floor and the coverage are read back out of the run.
    """
    result = run_result()
    a11y = result["a11y"]
    assert a11y["violations_total"] == 0, (
        f"the cited run holds {a11y['violations_total']} axe violations"
    )
    assert a11y["rules_run_min"] >= 20, (
        "a run whose thinnest pass ran fewer than 20 rules cannot support 'no "
        "violations': " + str(a11y["rules_run_min"])
    )
    assert a11y["tags"] == ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]
    runs = a11y["runs"]
    assert a11y["pages"] == 9 and len({run["route"] for run in runs}) == 9, (
        "the run no longer covers all nine pages"
    )
    assert {run["theme"] for run in runs} == {"dark", "light"}
    assert {run["viewport"] for run in runs} == {"phone", "tablet", "desktop"}
    # Every combination of theme, viewport and page, rather than a total that a
    # run could reach by covering one theme thinly: an earlier version of this
    # section ran the light theme at desktop width only (36 runs, 27 dark against
    # 9 light) while the page claimed both themes at all three viewports, and a
    # cheap+ pass on this slice called that a lie. The count and the set are both
    # asserted now, so a theme that quietly narrows fails here.
    combinations = {(run["theme"], run["viewport"], run["route"]) for run in runs}
    assert len(combinations) == len(runs) == 54, (
        f"the run covered {len(runs)} passes and {len(combinations)} distinct "
        "theme/viewport/page combinations, not 54 (2 themes x 3 viewports x 9 pages): "
        f"{sorted(combinations)}"
    )
    text = page_text()
    for stated in ("axe-core", "4.14.0", "MPL-2.0"):
        assert stated in text, f"the page never names {stated}"

#: The run taken before this slice's fixes. It is the only artifact here that holds
#: axe violations at all — the cited runs hold none, which is the point of the slice
#: — so it is where D2's claim that the suite records each violation with its rule
#: id, impact, node count and first target can be checked rather than taken on trust.
DEFECT_RECORD = REPO_ROOT / "hermes-reports" / "M4-slot89-web-e2e-stub-before" / "result.json"


def test_the_violation_record_the_page_describes_is_in_the_defect_record():
    """D2 says what the suite writes down about a violation. Show the schema.

    A cheap+ pass on this slice was right that the bundle proved the *absence* of
    violations and nothing about their shape: a run with zero violations cannot
    demonstrate that a violation would have been recorded with its rule id, its
    impact, its node count and its first target. The defect record can, because it
    was taken before the fixes and holds 64 of them, so the four rule ids the page
    names and the four fields it promises are read out of that file here.
    """
    assert DEFECT_RECORD.is_file(), f"the defect record is gone: {DEFECT_RECORD}"
    data = json.loads(DEFECT_RECORD.read_text())
    recorded = [v for run in data["a11y"]["runs"] for v in (run.get("violations") or [])]
    assert recorded, "the defect record holds no violations, so this page's claim is uncheckable"
    for violation in recorded:
        assert violation.get("id"), f"a recorded violation without a rule id: {violation}"
        assert violation.get("impact"), f"a recorded violation without an impact: {violation}"
        assert isinstance(violation.get("nodes"), int) and violation["nodes"] > 0, (
            f"a recorded violation with no node count: {violation}"
        )
        assert violation.get("targets"), f"a recorded violation with no first target: {violation}"
    text = page_text()
    for rule_id in sorted({v["id"] for v in recorded}):
        assert rule_id in text, (
            f"the defect record holds {rule_id} and the page never names it, so a "
            "reader cannot check the rule id this page claims was fixed"
        )
