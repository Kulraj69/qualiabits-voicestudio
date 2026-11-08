/**
 * The browser harness's own claims, checked against its source.
 *
 * `e2e/dashboard.mjs` is the only place a few of the acceptance page's rows can
 * point at, and it is not run by `npm test` — it needs a Chromium, a plane, an
 * engine and, on the real path, several minutes of CPU. So the file that decides
 * what the run records is read here instead: if somebody deletes the
 * transcription model's fingerprint, drops the ceiling case, or stops pinning the
 * Whisper weights, these tests fail even though no browser was launched.
 *
 * This is not a substitute for the run. It is the guard that keeps the run's
 * evidence from quietly getting thinner between runs.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const HARNESS = readFileSync(path.join(WEB, "e2e", "dashboard.mjs"), "utf8");
const REAL_ENGINE = readFileSync(path.join(WEB, "e2e", "real-engine.sh"), "utf8");

test("the harness records the transcription model's own bytes", () => {
  assert.match(HARNESS, /function asrFingerprint\(\)/);
  assert.match(HARNESS, /transcription_model: asrFingerprint\(\)/);
  assert.match(HARNESS, /files\[name\] = sha256\(file\)/);
});

test("the transcription weights are checked against the hash the script pins", () => {
  assert.match(HARNESS, /transcription_model_pinned/);
  assert.match(
    HARNESS,
    /asr\.files\["model\.bin"\] === result\.transcription_model_pinned/,
  );
  assert.match(REAL_ENGINE, /QB_E2E_ASR_MODEL_PINNED="\$SHA_ASR_MODEL"/);
  assert.match(REAL_ENGINE, /QB_E2E_ASR_MODEL="\$ASR"/);
});

test("the ceiling case sends one byte past the limit the plane publishes", () => {
  // 25 MiB is the plane's default (hosted/control_plane/config.py), but the run
  // no longer carries that number: it reads `transcribe_max_bytes` from the
  // plane's own /healthz and sends one byte past it, so a plane configured with
  // a different ceiling is still tested against *its* ceiling, not a test's.
  assert.match(HARNESS, /const ceiling = Number\(planeHealth\?\.transcribe_max_bytes\)/);
  assert.match(HARNESS, /\}, ceiling \+ 1\)/);
  assert.match(HARNESS, /fetch\("\/api\/transcribe", \{ method: "POST", body \}\)/);
  assert.match(HARNESS, /overCeiling\.status === 413/);
  // The sentence the dashboard returns has to be the plane's own, for the
  // plane's own number -- the bug this replaced was a bare 500 with no sentence.
  assert.match(HARNESS, /expectedSentence = `That upload is larger than \$\{ceiling \/ \(1024 \* 1024\)\} MiB\.`/);
  assert.match(HARNESS, /the dashboard refused the oversized upload itself, so the plane never saw a request/);
  assert.match(HARNESS, /usageAfterCeiling\.requests === usageBeforeCeiling\.requests/);
});

test("the three transcript downloads are read back, not merely clicked", () => {
  for (const testId of ["download-txt", "download-srt", "download-json"]) {
    assert.match(HARNESS, new RegExp(`\\["[a-z]+", "${testId}"\\]`));
  }
  assert.match(HARNESS, /page\.waitForEvent\("download"\)/);
  assert.match(HARNESS, /readFileSync\(downloadPath, "utf8"\)/);
  assert.match(HARNESS, /transcript\.downloads = Object\.fromEntries/);
});

test("the run's own revision and worktree state are recorded, not assumed", () => {
  assert.match(HARNESS, /tested_commit: git\(\["rev-parse", "HEAD"\]\)/);
  assert.match(HARNESS, /worktree_dirty: status === null \? null : status\.length > 0/);
});
test("the library run compares the page with the plane in both directions", () => {
  // A page that lists fewer voices than the plane would hide some; one that lists
  // more would be inventing them. The run has to say which it saw.
  assert.match(HARNESS, /const catalogue = \(await plane\(planeBase, "\/v1\/voices"/);
  assert.match(HARNESS, /libraryCards\.length === catalogue\.voices\.length/);
  assert.match(HARNESS, /catalogue\.voices\.every\(\(voice\) => libraryCards\.includes\(voice\.id\)\)/);
});

test("the library preview is a real synthesis, not a bundled sample", () => {
  assert.match(HARNESS, /\[data-voice-id="\$\{previewVoice\}"\] \[data-testid="voice-preview"\]/);
  assert.match(HARNESS, /preview\.header === "RIFF"/);
  assert.match(HARNESS, /preview\.bytes > 1000/);
});

test("the preview's metering is read as a delta out of the plane's own meter", () => {
  assert.match(HARNESS, /const usageBeforeLibrary = \(await plane\(planeBase, "\/v1\/usage"/);
  assert.match(HARNESS, /usageAfterLibrary\.requests - usageBeforeLibrary\.requests === 1/);
  assert.match(HARNESS, /usageAfterLibrary\.audio_seconds - usageBeforeLibrary\.audio_seconds/);
});

test("the hand-off into Speak is driven and checked, both ways", () => {
  assert.match(HARNESS, /\[data-testid="voice-use"\]/);
  assert.match(HARNESS, /handedOver === previewVoice/);
  assert.match(HARNESS, /a voice the plane does not have is not preselected/);
});

test("the library is one of the pages the run visits, tabs into and captures", () => {
  assert.match(HARNESS, /const PAGES = \["\/", "\/speak", "\/voices"/);
});

test("the run dubs the audio it spoke, and checks both halves of the loop", () => {
  // The page the run visits and the control that starts a dub.
  assert.match(HARNESS, /"\/transcribe", "\/dub"/);
  assert.match(HARNESS, /\[data-testid="dub-file"\]/);
  assert.match(HARNESS, /\[data-testid="dub-submit"\]/);
  // The transcript the engine returned is read off the page, not assumed.
  assert.match(HARNESS, /\[data-testid="dub-transcript"\]/);
  // The dubbed bytes are fetched and their header read, and the download is named.
  assert.match(HARNESS, /\[data-testid="dub-download"\]/);
  assert.match(HARNESS, /header === "RIFF" && dubbed\.bytes > 44/);
  // A dub is two engine calls, and the run says so on the meter.
  assert.match(HARNESS, /usageAfterDub\.requests === usageBeforeDub\.requests \+ 2/);
  assert.match(HARNESS, /result\.dub = \{/);
});

test("the long-form page is visited, and the run queues a real manuscript on it", () => {
  assert.match(HARNESS, /"\/jobs", "\/projects"\]/);
  assert.match(HARNESS, /await page\.goto\(`\$\{webBase\}\/projects`/);
  assert.match(HARNESS, /\[data-testid="project-text"\]/);
  assert.match(HARNESS, /\[data-testid="project-create"\]/);
  // The manuscript is written as paragraphs of their own, so the number of
  // chapters is a fact about the text and not about the splitter's arithmetic.
  assert.match(HARNESS, /const chapterOne = /);
  assert.match(HARNESS, /`\$\{chapterOne\}\\n\\n\$\{chapterTwo\}`/);
});

test("the run checks the dashboard's refusal against the plane's own sentence", () => {
  assert.match(HARNESS, /const projectCeiling = Number\(planeHealth\?\.project_max_chars\)/);
  assert.match(HARNESS, /\[data-testid="project-over"\]/);
  assert.match(HARNESS, /pageRefusal === planeRefusal\.detail/);
  assert.match(HARNESS, /an over-long manuscript is refused by the dashboard in the plane's own sentence/);
});

test("the run asks for the audiobook before and after the worker, and compares both", () => {
  // Before: the refusal, with the chapters that are still waiting named.
  assert.match(HARNESS, /fetch\(`\/api\/projects\/\$\{id\}\/audio`\)/);
  assert.match(HARNESS, /earlyDownload\.detail\.waiting\.length === 2/);
  // After: the file, its own chapter count header, and the sum of the chapters.
  assert.match(HARNESS, /x-qualiabits-project-chapters/);
  assert.match(HARNESS, /chapterBytes - audiobook\.bytes < 200/);
  // And the meter: chapters are orders, a project is not.
  assert.match(HARNESS, /usageAfterProject\.requests === usageBeforeProject\.requests \+ 2/);
});

test("the run records the long-form numbers it measured, not a summary of them", () => {
  assert.match(HARNESS, /result\.projects = \{/);
  for (const field of ["chapter_ceiling", "chapters", "chapter_bytes", "audiobook_bytes", "audiobook_seconds", "meter_before", "meter_after"]) {
    assert.match(HARNESS, new RegExp(`${field}:`));
  }
});

test("the keyboard walk covers a page's whole control set, not its first 120", () => {
  // The real engine's 54-voice library renders 150 controls on /voices. A fixed
  // cap of 120 Tab presses in this harness made that page fail the reachability
  // check on a limit of the harness's own, with a detail string that read like a
  // page defect: 120/150, five controls named as unreachable that were never
  // tried. The bound is now the control count, plus the two lookahead presses,
  // and the ceiling that remains is for a pathological page -- recorded in the
  // run so a reader can tell "the page could not be tabbed through" apart from
  // "the walk gave up".
  assert.match(HARNESS, /const MAX_TAB_PRESSES = \d+;/);
  assert.match(HARNESS, /const presses = Math\.min\(MAX_TAB_PRESSES, controls\.length \+ 2\)/);
  assert.doesNotMatch(HARNESS, /Math\.min\(120,/, "the fixed 120-press cap is back");
  assert.match(HARNESS, /const truncated = controls\.length \+ 2 > MAX_TAB_PRESSES/);
  assert.match(HARNESS, /truncated,/);
  assert.match(HARNESS, /!truncated/);
});

test("the failing-plane pass drives a real second server and a real closed port", () => {
  // M4 slice B. Two modes, and neither is simulated: a stub that answers every
  // path with the status it was given, and the same port once the stub is
  // stopped, where the connection is refused. A pass that asserted the pages'
  // behaviour against a mocked `fetch` inside the browser would prove nothing,
  // because these reads happen in the web server's own process.
  assert.match(HARNESS, /const FAILED_PLANE_PAGES = \[/);
  assert.match(HARNESS, /path\.join\(HERE, "plane_stub\.py"\)/);
  assert.match(HARNESS, /"--status", "500", "--detail", STUB_DETAIL/);
  assert.match(HARNESS, /QUALI_CP_BASE_URL: stubBase/);
  assert.match(HARNESS, /if \(mode === "the plane is not there"\) await stop\(failedPlane\);/);
  assert.match(HARNESS, /await waitFor\(stubBase, "\/healthz", \{ expect: 500 \}\)/);
  assert.match(HARNESS, /data-testid=\"plane-failed\"/);
  assert.match(HARNESS, /result\.failed_plane = \{/);
});

test("the failing-plane pass covers every page, and knows which one reads nothing", () => {
  // /transcribe fetches nothing while it renders, so it must keep its form when
  // the plane is gone: a failure elsewhere is not allowed to take the product
  // down. Every other page must show the notice and must not show the control
  // that would only be there if a failed read had been treated as empty.
  const block = HARNESS.slice(
    HARNESS.indexOf("const FAILED_PLANE_PAGES = ["),
    HARNESS.indexOf("const PASSWORD"),
  );
  for (const route of ["/", "/speak", "/voices", "/transcribe", "/dub", "/jobs", "/projects", "/keys", "/usage"]) {
    assert.match(block, new RegExp(`route: "${route === "/" ? "\\/" : route}"`), `${route} is not driven`);
  }
  assert.match(block, /route: "\/transcribe", reads: false, mustShow: 'input\[type="file"\]'/);
  assert.equal((block.match(/reads: true/g) || []).length, 8, "eight pages read the plane while they render");
  assert.match(block, /mustNotText: \["No keys yet"\]/);
  assert.match(block, /mustNotText: \["reported no voices"\]/);
  assert.match(block, /mustNot: \['\[aria-label="Usage at a glance"\]'\]/);
});

test("the dashboard's pages read the plane through one reader", () => {
  // The defect was eight one-line fallbacks of the same shape. The fix is one
  // reader, and this is what keeps a ninth page from going back to `cpJson`:
  // no page under app/ may import it at all. Route handlers (app/api/**) may,
  // because they proxy a request rather than decide what a page shows.
  const pages = readdirSync(path.join(WEB, "app"), { recursive: true })
    .filter((name) => String(name).endsWith("page.jsx"))
    .map((name) => path.join(WEB, "app", String(name)));
  assert.ok(pages.length >= 9, `expected every page, found ${pages.length}`);
  for (const file of pages) {
    const source = readFileSync(file, "utf8");
    assert.doesNotMatch(
      source,
      /from "[.\/]*lib\/controlplane\.js"/,
      `${path.relative(WEB, file)} reads the plane directly instead of through lib/planeRead.js`,
    );
  }
});
