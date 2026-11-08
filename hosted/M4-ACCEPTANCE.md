# What M4 asked for so far — slice A — and where each piece is

No official judging criteria are mapped here, and none can be: M4 is a milestone in
this repository's own plan, not a hackathon this product is entered in. There is no
rules file for it to map against, so this page claims product scope only.

The milestone plan for M4 is: *Fix bugs found in M1–M3; add tests (backend +
Playwright E2E for every page).* That is the owner's brief, quoted as it was written.

One slice is built. **Slice A** is one class of defect that a real tool run finds and
that neither the width pass nor the theme pass looked for: what an
assistive-technology user gets. The width section measures geometry and the theme
section measures three computed colours on the home page; neither reads the
accessibility tree, which is what a screen reader actually consumes, and neither
would have caught a form field with no label or a button whose only content is a
glyph. So the browser suite runs `axe-core` — WCAG 2.1 level A and AA, no
opinionated `best-practice` rule set, because this repository's claim is the
standard and a suite that fails on taste is a suite people learn to ignore — over
the same nine pages at the same three viewports, in both themes, and then walks
every page with Tab, per control rather than per page.

The tool is a pinned dependency, not a vendored copy: `axe-core` **4.14.0** in
`hosted/web/package.json`, `MPL-2.0`, recorded in `package-lock.json` with an
integrity hash, and the sha256 of the file this suite injects
(`axe-core/axe.min.js`, `20c09fe157a8a34a30e241aaa1fcdade657734f08ab379ecfbeb7d45cc46e878`)
is recorded in the run and compared against the installed file by a test. What ran
is therefore a fact about bytes, not about a version string.

**The starting point was measured, not assumed.** Before any edit, this slice's own
arithmetic on `hosted/web/app/globals.css` put dark `--fg-subtle` on `--bg` at
**3.95:1** and white `--accent-ink` on `--accent` at **3.86:1** — normal text needs
4.5 — so the defect was not hypothetical. The stub run taken *before* the fixes is
committed as the defect record:
`hermes-reports/M4-slot89-web-e2e-stub-before/result.json`, **172 passed, 26
failed**, with four rule ids behind it: `color-contrast` (dark, all three widths),
`label-content-name-mismatch`, `scrollable-region-focusable` (phone) and
`link-in-text-block` (light). Every one of its 64 recorded violations carries the
rule id, the impact, the number of nodes and the first target it hit — the four
fields D2 below is about — and `tests/hosted/test_m4_acceptance_matrix.py` reads
that schema and those four rule ids out of this file rather than trusting this
sentence.

**Four fixes, each where the tool said it was wrong**: dark `--fg-subtle`
`#6f6b8c` → `#8a87a6` (labels, hints and table headers measured 3.95:1 on `--bg`,
3.75:1 on `--bg-elev` and 3.53:1 on `--bg-elev-2`, all under 4.5); dark
`--accent-ink` white → `#0b0a16` (white on the accent is 3.86:1 and 3.09:1 on the
hover, and that is the label of every primary button); the header's brand link
carried an `aria-label` its visible text did not contain, so the label goes and the
text is the name; and the seven scrollable table wrappers are now focusable named
regions, because a keyboard user could not scroll the phone-width tables. Prose
links are underlined as well (`link-in-text-block` fired in the light theme).

**The second finding is this slice's own, and it is about the harness.** The Tab
walk was capped at a fixed 120 presses. With the real engine's 54-voice library
rendering **150 controls** on `/voices`, the walk stopped at 120 and the run failed
the reachability check naming five controls it had never tried — a limit of the
harness's own, reading like a defect of the page. The bound is now the page's own
control count plus two lookahead presses, with a ceiling of 1000 for a pathological
page that is recorded in the run as `truncated` and fails the check when it is hit.
Two runs of the *same* code show the difference: the run before the fix
(`hermes-reports/M4-slot99-web-e2e-before-real/result.json`, at `dea9141b0`)
records 202 passed and 1 failed, with `/voices` at 120 of 150; the cited run records
**204 checks, 204 passed**, with `/voices` at 150 of 150.

Axe now reports **no violation in any of its 54 passes** — nine pages × three
viewports, in both themes — with its thinnest pass running 22 rules, and the
keyboard walk reaches **all 283
controls** across the nine pages. The pages and viewports the axe pass covers, the
rule-set floor, the absence of violations and every per-page control count are read
back out of the run file by `tests/hosted/test_m4_acceptance_matrix.py`; a page
claiming "no violations" while the run it cites holds one fails the suite.

Two of the pages M4 eventually touches are **not built and are not stubbed** —
voice cloning and voice design stay unbuilt on this box for the engine reasons
`hosted/M3-ACCEPTANCE.md` records — and no human has listened to the audio, so
nothing here claims the voice is good. That remains the owner's check.

Every row names the code, the tests and a check in the browser run a reader can look
at: the checks come from `hermes-reports/M4-slot104-web-e2e-r2/result.json`, taken
with the real engine at `45af954bb` over a clean tree, and the stub path beside it
(`hermes-reports/M4-slot104-web-e2e-stub-r2/result.json`) was taken at the same
revision — as was the cold clone recorded in
`hermes-reports/V0-slot104-coldclone-r1.log`, so the browser pair and the clone are
evidence about one state of the code. `tests/hosted/test_m4_acceptance_matrix.py`
reads this table and fails if a path stops existing, a named check stops appearing
in the run the page cites, or the code under the directories that run measured has
changed since — so the table cannot quietly become a description of an earlier
version of the code. `tests/hosted/test_axe_is_pinned.py` checks the pin, the
licence and the injected bytes. The clone proves the guards and not the pages: a
tree with no `.venv`, no `node_modules` and no model cannot run the browser suite,
which is exactly what the two runs above are for.

| The slice claims | Code | Tests | A check in the browser run |
| --- | --- | --- | --- |
| The accessibility tool is a pinned dependency, and its licence is named | `hosted/web/package.json` | `tests/hosted/test_axe_is_pinned.py` | `axe-core runs and reports its own version` |
| The rule set ran on every page, so no page passed by not running | `hosted/web/e2e/dashboard.mjs` | `hosted/web/test/harness.test.mjs` | `axe ran its own rule set on every page, so no page passed by not running` |
| No WCAG 2.1 A/AA violation on any page, in either theme | `hosted/web/app/globals.css` | `tests/hosted/test_m4_acceptance_matrix.py` | `/ has no WCAG 2.1 A/AA violation (dark)` |
| Labels, hints and table headers clear 4.5:1 in the dark theme | `hosted/web/app/globals.css` | `tests/hosted/test_theme_contrast.py` | `/usage has no WCAG 2.1 A/AA violation (dark)` |
| A primary button's own label clears 4.5:1 in the dark theme | `hosted/web/app/globals.css` | `tests/hosted/test_theme_contrast.py` | `/keys has no WCAG 2.1 A/AA violation (dark)` |
| The brand link's accessible name is its visible text | `hosted/web/components/SiteHeader.jsx` | `tests/hosted/test_m4_acceptance_matrix.py` | `/ has no WCAG 2.1 A/AA violation (light)` |
| A keyboard user can scroll the phone-width data tables | `hosted/web/app/usage/page.jsx` | `tests/hosted/test_m4_acceptance_matrix.py` | `/usage has no WCAG 2.1 A/AA violation (dark)` |
| Every control on every page is reachable by Tab | `hosted/web/e2e/dashboard.mjs` | `tests/hosted/test_m4_acceptance_matrix.py` | `every control on /voices is reachable by Tab` |
| The walk covers the page's whole control set, not a fixed prefix | `hosted/web/e2e/dashboard.mjs` | `hosted/web/test/harness.test.mjs` | `every control on /projects is reachable by Tab` |
| Every control that takes focus shows an outline | `hosted/web/app/globals.css` | `hosted/web/test/harness.test.mjs` | `every control on /speak shows a focus outline` |
| The theme toggle is operable from the keyboard | `hosted/web/components/SiteHeader.jsx` | `tests/hosted/test_theme_contrast.py` | `the theme toggle is operable from the keyboard, and Enter flips the theme` |
| The theme toggle is the same control a pointer user sees | `hosted/web/components/SiteHeader.jsx` | `tests/hosted/test_theme_contrast.py` | `every page shows a visible focus outline` |
| The numbers above are the run's own, not restated from memory | `tests/hosted/test_m4_acceptance_matrix.py` | `tests/hosted/test_web_pages_claims.py` | no run evidence: the guard reads the run file itself |

## What is not here, and is not stubbed

- **No human has listened to anything.** Machine checks, metering and header bytes
  are what the runs prove; the listening verdict is the owner's and is unchanged
  from M1.
- **Voice cloning and voice design are still unbuilt**, for the engine reasons
  recorded on `hosted/M3-ACCEPTANCE.md`; the axe pass over `/voices` measures the
  library page, not a cloning flow that does not exist.
- **Nothing here is deployed**, and nothing was published, messaged, submitted or
  priced. The runs are loopback only, on one CPU box, with no GPU and no real
  object-store bucket.