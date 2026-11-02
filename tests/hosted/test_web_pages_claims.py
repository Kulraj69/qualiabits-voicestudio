"""The dashboard's own numbers, and one defect a judge found in the library.

Two small guards, both about claims that used to live only in prose.

**The counts.** `hosted/web/README.md` says how many checks each browser path runs,
and those counts move every time a check is added — which is exactly what happened
when slice C added the voice library's own checks. The numbers were a sentence a
reader had to take on trust, and a judge pass on this slice was right to flag them:
the page said sixty-eight while the real-engine run it sat next to recorded
seventy-five. They are read back out of the two committed runs here, including the
seven checks the real path adds and the one it drops, so a count that drifts fails
a test instead of shipping.

**The preview's object URL.** The library builds a blob URL per preview; the first
version never revoked the previous one, so auditioning voice after voice retained
one audio blob per press for the life of the page. That is a source-level claim
(the component is a client component and this suite runs no browser), so it is
checked at the source: one URL is live at a time, released before a new preview is
built and on unmount.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_README = REPO_ROOT / "hosted" / "web" / "README.md"
LIBRARY = REPO_ROOT / "hosted" / "web" / "components" / "VoiceLibraryView.jsx"
RUN_RE = re.compile(r"hermes-reports/(M3-slot\d+-web-e2e(?:-stub)?-r\d+)/result\.json")

#: The page spells its small numbers out, so they are read back the same way.
WORD_NUMBERS = {
    "one": 1,
    "six": 6,
    "seven": 7,
    "sixty-eight": 68,
    "sixty-nine": 69,
    "seventy-four": 74,
    "seventy-five": 75,
}

SCREENSHOT_CHECK = "every page was captured as a screenshot"


def readme() -> str:
    return WEB_README.read_text()


def cited_runs() -> dict[str, dict]:
    """The runs the page names, keyed by directory name."""
    out = {}
    for name in RUN_RE.findall(readme()):
        path = REPO_ROOT / "hermes-reports" / name / "result.json"
        assert path.is_file(), f"the page cites {name}, which is not in the repository"
        out[name] = json.loads(path.read_text())
    return out


def counted(result: dict) -> tuple[list[str], int, int]:
    """(check names, checks with --keep, checks without it)."""
    names = [check["name"] for check in result["checks"]]
    with_keep = len(names)
    without_keep = with_keep - 1 if SCREENSHOT_CHECK in names else with_keep
    assert result["passed"] == with_keep, "a cited run has failing checks"
    return names, with_keep, without_keep


def test_the_page_names_two_runs_one_per_path():
    cited = cited_runs()
    assert len(cited) == 2, f"the page names {sorted(cited)}"
    stub = [name for name in cited if "-stub-" in name]
    real = [name for name in cited if "-stub-" not in name]
    assert len(stub) == 1 and len(real) == 1, sorted(cited)


def test_the_stated_counts_are_the_counts_in_the_two_runs():
    text = readme()
    cited = cited_runs()
    stub = cited[[name for name in cited if "-stub-" in name][0]]
    real = cited[[name for name in cited if "-stub-" not in name][0]]

    _, stub_keep, stub_without = counted(stub)
    _, real_keep, real_without = counted(real)
    assert stub["engine_is_stub"] is True and real["engine_is_stub"] is False, (
        "the run the page calls the stub path is not the stub path"
    )

    for word, expected in (
        ("sixty-eight", stub_without),
        ("sixty-nine", stub_keep),
        ("seventy-four", real_without),
        ("seventy-five", real_keep),
    ):
        assert word in text, f"the page no longer states {word!r}"
        assert WORD_NUMBERS[word] == expected, (
            f"the page says {word!r} ({WORD_NUMBERS[word]}) and the run records {expected}"
        )
    assert "sixty-eight checks on the stub path" in text
    assert "seventy-four against the real engine" in text


def test_the_stated_difference_is_the_real_difference_between_the_two_runs():
    text = readme()
    cited = cited_runs()
    stub = cited[[name for name in cited if "-stub-" in name][0]]
    real = cited[[name for name in cited if "-stub-" not in name][0]]

    stub_names, _, _ = counted(stub)
    real_names, _, _ = counted(real)
    added = set(real_names) - set(stub_names)
    dropped = set(stub_names) - set(real_names)

    assert "seven checks a constant-size stub cannot pass" in text
    assert "drops the one check that only a stub can satisfy" in text
    assert len(added) == 7, f"the real path adds {len(added)} checks, not seven: {sorted(added)}"
    assert len(dropped) == 1, f"the real path drops {len(dropped)}, not one: {sorted(dropped)}"
    assert "so the\ntwo totals differ by six" in text, "the page no longer says how the totals differ"


def test_the_library_releases_a_preview_when_it_replaces_one():
    source = LIBRARY.read_text()
    assert "URL.revokeObjectURL" in source, (
        "the library never revokes a preview's object URL, so auditioning leaks a blob per press"
    )
    assert "useRef" in source and "previewUrl.current" in source, (
        "there is no single live preview URL being tracked"
    )
    assert "useEffect(() => releasePreview, [releasePreview])" in source, (
        "the live preview URL is not released on unmount"
    )
    assert source.index("releasePreview();\n    setResult(null);") < source.index(
        "const url = URL.createObjectURL(blob)"
    ), "the previous URL is not released before the next one is built"


def test_the_kind_filter_says_why_it_is_a_native_control():
    source = LIBRARY.read_text()
    assert "A native control, on purpose" in source, (
        "the kind filter is a bare native select again, with nothing to explain it"
    )
    # And the reason has to be the real one: this package imports nothing outside
    # itself, so the fork's styled searchable select (an Electron component) is not
    # available here.
    assert "imports nothing outside" in source
    assert (REPO_ROOT / "electron" / "src" / "shared" / "components" / "SearchableSelect.jsx").is_file(), (
        "the component that comment points at is gone; the comment is stale"
    )