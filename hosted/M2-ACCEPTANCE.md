# What M2 asked for, and where each piece is

The milestone plan for M2 is: *wrap the local engine's API as a hosted service —
auth (email login), workspaces, API keys, per-workspace job queue + storage (S3),
usage metering. No desktop or Electron in the hosted path.* Slice A built the first
half; slice B is object storage, the per-workspace queue and the migration path.
Billing, email verification and deployment are explicitly not in this milestone, so
they are not rows here.

Every row names the code, the tests and something in the end-to-end run that a
reader can check. `tests/hosted/test_m2_acceptance_matrix.py` reads this table and
fails if a path, a test file or a run check stops existing, so the table cannot
quietly become a description of a previous version of the code.

| The plan asks for | Code | Tests | Something in the run |
| --- | --- | --- | --- |
| Object storage for the audio, content-addressed | `hosted/control_plane/storage.py` | `tests/hosted/test_hosted_storage.py` | `a_queued_job_is_spoken_by_a_real_worker_and_metered_once` |
| An S3 path beside the local one, signed requests over a socket | `hosted/control_plane/storage.py` | `tests/hosted/test_hosted_storage.py` | no run evidence: emulator only, no bucket |
| A queue that survives a worker dying mid-job | `hosted/control_plane/jobs.py` | `tests/hosted/test_hosted_queue.py` | `a_cancelled_job_is_never_spoken_or_metered` |
| A worker as its own process, speaking jobs one at a time | `hosted/worker/runner.py` | `tests/hosted/test_hosted_jobs.py` | `a_queued_job_is_spoken_by_a_real_worker_and_metered_once` |
| One job refused, one cancelled, and neither metered twice | `hosted/control_plane/jobs.py` | `tests/hosted/test_hosted_jobs.py` | `a_refused_job_fails_once_with_the_engine_s_own_message` |
| Slice A's database upgradable in place, rows intact | `hosted/control_plane/migrations.py` | `tests/hosted/test_hosted_migrations.py` | `"schema_version": "m2c1"` |
| Workspaces: signup with email and password, one per customer | `hosted/control_plane/app.py` | `tests/hosted/test_hosted_accounts.py` | `two_workspaces_signed_up_with_keys` |
| Login that returns a session token | `hosted/control_plane/auth.py` | `tests/hosted/test_hosted_accounts.py` | `key_refusals_are_401` |
| API keys stored as digests, shown once, revocable | `hosted/control_plane/security.py` | `tests/hosted/test_hosted_accounts.py` | `revoked_key_refused_and_other_workspace_untouched` |
| Usage metered per workspace and per key | `hosted/control_plane/metering.py` | `tests/hosted/test_hosted_metering.py` | `meter_matches_the_audio_and_is_per_workspace` |
| Speech passed through, metered, engine errors kept | `hosted/control_plane/upstream.py` | `tests/hosted/test_hosted_metering.py` | `hindi_and_english_audio_through_the_control_plane` |
| The front page's numbers are the run's numbers | `hosted/scripts/render_measured.py` | `tests/hosted/test_readme_measured_is_generated.py` | `"passed": true` |
| The judge bundle agrees with the receipts it pastes | `hermes-reports/build_review_prompt.py` | `tests/hosted/test_review_bundle_is_faithful.py` | `"worktree_dirty": false` |

Two rows deserve a plain sentence each, because the difference between what is
tested and what is not is where a product page usually starts lying:

- The S3 path is exercised with real signed requests against a local S3-compatible
  emulator on loopback, and the end-to-end run uses the local filesystem backend. **No
  real bucket has ever been used**, no credentials exist for one, and IAM policy,
  lifecycle rules and cross-region behaviour are reasoned about in
  `hosted/DESIGN.md` rather than proven. That is an owner decision, not a bug.
- Deployment, HTTPS, a domain, billing and email verification are not built. The run
  file says so in its own fields (`deployed`, `public_url`), and the front page says
  so in its first lines.