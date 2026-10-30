# QualiaBits Voice: M1 architecture and headless baseline

Status: M1 incomplete; V0 is not live. This fork's current addition is a
CPU-only API diagnostic, not a hosted product or a speech demo.

Upstream: https://github.com/debpalash/VoiceStudio
Fork: https://github.com/Kulraj69/qualiabits-voicestudio
Inspected base: `9680f4e43b2b8c765bcd1082ef0e1a181346d885`.
Preserve `LICENSE`, `LICENSE-NOTICE.md`, Palash Debnath and contributors'
attribution, and third-party notices. Modified network use requires offering
the complete corresponding AGPL-3.0 source, including build/install scripts.
The application license does not grant rights to speech weights or voices.

## Run the baseline

Requirements: Linux/POSIX, Python 3.12, uv, ffmpeg on PATH. No Electron,
GUI, account, GPU or model download is needed for this diagnostic.

```sh
git clone --branch work/m1-headless https://github.com/Kulraj69/qualiabits-voicestudio.git
cd qualiabits-voicestudio
uv sync --frozen --no-default-groups --group cpu --group dev --python python3.12
python3 -c "from pathlib import Path; Path('.env').open('x').close()"
.venv/bin/python scripts/headless_baseline.py --output /path/to/new-evidence-directory
```

Choose an output directory that does not exist. The harness starts the actual
`backend.main:app` with a reserved loopback socket, empty temporary state/cache,
HF offline mode, no proxy-header trust and no inherited provider credentials.
It polls readiness, requests OpenAPI/model/engine catalogs, sends short English
and Hindi requests to `/v1/audio/speech`, then sends a malformed request. It
stops its own server and writes `result.json` and `server.log`. It neither
accepts model terms nor installs weights. The speech refusal is expected;
`speech_verified` and `gpu_verified` remain false even when the baseline passes.
Only `api_contract_passed` can be true; `m1_complete` and
`production_readiness_verified` remain false. The result records startup time,
the 120-second readiness bound, loop-stall warnings, source hashes and orderly
shutdown. Loop stalls warn rather than fail the API contract; this is not a
production responsiveness acceptance test. Model/engine identities must be
present and unique, but are not inferred to be usable.
The `requests` journal records each post-readiness request's method, fixed
path, elapsed seconds and HTTP status or exception type. Completed checks and
catalog counts survive a later request failure. No request/response bodies or
exception text are copied into this journal. A failed HTTP/JSON read may have
no status in the journal even if the server later logs a response. Timings are
client observations, not synthesis latency; health polling is not journaled.
The existing 15-second socket timeout is unchanged, with no inspection retry
or warmup; it is not a hard total deadline for a slow-drip response body.
This is not a production launcher. The exclusive-create command makes an empty
`.env` sentinel that prevents upstream dotenv discovery from reading ancestor
configuration. It refuses to overwrite an existing file. Use a clean checkout;
if rerunning in the same checkout, skip sentinel creation only when it is
already an empty regular file. The harness independently checks that condition.
Never copy production configuration into this diagnostic. The harness rejects
nonempty environment files and symlinks without reading their contents.

```sh
HF_HUB_OFFLINE=1 HF_HUB_CACHE=/path/to/empty-cache .venv/bin/python -m pytest -q --tb=short tests/test_headless_baseline.py tests/test_loopback_server_mode.py tests/test_api.py tests/test_model_manager_preload.py tests/test_engine_language_rejection.py
```

These are targeted tests, not the full upstream suite. The upstream tests
use fake engine fixtures; the harness uses real HTTP without engine mocks.
No audio-quality, UI, SIP, hosted-login or GPU behavior is verified here.

## Request and ownership map

- `backend/main.py`: real entry point, early health socket, deferred heavy imports
  and router installation, then migrations/background services. `/health` is
  503 while starting, 200 after startup. HTTP 200 does not prove an engine can
  synthesize. `/startup/progress` reports the startup stages.
- `backend/api/routers/openai_compat.py`: `/v1/audio/speech` and
  `/v1/audio/transcriptions`; OpenAI-shaped errors and voice aliases. The name
  `alloy` maps to the selected engine default, not OpenAI's actual voice.
- `generation.py`, `tts_stream.py`, `profiles.py`, `describe_voice.py`:
  studio generation/streaming, saved voices/consent and design requests.
- `dub_core.py`, `dub_generate.py`, `dub_translate.py`, `dub_export.py`:
  dubbing entry points; business pipelines live in `backend/services/`.
- `dictation.py`, `capture.py`, `capture_ws.py`, `audiobook.py`,
  `longform_jobs.py`: dictation/capture and persistent longform orchestration.
  Route presence is not proof these flows work on this host.
- `backend/services/tts_backend.py`: engine contract and selection;
  `backend/engines/`: engine-specific implementations and subprocess runtimes.
  ASR and translation have their own service adapters.
- `backend/services/model_manager.py`: lazy imports, loaded model ownership,
  execution pools, guarded GPU work, preloading and idle lifecycle. A local
  worker pool is not a durable per-tenant SaaS queue or GPU autoscaler.
- `backend/config/models.yaml`, `api/routers/setup/models.py`,
  `setup/download.py`, `model_licenses.py`: catalog/install state, downloads and
  license receipts. `services/watermark.py:mark_synthetic` is the shared
  synthetic-audio provenance boundary; do not bypass it when adding engines.
- `backend/core/db.py`, `job_store.py`, `backend/migrations/`: SQLite-backed
  application state/jobs and Alembic evolution. Files live beneath configured
  data/cache roots. No verified per-customer ownership boundary exists here.
- `electron/src/main` supervises the local backend and native integrations;
  preload exposes the bridge; renderer holds the React application.
  `electron/vite.web.config.ts` builds the browser UI into `frontend/dist`.
  A Python-only checkout has no built dashboard at `/`.
- `calls.py`/`telephony_twilio.py` are upstream call surfaces, not evidence of
  the QualiaBits LiveKit/SIP integration. Keep the existing agents repository
  private until its source-publication scope is reviewed.

## Observed, not inferred

`hermes-reports/M1-install.log`: frozen CPU dependency install succeeded with
no manifest or lockfile changes. Initial manual `/health` call returned 503;
a later call returned `status=ok`, `device=cpu`, `version=0.5.7`.
`M1-server-baseline.log` records an event-loop stall during Alembic/SQLAlchemy
startup imports, followed by successful migrations and readiness. This is an
observed startup warning, not a fixed bug or a production latency benchmark.

Actual catalogs returned 42 model entries, zero installed, and engine catalogs
of 19 TTS, 12 ASR and 2 LLM entries. Catalog size does not mean those engines
are installed, usable, commercially cleared or tested.

Both English and Hindi requests returned HTTP 403,
`model_licence_required`. The local license inventory identifies
`k2-fsa/OmniVoice` weights as noncommercial and the
`eustlb/higgs-audio-v2-tokenizer` dependency as unresolved. See
`LICENSE-NOTICE.md` and `backend/config/model_licenses.json`. These are local
inventory findings, not a fresh legal audit of upstream model cards. Do not
accept these terms on the owner's behalf or treat AGPL as model clearance.
No WAV was produced and no synthetic stand-in is provided.

Round 1: upstream targeted tests 151 passed, 1 xfailed; harness tests 15 passed.
The first harness run reached all seven HTTP checks but correctly retained
`baseline_passed=false` because it did not yet classify Uvicorn's SIGTERM exit
code. Server log shows application shutdown completed. The original failed result is
preserved. Round 2 fixes verify shutdown markers as well as SIGTERM status and
reject unexpected environment configuration. See `hermes-reports/V0-rounds.md`
and `M1-round4-http/result.json` for current evidence and known limitations.

Rotation continuation: fresh-source frozen CPU install passed; the harness
suite passed 32 tests, and the combined targeted suite passed 183 tests with
1 expected failure and 7 warnings. Lint passed. Actual HTTP again returned
the two licence refusals and verified shutdown. `M1-round4-manifest.json`
records the base and hashes of the copied source; README is the only modified
pre-existing tracked file. Backend and lockfile remain unchanged from that base.
This is a fresh source copy, not a remote clone or the entire upstream suite.
The unawaited-coroutine warning also reproduces without importing the diagnostic
(52 passed, 1 xfailed, 3 warnings; `M1-round4-upstream-warning.log`).

The harness retains explicit non-acceptance flags on startup/spawn failures.
KeyboardInterrupt and SystemExit execute its finally cleanup, as tested.
It is not a process supervisor: SIGKILL, repeated interrupts during cleanup,
or filesystem/socket setup failures need not yield structured evidence. Do not
use this harness to manage a paid GPU instance or production lifecycle.

Post-push remote-clone check at `e7a10dc36bdda2221a34a0326dc726bfc6270073`:
frozen install passed, but the first cold HTTP run timed out after readiness.
The unchanged clone passed when rerun to a new evidence directory. Both runs
verified shutdown. This is an unresolved intermittent startup-responsiveness
finding, not a reliably passing cold-start path. Logs are retained in the
agents repository at `hermes-reports/M1-remote-clone{,-retry}.log`.

The retained failed server log narrows the post-readiness problem: catalogs
returned before the first speech request; the main-thread stall dump includes
torch distributed/dynamo and SymPy imports, then speech eventually logs 403.
The dump truncates before the initiating application frame. This is evidence
of blocking imports, not proof of their full call chain or a backend fix.
I1's unchanged-backend run returned seven responses; `/engines` took 8.838s
and the first speech refusal took 8.858s (`M1-I1-http/result.json`). This is a
warm installed checkout, not a cold-machine benchmark. No GPU/audio acceptance.
I2's fresh-source targeted suite passed 200 tests (1 xfailed, 7 warnings);
lint and real HTTP passed. Tests ran before HTTP: not a cold-start check.
`M1-I2-manifest.json` records the source-copy hashes and unchanged backend/base
comparison; the result's four hashes alone do not prove source integrity.
The second independent review found the evidence-loss fix correct but failed
the full M1 rubric. Startup imports, speech/GPU and model rights remain open.

## Real English speech on CPU, no licence acceptance (do X -> see Y)

The default model is noncommercial, so it stays gated until the owner accepts its
terms personally. Three catalogue engines are licence-category `commercial` and
need no acceptance; KittenTTS is already installed in the CPU venv and runs on CPU.

```sh
git clone --branch work/m1-headless https://github.com/Kulraj69/qualiabits-voicestudio.git
cd qualiabits-voicestudio
uv sync --frozen --no-default-groups --group cpu --group dev --python python3.12
python3 -c "from pathlib import Path; Path('.env').open('x').close()"
out=$(mktemp -d)/m1-speech        # the probe refuses an existing output directory
.venv/bin/python scripts/m1_speech_probe.py --output "$out" --hf-cache ~/.cache/hf-voice
```

First run fetches two checkpoints. ~78 MB for the KittenTTS ONNX graph goes into
`--hf-cache` — the probe passes that path as both `HF_HUB_CACHE` and
`OMNIVOICE_CACHE_DIR`, because `backend/main.py` rewrites the former from the
latter; a second run with the same `--hf-cache` reuses it (measured cold/warm-cache
first-request times on a 2-vCPU box: 32.9 s / 27.3 s, the difference being the
download). ~59 MB for the AudioSeal watermark checkpoint
`facebook/audioseal` goes into the run's own temporary state directory, so it is
re-fetched every run. Without `--hf-cache` the probe runs offline and fails for
missing weights.

See in `"$out"/result.json`: exit code 0, `"speech_produced": true`,
`"active_tts_backend": "kittentts"`, `"env_override": true`, a
`"licence_provenance"` entry showing `"license": "apache-2.0"`,
`"category": "commercial"`, `"required": false`, an `english_audio` block with
`"sample_rate": 24000` and `"non_silent": true`, and `"hindi_attempt"` with
`"http_status": 400` — the engine is English-only and says so. Listen to
`"$out"/audio/english.wav`.

`english_cold_seconds` and `english_warm_seconds` are end-to-end HTTP request
times (client-observed), not synthesis-only latency: the first request pays for
the model download, the ONNX load and the AudioSeal checkpoint. `host` records the
box they were measured on.

`m1_complete`, `gpu_verified`, `hindi_verified` and `intelligibility_verified`
remain false in every run: no GPU, no Hindi audio, and nothing here listens to the
audio. Tests for the probe and for a refusal fix (an unsupported language used to
answer 500 on `/v1/audio/speech`; it now answers 400):

```sh
HF_HUB_OFFLINE=1 HF_HUB_CACHE=$TMPDIR/empty .venv/bin/python -m pytest -q \
  tests/test_m1_speech_probe.py tests/test_openai_speech_language_refusal_400.py
```

## Next gated M1 step

This host has no NVIDIA device. Boto3 found an IAM instance role, but
`ec2:DescribeInstances` in `ap-south-1` returned `UnauthorizedOperation`.
Missing static AWS environment variables alone are not an access test.
No GPU instance was provisioned or started.

Needs-Kulraj: provide an approved g5/g6 spot target and scoped runtime access
(or provision it with automatic idle shutdown and a hard spending limit);
select an English/Hindi-capable engine whose weights, tokenizer and output
terms permit the intended commercial service, and have the owner complete any
required acceptance. Then run unchanged VoiceStudio, synthesize both languages
through its API, verify nonempty decoded audio and listen for intelligibility,
record engine/revision/device/timing and shut the GPU down. Do not proceed to
M2 as if M1 passed. A provider-only path would require an explicit milestone
change, not silently replacing the requested GPU test.

M2 must enforce ownership on all jobs, profiles, files and API requests before
public exposure. Keep this desktop API private: loopback requests bypass the
upstream bearer gate. A local reverse proxy alone is not tenant auth. See
`docs/production-private-api.md` for proxy and network precautions.
