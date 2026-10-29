"""Discover Codex outside PATH and bootstrap its official per-user installer."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen


class DiscoveryError(RuntimeError):
    pass


def find_powershell(environ=None):
    environment = os.environ if environ is None else environ
    for name in ("powershell.exe", "pwsh.exe"):
        result = shutil.which(name, path=environment.get("PATH", ""))
        if result:
            return result
    system_root = environment.get("SystemRoot") or environment.get("WINDIR")
    if system_root:
        result = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if result.is_file():
            return str(result)
    return None


def windows_desktop_roots(environ=None):
    """Query current-user package/process locations, without changing AppX ACLs."""
    environment = os.environ if environ is None else environ
    powershell = find_powershell(environment)
    if not powershell:
        return []
    script = (
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
        "$roots = @(); "
        "$roots += @(Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue | "
        "Sort-Object Version -Descending | ForEach-Object { $_.InstallLocation }); "
        "$roots += @(Get-Process -Name ChatGPT,Codex -ErrorAction SilentlyContinue | "
        "ForEach-Object { if ($_.Path) { [System.IO.Path]::GetDirectoryName($_.Path) } }); "
        "ConvertTo-Json -InputObject @($roots | Where-Object { $_ } | Select-Object -Unique) -Compress"
    )
    try:
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, check=False, shell=False, env=dict(environment))
        if result.returncode:
            return []
        locations = json.loads(result.stdout or "[]")
        if isinstance(locations, str):
            locations = [locations]
        return [Path(location) for location in locations if isinstance(location, str) and location.strip()] if isinstance(locations, list) else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def _recent_files(directory, pattern, limit=3):
    try:
        matches = []
        for item in directory.glob(pattern):
            try:
                if item.is_file():
                    matches.append((item.stat().st_mtime, item))
            except OSError:
                continue
        return [item for _, item in sorted(matches, key=lambda row: row[0], reverse=True)[:limit]]
    except OSError:
        return []


def codex_candidates(explicit=None, environ=None, home=None, windows=None, include_desktop=True):
    environment = os.environ if environ is None else environ
    user_home = Path.home() if home is None else Path(home)
    is_windows = os.name == "nt" if windows is None else windows
    result = []
    seen = set()

    def add(value, source):
        if not value:
            return
        path = Path(os.path.expandvars(str(value))).expanduser().absolute()
        try:
            if not path.is_file():
                return
        except OSError:
            return
        # Keep the launcher path; resolving standalone junctions can bypass its layout.
        key = str(path).casefold() if is_windows else str(path)
        if key not in seen:
            seen.add(key)
            result.append((str(path), source))

    if explicit:
        found = shutil.which(str(explicit), path=environment.get("PATH", ""))
        add(found or explicit, "--codex")
        if not result:
            raise DiscoveryError("--codex 指定的文件不存在或不可执行：" + str(explicit))
        return result

    hint = environment.get("CODEX_CLI_PATH")
    if hint:
        add(shutil.which(hint, path=environment.get("PATH", "")) or hint, "CODEX_CLI_PATH")
    names = ("codex.exe", "codex.cmd", "codex") if is_windows else ("codex",)
    for name in names:
        add(shutil.which(name, path=environment.get("PATH", "")), "PATH")

    codex_home = Path(environment.get("CODEX_HOME") or user_home / ".codex").expanduser()
    install_dir = environment.get("CODEX_INSTALL_DIR")
    if install_dir:
        for name in names:
            add(Path(install_dir).expanduser() / name, "CODEX_INSTALL_DIR")
    if is_windows:
        local = Path(environment.get("LOCALAPPDATA") or user_home / "AppData" / "Local")
        roaming = Path(environment.get("APPDATA") or user_home / "AppData" / "Roaming")
        add(local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe", "官方用户安装目录")
        for name in ("codex.cmd", "codex.exe"):
            add(roaming / "npm" / name, "npm 用户目录")
        for relative in (("bin", "codex.exe"), ("codex.exe",)):
            add(codex_home / "packages" / "standalone" / "current" / Path(*relative), "官方 standalone current")
        if include_desktop:
            for item in _recent_files(local / "OpenAI" / "Codex" / "bin", "*/codex.exe"):
                add(item, "桌面应用用户运行时")
            roots = [
                local / "Programs" / "Codex", local / "Programs" / "ChatGPT",
                local / "Programs" / "OpenAI" / "Codex", local / "Programs" / "OpenAI" / "ChatGPT",
            ]
            roots.extend(windows_desktop_roots(environment))
            for root in roots:
                for parts in (("app", "resources", "codex.exe"), ("resources", "codex.exe"), ("resources", "bin", "codex.exe"), ("codex.exe",)):
                    # Only inspect the package app/resources or an identified resources directory.
                    if parts == ("codex.exe",) and root.name.casefold() not in ("resources", "bin"):
                        continue
                    add(root / Path(*parts), "桌面应用随附 CLI")
    else:
        for directory in (user_home / ".local" / "bin", user_home / ".cargo" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            add(directory / "codex", "常见用户安装目录")
        add(codex_home / "packages" / "standalone" / "current" / "bin" / "codex", "官方 standalone current")
        add(codex_home / "packages" / "standalone" / "current" / "codex", "官方 standalone current")
        if include_desktop:
            for app in (Path("/Applications/ChatGPT.app"), Path("/Applications/Codex.app"), user_home / "Applications" / "ChatGPT.app", user_home / "Applications" / "Codex.app"):
                add(app / "Contents" / "Resources" / "codex", "桌面应用随附 CLI")
    return result


def bootstrap_codex(windows=None):
    """Run the official installer, never a third-party mirror or global npm script."""
    is_windows = os.name == "nt" if windows is None else windows
    suffix = ".ps1" if is_windows else ".sh"
    url = "https://chatgpt.com/codex/install" + suffix
    environment = os.environ.copy()
    environment["CODEX_NON_INTERACTIVE"] = "1"
    environment.pop("CODEX_INSTALL_DAEMON_ONLY", None)
    environment.pop("CODEX_INSTALL_DEFER_SELECTION", None)
    if is_windows:
        shell = find_powershell(environment)
        if not shell:
            raise DiscoveryError("找不到 PowerShell，无法运行官方 CLI 安装器。可用 --codex 指定现有 codex.exe。")
    else:
        shell = shutil.which("sh")
        if not shell:
            raise DiscoveryError("找不到 sh，无法运行官方 CLI 安装器。")
    print("未找到支持插件安装的 CLI，正在通过 OpenAI 官方渠道安装：" + url, flush=True)
    try:
        with urlopen(url, timeout=30) as response:
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname not in ("chatgpt.com", "releases.openai.com") or final.path != "/codex/install" + suffix:
                raise DiscoveryError("官方安装脚本跳转到未预期的地址，已停止：" + response.geturl())
            script = response.read(2 * 1024 * 1024 + 1)
        if not script or len(script) > 2 * 1024 * 1024:
            raise DiscoveryError("官方安装脚本为空或超过预期大小")
        with tempfile.TemporaryDirectory(prefix="codex-memory-cli-") as temporary:
            path = Path(temporary) / ("install" + suffix)
            if is_windows:
                # Windows PowerShell 5 reads UTF-8 scripts correctly with a BOM.
                path.write_text(script.decode("utf-8-sig"), encoding="utf-8-sig")
            else:
                path.write_bytes(script)
            if is_windows:
                command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)]
            else:
                command = [shell, str(path)]
            result = subprocess.run(command, env=environment, check=False, timeout=600, shell=False)
        if result.returncode:
            raise DiscoveryError("官方 CLI 安装器返回错误码 " + str(result.returncode) + "；请查看上面的网络、代理或安装目录错误。")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise DiscoveryError("官方 CLI 自动安装失败：" + str(error) + "。也可按 https://developers.openai.com/codex/cli 安装后重试。") from error


def select_codex(probe, explicit=None, allow_bootstrap=True):
    """Try all discovered runtimes; a broken PATH entry must not mask a valid one."""
    failures = []

    def attempt():
        for path, source in codex_candidates(explicit=explicit):
            try:
                version = probe(path)
                print("使用 Codex CLI（" + source + "）：" + path, flush=True)
                return path, version
            except (RuntimeError, OSError, ValueError) as error:
                failures.append(source + "：" + path + "\n" + str(error)[:700])
        return None

    chosen = attempt()
    if chosen:
        return chosen
    if explicit:
        raise DiscoveryError("--codex 指定的程序不能完成插件安装：\n" + "\n".join(failures))
    if allow_bootstrap:
        bootstrap_codex()
        chosen = attempt()
        if chosen:
            return chosen
    detail = "\n" + "\n".join(failures[-5:]) if failures else ""
    raise DiscoveryError(
        "未找到可用的 Codex CLI。已检查 PATH、CODEX_CLI_PATH、官方用户安装目录、npm 目录和桌面应用随附运行时。"
        + ("官方安装后仍未检测到可用程序。" if allow_bootstrap else "本次没有下载或安装 CLI；去掉 --check/--no-bootstrap-cli 后可自动补齐。")
        + "可用 --codex 指定 codex.exe；可视化页面仍可直接用 start.cmd 启动。" + detail
    )
