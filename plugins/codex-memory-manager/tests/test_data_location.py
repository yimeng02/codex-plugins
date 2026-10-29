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

    def test_existing_user_database_wins_over_shared_database(self):
        self.existing_file(self.home_database)
        self.existing_file(self.shared_database)
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.assertEqual(default_database(), self.home_database)

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
        self.home_database.mkdir(parents=True)
        self.existing_file(self.shared_database)
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.assertEqual(default_database(), self.shared_database)


if __name__ == "__main__":
    unittest.main()
