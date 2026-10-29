"""Installer contract checks using only the Python standard library.

Run from the project root with ``python -m unittest discover -s tests``.
These checks use temporary payloads and never change the user's Codex setup.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "memory_manager_installer_under_test", PROJECT_ROOT / "install.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load the project installer")
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    @staticmethod
    def write(root: Path, relative: str, text: str = "fixture\n") -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def payload(self, name: str = "source") -> Path:
        root = self.root / name
        self.write(root, "plugin.json", '{"name":"codex-memory-manager"}\n')
        self.write(root, "run.py", "print('fixture')\n")
        self.write(root, "memory_manager/__init__.py", "")
        self.write(root, "web/index.html", "<main>记忆管理</main>\n")
        self.write(root, "skills/example/SKILL.md", "# Example\n")
        return root

    def test_cli_json_accepts_one_object_and_rejects_logs_or_other_values(self):
        self.assertEqual(
            installer.load_json(' \n {"installed": []} \t\n'),
            {"installed": []},
        )
        invalid = (
            "",
            "[]",
            "null",
            "true",
            '"text"',
            'status: ready\n{"installed": []}',
            '{"installed": []}\nfinished',
            "{} {}",
            "{broken}",
        )
        for stdout in invalid:
            with self.subTest(stdout=stdout):
                with self.assertRaises(installer.InstallError):
                    installer.load_json(stdout, label="plugin list")

    def test_plugin_match_requires_both_name_and_marketplace(self):
        expected = {
            "name": "codex-memory-manager",
            "marketplaceName": "personal-memory-tools",
            "installed": True,
            "enabled": True,
        }
        payload = {
            "installed": [
                {**expected, "marketplaceName": "another-marketplace"},
                {**expected, "name": "another-plugin"},
                expected,
            ]
        }
        self.assertEqual(installer.matching_plugin(payload), expected)
        self.assertIsNone(installer.matching_plugin({"installed": []}))
        self.assertIsNone(
            installer.matching_plugin(payload, marketplace="missing")
        )

    def test_duplicate_plugin_matches_fail_instead_of_selecting_one(self):
        row = {
            "name": "codex-memory-manager",
            "marketplaceName": "personal-memory-tools",
            "installed": True,
            "enabled": True,
        }
        with self.assertRaises(installer.InstallError):
            installer.matching_plugin({"installed": [row, dict(row)]})

    def test_enabled_check_requires_literal_true_for_both_fields(self):
        installer.require_enabled({"installed": True, "enabled": True})
        invalid = (
            {},
            {"installed": False, "enabled": True},
            {"installed": True, "enabled": False},
            {"installed": True},
            {"installed": "true", "enabled": True},
            {"installed": True, "enabled": "true"},
            {"installed": 1, "enabled": 1},
        )
        for row in invalid:
            with self.subTest(row=row):
                with self.assertRaises(installer.InstallError):
                    installer.require_enabled(row)

    def test_mcp_uses_the_shared_absolute_memory_directory(self):
        python = Path(sys.executable).resolve()
        data = (self.root / "shared memory" / "data").resolve()
        configuration = installer.build_mcp(python, data)
        server = configuration["mcpServers"]["memory-manager"]
        self.assertEqual(server["command"], python.name)
        self.assertFalse(Path(server["command"]).is_absolute())
        self.assertEqual(server["env"]["PATH"].split(os.pathsep)[0], str(python.parent))
        self.assertEqual(server["args"], ["${PLUGIN_ROOT}/run.py", "mcp"])
        self.assertEqual(server["cwd"], "${PLUGIN_ROOT}")
        self.assertEqual(server["env"]["CODEX_MEMORY_HOME"], str(data))
        self.assertTrue(Path(server["env"]["CODEX_MEMORY_HOME"]).is_absolute())

    def test_digest_is_stable_and_changes_when_source_changes(self):
        source = self.payload()
        digest = installer.payload_digest(source)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(installer.payload_digest(source), digest)
        same_payload = self.payload("same-payload-at-another-path")
        self.assertEqual(installer.payload_digest(same_payload), digest)
        os.utime(source / "run.py", (1, 1))
        self.assertEqual(installer.payload_digest(source), digest)
        self.write(source, "run.py", "print('updated source')\n")
        self.assertNotEqual(installer.payload_digest(source), digest)

    def test_digest_excludes_databases_caches_and_build_outputs(self):
        source = self.payload()
        digest = installer.payload_digest(source)
        for relative in (
            "memory.db",
            "data/memory.db",
            "memory_manager/__pycache__/store.cpython-313.pyc",
            "web/session.db",
            "dist/run.py",
            ".git/config",
        ):
            self.write(source, relative, "runtime contents\n")
        self.assertEqual(installer.payload_digest(source), digest)

    def test_copy_includes_only_allowed_payload_files(self):
        source = self.payload()
        allowed = (
            "README.md",
            "mcp.json",
            "memory_manager/store.py",
            "web/app.js",
            "web/style.css",
            "skills/example/agents/openai.yaml",
            "tests/test_fixture.py",
            "docs/installation.md",
        )
        excluded = (
            "private.txt",
            "memory.db",
            "data/memory.db",
            "memory_manager/session.db",
            "memory_manager/__pycache__/store.cpython-313.pyc",
            "web/session.db",
            "dist/run.py",
            ".git/config",
        )
        for relative in allowed + excluded:
            self.write(source, relative)
        destination = self.root / "installed"
        installer.copy_payload(source, destination)
        for relative in ("plugin.json", "run.py", "web/index.html") + allowed:
            with self.subTest(included=relative):
                self.assertEqual(
                    (destination / relative).read_bytes(),
                    (source / relative).read_bytes(),
                )
        for relative in excluded:
            with self.subTest(excluded=relative):
                self.assertFalse((destination / relative).exists())

    def test_copy_rejects_symlinks_within_allowed_directories(self):
        source = self.payload()
        outside = self.write(self.root, "outside.py", "private contents\n")
        link = source / "memory_manager" / "linked.py"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlinks unavailable on this filesystem: {error}")
        with self.assertRaises(installer.InstallError):
            installer.copy_payload(source, self.root / "installed")

    def test_atomic_json_write_replaces_document_without_temporary_files(self):
        path = self.root / "installation.json"
        installer.atomic_write_json(path, {"status": "旧版本", "items": [1]})
        expected = {"status": "已更新", "items": [2, 3], "enabled": True}
        installer.atomic_write_json(path, expected)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected)
        self.assertEqual({item.name for item in self.root.iterdir()}, {path.name})


if __name__ == "__main__":
    unittest.main()
