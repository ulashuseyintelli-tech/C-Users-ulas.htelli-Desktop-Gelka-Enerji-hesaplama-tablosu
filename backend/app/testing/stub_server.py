"""
StubServer — in-process HTTP server for downstream API simulation.

Real HTTP connections (not mocks) for fault injection testing.
port=0 lets the OS assign a free port (CI-safe).
daemon=True ensures cleanup when tests finish.

Feature: fault-injection, Task 2.1
Requirements: 2.1, 2.2, 2.3
"""

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class StubHandler(BaseHTTPRequestHandler):
    """Handler with injectable failure mode."""

    fail_mode: bool = False
    fail_count: int = 0  # 0 = unlimited failures when fail_mode=True
    request_counter: int = 0

    def do_GET(self):
        StubHandler.request_counter += 1
        if StubHandler.fail_mode:
            if StubHandler.fail_count == 0 or StubHandler.request_counter <= StubHandler.fail_count:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"error": "injected_failure"}')
                return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def do_POST(self):
        self.do_GET()

    def log_message(self, format, *args):
        pass  # suppress log noise in tests


class StubServer:
    """
    In-process HTTP stub server.

    Usage:
        server = StubServer()
        server.start()
        url = server.url  # e.g. http://127.0.0.1:54321
        server.set_fail_mode(True, fail_count=5)
        # ... make HTTP requests ...
        server.stop()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = HTTPServer((host, port), StubHandler)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=5)

    @staticmethod
    def set_fail_mode(enabled: bool, fail_count: int = 0) -> None:
        """Set failure mode. fail_count=0 means unlimited failures."""
        StubHandler.fail_mode = enabled
        StubHandler.fail_count = fail_count
        StubHandler.request_counter = 0
