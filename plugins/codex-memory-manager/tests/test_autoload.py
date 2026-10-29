"""Exercise lifecycle hooks without MCP, native memory access, or model calls."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from memory_manager.app import dispatch
from memory_manager.autoload import MAX_CONTEXT_BYTES, prepare_context, session_status
from memory_manager.store import Store, StoreError


ROOT = Path(__file__).resolve().parents[1]


class AutoloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "data" / "memory.sqlite3"
        self.store = Store(self.database)

    def create(self, **patch):
        return self.store.entry_create({"title": "Preference", "content": "Apply this preference", **patch})

    def payload(self, event="SessionStart", identifier="chat-a", **patch):
        return {"hook_event_name": event, "session_id": identifier,
                "source": "startup", "cwd": str(self.root), **patch}

    @staticmethod
    def data(output):
        text = output["hookSpecificOutput"]["additionalContext"]
        return json.loads(text.split("MEMORY_MANAGER_DATA_JSON:\n", 1)[1])

    def hook_process(self, payload, home=None):
        environment = os.environ.copy()
        environment.update(CODEX_MEMORY_HOME=str(home or self.database.parent),
                           CODEX_HOME=str(self.root / "codex-home"),
                           PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "hooks" / "memory_context.py")],
                              input=json.dumps(payload), capture_output=True, text=True,
                              encoding="utf-8", env=environment, cwd=self.root, timeout=8)

    def test_startup_injects_enabled_globals_even_for_hello_without_mcp(self):
        active = self.create(content="每次回复都告诉我当前时间")
        self.create(content="DISABLED_BODY", enabled=False)
        deleted = self.create(content="DELETED_BODY")
        self.store.entry_delete(deleted["id"])
        self.create(scope="plugin", target="Exact Plugin", content="SCOPED_BODY_DO_NOT_AUTOLOAD")
        self.create(scope="plugin", target="my-plugin", content="OTHER_SCOPED_BODY_DO_NOT_AUTOLOAD")
        before = self.store.snapshot()
        run = self.hook_process(self.payload(prompt="你好"))
        self.assertEqual(run.returncode, 0, run.stderr)
        output = json.loads(run.stdout)
        data = self.data(output)
        self.assertEqual([entry["id"] for entry in data["global_entries"]], [active["id"]])
        self.assertEqual(data["global_entries"][0]["content"], "每次回复都告诉我当前时间")
        self.assertNotIn("DISABLED_BODY", run.stdout)
        self.assertNotIn("DELETED_BODY", run.stdout)
        self.assertNotIn("SCOPED_BODY_DO_NOT_AUTOLOAD", run.stdout)
        self.assertNotIn("OTHER_SCOPED_BODY_DO_NOT_AUTOLOAD", run.stdout)
        self.assertEqual({row["target"] for row in data["available_targets"]}, {"Exact Plugin", "my-plugin"})
        self.assertTrue(all(set(row) == {"scope", "target", "count"} for row in data["available_targets"]))
        after = self.store.snapshot()
        self.assertEqual(after["entries"], before["entries"])
        self.assertEqual(after["categories"], before["categories"])
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["application"]["onboarding_decision"], "pending")
        self.assertEqual(len(self.store.history(active["id"])), 1)

    def test_receipts_are_per_chat_not_global_last_read(self):
        self.create()
        self.store.refresh()
        self.assertFalse(self.store.application_status()["needs_refresh"])
        self.assertIsNotNone(prepare_context(self.store, self.payload(identifier="one")))
        self.assertIsNotNone(prepare_context(self.store, self.payload("UserPromptSubmit", "two")))
        self.assertIsNone(prepare_context(self.store, self.payload("UserPromptSubmit", "one")))
        self.assertEqual(session_status(self.store, "one")["revision"], 1)
        self.assertEqual(session_status(self.store, "two")["revision"], 1)
        self.assertIsNone(session_status(self.store, "unknown"))

    def test_resuming_clearing_compacting_and_forking_always_restore_context(self):
        self.create()
        for source in ("startup", "resume", "clear", "compact", "fork"):
            with self.subTest(source=source):
                output = prepare_context(self.store, self.payload(source=source))
                self.assertEqual(self.data(output)["revision"], 1)
                self.assertEqual(session_status(self.store, "chat-a")["source"], source)

    def test_submit_checks_version_and_refreshes_after_edits(self):
        entry = self.create(content="old content")
        prepare_context(self.store, self.payload())
        unchanged = self.hook_process(self.payload("UserPromptSubmit"))
        self.assertEqual(unchanged.returncode, 0)
        self.assertEqual(unchanged.stdout, "")
        self.store.entry_update(entry["id"], {"content": "updated content"})
        self.assertTrue(session_status(self.store, "chat-a")["needs_refresh"])
        updated = self.hook_process(self.payload("UserPromptSubmit", prompt="你好"))
        data = self.data(json.loads(updated.stdout))
        self.assertEqual(data["revision"], 2)
        self.assertEqual(data["global_entries"][0]["content"], "updated content")
        self.assertFalse(session_status(self.store, "chat-a")["needs_refresh"])

    def test_disabling_all_globals_injects_empty_replacement_and_revocations(self):
        entry = self.create()
        prepare_context(self.store, self.payload())
        self.store.entry_update(entry["id"], {"enabled": False})
        output = prepare_context(self.store, self.payload("UserPromptSubmit"))
        data = self.data(output)
        self.assertEqual(data["global_entries"], [])
        self.assertEqual(data["revoked_entry_ids"], [entry["id"]])
        self.assertIn("替代", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(session_status(self.store, "chat-a")["entry_ids"], [])

    def test_whole_entries_are_bounded_and_omissions_are_explicit(self):
        huge = self.create(title="Oversized", content="超" * MAX_CONTEXT_BYTES, priority=100)
        small = self.create(title="Small", content="Still fits", priority=90)
        for index in range(12):
            self.create(title=f"Entry {index}", content=('\n"中文' * 2500), priority=80)
        output = prepare_context(self.store, self.payload())
        text = output["hookSpecificOutput"]["additionalContext"]
        data = self.data(output)
        self.assertLessEqual(len(text.encode("utf-8")), MAX_CONTEXT_BYTES)
        self.assertIn(small["id"], [entry["id"] for entry in data["global_entries"]])
        self.assertNotIn(huge["id"], [entry["id"] for entry in data["global_entries"]])
        self.assertGreater(data["omitted_global_count"], 0)
        self.assertEqual(session_status(self.store, "chat-a")["omitted_count"], data["omitted_global_count"])

    def test_plugin_update_reprepares_same_revision(self):
        self.create()
        prepare_context(self.store, self.payload())
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE session_loads SET plugin_version='old-version'")
        self.assertIsNotNone(prepare_context(self.store, self.payload("UserPromptSubmit")))

    def test_invalid_inputs_cannot_acknowledge_a_read(self):
        self.create()
        before = self.store.application_status()
        for payload in (None, {}, self.payload(event="Stop"), self.payload(identifier=""),
                        self.payload(identifier="invalid\nid"), self.payload(source=[])):
            with self.subTest(payload=payload), self.assertRaises(StoreError):
                prepare_context(self.store, payload)
        self.assertEqual(self.store.application_status(), before)
        self.assertIsNone(session_status(self.store, "chat-a"))

    def test_decode_failure_rolls_back_receipt_and_read_marker(self):
        entry = self.create()
        before = self.store.application_status()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE entries SET tags='not-json' WHERE id=?", (entry["id"],))
        with self.assertRaises(ValueError):
            prepare_context(self.store, self.payload())
        self.assertIsNone(session_status(self.store, "chat-a"))
        self.assertEqual(self.store.application_status(), before)

    def test_missing_database_warns_without_creating_an_empty_library(self):
        missing = self.root / "not-initialized"
        run = self.hook_process(self.payload(), home=missing)
        self.assertEqual(run.returncode, 0)
        self.assertIn("未找到", json.loads(run.stdout)["systemMessage"])
        self.assertFalse(missing.exists())

    def test_locked_database_warns_and_does_not_block_a_chat_indefinitely(self):
        self.create()
        with closing(sqlite3.connect(self.database)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = self.hook_process(self.payload())
            self.assertEqual(run.returncode, 0)
            self.assertIn("自动读取未完成", json.loads(run.stdout)["systemMessage"])
            conn.rollback()
        self.assertIsNone(session_status(self.store, "chat-a"))

    def test_no_agents_native_memory_or_prompt_persistence(self):
        self.create()
        home = self.root / "codex-home"
        (home / "memories").mkdir(parents=True)
        agents = home / "AGENTS.md"
        native = home / "memories" / "MEMORY.md"
        agents.write_text("Existing global instructions", encoding="utf-8")
        native.write_text("Existing native memories", encoding="utf-8")
        run = self.hook_process(self.payload(prompt="DO_NOT_STORE_PROMPT", transcript_path="do-not-read"))
        self.assertEqual(run.returncode, 0)
        self.assertEqual(agents.read_text(encoding="utf-8"), "Existing global instructions")
        self.assertEqual(native.read_text(encoding="utf-8"), "Existing native memories")
        self.assertNotIn("DO_NOT_STORE_PROMPT", run.stdout)
        with closing(sqlite3.connect(self.database)) as conn:
            rows = conn.execute("SELECT * FROM session_loads").fetchall()
        self.assertNotIn("DO_NOT_STORE_PROMPT", str(rows))

    def test_status_can_report_one_chat_without_creating_a_receipt(self):
        self.create()
        empty = dispatch(self.store, "memory_status", {"session_id": "chat-a"})
        self.assertIsNone(empty["session_context"])
        self.assertIsNone(self.store.application_status()["last_read_revision"])
        prepare_context(self.store, self.payload())
        result = dispatch(self.store, "memory_status", {"session_id": "chat-a"})["session_context"]
        self.assertEqual(result["revision"], 1)
        self.assertIn("not confirmed", result["delivery"])

    def test_local_installer_pins_runtime_and_custom_database_without_trusting_hooks(self):
        from install import build_hooks
        configuration = build_hooks(sys.executable, self.root / "custom data")
        text = json.dumps(configuration)
        self.assertNotIn("trusted_hash", text)
        field = "commandWindows" if os.name == "nt" else "command"
        for group in configuration["hooks"].values():
            command = group[0]["hooks"][0][field]
            self.assertIn(str(Path(sys.executable).resolve()), command)
            self.assertIn("custom data", command)
            self.assertIn("--memory-home", command)

    def test_both_manifest_formats_and_installer_include_hooks(self):
        from install import payload_files
        compatibility = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(compatibility["hooks"], "./hooks/hooks.json")
        if (ROOT / "plugin.json").is_file():
            portable = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(portable["extensions"]["com.openai"]["hooks"], "./hooks/hooks.json")
            self.assertEqual(portable["extensions"]["com.openai"]["interface"], compatibility["interface"])
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config["hooks"]), {"SessionStart", "UserPromptSubmit"})
        for group in config["hooks"].values():
            command = group[0]["hooks"][0]
            self.assertEqual(command["type"], "command")
            self.assertLessEqual(command["timeout"], 5)
            self.assertIn('"' + '$' + '{PLUGIN_ROOT}', command["command"])
            self.assertIn("commandWindows", command)
        names = {p.relative_to(ROOT).as_posix() for p in payload_files(ROOT)}
        self.assertIn("hooks/hooks.json", names)
        self.assertIn("hooks/memory_context.py", names)

    def test_codex_distribution_uses_native_manifest_and_is_valid_install_source(self):
        from install import copy_codex_payload, validate_source
        distribution = self.root / "native plugin"
        copy_codex_payload(ROOT, distribution)
        self.assertFalse((distribution / "plugin.json").exists())
        self.assertTrue((distribution / ".codex-plugin/plugin.json").exists())
        self.assertTrue((distribution / "hooks/hooks.json").exists())
        manifest = validate_source(distribution)
        self.assertEqual(manifest["name"], "codex-memory-manager")


if __name__ == "__main__":
    unittest.main()
