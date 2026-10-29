import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from memory_manager.app import default_database


class DataLocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "user-home"
        self.home.mkdir()
        self.codex_home = self.root / "shared-profile" / ".codex"
        self.home_database = self.home / ".codex-memory-manager" / "memory.sqlite3"
        self.shared_database = self.codex_home.parent / ".codex-memory-manager" / "memory.sqlite3"
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        home = patch("memory_manager.app.Path.home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        source = patch("memory_manager.app.ROOT", self.root / "source")
        source.start()
        self.addCleanup(source.stop)

    def existing_file(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def test_explicit_memory_home_wins_without_creating_it(self):
        self.existing_file(self.home_database)
        self.existing_file(self.shared_database)
        explicit = self.root / "explicit-choice"
        os.environ["CODEX_MEMORY_HOME"] = str(explicit)
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.assertEqual(default_database(), explicit / "memory.sqlite3")
        self.assertFalse(explicit.exists())

    def test_configured_codex_sibling_wins_over_existing_user_database(self):
        self.existing_file(self.home_database)
        self.existing_file(self.shared_database)
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.assertEqual(default_database(), self.shared_database)

    def test_existing_codex_home_sibling_is_reused(self):
        self.existing_file(self.shared_database)
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.assertEqual(default_database(), self.shared_database)
        self.assertFalse(self.home_database.parent.exists())

    def test_missing_databases_keep_user_default_without_creating_files(self):
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.assertEqual(default_database(), self.home_database)
        self.assertFalse(self.home_database.parent.exists())
        self.assertFalse(self.shared_database.parent.exists())

    def test_no_codex_home_keeps_user_default(self):
        self.assertEqual(default_database(), self.home_database)
        self.assertFalse(self.home_database.parent.exists())

    def test_directory_named_memory_sqlite3_is_not_an_existing_database(self):
        self.shared_database.mkdir(parents=True)
        self.existing_file(self.home_database)
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.assertEqual(default_database(), self.home_database)

    def test_installed_cache_without_environment_reuses_associated_database(self):
        self.existing_file(self.home_database)
        self.existing_file(self.shared_database)
        installed = self.codex_home / "plugins" / "cache" / "my-market" / "codex-memory-manager" / "0.1.5"
        with patch("memory_manager.app.ROOT", installed):
            self.assertEqual(default_database(), self.shared_database)

    def test_explicit_memory_home_wins_over_inferred_cache_database(self):
        self.existing_file(self.home_database)
        self.existing_file(self.shared_database)
        explicit = self.root / "explicit-choice"
        os.environ["CODEX_MEMORY_HOME"] = str(explicit)
        installed = self.codex_home / "plugins" / "cache" / "my-market" / "codex-memory-manager" / "0.1.5"
        with patch("memory_manager.app.ROOT", installed):
            self.assertEqual(default_database(), explicit / "memory.sqlite3")
        self.assertFalse(explicit.exists())

    def test_invalid_cache_layout_does_not_infer_codex_home(self):
        self.existing_file(self.home_database)
        self.existing_file(self.shared_database)
        invalid = [
            self.codex_home / "plugins" / "other" / "my-market" / "codex-memory-manager" / "0.1.5",
            self.codex_home / "other" / "cache" / "my-market" / "codex-memory-manager" / "0.1.5",
            self.codex_home / "plugins" / "cache" / "my-market" / "other-plugin" / "0.1.5",
        ]
        for installed in invalid:
            with self.subTest(root=installed), patch("memory_manager.app.ROOT", installed):
                self.assertEqual(default_database(), self.home_database)

    def test_configured_codex_home_is_used_instead_of_inferred_cache_profile(self):
        self.existing_file(self.shared_database)
        installed = self.codex_home / "plugins" / "cache" / "my-market" / "codex-memory-manager" / "0.1.5"
        os.environ["CODEX_HOME"] = str(self.root / "another-profile" / ".codex")
        with patch("memory_manager.app.ROOT", installed):
            self.assertEqual(default_database(), self.home_database)


if __name__ == "__main__":
    unittest.main()
