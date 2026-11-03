"""The light theme's two blocks have to agree, and every colour has to be a token.

M3's brief asks for a dashboard that is "responsive, fast, dark/light". The light
palette is declared twice in `hosted/web/app/globals.css`: once under
`:root[data-theme="light"]`, which the inline script in `app/layout.jsx` triggers,
and once inside `@media (prefers-color-scheme: light)` for a reader whose browser
runs no script at all. Two copies of the same values is exactly the kind of thing
that drifts — one token corrected in the block a test happened to open, and the
no-script reader quietly gets a page that is half light and half dark.

So the duplication is checked rather than trusted:

  * the two light blocks must declare the same tokens with the same values,
  * every dark token that carries a colour must be redeclared in both of them,
  * and no colour literal may appear anywhere else in the file — a component that
    hard-codes `#fff` is a colour the light theme cannot reach.

The rest of the file is checked for the things this slice claims: the no-flash
script is rendered by the layout, the toggle exists, is reachable from the header,
names the theme it switches to, and the card grid no longer leaves the hole a
judge pass on slice D pointed at.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "hosted" / "web"
CSS = WEB / "app" / "globals.css"
LAYOUT = WEB / "app" / "layout.jsx"
HEADER = WEB / "components" / "SiteHeader.jsx"
TOGGLE = WEB / "components" / "ThemeToggle.jsx"
THEME_MODULE = WEB / "lib" / "theme.js"

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
#: A declaration inside a rule block. Values are kept verbatim, whitespace folded.
DECL_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;{}]+);")
COLOUR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
#: `color-mix()` is allowed outside the token blocks: it composes tokens rather
#: than naming a colour, and any literal inside it is still caught by COLOUR_RE.


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


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


def declarations(block: str) -> dict[str, str]:
    return {name: " ".join(value.split()) for name, value in DECL_RE.findall(block)}


def css() -> str:
    return strip_comments(CSS.read_text(encoding="utf-8"))


def dark_tokens() -> dict[str, str]:
    return declarations(block_after(css(), ":root {"))


def light_attribute_tokens() -> dict[str, str]:
    return declarations(block_after(css(), ':root[data-theme="light"]'))


def light_media_tokens() -> dict[str, str]:
    media = block_after(css(), "@media (prefers-color-scheme: light)")
    return declarations(block_after(media, ":root:not([data-theme])"))


def coloured(tokens: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in tokens.items() if COLOUR_RE.search(value)}


def test_the_two_light_blocks_declare_the_same_tokens_with_the_same_values():
    attribute = light_attribute_tokens()
    media = light_media_tokens()
    assert attribute, "the data-theme light block declares no tokens"
    assert set(attribute) == set(media), (
        "the two light blocks declare different tokens: "
        f"only under data-theme: {sorted(set(attribute) - set(media))}; "
        f"only under the media query: {sorted(set(media) - set(attribute))}"
    )
    differing = {n: (attribute[n], media[n]) for n in attribute if attribute[n] != media[n]}
    assert not differing, f"the two light blocks disagree: {differing}"


def test_every_dark_token_that_carries_a_colour_is_redeclared_for_light():
    dark = coloured(dark_tokens())
    assert dark, "no colour-carrying tokens found in the dark block: has the parse broken?"
    for name, value in dark.items():
        for label, block in (
            ('data-theme="light"', light_attribute_tokens()),
            ("prefers-color-scheme: light", light_media_tokens()),
        ):
            assert name in block, (
                f"{name} is {value!r} in the dark block but is not redeclared under {label}, "
                "so a reader in that path would keep a dark value on a light page"
            )


def test_no_colour_literal_lives_outside_the_three_token_blocks():
    """Every colour on the site is a token, so a theme can reach all of them."""
    source = css()
    # Cut the three token blocks out and see what colours are left.
    remaining = source
    for marker in (
        ":root {",
        ':root[data-theme="light"]',
        "@media (prefers-color-scheme: light)",
    ):
        body = block_after(source, marker)
        remaining = remaining.replace(body, " ", 1)
    strays = COLOUR_RE.findall(remaining)
    line_numbers = [
        n
        for n, line in enumerate(remaining.splitlines(), start=1)
        if COLOUR_RE.search(line)
    ]
    assert not strays, (
        "colour literals outside the token blocks "
        f"({len(strays)} of them, near lines {line_numbers[:10]}). "
        "Declare them as tokens so both themes can set them."
    )


def test_each_theme_states_its_own_color_scheme():
    """`color-scheme` is what makes form controls and scrollbars match."""
    for label, block in (
        (":root", block_after(css(), ":root {")),
        ('data-theme="light"', block_after(css(), ':root[data-theme="light"]')),
        ("prefers-color-scheme: light", block_after(css(), "@media (prefers-color-scheme: light)")),
    ):
        assert "color-scheme:" in block, f"{label} does not state a color-scheme"


def test_the_markup_applies_the_theme_before_the_first_paint():
    """The script has to be rendered by the layout, not by a component lower down."""
    layout = LAYOUT.read_text(encoding="utf-8")
    assert "NO_FLASH_SCRIPT" in layout, "the layout does not render the no-flash script"
    assert "suppressHydrationWarning" in layout, (
        "the script writes data-theme onto <html> before hydration, so React is told to expect it"
    )
    body = layout[layout.index("<body>") :]
    script_at = body.index("NO_FLASH_SCRIPT")
    shell_at = body.index('className="shell"')
    assert script_at < shell_at, "the script must come before the page's own markup"


def test_the_toggle_is_in_the_header_and_says_what_it_does():
    header = HEADER.read_text(encoding="utf-8")
    assert "ThemeToggle" in header and "<ThemeToggle />" in header, (
        "the toggle is not rendered in the top bar"
    )
    toggle = TOGGLE.read_text(encoding="utf-8")
    assert '"use client"' in toggle, "the toggle handles a click, so it is a client component"
    assert "themeToggleLabel" in toggle, (
        "the toggle's accessible name has to come from the shared rule, not a literal"
    )
    assert "aria-label=" in toggle, "an icon-only button needs an accessible name"
    assert "THEME_STORAGE_KEY" in toggle, "the toggle must write the key the script reads"
    module = THEME_MODULE.read_text(encoding="utf-8")
    assert "export const NO_FLASH_SCRIPT" in module, (
        "the script and the rules it implements belong in one module"
    )


def test_the_card_grid_fills_its_trailing_row():
    """Slot 69 deferred this: seven tiles in three columns left two gaps."""
    block = block_after(css(), ".grid--cards {")
    assert "grid-template-columns" not in block, (
        "fixed columns are what leaves the hole; this is a wrapping row now"
    )
    assert "flex-wrap: wrap" in block, "the card row has to wrap"
    children = block_after(css(), ".grid--cards > * {")
    assert "flex:" in children and "280px" in children, (
        "cards grow from a 280px basis so the last row is filled"
    )