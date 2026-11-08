# What M4 asked for so far — slice A — and where each piece is

No official judging criteria are mapped here, and none can be: M4 is a milestone in
this repository's own plan, not a hackathon this product is entered in. There is no
rules file for it to map against, so this page claims product scope only.

The milestone plan for M4 is: *Fix bugs found in M1–M3; add tests (backend +
Playwright E2E for every page).* That is the owner's brief, quoted as it was written.

Two slices are built. **Slice A** is one class of defect that a real tool run finds and
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
**213 checks, 213 passed**, with `/voices` at 150 of 150.

Axe now reports **no violation in any of its 54 passes** — nine pages × three
viewports, in both themes — with its thinnest pass running 22 rules, and the
keyboard walk reaches **all 283
controls** across the nine pages. The pages and viewports the axe pass covers, the
rule-set floor, the absence of violations and every per-page control count are read
back out of the run file by `tests/hosted/test_m4_acceptance_matrix.py`; a page
claiming "no violations" while the run it cites holds one fails the suite.

**Slice B is the other class a suite that only ever drives a healthy plane walks
past: what a page shows when the plane refuses.** All eight pages that read the
control plane during their render used to fold "the read failed" into "the list is
empty", because the read and the fallback sat on one line —
`initialKeys={status === 200 ? data?.api_keys ?? [] : []}`. The starting point was
read rather than assumed, and it is plain: with the plane answering 500, `/keys`
rendered *"No keys yet. Create one above."* to a customer whose keys had not gone
anywhere; `/voices` rendered *"The control plane reported no voices."* three lines
under a heading saying the engine did not answer; and the home page's meter tiles
printed `Requests 0`, `Characters 0`, `Audio returned 0.00 s` and `Failed 0` for a
usage read that never happened. A plane that is not there at all — the address
pointing at a closed port — made the server component throw, and Next answered its
own *"Application error: a server-side exception has occurred"*, so the dashboard's
worst failure mode was a page with no product on it.

One reader decides the difference now (`hosted/web/lib/planeRead.js`, returning
`{status, ok, failed, data, error}` around an injectable reader, with ten unit tests
that need no server) and one component shows the sentence
(`hosted/web/components/PlaneFailed.jsx`). A page renders its view or that notice,
never both, so the empty state cannot sit underneath a sentence saying nothing was
read. The notice is the plane's own detail when it sent one — the run records the
stub's sentence and the page shows it verbatim — and otherwise names the failure
itself: the status for an error reply, and the address with the socket's own code
for a refused connection. `/transcribe` reads nothing while it renders and keeps its
form in both modes, because a failure elsewhere must not take the product down.

The browser suite drives this for real instead of mocking `fetch`: these reads
happen in the web server's process, not the browser's, so a second web server runs
from the same production build against `hosted/web/e2e/plane_stub.py` (every path,
`/healthz` included, answers 500 with one sentence), every page is loaded in both
modes, and then the stub is stopped so the same port is genuinely closed. That is
one check per page, recorded in the run with the notice each page showed, and
`hosted/web/test/harness.test.mjs` reads the harness so a page cannot silently drop
out of the pass.

Two of the pages M4 eventually touches are **not built and are not stubbed** —
voice cloning and voice design stay unbuilt on this box for the engine reasons
`hosted/M3-ACCEPTANCE.md` records — and no human has listened to the audio, so
nothing here claims the voice is good. That remains the owner's check.

Every row names the code, the tests and a check in the browser run a reader can look
at: the checks come from `hermes-reports/M4-slot106-web-e2e-r1/result.json`, taken
with the real engine at `a2a0b2a15` over a clean tree, and the stub path beside it
(`hermes-reports/M4-slot106-web-e2e-stub-r1/result.json`) was taken at the same
revision — as was the cold clone recorded in
`hermes-reports/V0-slot106-coldclone-r1.log`, so the browser pair and the clone are
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
| A failed read is not an empty list, on the page where that was the plainest lie | `hosted/web/lib/planeRead.js` | `hosted/web/test/plane-read.test.mjs` | `with the plane refusing to answer, /keys shows the plane's notice and not its empty state` |
| The voice library does not claim the plane reported nothing when it did not answer | `hosted/web/app/voices/page.jsx` | `tests/hosted/test_plane_failure_pages.py` | `with the plane refusing to answer, /voices shows the plane's notice and not its empty state` |
| The meter is never invented from a usage read that failed | `hosted/web/app/page.jsx` | `tests/hosted/test_plane_failure_pages.py` | `with the plane refusing to answer, / shows the plane's notice and not its empty state` |
| The notice is the plane's own sentence when the plane sent one | `hosted/web/components/PlaneFailed.jsx` | `tests/hosted/test_plane_failure_pages.py` | `with the plane refusing to answer, /usage shows the plane's notice and not its empty state` |
| A page that reads nothing keeps its form when the plane is gone | `hosted/web/app/transcribe/page.jsx` | `tests/hosted/test_plane_failure_pages.py` | `with the plane refusing to answer, /transcribe still renders its form, because it reads nothing to render` |
| A page that queues work says the plane did not answer, not that nothing is queued | `hosted/web/app/jobs/page.jsx` | `tests/hosted/test_plane_failure_pages.py` | `with the plane refusing to answer, /jobs shows the plane's notice and not its empty state` |
| The long-form page does not offer a composer it cannot submit | `hosted/web/app/projects/page.jsx` | `tests/hosted/test_plane_failure_pages.py` | `with the plane refusing to answer, /projects shows the plane's notice and not its empty state` |
| The dub and speak pages do not offer a voice picker the plane never sent | `hosted/web/app/dub/page.jsx` | `tests/hosted/test_plane_failure_pages.py` | `with the plane refusing to answer, /dub shows the plane's notice and not its empty state` |
| Every page that reads the plane while it renders is one the pass drove | `hosted/web/e2e/dashboard.mjs` | `hosted/web/test/harness.test.mjs` | `with the plane refusing to answer, /speak shows the plane's notice and not its empty state` |

## What is not here, and is not stubbed

- **No screen reader was driven.** The axe pass reads the accessibility tree a screen
  reader consumes and the keyboard walk drives Tab, Enter and Escape for real; neither
  is a session with NVDA, VoiceOver or TalkBack, and this page does not claim one. Two
  cheap+ passes were right that a slice about what a screen reader gets should say that
  in the place a reader looks for the boundaries rather than only in the harness.
- **No human has listened to anything.** Machine checks, metering and header bytes
  are what the runs prove; the listening verdict is the owner's and is unchanged
  from M1.
- **Voice cloning and voice design are still unbuilt**, for the engine reasons
  recorded on `hosted/M3-ACCEPTANCE.md`; the axe pass over `/voices` measures the
  library page, not a cloning flow that does not exist.
- **Nothing here is deployed**, and nothing was published, messaged, submitted or
  priced. The runs are loopback only, on one CPU box, with no GPU and no real
  object-store bucket.
- **Slice B adds no retry, no backoff and no offline mode.** A page whose read failed
  says so and offers a reload; it does not keep asking the plane, does not queue the
  request, and does not serve stale data. That is a design decision, not a gap this
  page is hiding: what is claimed is that the failure is *said*, not that it is
  handled.
- **The plane that refuses is the suite's own stub, and the run says so.** It has no
  routes, no storage and no session; it answers every path with the status it was
  given. The closed-port mode is not a stub at all — the listener is stopped and the
  connection is really refused.