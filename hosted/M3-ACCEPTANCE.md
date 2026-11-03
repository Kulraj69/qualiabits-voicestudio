# What M3 asked for so far — slices A to D — and where each piece is

No official judging criteria are mapped here, and none can be: M3 is a milestone in
this repository's own plan, not a hackathon this product is entered in. There is no
rules file for it to map against, so this page claims product scope only.

The milestone plan for M3 is: *new web UI (Next.js): clean ElevenLabs-quality
dashboard — Text to Speech, Voice Library, Voice Clone (with consent), Voice Design,
Dubbing, Transcription, Audiobooks/Projects, API Keys, Usage. Responsive, fast,
dark/light.*

Four slices are built. **Slice A** is the four things a customer does first — sign
up, speak, mint keys, see what was used. **Slice B** is transcription: a recording in,
the engine's transcript out, metered in its own counters. **Slice C** is the Voice
Library: every voice the plane reports, searchable, each one previewable through the
same metered route the Speak page uses, with a hand-off into Speak. Slice C also had
to satisfy the repository's own select rule (`AGENTS.md`, "Shared select controls"):
the five pickers on the site — the voice on Speak and Jobs, the language filter, the
transcription language and answer format, and the library's kind filter — are now one
package-local searchable control instead of native selects, with its pure half tested
without a browser. **Slice D** is long-form: a manuscript in, chapters out. The plane
splits the text at paragraph, then sentence, then word boundaries — never inside a
word — queues one ordinary metered job per chapter, shows per-chapter progress on the
page, and assembles the chapters the engine actually produced into one downloadable
WAV. It refuses in two places rather than guessing: a manuscript whose chapter count
or single word breaks the stated limits, and an assembly whose chapter formats
disagree, because joining 24 kHz mono to 48 kHz stereo produces a file that every
player opens and every listener hears at the wrong speed. Three of the pages
M3 names — Voice Clone with a consent checkbox, Voice Design and Dubbing — are
**not built**, and the last section of this page says so
in the place a reader would otherwise expect a link.

Every row names the code, the tests and a check in the browser run a reader can look
at: the checks come from `hermes-reports/M3-slot60-web-e2e-r1/result.json`, taken
with the real engine at the revision that file records. `tests/hosted/test_m3_acceptance_matrix.py` reads this table and fails if a path
stops existing or a named check stops appearing in the run the page cites, so the
table cannot quietly become a description of a previous version of the code.
Documentation and tests keep moving after a run; the directories a run measured do
not, and the same guard runs the `git diff` that decides it — so the evidence here
is a command a reader can re-run, not a policy to take on trust.

| The plan asks for | Code | Tests | A check in the browser run |
| --- | --- | --- | --- |
| A real Next.js dashboard, its own package, importing nothing from `backend/` | `hosted/web/app/layout.jsx` | `hosted/web/test/controlplane.test.mjs` | `the dashboard is up and rooted at /` |
| Sign up and sign in, session held server-side only | `hosted/web/app/api/auth/login/route.js` | `tests/hosted/test_hosted_accounts.py` | `the session token is not readable by page JavaScript` |
| Text to speech: pick a voice the engine has, hear the audio | `hosted/web/components/SpeakView.jsx` | `tests/hosted/test_hosted_metering.py` | `the Speak page returns real WAV bytes through the plane` |
| The voice list comes from the plane, not the page | `hosted/control_plane/voices.py` | `tests/hosted/test_hosted_voices.py` | `the voice list is served by the plane, not hard-coded` |
| The engine's own refusal, in its own words | `hosted/control_plane/upstream.py` | `tests/hosted/test_hosted_metering.py` | `an unknown voice is refused by the plane with the engine's own sentence` |
| Transcription: a recording in, a transcript out | `hosted/control_plane/app.py` | `tests/hosted/test_hosted_transcription.py` | `the Transcribe page shows a transcript` |
| The upload is bounded before it is read | `hosted/control_plane/app.py` | `tests/hosted/test_hosted_transcription.py` | `an upload with no file is refused with a sentence that says so` |
| Speech in and speech out are separate counters | `hosted/control_plane/metering.py` | `tests/hosted/test_hosted_transcription.py` | `the plane counted the transcript in its own counter, not the speech one` |
| An older database gains the new counter, rows intact | `hosted/control_plane/migrations.py` | `tests/hosted/test_hosted_migrations.py` | no run evidence: the browser run creates a fresh database |
| API keys: shown once, listed as prefixes, revocable | `hosted/web/components/KeysView.jsx` | `tests/hosted/test_hosted_accounts.py` | `the key is not shown again after a reload` |
| A queued job, watched through the page, spoken by a real worker | `hosted/web/components/JobsView.jsx` | `tests/hosted/test_hosted_jobs.py` | `the real worker drained the queue` |
| Usage: the workspace totals and the per-key breakdown | `hosted/web/app/usage/page.jsx` | `tests/hosted/test_hosted_metering.py` | `the Usage page's request count matches the plane's meter` |
| The transcript can be taken away as text or subtitles | `hosted/web/lib/transcript.js` | `hosted/web/test/transcript.test.mjs` | `the transcript downloads as .txt holding the text the page shows` |
| Subtitles built from the timings the engine itself reported | `hosted/web/lib/transcript.js` | `hosted/web/test/transcript.test.mjs` | `the transcript downloads as .srt with cues carrying the engine's own timings` |
| The engine's own payload, kept as JSON | `hosted/web/app/transcribe/page.jsx` | `hosted/web/test/transcript.test.mjs` | `the transcript downloads as .json holding the engine's own payload` |
| The model that read the words back, named by its own bytes | `hosted/web/e2e/real-engine.sh` | `hosted/web/test/harness.test.mjs` | `the transcription model on disk is the one this run pins` |
| The upload limit is the plane's, refused before a byte is read | `hosted/control_plane/app.py` | `hosted/web/test/harness.test.mjs` | `an upload past the plane's ceiling is refused with a sentence naming the limit`, `the dashboard refused the oversized upload itself, so the plane never saw a request` |
| The limit the dashboard refuses on is the one the plane publishes, not a copy | `hosted/web/lib/limits.js` | `hosted/web/test/limits.test.mjs` | `an upload past the plane's ceiling is refused with a sentence naming the limit` |
| The engine's catalogue, browsable, every card a voice the plane reported | `hosted/web/components/VoiceLibraryView.jsx` | `hosted/web/test/voices.test.mjs` | `the voice library lists exactly the voices the plane serves` |
| The library's search narrows what arrived and can never add a voice | `hosted/web/lib/voices.js` | `hosted/web/test/voices.test.mjs` | `searching the library narrows it to the voices that match, and invents none` |
| Auditioning a voice is a real synthesis, through the plane, not a bundled sample | `hosted/web/app/voices/page.jsx` | `hosted/web/test/harness.test.mjs` | `the library previews a voice it offered, and real WAV bytes come back through the plane` |
| One press of Preview moves the plane's meter by exactly one request | `hosted/web/e2e/dashboard.mjs` | `hosted/web/test/harness.test.mjs` | `the preview was metered as exactly one more speech request under this workspace` |
| A voice chosen in the library carries into Speak, and only if the plane has it | `hosted/web/app/speak/page.jsx` | `hosted/web/test/harness.test.mjs` | `Use in Speak carries the voice to the Speak page, which selects it` |
| Every page one click from home, keyboard focus visible | `hosted/web/components/NavLinks.jsx` | `hosted/web/test/format.test.mjs` | `every page takes a keyboard Tab onto a focusable control` |
| Every picker is the package's own searchable select, and no page renders a native one | `hosted/web/components/SearchableSelect.jsx` | `hosted/web/test/searchable-select.test.mjs` | `the app contains no native select control on any page` |
| The control's keyboard: open, narrow, step to a row, commit, leave | `hosted/web/lib/searchable-select.js` | `hosted/web/test/searchable-select.test.mjs` | `the kind filter is a labeled combobox, closed until it is opened` |
| The voice picker offers the plane's voices and nothing else | `hosted/web/components/SpeakView.jsx` | `hosted/web/test/searchable-select.test.mjs` | `the voice picker offers every voice the plane serves, and nothing else` |
| Grouped rows come from the kind the plane reported, and filtering keeps the grouping | `hosted/web/lib/searchable-select.js` | `hosted/web/test/voices.test.mjs` | `choosing a kind with the keyboard narrows the cards to exactly that kind, and invents none` |
| The transcription controls are the same control as every other picker | `hosted/web/components/TranscribeView.jsx` | `hosted/web/test/searchable-select.test.mjs` | `the Transcribe page shows a transcript` |
| Dark, and the QualiaBits palette and spacing | `hosted/web/app/globals.css` | `hosted/web/test/format.test.mjs` | `every page shows a visible focus outline` |
| Long-form: a manuscript in, chapters out, one job per chapter | `hosted/control_plane/projects.py` | `tests/hosted/test_project_chapters.py` | `the plane split the manuscript into chapters and queued one job each` |
| The split lands on a boundary, never inside a word, and repeats exactly | `hosted/control_plane/projects.py` | `tests/hosted/test_project_chapters.py` | no run evidence: the splitter is pure, so the browser run says nothing a unit test does not |
| A chapter ceiling the queue will accept, read from both modules | `hosted/control_plane/projects.py` | `tests/hosted/test_project_chapters.py` | no run evidence: the same pure unit test reads both numbers |
| Per-chapter progress, counted from the plane's own statuses | `hosted/web/components/ProjectsView.jsx` | `tests/hosted/test_hosted_projects.py` | `the page's progress sentence is counted from the plane's chapter statuses` |
| A manuscript past the limit is refused in the plane's own sentence, before the request | `hosted/web/lib/projects.js` | `hosted/web/test/projects.test.mjs` | `an over-long manuscript is refused by the dashboard in the plane's own sentence` |
| No audiobook before every chapter is spoken: a 409 that names the chapters waiting | `hosted/control_plane/app.py` | `tests/hosted/test_hosted_projects.py` | `the assembled audiobook is a 409 that names the chapters still waiting` |
| The audiobook is the chapters' own frames under one header | `hosted/control_plane/projects.py` | `tests/hosted/test_project_assembly.py` | `the assembled file is the chapters' frames under one header, not two` |
| The assembler refuses formats it cannot join, naming both chapters | `hosted/control_plane/projects.py` | `tests/hosted/test_project_assembly.py` | no run evidence: the refusal is a pure function of two byte strings |
| Chapters are metered, not projects: one request per spoken chapter | `hosted/control_plane/app.py` | `tests/hosted/test_hosted_projects.py` | `the meter moved by one request per chapter, not one per project` |
| Another workspace can never read a project, a chapter or its audio | `hosted/control_plane/app.py` | `tests/hosted/test_hosted_projects.py` | no run evidence: the browser run signs one workspace up |
| An older database gains the two new tables, rows intact | `hosted/control_plane/migrations.py` | `tests/hosted/test_hosted_migrations.py` | no run evidence: the browser run creates a fresh database |

Two sentences that belong beside that table, because the gap between what is proven
and what is not is exactly where a product page starts lying:

- **Nothing here is deployed.** Every check above was taken against processes started
  on this machine on loopback: no public URL, no domain, no HTTPS, no real object-store
  bucket. The run file says so in its own fields rather than only in prose.
- **The voice is machine-checked, never human-checked.** What the run proves about the
  audio is that bytes came back, what the header says about their length, and — with
  the real engine — that a second engine could hear words it had said. In the run this
  page cites, three of the five words of the Hindi sentence came back — एक, हिन्दी and
  है — and a guard below reads that number back out of the run's own
  `transcript.shared_words` rather than trusting this sentence. The count moves
  between runs of the *same code*: this run heard यह as *यहा* and वाक्य as
  *वाग के*, and the one before it recorded two shared words instead of three,
  picking up हिन्दी as *हिंदी* for the same reason. It is not a transcription-quality claim, and the page
  does not make one; what is stable is the loop: the wiring, the metering
  and which bytes spoke,
  not transcription quality, and this page will not pretend otherwise. Whether anybody
  should *pay* for that voice is a listening judgement this repository has never made,
  and the WAVs are committed so the owner can.
- **Which model heard the words is recorded, not asserted.** `result.json` carries the
  transcription model's directory and the sha256 of its `model.bin`,
  `config.json`, `tokenizer.json` and `vocabulary.txt`, and the run fails unless
  `model.bin` is the hash `hosted/web/e2e/real-engine.sh` pins. The speech model is
  recorded the same way. A transcription result whose model is only named in prose is
  not evidence a reader can check. The directories in those two records are where
  *this* machine keeps the weights, not something a reader must copy: `real-engine.sh`
  downloads and hash-checks the speech model when a machine has none, and stops with
  the exact command to fetch the transcription model rather than substituting one.
  Which bytes are acceptable is decided in that one script, not in this page.

## What is not built, and is not stubbed

Voice Clone with a consent checkbox, Voice Design, Dubbing, the
agents tab, a light theme, and the marketing landing page are all **later slices of
M3**. None of
them is a greyed-out button or a "coming soon" card: a page that is not built is not on
the site, and the dashboard's navigation lists only pages that answer.