"""The HTTP client that talks to the engine.

The control plane wraps the engine over HTTP rather than importing it, so this is
the only place that knows the engine's address. It also owns the decision that
matters for metering: what counts as a delivered response.

- 2xx with a body: delivered. The bytes are handed back untouched.
- non-2xx: the engine's own error is passed through, with its status and JSON
  body, so a customer debugging a refusal sees the engine's message and not ours.
- connection refused, DNS failure, timeout: our failure, not the engine's, and it
  becomes 502/504 with a short reason. The engine's exception text is not copied
  into the response or the ledger — it can contain local paths.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx


#: Response headers from the engine that a customer is allowed to see. The
#: engine marks every response with its backend name; hiding it would make a
#: customer's bug report unanswerable ("which engine spoke?").
FORWARDED_HEADER_PREFIX = "x-omnivoice-"


@dataclass
class UpstreamResult:
    status: int
    body: bytes | None
    media_type: str | None
    elapsed_seconds: float
    error: str = ""
    engine_headers: dict[str, str] = field(default_factory=dict)

    @property
    def delivered(self) -> bool:
        return 200 <= self.status < 300 and bool(self.body)


class UpstreamClient:
    """A thin, testable wrapper. Pass ``client`` to point it at a stub."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout_seconds
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def healthy(self) -> dict:
        """Is the engine up, and what does it say it is? Never raises."""
        try:
            response = await self._client.get("/health", timeout=5.0)
        except Exception as exc:  # noqa: BLE001 - health must never propagate
            return {"reachable": False, "reason": type(exc).__name__}
        detail: dict = {"reachable": response.status_code == 200}
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            for field in ("status", "device", "version"):
                if field in payload:
                    detail[field] = payload[field]
        else:
            detail["status_code"] = response.status_code
        return detail

    async def speech(self, payload: dict) -> UpstreamResult:
        started = time.monotonic()
        try:
            response = await self._client.post(
                "/v1/audio/speech", json=payload, timeout=self.timeout_seconds
            )
        except httpx.TimeoutException:
            return UpstreamResult(
                504, None, None, round(time.monotonic() - started, 3), "upstream_timeout"
            )
        except httpx.HTTPError:
            return UpstreamResult(
                502, None, None, round(time.monotonic() - started, 3),
                "upstream_unreachable",
            )
        elapsed = round(time.monotonic() - started, 3)
        marker = {
            key.lower(): value
            for key, value in response.headers.items()
            if key.lower().startswith(FORWARDED_HEADER_PREFIX)
        }
        if not response.is_success:
            return UpstreamResult(
                response.status_code,
                response.content,
                response.headers.get("content-type"),
                elapsed,
                "upstream_refused",
                marker,
            )
        return UpstreamResult(
            response.status_code,
            response.content,
            response.headers.get("content-type"),
            elapsed,
            "",
            marker,
        )