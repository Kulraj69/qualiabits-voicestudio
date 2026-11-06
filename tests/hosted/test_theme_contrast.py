"""The contrast the dashboard claims, computed out of the stylesheet itself.

WCAG 2.1's relative-luminance formula and its 4.5:1 bar for normal-size text,
applied to the tokens `app/globals.css` declares — both themes, and the pairs a
reader really reads text on. The browser pass (`e2e/dashboard.mjs`, axe-core)
measures the same thing on the rendered pixels, which is the real evidence; this
is the guard that runs in a fifth of a second, names the token that fails and the
surface it failed on, and cannot be satisfied by a page that happens not to use
the token it is checking.

This file exists because the dashboard shipped with the defect it now checks:
dark `--fg-subtle` was `#6f6b8c`, which is 3.95:1 on `--bg`, 3.75:1 on
`--bg-elev` and 3.53:1 on `--bg-elev-2`, and it is the colour of field labels,
hints, table headers and the disabled select — normal-size text, in the theme the
dashboard ships as its default. Dark `--accent-ink` was white on `--accent`,
3.86:1, and on the hover 3.09:1, which is the label of every primary button.

Two kinds of pair are measured. What is opaque is straightforward. What is
translucent (`--surface`, `--ok-soft`, `--err-soft` are `rgba()`) is composited
over the surfaces a notice can sit on — `--bg` and `--bg-elev` — and the *worse*
of the two is the number asserted, because a notice inside a card and a notice on
the page are both real and the stylesheet does not say which one is coming.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "hosted" / "web" / "app" / "globals.css"

COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
DECL_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;{}]+);")
HEX_RE = re.compile(r"^#([0-9a-fA-F]{6})$")
RGBA_RE = re.compile(
    r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)$"
)
#: WCAG AA for normal-size text. Nothing in this dashboard sets text large enough
#: for the 3:1 large-text exemption (the biggest body copy is 1.05rem).
AA_NORMAL = 4.5

#: Foreground token -> the surface token it is used on. Read off the rules that
#: use them (`th`, `.field__label`, `.field__hint`, `.stat__note`, `.btn`,
#: `.linkish`, `a`, the badges), not from a list of pairs that looks thorough.
TEXT_PAIRS = (
    ("--fg", "--bg"),
    ("--fg", "--bg-elev"),
    ("--fg", "--bg-elev-2"),
    ("--fg-muted", "--bg"),
    ("--fg-muted", "--bg-elev"),
    ("--fg-subtle", "--bg"),
    ("--fg-subtle", "--bg-elev"),
    ("--fg-subtle", "--bg-elev-2"),
    ("--accent", "--bg"),
    ("--accent-hover", "--bg"),
    ("--accent-ink", "--accent"),
    ("--accent-ink", "--accent-hover"),
    ("--ok", "--bg"),
    ("--warn", "--bg"),
    ("--err", "--bg"),
)
#: (foreground, translucent background, surface underneath). Both surfaces are
#: asserted: a notice or a badge can be inside a card or on the page. Every entry
#: is a pair the stylesheet really paints — the notices (`.notice--ok`,
#: `.notice--error`), the badges (`.badge--*`, six of them), the current-page nav
#: link and `::selection`. The first draft of this list had `--accent-ink` on
#: `--accent-soft` and it failed at 1.21:1, which is right twice over: nothing
#: paints that pair (the ink goes on the *solid* accent), and if anything did it
#: would be unreadable.
COMPOSITED_PAIRS = (
    ("--ok-ink", "--ok-soft", "--bg"),
    ("--ok-ink", "--ok-soft", "--bg-elev"),
    ("--err-ink", "--err-soft", "--bg"),
    ("--err-ink", "--err-soft", "--bg-elev"),
    ("--fg-muted", "--surface", "--bg"),
    ("--fg-muted", "--surface", "--bg-elev"),
    ("--ok", "--ok-soft", "--bg"),
    ("--ok", "--ok-soft", "--bg-elev"),
    ("--err", "--err-soft", "--bg"),
    ("--err", "--err-soft", "--bg-elev"),
    ("--warn", "--warn-soft", "--bg"),
    ("--warn", "--warn-soft", "--bg-elev"),
    ("--accent-hover", "--accent-soft", "--bg"),
    ("--accent-hover", "--accent-soft", "--bg-elev"),
    ("--fg", "--accent-soft", "--bg"),
    ("--fg", "--accent-soft", "--bg-elev"),
)
#: Tokens that carry a colour and are deliberately not in a contrast pair, each
#: with the reason. The coverage test below fails on a new colour token that is in
#: neither list, so a token cannot be added without deciding this question.
EXEMPT = {
    "--surface": "a translucent fill; it is a background in COMPOSITED_PAIRS",
    "--surface-hover": "a hover fill, never a text background",
    "--border": "a 1px line, not text",
    "--border-strong": "a 1px line, not text",
    "--accent-soft": "a translucent fill; it is the primary button's ring",
    "--accent-line": "a 1px line, not text",
    "--ok-soft": "a translucent fill; it is a background in COMPOSITED_PAIRS",
    "--ok-line": "a 1px line, not text",
    "--warn-soft": "a translucent fill, only ever behind --warn in a badge",
    "--warn-line": "a 1px line, not text",
    "--err-soft": "a translucent fill; it is a background in COMPOSITED_PAIRS",
    "--err-line": "a 1px line, not text",
    "--shadow-card": "a box-shadow, not text",
    "--page-glow": "a background gradient, not text",
}

DARK_BLOCK = ":root {"
LIGHT_BLOCK = ':root[data-theme="light"] {'
#: The no-script copy of the light palette, inside `@media (prefers-color-scheme: light)`.
LIGHT_NO_SCRIPT_BLOCK = ":root:not([data-theme]) {"


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


def tokens(block: str) -> dict[str, str]:
    """`--token: value` for one rule block, whitespace folded, comments gone."""
    body = strip_comments(block)
    return {name: " ".join(value.split()) for name, value in DECL_RE.findall(body)}


def theme_tokens() -> dict[str, dict[str, str]]:
    """The three token blocks that matter: dark, light, light without a script."""
    source = CSS.read_text(encoding="utf-8")
    return {
        "dark": tokens(block_after(source, DARK_BLOCK)),
        "light": tokens(block_after(source, LIGHT_BLOCK)),
        "light (no script)": tokens(block_after(source, LIGHT_NO_SCRIPT_BLOCK)),
    }


def channels(value: str) -> tuple[float, float, float, float]:
    """`#rrggbb` or `rgba(r, g, b, a)` -> (r, g, b, alpha) in 0..255 and 0..1."""
    hex_match = HEX_RE.match(value.strip())
    if hex_match:
        digits = hex_match.group(1)
        return (
            float(int(digits[0:2], 16)),
            float(int(digits[2:4], 16)),
            float(int(digits[4:6], 16)),
            1.0,
        )
    rgba_match = RGBA_RE.match(value.strip())
    assert rgba_match, f"{value!r} is neither #rrggbb nor rgba()"
    r, g, b, alpha = rgba_match.groups()
    return (float(r), float(g), float(b), 1.0 if alpha is None else float(alpha))


def composite(over: str, under: str) -> tuple[float, float, float, float]:
    """`over` painted on top of the opaque `under`."""
    top_r, top_g, top_b, alpha = channels(over)
    base_r, base_g, base_b, _ = channels(under)
    return (
        top_r * alpha + base_r * (1 - alpha),
        top_g * alpha + base_g * (1 - alpha),
        top_b * alpha + base_b * (1 - alpha),
        1.0,
    )


def relative_luminance(rgb: tuple[float, float, float, float]) -> float:
    def channel(value: float) -> float:
        scaled = value / 255
        return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4

    r, g, b, _ = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(foreground: tuple[float, float, float, float],
             background: tuple[float, float, float, float]) -> float:
    a = relative_luminance(foreground)
    b = relative_luminance(background)
    high, low = (a, b) if a >= b else (b, a)
    return (high + 0.05) / (low + 0.05)


def pair_contrast(palette: dict[str, str], foreground: str, *surfaces: str) -> tuple[float, str]:
    """The contrast of `foreground` on the first surface, compositing if it must.

    With one surface the two colours are compared directly. With two, the first
    surface is translucent and is composited over the second before comparing.
    """
    assert foreground in palette, f"the theme does not declare {foreground}"
    assert len(surfaces) in (1, 2), "a pair is one surface, or a translucent one over another"
    front = channels(palette[foreground])
    for surface in surfaces:
        assert surface in palette, f"the theme does not declare {surface}"
    if len(surfaces) == 1:
        back = channels(palette[surfaces[0]])
        return contrast(front, back), f"{foreground} on {surfaces[0]}"
    back = composite(palette[surfaces[0]], palette[surfaces[1]])
    return (
        contrast(front, back),
        f"{foreground} on {surfaces[0]} over {surfaces[1]}",
    )


def test_every_declared_text_pair_clears_wcag_aa():
    failures = []
    measured = []
    for theme, palette in theme_tokens().items():
        for foreground, *surfaces in TEXT_PAIRS:
            ratio, label = pair_contrast(palette, foreground, *surfaces)
            measured.append(f"{theme}: {label} {ratio:.2f}:1")
            if ratio < AA_NORMAL:
                failures.append(f"{theme}: {label} is {ratio:.2f}:1, under {AA_NORMAL}")
    assert not failures, (
        "these text-on-surface pairs are under WCAG AA; the value that fails is named "
        "so the fix is a token, not a screenshot:\n  " + "\n  ".join(failures)
    )
    assert len(measured) == len(TEXT_PAIRS) * 3, (
        "every pair has to be measured in all three token blocks (dark, light, light "
        f"without a script); measured {len(measured)}"
    )


def test_every_translucent_text_background_clears_wcag_aa_on_either_surface():
    failures = []
    for theme, palette in theme_tokens().items():
        for foreground, translucent, surface in COMPOSITED_PAIRS:
            ratio, label = pair_contrast(palette, foreground, translucent, surface)
            if ratio < AA_NORMAL:
                failures.append(f"{theme}: {label} is {ratio:.2f}:1, under {AA_NORMAL}")
    assert not failures, (
        "a notice ink or a muted line on a translucent fill is under WCAG AA; both "
        "surfaces it can sit on are checked, so the worse one is named here:\n  "
        + "\n  ".join(failures)
    )


def test_the_dark_theme_is_not_the_only_one_measured():
    """A guard that only ever saw the dark block would pass the light one by not looking."""
    palettes = theme_tokens()
    assert set(palettes) == {"dark", "light", "light (no script)"}, sorted(palettes)
    # The two light copies have to agree, which `test_theme_tokens.py` also asserts;
    # here it matters because the pairs above are computed per copy.
    assert palettes["light"] == palettes["light (no script)"], (
        "the two light blocks disagree, so one of the two contrast results above "
        "describes a palette no reader gets"
    )
    # And the two themes must not be the same palette, or the pairs above measured
    # one theme three times.
    assert palettes["dark"]["--fg-subtle"] != palettes["light"]["--fg-subtle"]
    assert palettes["dark"]["--bg"] != palettes["light"]["--bg"]


def test_no_colour_token_escapes_the_pairs_above():
    """Every colour a theme declares is either measured or exempted with a reason."""
    colour_tokens = set()
    for palette in theme_tokens().values():
        for name, value in palette.items():
            stripped = value.strip()
            if HEX_RE.match(stripped) or RGBA_RE.match(stripped):
                colour_tokens.add(name)
    measured = {name for pair in TEXT_PAIRS for name in pair}
    measured |= {name for pair in COMPOSITED_PAIRS for name in pair}
    unaccounted = colour_tokens - measured - set(EXEMPT)
    assert not unaccounted, (
        f"these colour tokens are in no contrast pair and not exempted: "
        f"{sorted(unaccounted)}. Add a pair, or an entry in EXEMPT saying why a "
        "contrast ratio does not apply to it."
    )
    assert all(reason.strip() for reason in EXEMPT.values()), (
        "an EXEMPT entry with no reason is how a token stops being measured"
    )
    # The two tokens this slice moved, in the theme that shipped the defect. Named
    # here so that a later slice cannot quietly move them back.
    dark = theme_tokens()["dark"]
    assert dark["--fg-subtle"] == "#8a87a6"
    assert dark["--accent-ink"] == "#0b0a16"