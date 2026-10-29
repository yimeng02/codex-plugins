"""Usage guidance stays available after setup and reports real manager URLs."""

import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from memory_manager import __version__
from memory_manager.app import TOOLS, dispatch, handler_for, main, rpc_response
from memory_manager.store import Store, StoreError


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "private-memory.sqlite3")
        self.manager = {
            "url": "http://127.0.0.1:9432/", "running": True, "verified": True,
            "status": "running", "message": "已核实本资料库管理页面。",
            "start_command": ["python3", "run.py", "serve", "--port", "9432"],
        }

    def remember(self):
        return self.store.entry_create({"title": "Private rule", "content": "PRIVATE_MEMORY_BODY",
                                        "category_id": "preferences"})

    def test_configured_users_can_get_full_help_with_actual_url(self):
        self.store.set_onboarding_decision("configured")
        self.remember()
        before = self.store.snapshot()
        with mock.patch("memory_manager.app.get_manager_status", return_value=self.manager) as get_status:
            result = dispatch(self.store, "memory_status", {"include_help": True})
        get_status.assert_called_once_with(self.store)
        self.assertIsNone(result["first_use_prompt"])
        self.assertEqual(result["web_manager"], self.manager)
        self.assertIn(self.manager["url"], result["usage_guide"])
        self.assertEqual(self.store.snapshot(), before)

    def test_regular_configured_status_omits_help_and_does_not_probe(self):
        self.store.set_onboarding_decision("configured")
        with mock.patch("memory_manager.app.get_manager_status") as get_status:
            for arguments in ({}, {"include_help": False}):
                result = dispatch(self.store, "memory_status", arguments)
                self.assertNotIn("usage_guide", result)
                self.assertNotIn("web_manager", result)
                self.assertIsNone(result["first_use_prompt"])
        get_status.assert_not_called()

    def test_pending_default_returns_complete_first_use_guidance_and_actual_url(self):
        with mock.patch("memory_manager.app.get_manager_status", return_value=self.manager):
            result = dispatch(self.store, "memory_status", {})
        self.assertEqual(result["onboarding_decision"], "pending")
        self.assertEqual(result["web_manager"], self.manager)
        prompt = result["first_use_prompt"]
        self.assertIn(self.manager["url"], prompt)
        for term in ("分类", "新增", "修改", "memory_refresh", "AGENTS.md"):
            self.assertIn(term, prompt)
        self.assertNotIn("usage_guide", result)

    def test_declining_setup_does_not_hide_usage_or_unverified_manager_state(self):
        self.store.set_onboarding_decision("declined")
        unverified = {**self.manager, "running": None, "verified": False,
                      "status": "unverified", "message": "无法核实服务是否正在运行。"}
        with mock.patch("memory_manager.app.get_manager_status", return_value=unverified):
            result = dispatch(self.store, "memory_status", {"include_help": True})
        self.assertIsNone(result["web_manager"]["running"])
        self.assertFalse(result["web_manager"]["verified"])
        self.assertEqual(result["web_manager"]["message"], unverified["message"])
        self.assertIsNone(result["first_use_prompt"])
        self.assertIn(unverified["url"], result["usage_guide"])

    def test_include_help_rejects_non_boolean_values_without_mutation(self):
        before = self.store.snapshot()
        with mock.patch("memory_manager.app.get_manager_status") as get_status:
            for value in (None, 0, 1, "true", [], {}):
                with self.subTest(value=value), self.assertRaisesRegex(StoreError, "include_help"):
                    dispatch(self.store, "memory_status", {"include_help": value})
        get_status.assert_not_called()
        self.assertEqual(self.store.snapshot(), before)

    def test_initialize_instructs_actual_usage_without_mutation_or_starting_service(self):
        entry = self.remember()
        before = self.store.snapshot()
        history = self.store.history(entry["id"])
        profile = self.root / "profile"
        profile.mkdir()
        agents = profile / "AGENTS.md"
        agents.write_text("Keep existing instructions.\n", encoding="utf-8")
        with mock.patch.dict("os.environ", {"CODEX_HOME": str(profile)}), \
                mock.patch("memory_manager.app.get_manager_status") as get_status, \
                mock.patch("memory_manager.app.register_server") as register, \
                mock.patch("memory_manager.app.setup_global_rule") as setup:
            result = rpc_response(self.store, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {"protocolVersion": "2025-11-25"}})
        instructions = result["result"]["instructions"]
        self.assertIn("include_help=true", instructions)
        self.assertIn("web_manager.url", instructions)
        self.assertIn("Only after showing that guidance", instructions)
        get_status.assert_not_called()
        register.assert_not_called()
        setup.assert_not_called()
        self.assertEqual(agents.read_text(encoding="utf-8"), "Keep existing instructions.\n")
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.history(entry["id"]), history)

    def test_help_schema_keeps_eighteen_tools_and_read_only_status(self):
        self.assertEqual(len(TOOLS), 18)
        status = next(tool for tool in TOOLS if tool["name"] == "memory_status")
        self.assertEqual(status["inputSchema"]["properties"]["include_help"], {"type": "boolean"})
        self.assertTrue(status["annotations"]["readOnlyHint"])

    def test_health_reports_instance_without_memory_or_path(self):
        self.remember()
        before = self.store.snapshot()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.store))
        server.daemon_threads = True
        server.memory_manager_instance = "test-manager-instance"
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 3)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        data = response.read().decode("utf-8")
        self.assertEqual(json.loads(data), {"service": "codex-memory-manager", "version": __version__,
                                           "instance_id": "test-manager-instance"})
        self.assertNotIn(str(self.root), data)
        self.assertNotIn("PRIVATE_MEMORY_BODY", data)
        self.assertEqual(self.store.snapshot(), before)

    def test_serve_registers_bound_port_and_unregisters_after_close(self):
        server = mock.Mock(server_port=9432)
        server.serve_forever.side_effect = KeyboardInterrupt
        record = {"instance_id": "registered-test-instance"}
        with mock.patch("memory_manager.app.ThreadingHTTPServer", return_value=server), \
                mock.patch("memory_manager.app.register_server", return_value=record) as register, \
                mock.patch("memory_manager.app.unregister_server") as unregister, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            main(["--db", str(self.root / "serve.sqlite3"), "serve", "--port", "0"])
        register.assert_called_once()
        self.assertEqual(register.call_args.args[1], 9432)
        self.assertEqual(server.memory_manager_instance, record["instance_id"])
        server.server_close.assert_called_once()
        unregister.assert_called_once_with(register.call_args.args[0], record)

    def test_serve_closes_socket_if_registration_fails(self):
        server = mock.Mock(server_port=9432)
        with mock.patch("memory_manager.app.ThreadingHTTPServer", return_value=server), \
                mock.patch("memory_manager.app.register_server", side_effect=StoreError("注册失败")), \
                mock.patch("memory_manager.app.unregister_server") as unregister, \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as error:
                main(["--db", str(self.root / "failed-serve.sqlite3"), "serve", "--port", "0"])
        self.assertEqual(error.exception.code, 1)
        server.server_close.assert_called_once()
        server.serve_forever.assert_not_called()
        unregister.assert_not_called()


if __name__ == "__main__":
    unittest.main()
