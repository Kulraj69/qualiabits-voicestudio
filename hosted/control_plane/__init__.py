"""Multi-tenant control plane for the hosted product (M2 slice A).

What this slice is: workspaces, email/password login, API keys, per-workspace
usage metering, and a metered proxy in front of the engine's
``POST /v1/audio/speech``.

What this slice is not: object storage, the job queue, deployment, email
verification or billing. Those are M2's remaining slices and M6.
"""

from .app import create_app

__all__ = ["create_app"]