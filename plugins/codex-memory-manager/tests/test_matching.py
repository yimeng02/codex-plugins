"""Exact scope/target matching and preservation of older saved formats."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from memory_manager.store import Store, StoreError


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "memory.sqlite3"
        self.store = Store(self.db_path)

    def create(self, **patch):
        return self.store.entry_create({"title": "匹配规则", "content": "规则内容", **patch})

    def ids(self, filters):
        return {entry["id"] for entry in self.store.select_entries(filters)}

    def legacy_record(self, target="android-engineering-ops"):
        entry = self.create(scope="plugin", target=target)
        old = {**entry, "target": "", "match_mode": "plugin_id", "plugin_id": target}
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE entries SET target='',match_mode='plugin_id',plugin_id=? WHERE id=?",
                         (target, entry["id"]))
            conn.execute("UPDATE versions SET entry_json=? WHERE entry_id=? AND version=1",
                         (json.dumps(old), entry["id"]))
        return old

    def test_scope_and_target_are_literal_and_independent(self):
        global_entry = self.create()
        exact = self.create(scope="plugin", target="android-engineering-ops")
        display = self.create(scope="plugin", target="Android Engineering Ops")
        other_scope = self.create(scope="skill", target="android-engineering-ops")
        self.assertEqual(self.ids({"scope": "plugin", "target": exact["target"]}), {exact["id"]})
        self.assertEqual(self.ids({"scope": "plugin", "target": display["target"]}), {display["id"]})
        self.assertEqual(self.ids({"scope": "skill", "target": exact["target"]}), {other_scope["id"]})
        self.assertEqual(self.ids({"scope": "plugin", "target": "ANDROID-ENGINEERING-OPS"}), set())
        self.assertEqual(self.ids({"scope": "plugin", "target": "android-engineering"}), set())
        self.assertEqual(self.ids({"scope": "global", "target": ""}), {global_entry["id"]})
        self.assertNotIn("match_mode", exact)
        self.assertNotIn("plugin_id", exact)

    def test_removed_write_fields_are_rejected_without_changing_data(self):
        entry = self.create(scope="plugin", target="my-plugin")
        baseline = self.store.snapshot()
        for patch in ({"plugin_id": "my-plugin"}, {"match_mode": "plugin_id"}, {"match_mode": "exact"}):
            with self.subTest(patch=patch):
                with self.assertRaises(StoreError):
                    self.create(**patch)
                with self.assertRaises(StoreError):
                    self.store.entry_update(entry["id"], patch)
                self.assertEqual(self.store.snapshot(), baseline)

    def test_removed_search_and_context_selectors_are_rejected(self):
        self.create()
        baseline = self.store.application_status()
        for filters in ({"plugin_id": "my-plugin"}, {"match_mode": "exact"},
                        {"scope": "plugin", "target": "my-plugin", "plugin_id": "my-plugin"}):
            with self.subTest(filters=filters), self.assertRaises(StoreError):
                self.store.select_entries(filters)
        for context in ({"scope": "plugin", "plugin_id": "my-plugin"},
                        {"scope": "plugin", "target": "my-plugin", "match_mode": "exact"}):
            with self.subTest(context=context), self.assertRaises(StoreError):
                self.store.refresh([context])
        self.assertEqual(self.store.application_status(), baseline)

    def test_global_normalization_and_non_global_targets_remain_required(self):
        entry = self.create(target="leftover target")
        self.assertEqual(entry["target"], "")
        for scope in ("plugin", "skill", "project"):
            with self.subTest(scope=scope), self.assertRaises(StoreError):
                self.create(scope=scope)
        spaced = self.create(scope="plugin", target=" My exact target ")
        self.assertEqual(self.ids({"scope": "plugin", "target": " My exact target "}), {spaced["id"]})
        self.assertEqual(self.ids({"scope": "plugin", "target": "My exact target"}), set())

    def test_legacy_records_are_read_without_rewriting_storage_or_history(self):
        old = self.legacy_record()
        with closing(sqlite3.connect(self.db_path)) as conn:
            before = conn.execute("SELECT * FROM entries").fetchall()
        reopened = Store(self.db_path)
        visible = reopened.snapshot()["entries"][0]
        self.assertEqual(visible["target"], old["plugin_id"])
        self.assertNotIn("plugin_id", visible)
        self.assertNotIn("match_mode", visible)
        self.assertEqual(reopened.select_entries({"scope": "plugin", "target": old["plugin_id"]}), [visible])
        refreshed = reopened.refresh([{"scope": "plugin", "target": old["plugin_id"]}])
        self.assertEqual(refreshed["entries"], [visible])
        self.assertEqual(refreshed["available_targets"], [{"scope": "plugin", "target": old["plugin_id"], "count": 1}])
        self.assertEqual(refreshed["revision"], 1)
        self.assertEqual(reopened.history(old["id"])[0]["entry"], old)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT * FROM entries").fetchall(), before)

    def test_explicit_edit_and_restore_preserve_the_legacy_target_and_snapshot(self):
        old = self.legacy_record()
        original = self.store.history(old["id"])[0]
        self.store.entry_update(old["id"], {"target": "new target", "content": "new content"})
        restored = self.store.restore_version(old["id"], original["version_id"])
        self.assertEqual(restored["target"], old["plugin_id"])
        self.assertEqual(restored["content"], old["content"])
        self.assertEqual(restored["version"], 3)
        self.assertEqual(self.store.history(old["id"])[-1]["entry"], old)
        self.assertNotIn("plugin_id", restored)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT match_mode,plugin_id FROM entries WHERE id=?", (old["id"],)).fetchone(), ("exact", ""))

    def test_export_roundtrip_uses_only_exact_targets(self):
        exact = self.create(scope="plugin", target="Display name")
        old = self.legacy_record()
        data = self.store.export_data()
        self.assertEqual(data["schema_version"], 3)
        self.assertTrue(all("match_mode" not in e and "plugin_id" not in e for e in data["entries"]))
        second = Store(Path(self.tmp.name) / "roundtrip.sqlite3")
        second.import_data(data)
        self.assertEqual({e["id"]: e["target"] for e in second.snapshot()["entries"]},
                         {exact["id"]: exact["target"], old["id"]: old["plugin_id"]})

    def test_import_accepts_legacy_and_current_formats(self):
        for version in (None, 1, 2, 3):
            payload = {"entries": [{"title": str(version), "scope": "plugin", "target": "exact label"}]}
            if version is not None:
                payload["schema_version"] = version
            self.store.import_data(payload)
        self.store.import_data({"schema_version": 2, "entries": [{"title": "Legacy ID", "scope": "plugin",
            "target": "", "match_mode": "plugin_id", "plugin_id": "my-plugin"}]})
        self.assertEqual(len(self.store.select_entries({"scope": "plugin", "target": "my-plugin"})), 1)
        self.assertTrue(all("match_mode" not in e and "plugin_id" not in e for e in self.store.snapshot()["entries"]))

    def test_ambiguous_legacy_import_and_unknown_versions_are_atomic(self):
        self.create()
        baseline = self.store.snapshot()
        for version in (0, 4, -1, "1", True, None, []):
            with self.subTest(version=version), self.assertRaises(StoreError):
                self.store.import_data({"schema_version": version, "entries": [{"title": "must not import"}]})
            self.assertEqual(self.store.snapshot(), baseline)
        with self.assertRaises(StoreError):
            self.store.import_data({"schema_version": 2, "entries": [{"title": "ambiguous", "scope": "plugin",
                "target": "different", "match_mode": "plugin_id", "plugin_id": "my-plugin"}]})
        self.assertEqual(self.store.snapshot(), baseline)


if __name__ == "__main__":
    unittest.main()
