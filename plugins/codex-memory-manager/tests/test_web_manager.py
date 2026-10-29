"""Behavioral checks for manager links without trusting a URL or changing memories."""

import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from memory_manager import __version__
from memory_manager.store import Store
from memory_manager.web_manager import (
    get_manager_status, register_server, registry_path, unregister_server,
)


class WebManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "memory.sqlite3"
        self.store = Store(self.database)
        self.store.entry_create({"title": "Existing rule", "content": "Keep this rule intact."})
        self.original_data = self.store.export_data()
        self.original_state = self.store.application_status()

    def assert_library_unchanged(self):
        self.assertEqual(self.store.export_data(), self.original_data)
        self.assertEqual(self.store.application_status(), self.original_state)

    def write_record(self, record, store=None):
        path = registry_path(store or self.store)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")

    def server(self, reply):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                status, headers, body = reply()
                if not isinstance(body, bytes):
                    body = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Type", "application/json")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(cleanup)
        return server.server_port, requests

    def healthy_server(self):
        record = {}
        port, requests = self.server(lambda: (200, {}, {
            "service": "codex-memory-manager", "instance_id": record["instance_id"],
        }))
        record.update(register_server(self.store, port))
        return record, requests

    def assert_status_shape(self, status):
        self.assertTrue({"url", "running", "verified", "status", "message", "start_command"} <= set(status))
        self.assertIsInstance(status["status"], str)
        self.assertIsInstance(status["message"], str)
        self.assertTrue(status["start_command"])

    def test_no_runtime_record_is_unknown_not_a_started_server(self):
        path = registry_path(self.store)
        self.assertIsInstance(path, Path)
        self.assertFalse(path.exists())
        result = get_manager_status(self.store)
        self.assert_status_shape(result)
        self.assertEqual(result["url"], "http://127.0.0.1:8765/")
        self.assertIsNone(result["running"])
        self.assertFalse(result["verified"])
        self.assertFalse(path.exists(), "Status inspection must not register a server")
        self.assert_library_unchanged()

    def test_registry_registration_binds_the_actual_library_and_custom_port(self):
        record, _ = self.healthy_server()
        self.assertEqual(record["database"], str(self.database.resolve()))
        self.assertEqual(record["platform"], os.name)
        self.assertEqual(record["version"], __version__)
        self.assertTrue(record["instance_id"])
        self.assertEqual(record["url"], f'http://127.0.0.1:{record["port"]}/')
        self.assertEqual(json.loads(registry_path(self.store).read_text(encoding="utf-8"))["instance_id"], record["instance_id"])
        self.assert_library_unchanged()

    def test_live_health_verifies_the_registered_instance_and_preserves_custom_url(self):
        record, requests = self.healthy_server()
        result = get_manager_status(self.store, timeout=0.5)
        self.assert_status_shape(result)
        self.assertTrue(result["running"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["url"], record["url"])
        self.assertEqual(requests, ["/api/health"])
        self.assert_library_unchanged()

    def test_service_or_instance_mismatch_is_never_reported_as_running(self):
        reply = {}
        port, requests = self.server(lambda: (200, {}, reply))
        record = register_server(self.store, port)
        for payload in (
            {"service": "another-service", "instance_id": record["instance_id"]},
            {"service": "codex-memory-manager", "instance_id": "different-instance"},
            {"service": "codex-memory-manager"},
        ):
            with self.subTest(payload=payload):
                reply.clear()
                reply.update(payload)
                result = get_manager_status(self.store, timeout=0.5)
                self.assertIsNot(result["running"], True)
                self.assertFalse(result["verified"])
        self.assertEqual(requests, ["/api/health"] * 3)
        self.assert_library_unchanged()

    def test_health_json_errors_do_not_claim_a_live_manager(self):
        body = [b"not json"]
        port, _ = self.server(lambda: (200, {}, body[0]))
        register_server(self.store, port)
        for malformed in (b"not json", b"[]", b"null", b'"text"', b'{}'):
            with self.subTest(body=malformed):
                body[0] = malformed
                result = get_manager_status(self.store, timeout=0.5)
                self.assertIsNot(result["running"], True)
                self.assertFalse(result["verified"])
        self.assert_library_unchanged()

    def unused_port(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        # Reserve without listening, so another process cannot win a free-port race.
        return listener.getsockname()[1]

    def test_stopped_local_server_does_not_look_running(self):
        record = register_server(self.store, self.unused_port())
        result = get_manager_status(self.store, timeout=0.15)
        self.assertFalse(result["running"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["url"], record["url"])
        self.assertTrue(result["message"])
        self.assert_library_unchanged()

    def test_unreachable_cross_platform_record_remains_unknown_with_saved_link(self):
        record = register_server(self.store, self.unused_port())
        record["platform"] = "nt" if os.name != "nt" else "posix"
        self.write_record(record)
        result = get_manager_status(self.store, timeout=0.15)
        self.assertIsNone(result["running"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["url"], record["url"])
        self.assertTrue(result["message"])
        self.assert_library_unchanged()

    def test_record_for_another_database_is_not_probed_or_claimed(self):
        record, requests = self.healthy_server()
        other = Store(self.root / "other.sqlite3")
        self.write_record(record, other)
        result = get_manager_status(other, timeout=0.5)
        self.assertIsNot(result["running"], True)
        self.assertFalse(result["verified"])
        self.assertEqual(requests, [])
        self.assert_library_unchanged()

    def test_record_url_cannot_redirect_probe_to_another_server(self):
        record, requests = self.healthy_server()
        destination, destination_requests = self.server(lambda: (200, {}, {
            "service": "codex-memory-manager", "instance_id": record["instance_id"],
        }))
        record["url"] = f"http://127.0.0.1:{destination}/private-path"
        self.write_record(record)
        result = get_manager_status(self.store, timeout=0.5)
        self.assertTrue(result["running"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["url"], f'http://127.0.0.1:{record["port"]}/')
        self.assertEqual(requests, ["/api/health"])
        self.assertEqual(destination_requests, [])
        self.assert_library_unchanged()

    def test_health_redirect_is_not_followed(self):
        record = {}
        destination, destination_requests = self.server(lambda: (200, {}, {
            "service": "codex-memory-manager", "instance_id": record["instance_id"],
        }))
        port, requests = self.server(lambda: (302, {"Location": f"http://127.0.0.1:{destination}/api/health"}, b""))
        record.update(register_server(self.store, port))
        result = get_manager_status(self.store, timeout=0.5)
        self.assertIsNot(result["running"], True)
        self.assertFalse(result["verified"])
        self.assertEqual(requests, ["/api/health"])
        self.assertEqual(destination_requests, [])
        self.assert_library_unchanged()

    def test_corrupt_runtime_record_does_not_break_memory_access(self):
        path = registry_path(self.store)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        result = get_manager_status(self.store, timeout=0.15)
        self.assertIsNot(result["running"], True)
        self.assertFalse(result["verified"])
        self.assertTrue(result["message"])
        self.assert_library_unchanged()

    def test_old_process_cannot_unregister_a_new_instance(self):
        older = register_server(self.store, self.unused_port())
        current = register_server(self.store, self.unused_port())
        self.assertNotEqual(older["instance_id"], current["instance_id"])
        unregister_server(self.store, older)
        self.assertEqual(json.loads(registry_path(self.store).read_text(encoding="utf-8"))["instance_id"], current["instance_id"])
        unregister_server(self.store, current)
        self.assertFalse(registry_path(self.store).exists())
        self.assert_library_unchanged()


if __name__ == "__main__":
    unittest.main()
