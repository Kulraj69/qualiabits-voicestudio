"""The width rules that make the dashboard responsive have to stay in place.

M3's brief asks for a dashboard that is "responsive, fast, dark/light". The
dark/light half has this file's sibling (`test_theme_tokens.py`) and the light
palette is declared twice so the two copies cannot drift. The *responsive* half
had nothing at all until slice H: `hosted/web/app/globals.css` held exactly one
`@media` rule and it was `prefers-color-scheme` — no width breakpoint anywhere in
983 lines — and the browser harness walked the nine pages at one viewport.

The browser run is the real evidence for the width (it measures what a reader
sees, at three widths, element by element). This file is the cheap guard that
runs without a browser: it parses the stylesheet and fails if the breakpoints
that the run depends on are removed, reordered, or made to break the promise the
theme half relies on — that a width rule never re-colours anything, because a
token declared for one theme has to be declared for the other.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "hosted" / "web"
CSS = WEB / "app" / "globals.css"
HARNESS = WEB / "e2e" / "dashboard.mjs"

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
MEDIA_WIDTH_RE = re.compile(r"@media\s*\(\s*max-width:\s*(\d+)px\s*\)")
DECL_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;{}]+);")
#: The widths the browser suite drives, in the order it drives them.
VIEWPORTS = ((390, 844), (768, 1024), (1440, 900))


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


def css() -> str:
    return strip_comments(CSS.read_text(encoding="utf-8"))


def block_after(text: str, marker: str) -> str:
    """The body of the first `{...}` block whose text starts at `marker`."""
    start = text.index(marker)
    open_brace = text.index("{", start)
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : i]
    raise AssertionError(f"unbalanced braces after {marker!r}")


def width_blocks() -> list[tuple[int, str]]:
    """Every `@media (max-width: Npx)` block, in file order, with its width."""
    source = css()
    out = []
    for match in MEDIA_WIDTH_RE.finditer(source):
        out.append((int(match.group(1)), block_after(source, match.group(0))))
    return out


def block_in(text: str, marker: str) -> str:
    """`block_after`, but a missing selector is a sentence, not a traceback.

    A rule that has been deleted is the failure this file is looking for, and it
    should read as one: `ValueError: substring not found` names neither the rule
    nor the width block it was in.
    """
    assert marker in text, f"this width block no longer contains {marker!r}"
    return block_after(text, marker)


def declarations(block: str) -> dict[str, str]:
    return {name: " ".join(value.split()) for name, value in DECL_RE.findall(block)}


def test_the_stylesheet_declares_width_breakpoints_in_descending_order():
    blocks = width_blocks()
    widths = [width for width, _ in blocks]
    assert len(widths) >= 2, (
        f"the dashboard is meant to be responsive, and this file declares {widths} "
        "width breakpoints; the browser run's width checks need them"
    )
    assert widths == sorted(widths, reverse=True), (
        f"width breakpoints must be written widest-first so a narrower rule can "
        f"override a wider one; found {widths}"
    )


def test_no_width_block_declares_a_theme_token():
    """A breakpoint may move things; it may not re-colour them.

    The two light blocks are checked against the dark block by
    `test_theme_tokens.py`. A width block that declared a token would be a third
    copy of the palette that guard knows nothing about — exactly the drift it
    exists to catch — so there is a rule instead: width rules are layout only.
    """
    offenders = {
        width: declarations(block)
        for width, block in width_blocks()
        if declarations(block)
    }
    assert not offenders, (
        f"these width blocks declare custom properties: {offenders}. "
        "A breakpoint is layout; colours belong in the theme blocks."
    )


def test_the_top_bar_wraps_into_two_rows_at_the_narrower_widths():
    """The defect slice H fixed: a bar whose items cannot shrink below content.

    A flex row whose children keep `min-width: auto` cannot get narrower than the
    sum of their content, so at 390px the bar forced the document to 560px and the
    session controls sat off the right edge. The rules that prevent it are named
    here, because deleting one of them would put it back and only a browser run
    would notice.
    """
    blocks = dict(width_blocks())
    wide = blocks.get(900, "")
    assert ".topbar" in wide, "the 900px block no longer mentions the top bar"
    topbar = block_in(wide, ".topbar {")
    assert "flex-wrap: wrap" in topbar, (
        "the top bar must wrap below 900px, or its children force the page wider "
        "than the window"
    )
    nav = block_in(wide, ".nav {")
    assert "width: 100%" in nav, (
        "the nav must take its own full-width row, rather than shattering into "
        "three lines beside the brand"
    )
    assert "order: 3" in nav, "the nav's own row has to come after the session controls"
    toggle = block_in(wide, ".theme-toggle {")
    assert "flex: 0 0 auto" in toggle, (
        "the theme switch is a hit target: it must not be squeezed below its size"
    )


def test_the_phone_block_lets_the_page_padding_and_the_session_shrink():
    blocks = dict(width_blocks())
    phone = blocks.get(640, "")
    assert phone, "there is no phone-width block"
    assert "min-width: 0" in block_in(phone, ".who {"), (
        "the session control has to be allowed to shrink, or it sets the page width"
    )
    padding = block_in(phone, ".main {")
    assert "var(--space-4)" in padding, (
        "the phone keeps the tablet's 24px side padding; it should tighten once more"
    )


def test_the_phone_block_drops_the_session_name_so_the_bar_keeps_two_rows():
    """The rule that the run's own row count asked for.

    At 390px the bar's first row holds the brand, the theme switch and the session
    button in the page's width minus its padding. A name beside the button does not
    fit: capped at 32vw it still pushed the button onto a line of its own, and the
    bar measured three rows on all nine pages — which nothing noticed until the
    harness stopped publishing its own count as `undefined`. The name is dropped on
    a phone (the home page's heading carries it) and this guard keeps it dropped,
    because a browser run is the only other thing that would notice it coming back.
    """
    phone = dict(width_blocks()).get(640, "")
    assert phone, "there is no phone-width block"
    block = block_in(phone, ".who__email {")
    assert "display: none" in block, (
        "the signed-in name is back in the phone bar, which puts the session button "
        "on a third row"
    )


def test_the_browser_suite_drives_the_three_widths_it_claims():
    """The stylesheet's rules are only evidence if a run measures them."""
    harness = HARNESS.read_text(encoding="utf-8")
    for width, height in VIEWPORTS:
        assert f"width: {width}" in harness and f"height: {height}" in harness, (
            f"the harness no longer drives a {width}x{height} viewport, so nothing "
            "measures the width rules above"
        )
    for phrase in ("no page scrolls sideways", "no element is wider than the window"):
        assert phrase in harness, f"the width checks lost their name: {phrase!r}"


def test_the_harness_reports_the_bar_s_row_count_as_a_number():
    """A check that reads a property of a number can never fail.

    The harness counts the top bar's rows into an integer (`let barRows = 0`, then
    `barRows += 1` per row), and the measurement it publishes was
    `topbar_rows: barRows.length` — `undefined`. The check that consumes it,
    "the top bar lays out in at most two rows, nav included", therefore compared
    `undefined > 2`, which is false for every page, so half of that check's own
    claim could not fail and its detail printed `undefined bar row(s)` beside real
    numbers. Nothing caught it because no test reads the value back, and no reader
    would notice `undefined` in a detail string. This guard is that reader.
    """
    harness = HARNESS.read_text(encoding="utf-8")
    assert "let barRows = 0;" in harness, (
        "the harness no longer counts the top bar's rows into an integer"
    )
    assert "topbar_rows: barRows," in harness, (
        "the published row count is not the integer the harness counted; a property "
        "read off a number is `undefined`, and a check against it cannot fail"
    )
    assert "topbar_rows: barRows.length" not in harness, (
        "the row count is read as a property of a number again, so the check that "
        "consumes it compares `undefined`"
    )
    assert "p.topbar_rows > 2" in harness, (
        "the check that consumes the row count is gone, so the count is published "
        "for nobody"
    )