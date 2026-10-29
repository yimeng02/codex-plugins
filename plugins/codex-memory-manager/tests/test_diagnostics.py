import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from memory_manager.diagnostics import DiagnosticError, MCP_SCHEMA, REQUIRED_TOOLS, _portable_environment, is_foreign_windows_command, launch_spec, parse_probe_output, probe_plugin


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("memory_installer_diagnostics_test", PROJECT_ROOT / "install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def replies(self):
        return [
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": name} for name in sorted(REQUIRED_TOOLS)]}},
            {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": json.dumps({"entries": []})}]}},
        ]

    def encode(self, replies):
        return "".join(json.dumps(reply) + "\n" for reply in replies)

    def write_config(self, server=None, filename="mcp.json", **fields):
        config = {"$schema": MCP_SCHEMA, "mcpServers": {"memory-manager": {
            "type": "stdio", "command": Path(sys.executable).name,
            "args": ["${PLUGIN_ROOT}/run.py", "mcp"], "cwd": "${PLUGIN_ROOT}",
        }}}
        if server is not None:
            config["mcpServers"]["memory-manager"].update(server)
        config.update(fields)
        installer.atomic_write_json(self.root / filename, config)
        return config

    def test_requires_a_real_read_call_after_tool_discovery(self):
        with self.assertRaises(DiagnosticError):
            parse_probe_output(self.encode(self.replies()[:2]))

    def test_rejects_missing_tool(self):
        replies = self.replies()
        replies[1]["result"]["tools"] = [{"name": "memory_search"}]
        with self.assertRaises(DiagnosticError):
            parse_probe_output(self.encode(replies))

    def test_rejects_failed_read_call(self):
        replies = self.replies()
        replies[2]["result"] = {"isError": True, "content": [{"type": "text", "text": "database inaccessible"}]}
        with self.assertRaises(DiagnosticError):
            parse_probe_output(self.encode(replies))

    def test_rejects_stdout_logging_that_breaks_mcp(self):
        with self.assertRaises(DiagnosticError):
            parse_probe_output("starting server\n" + self.encode(self.replies()))

    def test_detects_windows_command_in_posix_runtime(self):
        self.assertTrue(is_foreign_windows_command(r"C:\Users\Example\Python\python.exe", windows=False))
        self.assertFalse(is_foreign_windows_command(r"C:\Users\Example\Python\python.exe", windows=True))
        self.assertFalse(is_foreign_windows_command("/usr/bin/python3", windows=False))

    def test_legacy_configuration_keeps_shared_python_and_database(self):
        portable = installer.build_mcp(sys.executable, self.root)["mcpServers"]["memory-manager"]
        legacy = installer.build_legacy_mcp(sys.executable, self.root)["mcpServers"]["memory-manager"]
        self.assertEqual(portable["command"], Path(sys.executable).resolve().name)
        self.assertEqual(legacy["command"], str(Path(sys.executable).resolve()))
        self.assertEqual(portable["env"]["CODEX_MEMORY_HOME"], legacy["env"]["CODEX_MEMORY_HOME"])
        self.assertEqual(legacy["cwd"], ".")
        self.assertEqual(legacy["args"], ["run.py", "mcp"])
        self.assertNotIn("type", legacy)

    def test_portable_bare_command_and_root_placeholders(self):
        self.write_config({"env": {"MY_PLUGIN_PATH": "${PLUGIN_ROOT}/assets"}})
        command, cwd, environment = launch_spec(self.root)
        self.assertEqual(command, [Path(sys.executable).name, str(self.root / "run.py"), "mcp"])
        self.assertEqual(cwd, str(self.root))
        self.assertEqual(environment["PLUGIN_ROOT"], str(self.root))
        self.assertEqual(environment["MY_PLUGIN_PATH"], str(self.root / "assets"))

    def test_portable_omitted_cwd_defaults_to_package(self):
        config = self.write_config()
        del config["mcpServers"]["memory-manager"]["cwd"]
        installer.atomic_write_json(self.root / "mcp.json", config)
        self.assertEqual(launch_spec(self.root)[1], str(self.root))

    def test_portable_rejects_absolute_or_placeholder_command(self):
        for command in (sys.executable, "/usr/bin/python3", r"C:\Python\python.exe", "${PLUGIN_ROOT}/python", "../python", "./../python"):
            with self.subTest(command=command):
                self.write_config({"command": command})
                with self.assertRaises(DiagnosticError):
                    launch_spec(self.root)

    def test_portable_package_relative_command(self):
        self.write_config({"command": "./runtime/python", "cwd": "./"})
        self.assertEqual(launch_spec(self.root)[0][0], str(self.root / "runtime" / "python"))

    def test_portable_rejects_invalid_cwd(self):
        for cwd in (".", str(self.root), "../", "./../", "${PLUGIN_ROOT}/..", "./a\\b", None, 1):
            with self.subTest(cwd=cwd):
                self.write_config({"cwd": cwd})
                with self.assertRaises(DiagnosticError):
                    launch_spec(self.root)

    def test_portable_cwd_cannot_escape_through_symlink(self):
        outside = self.root / "outside"
        package = self.root / "package"
        outside.mkdir()
        package.mkdir()
        try:
            (package / "escape").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest("此运行环境不能创建符号链接：" + str(error))
        config = self.write_config({"cwd": "./escape"})
        installer.atomic_write_json(package / "mcp.json", config)
        with self.assertRaises(DiagnosticError):
            launch_spec(package)

    def test_portable_data_placeholder_requires_explicit_root(self):
        self.write_config({"args": ["${PLUGIN_ROOT}/run.py", "${PLUGIN_DATA}/scratch"], "cwd": "${PLUGIN_DATA}"})
        with self.assertRaisesRegex(DiagnosticError, "plugin_data_root"):
            launch_spec(self.root)
        data = self.root / "plugin-data"
        data.mkdir()
        command, cwd, environment = launch_spec(self.root, plugin_data_root=data)
        self.assertEqual(command[-1], str(data / "scratch"))
        self.assertEqual(cwd, str(data))
        self.assertEqual(environment["PLUGIN_DATA"], str(data))

    def test_portable_rejects_reserved_env_and_windows_case_duplicates(self):
        for name in ("PLUGIN_ROOT", "PLUGIN_DATA"):
            with self.subTest(name=name):
                self.write_config({"env": {name: "override"}})
                with self.assertRaises(DiagnosticError):
                    launch_spec(self.root)
        for environment in ({"plugin_root": "override"}, {"Plugin_Data": "override"}, {"Path": "one", "PATH": "two"}):
            with self.subTest(environment=environment):
                with self.assertRaises(DiagnosticError):
                    _portable_environment(environment, windows=True)
        self.assertEqual(_portable_environment({"Path": "one"}, windows=True), {"PATH": "one"})

    def test_portable_requires_schema_and_rejects_unknown_fields(self):
        for fields in ({"$schema": "unsupported"}, {"$schema": None}, {"extra": True}, {"mcpServers": []}):
            with self.subTest(fields=fields):
                self.write_config(**fields)
                with self.assertRaises(DiagnosticError):
                    launch_spec(self.root)
        for fields in ({"type": "sse"}, {"type": None}, {"extra": True}, {"args": None}, {"env": None}, {"env": {"X": 1}}, {"args": [1]}):
            with self.subTest(server=fields):
                self.write_config(fields)
                with self.assertRaises(DiagnosticError):
                    launch_spec(self.root)

    def test_legacy_absolute_runtime_and_dot_cwd_remain_supported(self):
        installer.atomic_write_json(self.root / ".mcp.json", installer.build_legacy_mcp(sys.executable, self.root / "data"))
        command, cwd, environment = launch_spec(self.root, ".mcp.json")
        self.assertEqual(command, [str(Path(sys.executable).resolve()), "run.py", "mcp"])
        self.assertEqual(cwd, str(self.root))
        self.assertEqual(environment["CODEX_MEMORY_HOME"], str(self.root / "data"))

    def test_probe_rejects_loader_invalid_config_before_launching_process(self):
        plugin = self.root / "plugin"
        installer.copy_payload(PROJECT_ROOT, plugin)
        config = self.write_config({"command": sys.executable})
        installer.atomic_write_json(plugin / "mcp.json", config)
        with mock.patch("memory_manager.diagnostics.subprocess.run") as run:
            with self.assertRaises(DiagnosticError):
                probe_plugin(plugin)
            run.assert_not_called()

    def test_probe_resolves_bare_runtime_using_configured_path(self):
        plugin = self.root / "plugin"
        installer.copy_payload(PROJECT_ROOT, plugin)
        configured_path = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
        command = Path(sys.executable).name
        installer.atomic_write_json(plugin / "mcp.json", {"$schema": MCP_SCHEMA, "mcpServers": {"memory-manager": {
            "type": "stdio", "command": command,
            "args": ["${PLUGIN_ROOT}/run.py", "mcp"],
            "env": {"PATH": configured_path, "CODEX_MEMORY_HOME": str(self.root / "isolated-data")},
        }}})
        with mock.patch("memory_manager.diagnostics.shutil.which", wraps=shutil.which) as which:
            result = probe_plugin(plugin)
        which.assert_called_once_with(command, path=configured_path)
        self.assertEqual(result["command"], command)
        self.assertTrue(Path(result["resolved_command"]).is_absolute())
        self.assertEqual(result["read_probe"], "passed")

    def test_copies_desktop_compatibility_files_and_skill_metadata(self):
        plugin = self.root / "plugin"
        installer.copy_payload(PROJECT_ROOT, plugin)
        for relative in (".mcp.json", ".codex-plugin/plugin.json", "skills/memory-manager/agents/openai.yaml", "doctor.py"):
            self.assertTrue((plugin / relative).is_file(), relative)

    def test_real_packaged_subprocess_exposes_tools_in_both_formats(self):
        plugin = self.root / "plugin"
        data = self.root / "data"
        installer.copy_payload(PROJECT_ROOT, plugin)
        installer.atomic_write_json(plugin / "mcp.json", installer.build_mcp(sys.executable, data))
        installer.atomic_write_json(plugin / ".mcp.json", installer.build_legacy_mcp(sys.executable, data))
        for filename in ("mcp.json", ".mcp.json"):
            with self.subTest(config=filename):
                result = probe_plugin(plugin, filename)
                self.assertEqual(result["read_probe"], "passed")
                self.assertTrue(REQUIRED_TOOLS.issubset(set(result["required_tools"])))
        self.assertTrue((data / "memory.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
