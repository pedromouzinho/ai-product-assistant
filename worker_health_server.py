#!/usr/bin/env python3
"""Tiny HTTP host so a worker-only App Service can stay healthy."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    server_version = "AppWorkerHealth/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/health"):
            payload = json.dumps({"status": "healthy", "role": "worker"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        payload = b'{"status":"not_found"}'
        self.send_response(404)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def serve(*, port: int) -> None:
    with ThreadingHTTPServer(("0.0.0.0", port), _Handler) as server:
        server.serve_forever()


def main() -> None:
    port = int(os.getenv("PORT") or os.getenv("WEBSITES_PORT") or "8000")
    serve(port=port)


if __name__ == "__main__":
    main()
