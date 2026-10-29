"""Revision receipts and first-use choices never change stored memories."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from memory_manager.store import Store, StoreError


class ApplicationStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "memory.sqlite3"
        self.store = Store(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **patch):
        return self.store.entry_create({"title": "规则", "content": "需要应用的内容", **patch})

    def test_empty_database_has_no_pending_refresh(self):
        expected = {"revision": 0, "last_read_revision": None, "last_read_at": None,
                    "needs_refresh": False, "onboarding_decision": "pending"}
        self.assertEqual(self.store.application_status(), expected)
        self.assertEqual(self.store.snapshot()["application"], expected)
        receipt = self.store.refresh()
        self.assertEqual(receipt["entries"], [])
        self.assertEqual(receipt["application"]["last_read_revision"], 0)
        self.assertFalse(receipt["application"]["needs_refresh"])

    def test_edits_require_refresh_and_read_receipt_does_not_bump_revision(self):
        entry = self.create()
        self.assertTrue(self.store.application_status()["needs_refresh"])
        first = self.store.refresh()
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["application"]["last_read_revision"], 1)
        self.assertFalse(first["application"]["needs_refresh"])
        datetime.fromisoformat(first["application"]["last_read_at"])
        self.assertIn("不代表所有 Codex 聊天已自动应用", first["note"])
        self.store.entry_update(entry["id"], {"content": "编辑后的规则"})
        status = self.store.application_status()
        self.assertEqual(status["revision"], 2)
        self.assertEqual(status["last_read_revision"], 1)
        self.assertTrue(status["needs_refresh"])
        refreshed = self.store.refresh()
        self.assertFalse(refreshed["application"]["needs_refresh"])
        self.assertEqual(refreshed["entries"][0]["content"], "编辑后的规则")
        self.assertEqual(self.store.snapshot()["revision"], 2)
        self.assertEqual(len(self.store.history(entry["id"])), 2)

    def test_management_reads_never_acknowledge_application(self):
        entry = self.create()
        for read in (self.store.snapshot, self.store.select_entries, self.store.export_data,
                     lambda: self.store.history(entry["id"])):
            read()
            self.assertIsNone(self.store.application_status()["last_read_revision"])
            self.assertTrue(self.store.application_status()["needs_refresh"])

    def test_refresh_unions_global_with_exact_contexts_and_hides_other_bodies(self):
        global_entry = self.create(title="全局", priority=100)
        plugin = self.create(scope="plugin", target="android-engineering-ops", priority=90)
        skill = self.create(scope="skill", target="android-engineering-ops:android-patch-capture")
        project = self.create(scope="project", target="/projects/demo")
        self.create(scope="plugin", target="Android Engineering Ops", content="不相关正文")
        self.create(scope="plugin", target="android-engineering-ops-extra", content="另一不相关正文")
        self.create(scope="plugin", target="disabled", enabled=False)
        deleted = self.create(scope="plugin", target="deleted")
        self.store.entry_delete(deleted["id"])
        result = self.store.refresh([
            {"scope": "plugin", "target": "android-engineering-ops"},
            {"scope": "project", "target": "/projects/demo"},
            {"scope": "skill", "target": "android-engineering-ops:android-patch-capture"},
            {"scope": "plugin", "target": "android-engineering-ops"},
        ])
        self.assertEqual([item["id"] for item in result["entries"]][:2], [global_entry["id"], plugin["id"]])
        self.assertEqual({item["id"] for item in result["entries"]},
                         {global_entry["id"], plugin["id"], project["id"], skill["id"]})
        targets = {(item["scope"], item["target"]): item["count"] for item in result["available_targets"]}
        self.assertEqual(targets[("plugin", "android-engineering-ops")], 1)
        self.assertEqual(targets[("plugin", "Android Engineering Ops")], 1)
        self.assertNotIn(("plugin", "disabled"), targets)
        self.assertNotIn(("plugin", "deleted"), targets)
        self.assertTrue(all(set(item) == {"scope", "target", "match_mode", "plugin_id", "count"}
                            for item in result["available_targets"]))
        self.assertEqual([item["id"] for item in self.store.refresh()["entries"]], [global_entry["id"]])

    def test_disabled_and_deleted_globals_are_excluded(self):
        active = self.create()
        self.create(enabled=False)
        deleted = self.create()
        self.store.entry_delete(deleted["id"])
        self.assertEqual([item["id"] for item in self.store.refresh()["entries"]], [active["id"]])

    def test_invalid_contexts_never_record_a_read(self):
        self.create()
        baseline = self.store.application_status()
        invalid = ["bad", {}, [None], [{"scope": "global", "target": ""}],
                   [{"scope": "plugin", "target": " "}], [{"scope": [], "target": "plugin"}],
                   [{"scope": "plugin"}], [{"scope": "project", "target": 12}],
                   [{"scope": "plugin", "target": "a", "unexpected": True}],
                   [{"scope": "plugin", "target": "valid"}, {"scope": "unknown", "target": "bad"}]]
        for contexts in invalid:
            with self.subTest(contexts=contexts), self.assertRaises(StoreError):
                self.store.refresh(contexts)
            self.assertEqual(self.store.application_status(), baseline)
        self.store.refresh()
        self.create()
        previous_receipt = self.store.application_status()
        with self.assertRaises(StoreError):
            self.store.refresh([{"scope": "project", "target": ""}])
        self.assertEqual(self.store.application_status(), previous_receipt)

    def test_failed_entry_decode_does_not_record_read(self):
        entry = self.create()
        baseline = self.store.application_status()
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE entries SET tags='invalid json' WHERE id=?", (entry["id"],))
        with self.assertRaises(ValueError):
            self.store.refresh()
        self.assertEqual(self.store.application_status(), baseline)

    def test_onboarding_decisions_persist_without_memory_edits(self):
        entry = self.create()
        history = self.store.history(entry["id"])
        for decision in ("configured", "declined", "deferred"):
            status = self.store.set_onboarding_decision(decision)
            self.assertEqual(status["onboarding_decision"], decision)
            reopened = Store(self.db_path)
            self.assertEqual(reopened.application_status()["onboarding_decision"], decision)
            self.assertEqual(reopened.snapshot()["entries"], [entry])
            self.assertEqual(reopened.snapshot()["revision"], 1)
            self.assertEqual(reopened.history(entry["id"]), history)
        baseline = self.store.application_status()
        for invalid in (None, [], "pending", "accepted", True):
            with self.subTest(invalid=invalid), self.assertRaises(StoreError):
                self.store.set_onboarding_decision(invalid)
            self.assertEqual(self.store.application_status(), baseline)

    def test_pre_application_state_database_keeps_entries_categories_and_history(self):
        entry = self.create(scope="plugin", target="existing-plugin")
        categories = self.store.snapshot()["categories"]
        history = self.store.history(entry["id"])
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT key FROM metadata").fetchall(), [("revision",)])
        reopened = Store(self.db_path)
        snapshot = reopened.snapshot()
        self.assertEqual(snapshot["entries"], [entry])
        self.assertEqual(snapshot["categories"], categories)
        self.assertEqual(reopened.history(entry["id"]), history)
        self.assertTrue(snapshot["application"]["needs_refresh"])
        reopened.refresh([{"scope": "plugin", "target": "existing-plugin"}])
        final = Store(self.db_path).snapshot()
        self.assertEqual(final["entries"], [entry])
        self.assertFalse(final["application"]["needs_refresh"])
        self.assertEqual(final["revision"], 1)


if __name__ == "__main__":
    unittest.main()
