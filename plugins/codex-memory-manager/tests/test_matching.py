"""Keep exact target matching separate from optional canonical plugin IDs."""

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
        self.db_path = Path(self.tmp.name) / "memory.sqlite3"
        self.store = Store(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **patch):
        return self.store.entry_create({"title": "匹配规则", "content": "规则内容", **patch})

    def plugin(self, plugin_id="android-engineering-ops", **patch):
        return self.create(scope="plugin", match_mode="plugin_id", plugin_id=plugin_id, **patch)

    def ids(self, filters):
        return {entry["id"] for entry in self.store.select_entries(filters)}

    def test_exact_remains_default_and_plugin_id_is_not_an_alias_for_target(self):
        global_entry = self.create()
        exact = self.create(scope="plugin", target="android-engineering-ops")
        identified = self.plugin()
        self.assertEqual((exact["match_mode"], exact["plugin_id"]), ("exact", ""))
        self.assertEqual((identified["target"], identified["match_mode"]), ("", "plugin_id"))
        self.assertEqual(self.ids({"scope": "plugin", "target": "android-engineering-ops"}), {exact["id"]})
        self.assertEqual(self.ids({"scope": "plugin", "plugin_id": "android-engineering-ops"}), {identified["id"]})
        self.assertEqual(self.ids({"scope": "plugin"}), {exact["id"], identified["id"]})
        self.assertEqual(self.ids({"scope": "global", "target": ""}), {global_entry["id"]})
        self.assertEqual(self.ids({"scope": "plugin", "match_mode": "exact"}), {exact["id"]})
        self.assertEqual(self.ids({"scope": "plugin", "match_mode": "plugin_id"}), {identified["id"]})
        self.assertEqual(self.ids({"scope": "project", "plugin_id": "android-engineering-ops"}), set())
        self.assertEqual(self.ids({"target": "", "plugin_id": "android-engineering-ops"}), {identified["id"]})
        self.assertEqual(self.ids({"scope": "plugin", "target": "Android Engineering Ops"}), set())

    def test_exact_mode_clears_unused_plugin_id(self):
        entry = self.create(scope="plugin", target="Free text target", plugin_id="unused-plugin")
        self.assertEqual(entry["plugin_id"], "")
        self.assertEqual(entry["target"], "Free text target")

    def test_switching_modes_preserves_history_and_requires_an_exact_target(self):
        entry = self.create(scope="plugin", target="Android Engineering Ops")
        identified = self.store.entry_update(entry["id"], {"match_mode": "plugin_id", "plugin_id": "android-engineering-ops"})
        self.assertEqual(identified["target"], "")
        self.assertEqual(identified["match_mode"], "plugin_id")
        with self.assertRaises(StoreError):
            self.store.entry_update(entry["id"], {"match_mode": "exact"})
        restored = self.store.entry_update(entry["id"], {"match_mode": "exact", "target": "My exact label"})
        self.assertEqual(restored["plugin_id"], "")
        self.assertEqual(restored["target"], "My exact label")
        self.assertEqual([row["entry"]["match_mode"] for row in self.store.history(entry["id"])],
                         ["exact", "plugin_id", "exact"])

    def test_plugin_id_validation_and_mixed_targets_are_atomic(self):
        baseline = self.store.snapshot()
        invalid_ids = ("", "UPPER", "Android Engineering Ops", "plugin@market", "plugin:skill",
                       "../plugin", "has/slash", "two--hyphens", "two..dots", "-plugin", "plugin.",
                       "x" * 65, "插件", None, [])
        for plugin_id in invalid_ids:
            with self.subTest(plugin_id=plugin_id), self.assertRaises(StoreError):
                self.plugin(plugin_id)
            self.assertEqual(self.store.snapshot(), baseline)
        for scope in ("global", "project", "skill"):
            with self.subTest(scope=scope), self.assertRaises(StoreError):
                self.create(scope=scope, match_mode="plugin_id", plugin_id="my-plugin")
        with self.assertRaisesRegex(StoreError, "target"):
            self.plugin(target="mixed target")
        with self.assertRaises(StoreError):
            self.create(match_mode="something-else")
        self.assertEqual(self.store.snapshot(), baseline)
        for valid_id in ("plugin", "a", "my-plugin", "company.plugin", "x" * 64):
            self.assertEqual(self.plugin(valid_id)["plugin_id"], valid_id)

    def test_invalid_or_ambiguous_search_filters_are_rejected(self):
        for filters in ({"target": "my target", "plugin_id": "my-plugin"},
                        {"plugin_id": "BadPlugin"}, {"plugin_id": []},
                        {"match_mode": []}, {"match_mode": "other"}):
            with self.subTest(filters=filters), self.assertRaises(StoreError):
                self.store.select_entries(filters)

    def test_refresh_unions_globals_and_both_modes_without_cross_matching(self):
        global_entry = self.create()
        exact = self.create(scope="plugin", target="android-engineering-ops")
        identified = self.plugin()
        self.plugin("other-plugin")
        default = self.store.refresh()
        self.assertEqual({entry["id"] for entry in default["entries"]}, {global_entry["id"]})
        exact_read = self.store.refresh([{"scope": "plugin", "target": "android-engineering-ops"}])
        self.assertEqual({entry["id"] for entry in exact_read["entries"]}, {global_entry["id"], exact["id"]})
        plugin_read = self.store.refresh([{"scope": "plugin", "plugin_id": "android-engineering-ops"}])
        self.assertEqual({entry["id"] for entry in plugin_read["entries"]}, {global_entry["id"], identified["id"]})
        both = self.store.refresh([{"scope": "plugin", "plugin_id": "android-engineering-ops"},
                                   {"scope": "plugin", "target": "android-engineering-ops"}])
        self.assertEqual({entry["id"] for entry in both["entries"]},
                         {global_entry["id"], exact["id"], identified["id"]})
        self.assertIn({"scope": "plugin", "target": "", "match_mode": "plugin_id",
                       "plugin_id": "android-engineering-ops", "count": 1}, both["available_targets"])
        self.assertIn({"scope": "plugin", "target": "android-engineering-ops", "match_mode": "exact",
                       "plugin_id": "", "count": 1}, both["available_targets"])

    def test_invalid_plugin_context_does_not_acknowledge_pending_revision(self):
        self.plugin()
        baseline = self.store.application_status()
        for context in ({"scope": "plugin", "target": "my target", "plugin_id": "my-plugin"},
                        {"scope": "skill", "plugin_id": "my-plugin"},
                        {"scope": "plugin", "plugin_id": "BadPlugin"},
                        {"scope": "plugin", "plugin_id": ""}):
            with self.subTest(context=context), self.assertRaises(StoreError):
                self.store.refresh([context])
            self.assertEqual(self.store.application_status(), baseline)

    def test_export_import_preserves_both_modes_and_old_payload_defaults(self):
        exact = self.create(scope="plugin", target="Old exact label")
        identified = self.plugin()
        exported = self.store.export_data()
        self.assertEqual(exported["schema_version"], 2)
        second = Store(Path(self.tmp.name) / "roundtrip.sqlite3")
        second.import_data(exported)
        self.assertEqual({entry["id"]: (entry["match_mode"], entry["target"], entry["plugin_id"])
                          for entry in second.snapshot()["entries"]},
                         {exact["id"]: ("exact", "Old exact label", ""),
                          identified["id"]: ("plugin_id", "", "android-engineering-ops")})
        second.import_data({"schema_version": 1,
                            "entries": [{"title": "Old exported memory", "scope": "plugin", "target": "custom name"}]})
        legacy = next(entry for entry in second.snapshot()["entries"] if entry["title"] == "Old exported memory")
        self.assertEqual((legacy["match_mode"], legacy["target"], legacy["plugin_id"]), ("exact", "custom name", ""))

    def test_import_accepts_both_schema_versions_and_missing_version_defaults_to_legacy(self):
        for version in (None, 1, 2):
            with self.subTest(version=version):
                payload = {"entries": [{"title": f"Legacy shape {version}", "scope": "plugin", "target": "free label"}]}
                if version is not None:
                    payload["schema_version"] = version
                self.store.import_data(payload)
                imported = next(entry for entry in self.store.snapshot()["entries"]
                                if entry["title"] == f"Legacy shape {version}")
                self.assertEqual((imported["match_mode"], imported["plugin_id"]), ("exact", ""))
        self.store.import_data({"schema_version": 2, "entries": [
            {"title": "ID shape", "scope": "plugin", "match_mode": "plugin_id", "plugin_id": "my-plugin"},
        ]})
        self.assertEqual(len(self.store.select_entries({"plugin_id": "my-plugin"})), 1)

    def test_import_unknown_or_invalid_schema_versions_is_atomic(self):
        self.create()
        baseline = self.store.snapshot()
        for version in (0, 3, -1, "1", "2", True, None, []):
            with self.subTest(version=version), self.assertRaisesRegex(StoreError, "版本"):
                self.store.import_data({"schema_version": version, "entries": [{"title": "Must not import"}]})
            self.assertEqual(self.store.snapshot(), baseline)

    def test_migrate_real_old_schema_and_restore_old_history_without_fields(self):
        legacy_path = Path(self.tmp.name) / "old-format.sqlite3"
        old = {"id": "old-entry", "title": "Old memory", "content": "Old content", "category_id": "preferences",
               "scope": "plugin", "target": "Android Engineering Ops", "tags": [], "priority": 50,
               "enabled": True, "deleted": False, "created_at": "2026-01-01T00:00:00+00:00",
               "updated_at": "2026-01-01T00:00:00+00:00", "version": 1}
        with closing(sqlite3.connect(legacy_path)) as conn, conn:
            conn.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO metadata VALUES ('revision','7');
                CREATE TABLE categories (id TEXT PRIMARY KEY,name TEXT NOT NULL,parent_id TEXT,
                                         created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE entries (id TEXT PRIMARY KEY,title TEXT NOT NULL,content TEXT NOT NULL,
                    category_id TEXT NOT NULL,scope TEXT NOT NULL,target TEXT NOT NULL,tags TEXT NOT NULL,
                    priority INTEGER NOT NULL,enabled INTEGER NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,version INTEGER NOT NULL);
                CREATE TABLE versions (id INTEGER PRIMARY KEY AUTOINCREMENT,entry_id TEXT NOT NULL,
                    version INTEGER NOT NULL,saved_at TEXT NOT NULL,entry_json TEXT NOT NULL,
                    UNIQUE(entry_id,version));
            """)
            conn.execute("INSERT INTO categories VALUES ('preferences','偏好',NULL,?,?)",
                         (old["created_at"], old["updated_at"]))
            conn.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(json.dumps(value) if key == "tags" else value for key, value in old.items()))
            conn.execute("INSERT INTO versions(entry_id,version,saved_at,entry_json) VALUES (?,?,?,?)",
                         (old["id"], 1, old["updated_at"], json.dumps(old)))
        migrated = Store(legacy_path)
        snapshot = migrated.snapshot()
        self.assertEqual(snapshot["revision"], 7)
        self.assertEqual(snapshot["entries"], [{**old, "match_mode": "exact", "plugin_id": ""}])
        self.assertEqual(migrated.history(old["id"])[0]["entry"], old)
        migrated.entry_update(old["id"], {"match_mode": "plugin_id", "plugin_id": "android-engineering-ops"})
        restored = migrated.restore_version(old["id"], migrated.history(old["id"])[-1]["version_id"])
        self.assertEqual((restored["match_mode"], restored["target"], restored["plugin_id"]),
                         ("exact", old["target"], ""))
        self.assertEqual(restored["version"], 3)
        self.assertEqual(migrated.snapshot()["revision"], 9)
        self.assertEqual(Store(legacy_path).snapshot(), migrated.snapshot())


if __name__ == "__main__":
    unittest.main()
