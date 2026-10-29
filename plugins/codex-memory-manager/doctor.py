#!/usr/bin/env python3
"""Diagnose this plugin's installed cache and local MCP transport."""

import argparse
import json
import os
from pathlib import Path
import sys

from memory_manager.diagnostics import DiagnosticError, host_path, probe_plugin, read_object
from memory_manager.app import default_database
from memory_manager import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description="诊断记忆管理插件：安装路径、技能文件、MCP 握手与工具调用")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--plugin-root", help="明确指定待检查的已安装插件目录")
    arguments = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data_dir = (arguments.data_dir or default_database().parent).expanduser().resolve()
    print("诊断版本：" + __version__)
    print("当前运行时：" + os.name + " / " + sys.executable)
    print("当前 CODEX_HOME：" + str(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    print("管理器数据目录：" + str(data_dir))
    try:
        if arguments.plugin_root:
            root = host_path(arguments.plugin_root)
        else:
            receipt = read_object(data_dir / "installation.json")
            installed = receipt.get("installed_path")
            if not isinstance(installed, str) or not installed:
                raise DiagnosticError("安装回执没有 installed_path；请用 --plugin-root 指定实际缓存目录")
            print("安装时 CODEX_HOME：" + str(receipt.get("codex_home") or "默认用户目录"))
            root = host_path(installed)
        print("检查实际安装目录：" + str(root))
        manifest = read_object(root / "plugin.json")
        print("缓存插件版本：" + str(manifest.get("version")))
        for filename in ("mcp.json", ".mcp.json"):
            if not (root / filename).is_file():
                if filename == "mcp.json":
                    raise DiagnosticError("安装缓存缺少 mcp.json")
                print("兼容 MCP 配置：未提供；旧客户端可能无法发现该服务")
                continue
            summary = probe_plugin(root, filename)
            print(filename + "：MCP 握手、工具列表与只读调用通过")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("本机插件进程自检通过。桌面客户端加载状态仍须在重启后的本地新会话确认。")
        print("预期技能：codex-memory-manager:memory-manager；预期工具：memory_snapshot、memory_search。")
        print("若当前聊天仍无这些工具，请检查客户端日志中的 memory-manager 启动错误、执行环境和 CODEX_HOME。")
        return 0
    except (DiagnosticError, OSError, ValueError) as error:
        print("诊断未通过：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
