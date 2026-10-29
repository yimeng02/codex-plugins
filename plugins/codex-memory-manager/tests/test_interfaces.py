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

from memory_manager.app import dispatch, handler_for, managed_document, publish, rpc_response, BEGIN, END
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

    def test_mcp_matchers_and_render_do_not_cross_match_same_text(self):
        exact = self.remember(title="Exact", scope="plugin", target="test-plugin")
        identified = self.remember(title="Plugin ID", scope="plugin", target="",
                                   match_mode="plugin_id", plugin_id="test-plugin")
        for args, expected in (({"scope": "plugin", "target": "test-plugin"}, exact),
                               ({"scope": "plugin", "plugin_id": "test-plugin"}, identified)):
            response = rpc_response(self.store, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "memory_search", "arguments": args}})
            body = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual([e["id"] for e in body["entries"]], [expected["id"]])
            render_args = dict(args)
            if "plugin_id" in args:
                render_args["match_mode"] = "plugin_id"
            rendered = dispatch(self.store, "memory_render", render_args)
            self.assertEqual(rendered["count"], 1)
            self.assertIn("### " + expected["title"], rendered["markdown"])
        mixed = rpc_response(self.store, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "memory_search", "arguments": {
                "scope": "plugin", "target": "test-plugin", "plugin_id": "test-plugin"}}})
        self.assertTrue(mixed["result"]["isError"])

    def test_cli_publish_accepts_plugin_id_without_leaking_exact_entries(self):
        self.remember(title="Exact must stay out", scope="plugin", target="test-plugin")
        self.remember(title="Included plugin memory", scope="plugin", match_mode="plugin_id", plugin_id="test-plugin")
        path = self.root / "AGENTS.md"
        run = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "run.py"),
            "--db", str(self.root / "memory.sqlite3"), "publish", "--file", str(path),
            "--scope", "plugin", "--match-mode", "plugin_id", "--plugin-id", "test-plugin"],
            capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        preview = json.loads(run.stdout)
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["count"], 1)
        self.assertIn("Included plugin memory", preview["content"])
        self.assertNotIn("Exact must stay out", preview["content"])
        self.assertFalse(path.exists())

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


if __name__ == "__main__":
    unittest.main()
