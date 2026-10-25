"""Exercise the actual urllib transport against a local HTTP server only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prompt_studio_core as core


class HTTPRequestCancellationTests(unittest.TestCase):
    def setUp(self):
        self.ready = threading.Event()
        self.release = threading.Event()
        self.disconnected = threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/ok":
                    body = b'{"ok": true}'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/headers":
                    owner.ready.set()
                    owner.release.wait(1.5)
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    try:
                        self.wfile.write(b"{}")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                self.send_response(429 if self.path == "/error" else 200)
                self.send_header("Content-Length", "1000000")
                self.end_headers()
                self.wfile.write(b'{"message": "')
                self.wfile.flush()
                owner.ready.set()
                self.connection.settimeout(0.1)
                while not owner.release.is_set():
                    try:
                        if not self.connection.recv(1):
                            owner.disconnected.set()
                            break
                    except socket.timeout:
                        continue
                    except OSError:
                        break
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.slots = threading.BoundedSemaphore(1)
        self.slot_patch = patch.object(core, "_CANCEL_REQUEST_SLOTS", self.slots)
        self.slot_patch.start()
        self.cancel = threading.Event()
        self.errors = []
        self.caller = None

    def tearDown(self):
        self.cancel.set()
        self.release.set()
        if self.caller is not None:
            self.caller.join(3)
        self.server.shutdown()
        self.server.server_close()
        self.slot_patch.stop()

    def url(self, path):
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    def start_request(self, path):
        def call():
            try:
                core._request_json(self.url(path), {}, timeout=2, cancel_event=self.cancel)
            except BaseException as error:
                self.errors.append(error)
        self.caller = threading.Thread(target=call, daemon=True)
        self.caller.start()
        self.assertTrue(self.ready.wait(2), "local server was not reached")
        time.sleep(0.12)

    def assert_cancel_and_release(self, path):
        self.start_request(path)
        started = time.monotonic()
        self.cancel.set()
        self.caller.join(0.6)
        self.assertFalse(self.caller.is_alive(), "cancel blocked waiting for response.close/read")
        self.assertLess(time.monotonic() - started, 0.6)
        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors[0], core.LLMRequestError)
        self.assertIn("cancelled", str(self.errors[0]))
        self.assertFalse(self.errors[0].retryable)
        self.assertTrue(self.slots.acquire(timeout=0.8), "cancelled HTTP reader did not free its request slot")
        self.slots.release()
        self.assertTrue(self.disconnected.wait(0.5), "underlying HTTP socket remained connected")

    def test_slow_response_cancel_closes_socket_and_frees_slot(self):
        self.assert_cancel_and_release("/slow")

    def test_slow_http_error_cancel_closes_socket_and_frees_slot(self):
        self.assert_cancel_and_release("/error")

    def test_cancel_waiting_for_headers_returns_without_unbounded_threads(self):
        self.start_request("/headers")
        self.cancel.set()
        self.caller.join(0.6)
        self.assertFalse(self.caller.is_alive())
        self.assertIsInstance(self.errors[0], core.LLMRequestError)
        self.release.set()
        self.assertTrue(self.slots.acquire(timeout=2.5))
        self.slots.release()

    def test_success_with_and_without_cancel_event(self):
        for event in (None, threading.Event()):
            with self.subTest(event=event):
                self.assertEqual(core._request_json(self.url("/ok"), {}, cancel_event=event), {"ok": True})

    def test_cancel_before_request_does_not_open_connection(self):
        self.cancel.set()
        with self.assertRaises(core.LLMRequestError):
            core._request_json(self.url("/slow"), {}, cancel_event=self.cancel)
        self.assertFalse(self.ready.is_set())
        self.assertTrue(self.slots.acquire(blocking=False))
        self.slots.release()


if __name__ == "__main__":
    unittest.main()
