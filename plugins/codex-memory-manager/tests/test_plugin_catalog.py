import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from memory_manager.catalog import MAX_MANIFEST_BYTES, discover_plugins, resolve_codex_home


class PluginCatalogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.home = self.directory / ".codex"

    def manifest(self, name="android-engineering-ops", version="3.3.9", market="engineering", portable=True, data=None):
        root = self.home / "plugins" / "cache" / market / name / version
        filename = root / "plugin.json" if portable else root / ".codex-plugin" / "plugin.json"
        filename.parent.mkdir(parents=True, exist_ok=True)
        filename.write_text(json.dumps(data if data is not None else {"name": name, "version": version}), encoding="utf-8")
        return root, filename

    def test_portable_identifier_is_separate_from_display_name(self):
        self.manifest(data={"name": "android-engineering-ops", "version": "3.3.9", "extensions": {
            "com.openai": {"interface": {"displayName": "Android Engineering Ops"}}
        }})
        result = discover_plugins(self.home)
        self.assertEqual(result["plugins"], [{
            "id": "android-engineering-ops", "display_name": "Android Engineering Ops",
            "marketplaces": ["engineering"], "versions": ["3.3.9"],
        }])
        self.assertEqual(result["source"], "local-cache")
        self.assertIn("不代表", result["note"])

    def test_legacy_manifest_and_compatibility_display_name(self):
        self.manifest(name="legacy", portable=False, data={"name": "legacy", "version": "3.3.9", "interface": {"displayName": "旧插件"}})
        root, _ = self.manifest(name="portable", data={"name": "portable", "version": "3.3.9"})
        overlay = root / ".codex-plugin" / "plugin.json"
        overlay.parent.mkdir()
        overlay.write_text(json.dumps({"name": "portable", "interface": {"displayName": "兼容显示名"}}), encoding="utf-8")
        plugins = {item["id"]: item for item in discover_plugins(self.home)["plugins"]}
        self.assertEqual(plugins["legacy"]["display_name"], "旧插件")
        self.assertEqual(plugins["portable"]["display_name"], "兼容显示名")

    def test_portable_name_and_inline_interface_take_precedence(self):
        root, _ = self.manifest(data={"name": "canonical-id", "version": "3.3.9", "extensions": {"com.openai": {"interface": {"displayName": "内联名字"}}}})
        overlay = root / ".codex-plugin" / "plugin.json"
        overlay.parent.mkdir()
        overlay.write_text(json.dumps({"name": "another-id", "interface": {"displayName": "另一个名字"}}), encoding="utf-8")
        plugin = discover_plugins(self.home)["plugins"][0]
        self.assertEqual(plugin["id"], "canonical-id")
        self.assertEqual(plugin["display_name"], "内联名字")

    def test_merges_versions_and_markets_without_using_mtime(self):
        self.manifest(version="3.3.9", market="first")
        self.manifest(version="3.3.10", market="first")
        self.manifest(version="3.3.9", market="second")
        result = discover_plugins(self.home)["plugins"]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["versions"], ["3.3.10", "3.3.9"])
        self.assertEqual(result[0]["marketplaces"], ["first", "second"])

    def test_invalid_manifest_does_not_hide_other_plugins(self):
        self.manifest(name="valid")
        for index, value in enumerate(([], {"name": "../escape"}, {"name": "Display Name"}, {"name": "two--hyphens"}, {"name": "bad..name"}, {"name": "valid-looking", "version": 12})):
            self.manifest(name="invalid-" + str(index), data=value)
        _, malformed = self.manifest(name="malformed")
        malformed.write_text("{not json", encoding="utf-8")
        _, oversized = self.manifest(name="oversized")
        oversized.write_bytes(b" " * (MAX_MANIFEST_BYTES + 1))
        self.assertEqual([item["id"] for item in discover_plugins(self.home)["plugins"]], ["valid"])

    def test_portable_invalid_manifest_does_not_fall_back_to_legacy_identity(self):
        root, filename = self.manifest(name="invalid")
        filename.write_text("{}", encoding="utf-8")
        overlay = root / ".codex-plugin" / "plugin.json"
        overlay.parent.mkdir()
        overlay.write_text('{"name":"legacy-identity"}', encoding="utf-8")
        self.assertEqual(discover_plugins(self.home)["plugins"], [])

    def test_missing_or_noncache_root_returns_empty_catalog(self):
        self.assertEqual(discover_plugins(self.home)["plugins"], [])
        self.manifest(name="cached")
        unrelated = self.home / "plugins" / "cached" / "nested" / "plugin.json"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text('{"name":"not-cached"}', encoding="utf-8")
        self.assertEqual([item["id"] for item in discover_plugins(self.home)["plugins"]], ["cached"])

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest("此运行环境不能创建符号链接：" + str(error))

    def test_skips_symlinked_directories_and_manifests(self):
        _, regular = self.manifest(name="regular")
        root, linked = self.manifest(name="linked-manifest")
        linked.unlink()
        self.symlink(linked, regular)
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "plugin.json").write_text('{"name":"linked-directory"}', encoding="utf-8")
        linked_directory = self.home / "plugins" / "cache" / "engineering" / "linked-version"
        linked_directory.mkdir()
        self.symlink(linked_directory / "3.3.9", outside, directory=True)
        self.assertEqual([item["id"] for item in discover_plugins(self.home)["plugins"]], ["regular"])

    def test_skips_symlinked_compatibility_directory_and_cache(self):
        root, manifest = self.manifest(name="legacy-link", portable=False)
        outside = self.directory / "overlay"
        manifest.parent.rename(outside)
        self.symlink(root / ".codex-plugin", outside, directory=True)
        self.assertEqual(discover_plugins(self.home)["plugins"], [])
        other_home = self.directory / "other-home"
        (other_home / "plugins").mkdir(parents=True)
        self.symlink(other_home / "plugins" / "cache", self.home / "plugins" / "cache", directory=True)
        self.assertEqual(discover_plugins(other_home)["plugins"], [])

    def test_explicit_native_codex_home_wins_over_cache_inference(self):
        root = self.home / "plugins" / "cache" / "engineering" / "codex-memory-manager" / "0.1.5"
        custom = self.directory / "custom-codex"
        actual = resolve_codex_home(root, {"CODEX_HOME": str(custom)}, self.directory)
        self.assertEqual(actual, custom)

    def test_only_exact_self_plugin_cache_layout_infers_home(self):
        root = self.home / "plugins" / "cache" / "engineering" / "codex-memory-manager" / "0.1.5"
        self.assertEqual(resolve_codex_home(root, {}, self.directory), self.home)
        fallback = self.directory / "user" / ".codex"
        for invalid in (root.parent, root / "extra", root.parent.parent / "another-plugin" / "0.1.5", self.directory / "source"):
            with self.subTest(root=invalid):
                self.assertEqual(resolve_codex_home(invalid, {}, self.directory / "user"), fallback)

    def test_invalid_explicit_home_does_not_enable_cache_inference(self):
        root = self.home / "plugins" / "cache" / "engineering" / "codex-memory-manager" / "0.1.5"
        fallback = self.directory / "user" / ".codex"
        self.assertEqual(resolve_codex_home(root, {"CODEX_HOME": "relative/path"}, self.directory / "user"), fallback)
        if os.name != "nt":
            self.assertEqual(resolve_codex_home(root, {"CODEX_HOME": r"C:\Users\Other\.codex"}, self.directory / "user"), fallback)

    def test_discovery_default_uses_resolved_home(self):
        self.manifest(name="discovered")
        with mock.patch("memory_manager.catalog.resolve_codex_home", return_value=self.home):
            self.assertEqual(discover_plugins()["plugins"][0]["id"], "discovered")


if __name__ == "__main__":
    unittest.main()
