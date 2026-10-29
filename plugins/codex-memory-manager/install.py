#!/usr/bin/env python3
"""Install this local plugin through supported Codex CLI commands."""

import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile

from memory_manager.installer_support import DiscoveryError, find_powershell, select_codex
from memory_manager.diagnostics import DiagnosticError, probe_plugin
from memory_manager.app import default_database


PLUGIN = "codex-memory-manager"
MARKETPLACE = "personal-memory-tools"
ROOT = Path(__file__).resolve().parent
FILES = (
    "plugin.json", "mcp.json", "README.md", "run.py", "start.sh", "start.cmd",
    "install.py", "install.cmd", "install.ps1", "install.sh", "package.py",
    "doctor.py", "doctor.cmd", ".mcp.json", ".codex-plugin/plugin.json",
    "CHANGELOG.md", ".gitignore", ".agents/plugins/marketplace.json",
)
FOLDERS = {
    "memory_manager": {".py"}, "web": {".html", ".js", ".css"},
    "skills": {".md", ".yaml"}, "tests": {".py"}, "docs": {".md"},
    "hooks": {".py", ".json"},
}


class InstallError(RuntimeError):
    pass


def load_json(text, label="CLI"):
    def invalid_constant(value):
        raise ValueError("Non-JSON constant: " + value)
    try:
        value = json.loads(text, parse_constant=invalid_constant)
    except (ValueError, TypeError) as error:
        raise InstallError(f"{label} 未返回有效 JSON 对象：{str(text)[:1000]}") from error
    if not isinstance(value, dict):
        raise InstallError(f"{label} JSON 顶层必须是对象")
    return value


def matching_plugin(payload, name=PLUGIN, marketplace=MARKETPLACE):
    if not isinstance(payload, dict) or not isinstance(payload.get("installed"), list):
        raise InstallError("插件列表缺少 installed 数组；请检查 Codex CLI 版本")
    matches = [row for row in payload["installed"] if isinstance(row, dict) and row.get("name") == name and row.get("marketplaceName") == marketplace]
    if len(matches) > 1:
        raise InstallError("发现重复的同名同市场插件，无法唯一验证安装状态")
    return matches[0] if matches else None


def require_enabled(row):
    if not isinstance(row, dict) or row.get("installed") is not True or row.get("enabled") is not True:
        raise InstallError("插件未同时处于已安装、已启用状态；请检查客户端策略和 Codex 输出")
    return row


def build_mcp(python_path, data_dir):
    python = Path(python_path).expanduser().resolve()
    # Portable MCP commands must be bare names or contained ./ paths. The
    # child PATH pins this installation's Python without an invalid absolute
    # command or a persistent change to the user's environment.
    runtime_path = str(python.parent)
    inherited_path = os.environ.get("PATH", "")
    if inherited_path:
        runtime_path += os.pathsep + inherited_path
    return {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "memory-manager": {
                "type": "stdio",
                "command": python.name,
                "args": ["${PLUGIN_ROOT}/run.py", "mcp"],
                "cwd": "${PLUGIN_ROOT}",
                "env": {
                    "CODEX_MEMORY_HOME": str(Path(data_dir).expanduser().resolve()),
                    "PATH": runtime_path,
                },
            }
        },
    }


def build_legacy_mcp(python_path, data_dir):
    server = dict(build_mcp(python_path, data_dir)["mcpServers"]["memory-manager"])
    server.pop("type", None)
    server["command"] = str(Path(python_path).expanduser().resolve())
    # Legacy clients resolve cwd relative to the plugin root without placeholders.
    server["args"] = ["run.py", "mcp"]
    server["cwd"] = "."
    return {"mcpServers": {"memory-manager": server}}


def build_hooks(python_path, data_dir):
    """Pin this local install's runtime and database without granting hook trust."""
    configuration = load_json((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"), "hooks.json")
    python = str(Path(python_path).expanduser().resolve())
    data = str(Path(data_dir).expanduser().resolve())
    for groups in configuration["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                if os.name == "nt":
                    handler["commandWindows"] = '"' + python + '" "' + '$' + '{PLUGIN_ROOT}/hooks/memory_context.py" --memory-home "' + data + '"'
                else:
                    handler["command"] = shlex.quote(python) + ' "' + '$' + '{PLUGIN_ROOT}/hooks/memory_context.py" --memory-home ' + shlex.quote(data)
    return configuration


def payload_files(root):
    root = Path(root)
    result = []
    for relative in FILES:
        path = root / relative
        for parent in path.parents:
            if parent == root:
                break
            if parent.is_symlink():
                raise InstallError("安装源不允许符号链接：" + str(parent))
        if path.is_symlink():
            raise InstallError("安装源不允许符号链接：" + str(path))
        if path.is_file():
            result.append(path)
    for folder, extensions in FOLDERS.items():
        directory = root / folder
        if directory.is_symlink():
            raise InstallError("安装源不允许符号链接：" + str(directory))
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                raise InstallError("安装源不允许符号链接：" + str(path))
            if path.is_file() and path.suffix in extensions:
                result.append(path)
    return sorted(set(result), key=lambda path: path.relative_to(root).as_posix())


def copy_payload(source, destination):
    source, destination = Path(source), Path(destination)
    files = payload_files(source)
    destination.mkdir(parents=True, exist_ok=True)
    for path in files:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def copy_codex_payload(source, destination):
    """Codex currently loads lifecycle hooks only for its native manifest.

    Keep the source's optional portable manifest for other consumers, but do not
    let it shadow the native manifest in the Codex distribution.
    """
    copy_payload(source, destination)
    destination = Path(destination)
    if ((destination / "hooks" / "hooks.json").is_file()
            and (destination / ".codex-plugin" / "plugin.json").is_file()):
        (destination / "plugin.json").unlink(missing_ok=True)


def payload_digest(root):
    root = Path(root)
    digest = hashlib.sha256()
    for path in payload_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=path.name + ".", dir=path.parent, delete=False, newline="\n") as output:
            temporary = output.name
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class Codex:
    def __init__(self, executable=None):
        candidates = [executable] if executable else (["codex.exe", "codex"] if os.name == "nt" else ["codex"])
        self.executable = next((found for item in candidates if item and (found := shutil.which(item))), None)
        if not self.executable:
            raise InstallError("未找到 Codex CLI。请先安装/启用 CLI，或使用 --codex 指定可执行文件路径。")
        self.executable = str(Path(self.executable).absolute())

    def run(self, arguments, timeout=60):
        command = [self.executable] + list(arguments)
        input_text = None
        if os.name == "nt" and Path(self.executable).suffix.lower() in (".cmd", ".bat"):
            # Pass arguments as JSON data rather than interpolating a shell command.
            powershell = find_powershell()
            if not powershell:
                raise InstallError("运行 Codex 的 .cmd 启动器需要 PowerShell；可用 --codex 指定原生 codex.exe")
            script = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); $spec = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd()); $forwardArgs = @($spec.arguments); & ([string]$spec.executable) @forwardArgs; if ($null -eq $LASTEXITCODE) { exit 1 }; exit $LASTEXITCODE"
            command = [powershell, "-NoProfile", "-NonInteractive", "-Command", script]
            input_text = json.dumps({"executable": self.executable, "arguments": list(arguments)}, ensure_ascii=False)
        try:
            completed = subprocess.run(command, input=input_text, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InstallError("无法执行 Codex CLI：" + str(error)) from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise InstallError("Codex 命令失败（" + " ".join(arguments) + "）：\n" + detail[:5000])
        return completed.stdout

    def run_json(self, arguments):
        return load_json(self.run(arguments), "codex " + " ".join(arguments))

    def preflight(self):
        version = self.run(["--version"], timeout=15).strip()
        if "codex-cli" not in version.lower():
            raise InstallError("该程序未返回 codex-cli 版本，不能作为 CLI 使用")
        try:
            for arguments in (["plugin", "add", "--help"], ["plugin", "marketplace", "add", "--help"], ["plugin", "list", "--help"]):
                help_text = self.run(arguments, timeout=15)
                if "--json" not in help_text:
                    raise InstallError("命令未提供 --json：codex " + " ".join(arguments[:-1]))
                if arguments[:2] == ["plugin", "list"] and "--marketplace" not in help_text:
                    raise InstallError("插件列表命令未提供 --marketplace")
        except InstallError as error:
            raise InstallError("当前 Codex CLI 不支持所需的自动安装命令，请更新 CLI。\n" + str(error)) from error
        return version


def validate_source(source):
    required = ["mcp.json", ".mcp.json", ".codex-plugin/plugin.json", "run.py", "doctor.py", "memory_manager/store.py", "memory_manager/app.py", "memory_manager/autoload.py", "memory_manager/diagnostics.py", "memory_manager/web_manager.py", "web/index.html", "web/app.js", "web/style.css", "skills/memory-manager/SKILL.md", "skills/memory-manager/agents/openai.yaml", "hooks/hooks.json", "hooks/memory_context.py", "tests/test_store.py", "tests/test_interfaces.py", "tests/test_installer.py", "tests/test_autoload.py", "tests/test_usage.py", "tests/test_web_manager.py"]
    for relative in required:
        if not (source / relative).is_file():
            raise InstallError("插件文件缺失：" + relative)
    manifest_path = source / "plugin.json"
    if not manifest_path.is_file():
        manifest_path = source / ".codex-plugin" / "plugin.json"
    manifest = load_json(manifest_path.read_text(encoding="utf-8"), "plugin manifest")
    if manifest.get("name") != PLUGIN or not isinstance(manifest.get("version"), str):
        raise InstallError("插件名称或版本无效")
    load_json((source / "mcp.json").read_text(encoding="utf-8"), "mcp.json")
    load_json((source / "hooks" / "hooks.json").read_text(encoding="utf-8"), "hooks.json")
    compatibility = load_json((source / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"), "compatibility plugin.json")
    if compatibility.get("name") != manifest["name"] or compatibility.get("version") != manifest["version"]:
        raise InstallError("兼容声明与插件名称或版本不一致")
    for path in payload_files(source):
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeError) as error:
                raise InstallError("Python 源码检查失败：" + str(error)) from error
    return manifest


def run_tests(source):
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=source, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False, shell=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallError("无法执行安装前测试：" + str(error)) from error
    output = (result.stdout + result.stderr).strip()
    if result.returncode:
        raise InstallError("安装前测试失败，未注册或安装插件：\n" + output[-10000:])
    print(output, flush=True)


@contextmanager
def install_lock(data_dir):
    lock = data_dir / ".installer.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise InstallError("检测到安装锁：" + str(lock) + "。确认没有其他安装进程后再移除过期锁。") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def stage_plugin(source, data_dir):
    marketplace_root = data_dir / "marketplace"
    plugins = marketplace_root / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    destination = plugins / PLUGIN
    temporary = Path(tempfile.mkdtemp(prefix=PLUGIN + ".staging-", dir=plugins))
    backup = None
    try:
        copy_codex_payload(source, temporary)
        atomic_write_json(temporary / "mcp.json", build_mcp(sys.executable, data_dir))
        atomic_write_json(temporary / ".mcp.json", build_legacy_mcp(sys.executable, data_dir))
        atomic_write_json(temporary / "hooks" / "hooks.json", build_hooks(sys.executable, data_dir))
        changed = not destination.is_dir() or payload_digest(destination) != payload_digest(temporary)
        if changed:
            if destination.exists():
                backups = data_dir / "backups"
                backups.mkdir(exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = backups / (PLUGIN + "-" + stamp)
                os.replace(destination, backup)
            try:
                os.replace(temporary, destination)
            except OSError:
                if backup is not None and not destination.exists():
                    os.replace(backup, destination)
                raise
        marketplace = {
            "name": MARKETPLACE,
            "interface": {"displayName": "个人规则与记忆工具"},
            "plugins": [{"name": PLUGIN, "source": {"source": "local", "path": "./plugins/" + PLUGIN}, "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}],
        }
        atomic_write_json(marketplace_root / ".agents" / "plugins" / "marketplace.json", marketplace)
        return marketplace_root, destination, str(backup) if backup else None
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def write_ui_launcher(data_dir, destination):
    launcher = data_dir / "open-manager.py"
    command = [str(Path(sys.executable).resolve()), str(destination / "run.py"), "--db", str(data_dir / "memory.sqlite3"), "serve"]
    content = "#!/usr/bin/env python3\nimport subprocess\n\nif __name__ == '__main__':\n    raise SystemExit(subprocess.call(" + repr(command) + ", shell=False))\n"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="open-manager.", dir=data_dir, delete=False, newline="\n") as output:
        output.write(content)
        temporary = Path(output.name)
    try:
        os.replace(temporary, launcher)
    finally:
        if temporary.exists():
            temporary.unlink()
    return launcher


def main(argv=None):
    parser = argparse.ArgumentParser(description="自动安装或更新 Codex 规则与记忆管理插件")
    parser.add_argument("--check", action="store_true", help="只检查环境、源码和测试，不注册市场或安装")
    parser.add_argument("--reinstall", action="store_true", help="明确请求刷新本插件（默认安装也会刷新并启用）")
    parser.add_argument("--codex", help="Codex CLI 可执行文件完整路径或命令名")
    parser.add_argument("--no-bootstrap-cli", action="store_true", help="不自动下载或安装缺失的 Codex CLI；--check 默认也不下载")
    parser.add_argument("--data-dir", type=Path, help="共享数据目录，默认 CODEX_MEMORY_HOME 或 ~/.codex-memory-manager")
    arguments = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 10):
            raise InstallError("需要 Python 3.10 或更高版本")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        data_dir = (arguments.data_dir or default_database().parent).expanduser().resolve()
        print("1/5 检查插件源码与 Python", flush=True)
        manifest = validate_source(ROOT)
        print("插件版本：" + manifest["version"], flush=True)
        print("2/5 检测可用的 Codex CLI", flush=True)
        executable, cli_version = select_codex(lambda path: Codex(path).preflight(), explicit=arguments.codex, allow_bootstrap=not arguments.check and not arguments.no_bootstrap_cli)
        codex = Codex(executable)
        print("Codex CLI 版本：" + cli_version, flush=True)
        print("3/5 运行安装前测试", flush=True)
        run_tests(ROOT)
        if arguments.check:
            print("检查通过，未注册市场或安装插件。数据目录：" + str(data_dir), flush=True)
            return 0
        data_dir.mkdir(parents=True, exist_ok=True)
        with install_lock(data_dir):
            print("4/5 准备稳定插件副本并注册本地市场", flush=True)
            marketplace_root, destination, backup = stage_plugin(ROOT, data_dir)
            added = codex.run_json(["plugin", "marketplace", "add", str(marketplace_root), "--json"])
            if added.get("marketplaceName") != MARKETPLACE:
                raise InstallError("市场注册结果名称不匹配，已停止；请检查同名市场来源")
            print("5/5 安装或更新插件并验证状态", flush=True)
            installed = codex.run_json(["plugin", "add", PLUGIN + "@" + MARKETPLACE, "--json"])
            if installed.get("name") != PLUGIN or installed.get("marketplaceName") != MARKETPLACE:
                raise InstallError("插件安装回执不匹配，不能确认安装成功")
            listing = codex.run_json(["plugin", "list", "--marketplace", MARKETPLACE, "--json"])
            row = require_enabled(matching_plugin(listing))
            if row.get("pluginId") != PLUGIN + "@" + MARKETPLACE:
                raise InstallError("安装后的插件标识与预期不一致")
            if row.get("version") != manifest["version"]:
                raise InstallError("安装后插件版本与源码版本不一致")
            cache_root = installed.get("installedPath")
            if not isinstance(cache_root, str) or not Path(cache_root).is_dir():
                raise InstallError("CLI 没有返回可读取的实际安装目录，无法验证 MCP")
            print("检查安装缓存中的技能文件、MCP 握手与只读调用", flush=True)
            portable_probe = probe_plugin(Path(cache_root), "mcp.json")
            legacy_probe = probe_plugin(Path(cache_root), ".mcp.json")
            launcher = write_ui_launcher(data_dir, destination)
            receipt = {
                "plugin": PLUGIN, "marketplace": MARKETPLACE, "version": manifest["version"],
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "source": str(ROOT), "source_digest": payload_digest(ROOT), "staged_path": str(destination),
                "marketplace_root": str(marketplace_root), "installed_path": installed.get("installedPath"),
                "database": str(data_dir / "memory.sqlite3"), "python": str(Path(sys.executable).resolve()),
                "codex": codex.executable, "codex_home": os.environ.get("CODEX_HOME"),
                "backup": backup, "installed": True, "enabled": True,
                "verification": {"cli_registration": True, "portable_mcp": portable_probe, "compatibility_mcp": legacy_probe, "desktop_session": "pending"},
            }
            atomic_write_json(data_dir / "installation.json", receipt)
        print("CLI 安装登记与本机 MCP 自检通过：" + PLUGIN + " " + manifest["version"], flush=True)
        print("桌面客户端会话加载尚待验证。请完全退出并重启客户端，在本地新聊天中调用 codex-memory-manager:memory-manager，核对 memory_snapshot 或 memory_search 工具是否可用。", flush=True)
        print("启动管理页面：", flush=True)
        print(json.dumps([str(Path(sys.executable).resolve()), str(launcher)], ensure_ascii=False), flush=True)
        print("记忆数据库：" + str(data_dir / "memory.sqlite3"), flush=True)
        return 0
    except (InstallError, DiscoveryError, DiagnosticError, OSError, ValueError) as error:
        print("安装未完成：" + str(error), file=sys.stderr)
        print("记忆数据库未被安装器删除；修复错误后可重新运行。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
