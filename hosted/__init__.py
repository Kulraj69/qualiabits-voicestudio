"""The hosted side of QualiaBits Voice.

This package is deliberately separate from ``backend/``. VoiceStudio's backend is
the engine runtime: it loads speech models, exposes them over HTTP and serves the
desktop app. The hosted product needs different things in front of it — customers,
logins, keys, per-customer metering, and later a job queue and object storage.

``hosted.control_plane`` is that front layer. It does not import the engine, does
not load models and shares no database with it; it talks to the engine over HTTP,
the same way a customer will. Keeping the two apart means an engine upgrade can
never change how a customer authenticates, and it keeps the AGPL engine's own
code path untouched.

Nothing here is deployed. It runs locally and is proven locally; the deployment
milestone is M6 and it has not started.
"""