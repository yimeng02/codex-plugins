"""Run with python -m unittest discover -s tests -v."""

import tempfile
import unittest
from pathlib import Path

from memory_manager.store import Store, StoreError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "memories.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **patch):
        return self.store.entry_create({"title": "验证构建产物", "content": "记录产物路径", **patch})

    def test_defaults_persist_and_versions_increment(self):
        self.assertEqual(len(self.store.snapshot()["categories"]), 6)
        entry = self.create(tags=["Android", "Android"])
        self.assertEqual(entry["tags"], ["Android"])
        updated = self.store.entry_update(entry["id"], {"content": "记录产物路径和校验值"})
        self.assertEqual(updated["version"], 2)
        reopened = Store(Path(self.tmp.name) / "memories.sqlite3")
        self.assertEqual(reopened.snapshot()["entries"][0], updated)
        self.assertEqual(reopened.snapshot()["revision"], 2)

    def test_import_validates_everything_before_mutation(self):
        baseline = self.store.snapshot()
        with self.assertRaises(StoreError):
            self.store.import_data({
                "categories": [{"id": "new", "name": "新分类"}],
                "entries": [{"id": "valid", "title": "应回滚", "category_id": "new"},
                            {"id": "invalid", "title": "无效优先级", "priority": 101}],
            })
        self.assertEqual(self.store.snapshot(), baseline)

    def test_import_merges_parent_graph_and_creates_local_versions(self):
        entry = self.create()
        exported = self.store.export_data()
        exported["entries"][0]["title"] = "导入后的标题"
        exported["entries"][0]["version"] = 900
        exported["categories"] += [{"id": "child", "name": "子", "parent_id": "parent"},
                                  {"id": "parent", "name": "父"}]
        result = self.store.import_data(exported)
        self.assertEqual(result["entries"], 1)
        updated = next(item for item in self.store.snapshot()["entries"] if item["id"] == entry["id"])
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["title"], "导入后的标题")
        self.assertEqual(len(self.store.history(entry["id"])), 2)

    def test_duplicate_import_ids_are_rejected_atomically(self):
        baseline = self.store.snapshot()
        with self.assertRaises(StoreError):
            self.store.import_data({"entries": [{"id": "same", "title": "一"}, {"id": "same", "title": "二"}]})
        self.assertEqual(self.store.snapshot(), baseline)

    def test_restore_history_creates_new_version_and_restores_deleted(self):
        original = self.create()
        first_version = self.store.history(original["id"])[0]["version_id"]
        self.store.entry_update(original["id"], {"title": "修改后的标题"})
        self.store.entry_delete(original["id"])
        self.assertEqual(self.store.select_entries(), [])
        restored = self.store.restore_version(original["id"], first_version)
        self.assertFalse(restored["deleted"])
        self.assertEqual(restored["title"], original["title"])
        self.assertEqual(restored["version"], 4)
        self.assertEqual([v["version"] for v in self.store.history(original["id"])], [4, 3, 2, 1])

    def test_history_cannot_restore_a_different_entry(self):
        first, second = self.create(), self.create(title="另一个条目")
        version_id = self.store.history(first["id"])[0]["version_id"]
        with self.assertRaises(StoreError) as caught:
            self.store.restore_version(second["id"], version_id)
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.store.history(second["id"])[0]["version"], 1)

    def test_bulk_missing_entry_rolls_back_everything(self):
        entry = self.create()
        baseline = self.store.snapshot()
        with self.assertRaises(StoreError):
            self.store.bulk({"ids": [entry["id"], "does-not-exist"], "action": "disable"})
        self.assertEqual(self.store.snapshot(), baseline)
        self.assertEqual(len(self.store.history(entry["id"])), 1)

    def test_bulk_deleted_entry_rolls_back_and_restore_works(self):
        first, second = self.create(), self.create(title="第二条")
        self.store.entry_delete(second["id"])
        baseline = self.store.snapshot()
        with self.assertRaises(StoreError):
            self.store.bulk({"ids": [first["id"], second["id"]], "action": "disable"})
        self.assertEqual(self.store.snapshot(), baseline)
        self.store.bulk({"ids": [second["id"]], "action": "restore"})
        result = self.store.bulk({"ids": [first["id"], second["id"], first["id"]], "action": "disable"})
        self.assertEqual(result["count"], 2)
        self.assertEqual(self.store.select_entries(), [])
        self.assertEqual(len(self.store.select_entries({"enabled": False})), 2)

    def test_category_cycle_and_nonempty_delete_are_rejected(self):
        parent = self.store.category_create({"name": "父分类"})
        child = self.store.category_create({"name": "子分类", "parent_id": parent["id"]})
        baseline = self.store.snapshot()
        with self.assertRaises(StoreError):
            self.store.category_update(parent["id"], {"parent_id": child["id"]})
        self.assertEqual(self.store.snapshot(), baseline)
        with self.assertRaises(StoreError) as caught:
            self.store.category_delete(parent["id"])
        self.assertEqual(caught.exception.status, 409)
        entry = self.create(category_id=child["id"])
        self.store.entry_delete(entry["id"])
        with self.assertRaises(StoreError):
            self.store.category_delete(child["id"])

    def test_import_cycle_rolls_back(self):
        baseline = self.store.snapshot()
        with self.assertRaises(StoreError):
            self.store.import_data({"categories": [{"id": "a", "name": "A", "parent_id": "b"},
                                                   {"id": "b", "name": "B", "parent_id": "a"}]})
        self.assertEqual(self.store.snapshot(), baseline)

    def test_import_missing_ancestor_is_a_validation_error(self):
        baseline = self.store.snapshot()
        with self.assertRaises(StoreError):
            self.store.import_data({"categories": [{"id": "child", "name": "Child", "parent_id": "parent"},
                                                   {"id": "parent", "name": "Parent", "parent_id": "missing"}]})
        self.assertEqual(self.store.snapshot(), baseline)

    def test_filter_scope_target_query_and_priority(self):
        global_entry = self.create(priority=10)
        project_entry = self.create(title="Project check", scope="project", target="/projects/demo", tags=["验收"], priority=90)
        self.assertEqual(self.store.select_entries()[0]["id"], project_entry["id"])
        self.assertEqual([entry["id"] for entry in self.store.select_entries({"query": "PROJECT"})], [project_entry["id"]])
        self.assertEqual([entry["id"] for entry in self.store.select_entries({"query": "验收"})], [project_entry["id"]])
        self.assertEqual([entry["id"] for entry in self.store.select_entries({"scope": "global"})], [global_entry["id"]])
        self.assertEqual(self.store.select_entries({"target": "/other"}), [])

    def test_global_target_is_empty_even_after_scope_changes(self):
        entry = self.create(scope="global", target="/stale-target")
        self.assertEqual(entry["target"], "")
        self.store.entry_update(entry["id"], {"scope": "project", "target": "/projects/demo"})
        restored = self.store.entry_update(entry["id"], {"scope": "global"})
        self.assertEqual(restored["target"], "")
        self.assertEqual([item["id"] for item in self.store.select_entries({"scope": "global", "target": "", "enabled": True})], [entry["id"]])

    def test_export_import_keeps_enabled_and_deleted_entries(self):
        active = self.create(title="启用")
        disabled = self.create(title="禁用", enabled=False)
        deleted = self.create(title="删除", enabled=False)
        self.store.entry_delete(deleted["id"])
        second = Store(Path(self.tmp.name) / "roundtrip.sqlite3")
        result = second.import_data(self.store.export_data())
        self.assertEqual(result["entries"], 3)
        restored = {entry["id"]: entry for entry in second.snapshot()["entries"]}
        self.assertEqual(set(restored), {active["id"], disabled["id"], deleted["id"]})
        self.assertFalse(restored[disabled["id"]]["enabled"])
        self.assertTrue(restored[deleted["id"]]["deleted"])
        second.bulk({"ids": [deleted["id"]], "action": "restore"})
        recovered = next(entry for entry in second.snapshot()["entries"] if entry["id"] == deleted["id"])
        self.assertFalse(recovered["deleted"])
        self.assertFalse(recovered["enabled"])
        self.assertEqual([item["id"] for item in second.select_entries()], [active["id"]])

    def test_bad_types_and_unknown_fields_are_rejected_without_writes(self):
        baseline = self.store.snapshot()
        for patch in ({"priority": True}, {"enabled": 1}, {"tags": "tag"}, {"scope": []},
                      {"scope": "project"}, {"deleted": False}):
            with self.subTest(patch=patch), self.assertRaises(StoreError):
                self.create(**patch)
        self.assertEqual(self.store.snapshot(), baseline)


if __name__ == "__main__":
    unittest.main()
