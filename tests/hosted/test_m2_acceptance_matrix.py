"""The acceptance table in `hosted/M2-ACCEPTANCE.md` has to stay true.

A table mapping "the plan asked for this" to "here is the code, the tests and the
run" is the artifact that makes criteria fit checkable instead of assertable, but it
rots the moment a file moves or a check is renamed — and a rotted table is worse than
none, because it reads like evidence. This suite reads the table and checks it
against the repository:

  * every backticked repository path in it exists,
  * every `tests/...` file it names exists and is not empty,
  * every end-to-end check it names is one of the checks in the run file the front
    page cites, and every non-check string appears in that run file.

Finding the run file is done by reading `hosted/README.md`, so the table, the page
and the run cannot drift apart without one of them failing here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX = REPO_ROOT / "hosted" / "M2-ACCEPTANCE.md"
README = REPO_ROOT / "hosted" / "README.md"
EVIDENCE_RE = re.compile(r"hermes-reports/(M2-slot\d+-e2e-r\d+)/result\.json")
ROW_RE = re.compile(r"^\|\s*(?P<cells>.+?)\s*\|\s*$", re.MULTILINE)


def table_rows() -> list[list[str]]:
    """The markdown table's rows, header and separator dropped."""
    text = MATRIX.read_text()
    assert "| The plan asks for |" in text, "the acceptance table lost its header"
    rows = [
        [cell.strip() for cell in match.group("cells").split("|")]
        for match in ROW_RE.finditer(text)
    ]
    rows = [row for row in rows if len(row) == 4 and not set(row[0]) <= set("-: ")]
    body = [row for row in rows if row[0] != "The plan asks for"]
    assert body, "the acceptance table has no rows"
    return body


def cited_run() -> dict:
    """The run file the front page quotes, read the same way the page's test does."""
    match = EVIDENCE_RE.search(README.read_text())
    assert match is not None, "hosted/README.md names no run file"
    path = REPO_ROOT / "hermes-reports" / match.group(1) / "result.json"
    assert path.is_file(), f"the page cites {path}, which does not exist"
    return json.loads(path.read_text())


def backticked(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def test_every_path_the_table_names_exists():
    missing: list[str] = []
    for row in table_rows():
        for cell in row[1:3]:  # code, tests
            for name in backticked(cell):
                if not (REPO_ROOT / name).exists():
                    missing.append(f"{row[0]!r} names {name}")
    assert missing == [], f"{len(missing)} row(s) name something that is not there: {missing}"


def test_every_test_file_the_table_names_has_tests_in_it():
    thin: list[str] = []
    for row in table_rows():
        for name in backticked(row[2]):
            if not name.startswith("tests/"):
                continue
            text = (REPO_ROOT / name).read_text()
            if not re.search(r"^def test_", text, re.MULTILINE):
                thin.append(f"{name} (named by {row[0]!r}) contains no test functions")
    assert thin == [], "the table cites files that test nothing: " + "; ".join(thin)


def test_every_run_reference_is_really_in_the_cited_run():
    run = cited_run()
    checks = set(run["checks"])
    text = json.dumps(run)
    wrong: list[str] = []
    for row in table_rows():
        named = backticked(row[3])
        assert named, f"the row {row[0]!r} names nothing in the run"
        for name in named:
            if name in checks:
                continue
            if name in text:
                continue
            wrong.append(f"{row[0]!r} cites {name}, which is neither a check nor in the run file")
    assert wrong == [], (
        "the acceptance table points at run evidence the quoted run does not have: "
        + "; ".join(wrong)
    )


def test_the_run_the_table_is_checked_against_is_the_page_s_run():
    """A table checked against a different run than the page quotes would be a trap."""
    run = cited_run()
    assert run["passed"] is True
    assert run["revision"]["worktree_dirty"] is False
    named = {name for row in table_rows() for name in backticked(row[3])}
    assert named & set(run["checks"]), (
        "no row of the acceptance table names a check of the quoted run, so the table "
        "and the run are connected by nothing"
    )
    # The two fields the table quotes as prose must be the ones the run recorded.
    assert any("schema_version" in name for name in named), (
        "the table no longer checks the schema version the run reports"
    )
    assert run["control_plane_health"]["schema_version"] == "m2b1"