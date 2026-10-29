from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from memory_manager import installer_support as support


class CliDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = {
            "PATH": "", "LOCALAPPDATA": str(self.root / "Local"),
            "APPDATA": str(self.root / "Roaming"), "CODEX_HOME": str(self.root / "codex-home"),
        }

    def executable(self, *parts):
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test fixture; not an executable")
        return path

    def candidates(self, **kwargs):
        with patch.object(support.shutil, "which", return_value=None):
            return support.codex_candidates(environ=self.environment, home=self.root, windows=True, include_desktop=False, **kwargs)

    def test_finds_official_windows_install_outside_path(self):
        executable = self.executable("Local", "Programs", "OpenAI", "Codex", "bin", "codex.exe")
        result = self.candidates()
        self.assertIn(str(executable), [path for path, source in result])

    def test_finds_npm_user_shim_outside_path(self):
        executable = self.executable("Roaming", "npm", "codex.cmd")
        self.assertIn(str(executable), [path for path, source in self.candidates()])

    def test_finds_standalone_under_custom_codex_home(self):
        executable = self.executable("codex-home", "packages", "standalone", "current", "bin", "codex.exe")
        self.assertIn(str(executable), [path for path, source in self.candidates()])

    def test_stale_environment_hint_does_not_mask_real_install(self):
        self.environment["CODEX_CLI_PATH"] = str(self.root / "removed-version" / "codex.exe")
        executable = self.executable("Roaming", "npm", "codex.cmd")
        self.assertEqual(self.candidates(), [(str(executable), "npm 用户目录")])

    def test_explicit_path_does_not_fall_back_to_other_cli(self):
        self.executable("Roaming", "npm", "codex.cmd")
        with self.assertRaises(support.DiscoveryError):
            self.candidates(explicit=str(self.root / "missing.exe"))

    def test_finds_packaged_cli_without_launching_gui_binary(self):
        package = self.root / "StorePackage"
        cli = self.executable("StorePackage", "app", "resources", "codex.exe")
        gui = self.executable("StorePackage", "Codex.exe")
        with patch.object(support.shutil, "which", return_value=None), patch.object(support, "windows_desktop_roots", return_value=[package]):
            result = support.codex_candidates(environ=self.environment, home=self.root, windows=True)
        self.assertIn(str(cli), [path for path, source in result])
        self.assertNotIn(str(gui), [path for path, source in result])

    def test_broken_path_candidate_does_not_mask_usable_desktop_cli(self):
        def probe(path):
            if path == "broken":
                raise RuntimeError("missing --json support")
            return "codex-cli test"
        with patch.object(support, "codex_candidates", return_value=[("broken", "PATH"), ("usable", "desktop")]), patch.object(support, "bootstrap_codex") as bootstrap:
            result = support.select_codex(probe)
        self.assertEqual(result, ("usable", "codex-cli test"))
        bootstrap.assert_not_called()

    def test_check_or_no_bootstrap_never_downloads(self):
        with patch.object(support, "codex_candidates", return_value=[]), patch.object(support, "bootstrap_codex") as bootstrap:
            with self.assertRaises(support.DiscoveryError):
                support.select_codex(lambda path: "unused", allow_bootstrap=False)
        bootstrap.assert_not_called()

    def test_missing_cli_bootstraps_then_discovers_again(self):
        with patch.object(support, "codex_candidates", side_effect=[[], [("new-cli", "official")]]), patch.object(support, "bootstrap_codex") as bootstrap:
            result = support.select_codex(lambda path: "codex-cli new")
        self.assertEqual(result, ("new-cli", "codex-cli new"))
        bootstrap.assert_called_once_with()

    def test_incompatible_explicit_binary_does_not_trigger_install(self):
        def broken(path):
            raise RuntimeError("not a supported CLI")
        with patch.object(support, "codex_candidates", return_value=[("explicit-cli", "--codex")]), patch.object(support, "bootstrap_codex") as bootstrap:
            with self.assertRaises(support.DiscoveryError):
                support.select_codex(broken, explicit="explicit-cli")
        bootstrap.assert_not_called()

    def test_powershell_can_be_found_when_system_path_is_stale(self):
        shell = self.executable("Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        with patch.object(support.shutil, "which", return_value=None):
            found = support.find_powershell({"PATH": "", "SystemRoot": str(self.root / "Windows")})
        self.assertEqual(found, str(shell))

    def test_bootstrap_uses_official_redirect_and_preserves_data_arguments(self):
        response = MagicMock()
        response.geturl.return_value = "https://releases.openai.com/codex/install.ps1"
        response.read.return_value = b"Write-Output 'official installer fixture'"
        opened = MagicMock()
        opened.__enter__.return_value = response
        with patch.object(support, "urlopen", return_value=opened) as download, patch.object(support, "find_powershell", return_value="powershell.exe"), patch.object(support.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            support.bootstrap_codex(windows=True)
        download.assert_called_once_with("https://chatgpt.com/codex/install.ps1", timeout=30)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-File", command)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["env"]["CODEX_NON_INTERACTIVE"], "1")
        self.assertNotIn("CODEX_INSTALL_DAEMON_ONLY", run.call_args.kwargs["env"])

    def test_bootstrap_rejects_unexpected_download_host(self):
        response = MagicMock()
        response.geturl.return_value = "https://unrelated.invalid/codex/install.ps1"
        opened = MagicMock()
        opened.__enter__.return_value = response
        with patch.object(support, "urlopen", return_value=opened), patch.object(support, "find_powershell", return_value="powershell.exe"), patch.object(support.subprocess, "run") as run:
            with self.assertRaises(support.DiscoveryError):
                support.bootstrap_codex(windows=True)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
