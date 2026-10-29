import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer

from memory_manager.app import dispatch, handler_for, rpc_response
from memory_manager.onboarding import BEGIN_READER, END_READER, READER_RULE, setup_global_rule
from memory_manager.store import Store, StoreError


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "codex-home"
        self.path = self.home / "AGENTS.md"
        self.store = Store(self.root / "memory.sqlite3")

    def setup(self, action="preview", **options):
        return setup_global_rule(self.store, action=action, codex_home=self.home, **options)

    def test_preview_creates_neither_file_nor_choice(self):
        revision = self.store.snapshot()["revision"]
        preview = self.setup()
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["rule"], "开始任务时，使用 memory-manager 读取全局启用记忆；\n涉及具体项目、技能或插件时，按准确作用域和目标检索相关记忆。\n仅应用与当前任务相关的内容；工具不可用时如实说明。\n")
        self.assertEqual(preview["sha256"], hashlib.sha256(b"").hexdigest())
        self.assertFalse(self.home.exists())
        self.assertEqual(self.store.application_status()["onboarding_decision"], "pending")
        self.assertEqual(self.store.snapshot()["revision"], revision)

    def test_apply_requires_explicit_consent_preview_hash_and_path(self):
        for options in ({}, {"confirmed": False}, {"confirmed": True},
                        {"confirmed": 1, "expected_sha256": "value"},
                        {"confirmed": True, "expected_sha256": hashlib.sha256(b"").hexdigest()},
                        {"confirmed": True, "expected_sha256": hashlib.sha256(b"").hexdigest(), "expected_path": []}):
            with self.subTest(options=options), self.assertRaises(StoreError):
                self.setup("apply", **options)
        self.assertFalse(self.home.exists())
        self.assertEqual(self.store.application_status()["onboarding_decision"], "pending")

    def test_apply_preserves_existing_crlf_and_creates_backup(self):
        self.home.mkdir()
        original = "# Personal rules\r\n\r\n保留这段原文。\r\n".encode("utf-8")
        self.path.write_bytes(original)
        preview = self.setup()
        result = self.setup("apply", confirmed=True, expected_sha256=preview["sha256"], expected_path=preview["path"])
        self.assertTrue(result["applied"])
        self.assertEqual(Path(result["backup"]).read_bytes(), original)
        content = self.path.read_bytes()
        self.assertTrue(content.startswith(original))
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
        self.assertEqual(content.count(BEGIN_READER.encode()), 1)
        self.assertEqual(self.store.application_status()["onboarding_decision"], "configured")
        self.assertIsNone(dispatch(self.store, "memory_status", {})["first_use_prompt"])
        self.assertEqual(self.store.snapshot()["revision"], 0)
        second = self.setup()
        self.assertTrue(second["already_configured"])
        self.setup("apply", confirmed=True, expected_sha256=second["sha256"], expected_path=second["path"])
        self.assertEqual(self.path.read_bytes(), content)

    def test_stale_preview_does_not_replace_later_edits(self):
        preview = self.setup()
        self.home.mkdir()
        self.path.write_text("new user content", encoding="utf-8")
        with self.assertRaises(StoreError):
            self.setup("apply", confirmed=True, expected_sha256=preview["sha256"], expected_path=preview["path"])
        self.assertEqual(self.path.read_text(), "new user content")
        self.assertEqual(self.store.application_status()["onboarding_decision"], "pending")

    def test_active_global_override_is_the_previewed_target(self):
        self.home.mkdir()
        self.path.write_text("base guidance", encoding="utf-8")
        override = self.home / "AGENTS.override.md"
        override.write_text("active guidance", encoding="utf-8")
        preview = self.setup()
        self.assertEqual(Path(preview["path"]), override)
        self.setup("apply", confirmed=True, expected_sha256=preview["sha256"], expected_path=preview["path"])
        self.assertEqual(self.path.read_text(), "base guidance")
        self.assertTrue(override.read_text().startswith("active guidance"))

    def test_same_content_override_switch_rejects_the_old_preview_path(self):
        self.home.mkdir()
        original = b"same guidance\n"
        self.path.write_bytes(original)
        preview = self.setup()
        override = self.home / "AGENTS.override.md"
        override.write_bytes(original)
        self.assertEqual(hashlib.sha256(override.read_bytes()).hexdigest(), preview["sha256"])
        with self.assertRaises(StoreError) as rejected:
            self.setup("apply", confirmed=True, expected_sha256=preview["sha256"], expected_path=preview["path"])
        self.assertEqual(rejected.exception.status, 409)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(override.read_bytes(), original)
        self.assertEqual(self.store.application_status()["onboarding_decision"], "pending")
        self.assertEqual(sorted(path.name for path in self.home.iterdir()), ["AGENTS.md", "AGENTS.override.md"])

    def test_post_write_sqlite_failure_reports_the_file_as_applied(self):
        preview = self.setup()
        with mock.patch.object(self.store, "set_onboarding_decision", side_effect=sqlite3.OperationalError("database is locked")):
            result = self.setup("apply", confirmed=True, expected_sha256=preview["sha256"], expected_path=preview["path"])
        self.assertTrue(result["applied"])
        self.assertFalse(result["application_recorded"])
        self.assertIn("database is locked", result["warning"])
        self.assertIn(READER_RULE, self.path.read_text(encoding="utf-8"))
        self.assertEqual(self.store.application_status()["onboarding_decision"], "pending")

    def test_preview_permission_failure_is_an_mcp_tool_error(self):
        self.home.mkdir()
        self.path.write_text("original guidance", encoding="utf-8")
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "memory_setup", "arguments": {"action": "preview"},
        }}
        with mock.patch("memory_manager.onboarding.resolve_codex_home", return_value=self.home), mock.patch.object(Path, "read_bytes", side_effect=PermissionError("permission denied")):
            response = rpc_response(self.store, request)
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn("error", response)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "original guidance")
        self.assertEqual(self.store.application_status()["onboarding_decision"], "pending")

    def test_malformed_managed_block_and_symlink_are_rejected(self):
        self.home.mkdir()
        self.path.write_text(BEGIN_READER, encoding="utf-8")
        with self.assertRaises(StoreError):
            self.setup()
        self.path.unlink()
        outside = self.root / "outside.md"
        outside.write_text("outside guidance", encoding="utf-8")
        try:
            self.path.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(StoreError):
            self.setup()
        self.assertEqual(outside.read_text(), "outside guidance")

    def test_decline_and_defer_are_persisted_without_writing_agents(self):
        for action, expected in (("decline", "declined"), ("defer", "deferred")):
            result = self.setup(action)
            self.assertFalse(result["applied"])
            self.assertEqual(result["application"]["onboarding_decision"], expected)
            self.assertFalse(self.home.exists())
            reply = rpc_response(self.store, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertNotIn("On actual use in a Codex chat", reply["result"]["instructions"])
            self.assertIsNone(dispatch(self.store, "memory_status", {})["first_use_prompt"])

    def test_initialize_offers_consent_gated_first_use(self):
        reply = rpc_response(self.store, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIn("On actual use in a Codex chat", reply["result"]["instructions"])
        self.assertIn("explicit affirmative answer", reply["result"]["instructions"])
        self.assertIn("call memory_status", reply["result"]["instructions"])
        self.assertFalse(self.home.exists())

    def test_dispatch_exposes_only_fixed_global_setup_target(self):
        with mock.patch("memory_manager.onboarding.resolve_codex_home", return_value=self.home):
            preview = dispatch(self.store, "memory_setup", {"action": "preview"})
        self.assertEqual(Path(preview["path"]), self.path)
        with self.assertRaises(StoreError):
            dispatch(self.store, "memory_setup", {"action": "apply", "confirmed": True, "filename": str(self.root / "other.md")})
        self.assertFalse(self.home.exists())

    def test_http_reads_status_and_catalog_without_exposing_setup_write(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.store))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 3)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["onboarding_decision"], "pending")
        with mock.patch("memory_manager.app.discover_plugins", return_value={"plugins": [{"id": "example-plugin", "display_name": "Example"}]}):
            connection.request("GET", "/api/plugins")
            response = connection.getresponse()
            self.assertEqual(json.loads(response.read())["plugins"][0]["id"], "example-plugin")
        connection.request("POST", "/api/setup", json.dumps({"action": "apply", "confirmed": True}), {"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 404)
        response.read()
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
