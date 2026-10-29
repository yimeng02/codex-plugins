import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer

from memory_manager.app import dispatch, handler_for, managed_document, publish, rpc_response, BEGIN, END, TOOLS
from memory_manager.store import Store, StoreError


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root / "memory.sqlite3")
        self.category = self.store.snapshot()["categories"][0]["id"]

    def remember(self, **changes):
        entry = {"title": "验证产物", "content": "交付前核对实际产物。", "category_id": self.category, "scope": "global", "target": "", "enabled": True, "priority": 50}
        entry.update(changes)
        return self.store.entry_create(entry)

    def test_mcp_read_and_write_roundtrip(self):
        result = rpc_response(self.store, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        self.assertEqual(result["result"]["protocolVersion"], "2025-11-25")
        entry = self.remember()
        result = rpc_response(self.store, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_search", "arguments": {"scope": "global", "target": ""}}})
        payload = json.loads(result["result"]["content"][0]["text"])
        self.assertEqual(payload["entries"][0]["id"], entry["id"])
        self.assertIsNone(rpc_response(self.store, {"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_render_filters_scope_target_and_enabled(self):
        self.remember()
        self.remember(title="已禁用", enabled=False)
        self.remember(title="其他项目", scope="project", target="/project/other")
        result = dispatch(self.store, "memory_render", {"scope": "global"})
        self.assertEqual(result["count"], 1)
        self.assertNotIn("已禁用", result["markdown"])
        self.assertNotIn("其他项目", result["markdown"])
        with self.assertRaises(StoreError):
            dispatch(self.store, "memory_render", {"scope": "project"})

    def test_mcp_search_and_render_keep_plugin_targets_exact(self):
        targets = ("test-plugin", "Test Plugin", "test-plugin-tools")
        entries = [self.remember(title="Rule " + target, scope="plugin", target=target)
                   for target in targets]
        self.remember(title="Same target in project", scope="project", target="test-plugin")
        self.remember(title="Disabled plugin rule", scope="plugin", target="test-plugin", enabled=False)
        for target, expected in zip(targets, entries):
            args = {"scope": "plugin", "target": target}
            response = rpc_response(self.store, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "memory_search", "arguments": args}})
            body = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual([e["id"] for e in body["entries"]], [expected["id"]])
            rendered = dispatch(self.store, "memory_render", args)
            self.assertEqual(rendered["count"], 1)
            self.assertIn("### " + expected["title"], rendered["markdown"])
            self.assertEqual(set(rendered), {"markdown", "count", "scope", "target"})

    def test_mcp_schemas_keep_all_tools_and_only_exact_target_fields(self):
        self.assertEqual(len(TOOLS), 18)
        self.assertIn("memory_plugins", {item["name"] for item in TOOLS})
        schema = json.dumps([item["inputSchema"] for item in TOOLS])
        self.assertNotIn("match_mode", schema)
        self.assertNotIn("plugin_id", schema)
        refresh = next(item for item in TOOLS if item["name"] == "memory_refresh")
        self.assertEqual(refresh["inputSchema"]["properties"]["contexts"]["items"]["required"],
                         ["scope", "target"])

    def test_removed_matching_parameters_are_errors_without_mutation(self):
        entry = self.remember(title="Plugin rule", scope="plugin", target="test-plugin")
        original = self.store.export_data()
        original_status = self.store.application_status()
        for field, value in (("match_mode", "exact"), ("plugin_id", "test-plugin")):
            cases = (
                ("memory_search", {"scope": "plugin", "target": "test-plugin", field: value}),
                ("memory_render", {"scope": "plugin", "target": "test-plugin", field: value}),
                ("memory_create", {"title": "Rejected", "content": "Do not save", "category_id": self.category,
                                   "scope": "plugin", "target": "test-plugin", field: value}),
                ("memory_update", {"id": entry["id"], "patch": {field: value}}),
                ("memory_refresh", {"contexts": [{"scope": "plugin", "target": "test-plugin", field: value}]}),
            )
            for name, arguments in cases:
                with self.subTest(field=field, tool=name):
                    response = rpc_response(self.store, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}})
                    self.assertTrue(response["result"].get("isError"), response)
                    self.assertEqual(self.store.export_data(), original)
                    self.assertEqual(self.store.application_status(), original_status)
        with self.assertRaises(StoreError):
            dispatch(self.store, "memory_refresh", {"contexts": [{"scope": "plugin"}]})
        with self.assertRaises(StoreError):
            dispatch(self.store, "memory_render", {"scope": "plugin", "plugin_id": "test-plugin"})

    def test_cli_publish_uses_only_exact_scope_and_target(self):
        self.remember(title="Included plugin memory", scope="plugin", target="test-plugin")
        self.remember(title="Other plugin must stay out", scope="plugin", target="test-plugin-tools")
        self.remember(title="Display name must stay out", scope="plugin", target="Test Plugin")
        self.remember(title="Other scope must stay out", scope="project", target="test-plugin")
        path = self.root / "AGENTS.md"
        command = [sys.executable, str(Path(__file__).resolve().parent.parent / "run.py"),
            "--db", str(self.root / "memory.sqlite3"), "publish", "--file", str(path),
            "--scope", "plugin", "--target", "test-plugin"]
        run = subprocess.run(command,
            capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        preview = json.loads(run.stdout)
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["count"], 1)
        self.assertIn("Included plugin memory", preview["content"])
        self.assertNotIn("must stay out", preview["content"])
        self.assertFalse(path.exists())
        for option, value in (("--match-mode", "exact"), ("--plugin-id", "test-plugin")):
            rejected = subprocess.run(command + [option, value], capture_output=True,
                                      text=True, encoding="utf-8", timeout=15)
            self.assertEqual(rejected.returncode, 2, rejected.stderr)
            self.assertIn("unrecognized arguments", rejected.stderr)
            self.assertFalse(path.exists())

    def test_render_normalizes_global_target_and_rejects_missing_non_global_target(self):
        self.remember(title="Global rule")
        self.remember(title="Plugin rule", scope="plugin", target="test-plugin")
        result = dispatch(self.store, "memory_render", {"scope": "global", "target": "ignored-for-global"})
        self.assertEqual(result["target"], "")
        self.assertEqual(result["count"], 1)
        for scope in ("project", "skill", "plugin"):
            with self.subTest(scope=scope), self.assertRaises(StoreError):
                dispatch(self.store, "memory_render", {"scope": scope, "target": " "})

    def test_publish_preserves_surrounding_text_and_rejects_stale_hash(self):
        self.remember()
        path = self.root / "AGENTS.md"
        original = "# 项目规则\r\n\r\n保留此段。\r\n"
        path.write_bytes(original.encode("utf-8"))
        preview = publish(self.store, path, "global", "")
        self.assertFalse(preview["applied"])
        self.assertEqual(path.read_bytes(), original.encode("utf-8"))
        result = publish(self.store, path, "global", "", True, preview["sha256"])
        self.assertTrue(result["applied"])
        self.assertEqual(Path(result["backup"]).read_bytes(), original.encode("utf-8"))
        written = path.read_bytes().decode("utf-8")
        self.assertTrue(written.startswith(original))
        self.assertEqual(written.count(BEGIN), 1)
        with self.assertRaises(StoreError):
            publish(self.store, path, "global", "", True, preview["sha256"])

    def test_malformed_markers_rejected(self):
        with self.assertRaises(StoreError):
            managed_document(BEGIN + "\nmissing end", "replacement")
        with self.assertRaises(StoreError):
            managed_document(END + "\n" + BEGIN, "replacement")

    def test_http_ui_write_read_and_origin_guard(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.store))
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 3)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request("POST", "/api/categories", json.dumps({"name": "自定义分类"}), {"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        category = json.loads(response.read())
        connection.request("GET", "/api/state")
        response = connection.getresponse()
        self.assertIn(category["id"], [row["id"] for row in json.loads(response.read())["categories"]])
        connection.request("POST", "/api/categories", json.dumps({"name": "不应保存"}), {"Content-Type": "application/json", "Origin": "https://example.invalid"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        self.remember(title="Plugin rule", scope="plugin", target="test-plugin")
        for legacy_field, value in (("match_mode", "exact"), ("plugin_id", "test-plugin")):
            connection.request("POST", "/api/render", json.dumps({
                "scope": "plugin", "target": "test-plugin", legacy_field: value,
            }), {"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            payload = json.loads(response.read())
            self.assertIn(legacy_field, payload["error"])
            self.assertNotIn("markdown", payload)


if __name__ == "__main__":
    unittest.main()
