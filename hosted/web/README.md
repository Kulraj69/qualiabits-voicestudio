# The QualiaBits Voice dashboard

The browser front end for the hosted control plane in [`hosted/`](../). Four
things a customer does first, in a real Next.js app: sign up and sign in; speak
text and hear it; mint and revoke API keys; watch a queued job and download what
it made, and read the meter the plane keeps.

Not deployed. Not public. No public URL, no real S3 bucket, no GPU. Everything
runs on this machine on loopback.

## Do this, see that

You need three processes. The dashboard is one of them; the engine and the
control plane are the other two, and running them is described in
[`hosted/RUN-IT-BY-HAND.md`](../RUN-IT-BY-HAND.md). You do not need any of that
for either test path below — `npm run e2e` and
[`e2e/real-engine.sh`](e2e/real-engine.sh) start all three themselves, on their
own ports and their own state directory, and stop them again. Start them by hand
only when you want to poke at the app in a browser. The short version, from the
repository root, in two terminals:

```sh
# terminal 1 — the engine (any engine the plane can reach; see RUN-IT-BY-HAND.md)
QUALI_STATE_DIR=$PWD/.hosted .venv/bin/python -m uvicorn backend.main:app --port 3900

# terminal 2 — the control plane, pointed at it
QUALI_STATE_DIR=$PWD/.hosted \
QUALI_DATABASE_URL=sqlite:///$PWD/.hosted/control-plane.db \
QUALI_UPSTREAM_BASE_URL=http://127.0.0.1:3900 \
.venv/bin/python -m uvicorn hosted.control_plane.app:app --port 8080
```

Then the dashboard itself:

```sh
cd hosted/web
npm install                 # Next, React, and Playwright as a dev dependency
npm run build
QUALI_CP_BASE_URL=http://127.0.0.1:8080 npm start
```

Open <http://localhost:3000>. `QUALI_CP_BASE_URL` is the only setting the app
has; it defaults to `http://127.0.0.1:8080`, so a plane on its usual port needs
nothing. `npm run dev` runs the same app with hot reload while you work on it.

Now walk it, and each step says what you should see:

1. **Create a workspace** at `/signup` — email, a password of at least twelve
   characters, an optional name. You land on `/`, which greets the workspace and
   shows four zeroed usage tiles straight from the plane's meter.
2. **Speak** at `/speak` — the voice list is whatever `GET /v1/voices` returned,
   headed with a sentence saying how many voices came from where. Pick a voice,
   type a sentence, press **Speak**. A player appears with the audio, the
   duration the plane measured from the WAV header, and a **Download WAV** link.
   Ask for a voice the engine does not have and you get *the engine's own
   sentence*, passed through the plane.
3. **Keys** at `/keys` — create a key. It is shown once, with a copy button;
   reload the page and it is gone from view, because only a digest is stored.
   The table shows each key's public id and label and nothing else. Press
   **Revoke** and the very next request with that key is refused.
4. **Jobs** at `/jobs` — queue a paragraph. It goes to **Queued**; a worker
   process (`hosted/scripts/run_worker.py`) claims it, speaks it, stores the
   audio, and the row moves to **Done** with **Play** and **Download**. Queue a
   second one and press **Cancel** before a worker runs: it reads **Cancelled**
   and the meter does not move.
5. **Usage** at `/usage` — the workspace totals and the per-key breakdown, with
   the plane's own notes about what each counter means.

Every page above is one click from home, every page is dark, and every control
takes a visible keyboard focus.

## Tests

Two layers, both runnable here:

```sh
npm test        # node --test test/ — the pure logic, no server in the way
npm run e2e     # the browser end-to-end (builds, starts everything, drives Chromium)
```

`npm test` covers the pieces that can be wrong on their own: cookie
serialising (and which cookie is httpOnly, and which is readable), the voice
grouping and language filtering, the number and status formatting, and the plane
client's URL joining and error-message handling. Twenty-nine checks, no browser.

`npm run e2e` is the honest one. It builds the app, then starts **the real
control plane** (its own SQLite file and object store), **the real web server**
(the production Next build) and **real headless Chromium**, signs up through the
form, speaks, mints and revokes a key, queues, cancels and speaks a job with the
real `run_worker.py --once`, and checks all of it **against the plane's own
records — the request count on the Usage page is compared with `GET /v1/usage`
taken straight from the plane, and a revoked key is shown to be refused on the
next request. **Forty-two checks** on this path. `node e2e/dashboard.mjs
--no-build` reuses an existing build; `--keep` writes `result.json`, the process
logs and a full-page screenshot of each of the five pages to `e2e/.evidence/`.

**The one substitution** is the engine: `e2e/engine_stub.py`, the same shape as
the stub the Python suite uses, so the browser run needs no model download. The
browser, the plane, the web server, the worker and the object store are real.

To run that same browser suite against the **real engine**, there is one command:

```sh
hosted/web/e2e/real-engine.sh --keep
```

It takes the model from `QUALI_MODEL_DIR`, or from one of four other places it
looks (`hosted/web/<name>`, the repository root, the repository's parent,
`/workspace/m1-models/<name>`); if none of those has it, it **downloads** the
release asset (about 132 MB) into `${TMPDIR:-/tmp}/qb-real-engine-model`, checks
the three pinned sha256 values and starts nothing until they match — the same
URL, the same pins and the same three files the hosted proof's script checks
(`../scripts/hosted_demo.sh`). It never falls back to the stub, because a run
that quietly did would be the opposite of evidence. So from a fresh clone this
path needs one thing: the venv the sync above builds.

That run carries five things a constant-size stub cannot produce, every one of
them the engine's own answer about itself: its identity from `/health`, the
backend it reports active with its voice count, the sha256 of `model.int8.onnx`,
`tokens.txt` and `voices.bin`, a paragraph that produced more audio than the
one-liner, and **forty-six checks** rather than forty-two — the four extra are the
engine's own claims above. On CPU it takes minutes rather than seconds, because
synthesis runs at about a seventh of real time. Two knobs, if your box is slower
than this one: `QB_E2E_SPEAK_TIMEOUT_MS` (default 20000; the script raises it to
300000) and `QB_E2E_UPSTREAM_TIMEOUT_SECONDS` (default 60; the script raises it
to 300).

`result.json` also records the revision the run was taken over and whether that
working tree was clean, so a run and a commit can be named together.

## How it is built

The app **proxies**: the browser talks to the Next server, and the Next server
talks to the control plane (`lib/controlplane.js`, `lib/proxy.js`). The session
token is written into an **httpOnly cookie** by the app's own route handlers
(`app/api/auth/*`), so page JavaScript never sees it — the end-to-end test
asserts exactly that. The workspace's own name, id and email sit in a second,
readable cookie so the header can render without a round-trip; nothing secret is
in it.

Speaking is the interesting one. The plane's rule is that speaking always needs
an API key, so that every byte of audio is attributable to a credential. The
dashboard honours it: the first time you speak, `app/api/speech/route.js` mints a
key labelled `dashboard`, keeps its secret in an httpOnly cookie and uses it. The
key appears in your key list like any other — and if you revoke it, the Speak
page stops working until you mint a fresh one, which is the milestone's
"revocation takes effect on the next request" made visible.

Nothing here imports from `backend/` or `hosted/control_plane`; the app's only
contact with either is HTTP.

## What is not here, and is not stubbed

Voice cloning, voice design, dubbing, transcription, audiobooks, the agents tab,
the marketing landing page, email verification, billing and deployment are all
later slices. None of them exists as a greyed-out button here — a page that is
not built is not on the site.

## Licence

AGPL-3.0, inherited from VoiceStudio. Run this for customers and you owe them
the corresponding source; keep the fork public and keep the attribution in
[`LICENSE`](../../LICENSE) and [`LICENSE-NOTICE.md`](../../LICENSE-NOTICE.md).