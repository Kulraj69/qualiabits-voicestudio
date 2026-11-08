#!/usr/bin/env python3
"""A control plane that refuses: every request gets one fixed answer.

This is the harness's own stub, named as such, and it exists so the browser suite
can check what a page shows when the plane answers with an error. Nothing else in
the suite uses it: the real control plane (`hosted.control_plane.app:app`, its own
SQLite file and its own object store) is started beside it and is what every other
check in the run is about.

It is not a mock of the plane's behaviour — it has no routes, no storage and no
session. It answers every path, including `/healthz`, with `--status` and a body of
`{"detail": <the --detail sentence>}`, and the run records the sentence so a reader
can see the dashboard showed the plane's own words rather than a paraphrase.

  plane_stub.py --port 9200 --status 500 --detail "The plane could not read its own storage."

Stopping it is the suite's other failure mode: with the listener gone, the port is
closed and the dashboard's reads fail at the socket, which is what an unreachable
plane is. The run does that rather than simulating it.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--status", type=int, default=500)
    parser.add_argument("--detail", default="The plane could not read its own storage.")
    args = parser.parse_args()

    body = json.dumps({"detail": args.detail}).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _answer(self, _with_body: bool) -> None:
            payload = body if _with_body else b""
            self.send_response(args.status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)
            sys.stdout.write(f"{self.command} {self.path} -> {args.status}\n")
            sys.stdout.flush()

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own names
            self._answer(True)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length") or 0)
            if length:
                self.rfile.read(length)
            self._answer(True)

        def do_PUT(self) -> None:  # noqa: N802
            self.do_POST()

        def do_DELETE(self) -> None:  # noqa: N802
            self._answer(True)

        def log_message(self, format, *args) -> None:  # noqa: A002 - the base class's names
            """The lines written by `_answer` are the record; the base class's are noise."""
            return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    sys.stdout.write(f"failed-plane stub on 127.0.0.1:{args.port} answering {args.status}\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())