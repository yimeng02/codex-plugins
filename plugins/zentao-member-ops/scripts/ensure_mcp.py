#!/usr/bin/env python3
"""Detect the plugin-bundled MCP connector and optionally install the plugin."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MCP_FILE = PLUGIN_ROOT / ".mcp.json"
PLUGIN_SELECTOR = "zentao-member-ops@personal"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def inspect() -> dict[str, object]:
    payload = json.loads(MCP_FILE.read_text(encoding="utf-8"))
    servers = payload.get("mcpServers", {}) if isinstance(payload, dict) else {}
    definition = servers.get("zentao-member-ops") if isinstance(servers, dict) else None
    listing = run("codex", "plugin", "list")
    installed = "zentao-member-ops@personal" in listing.stdout and "installed, enabled" in next(
        (line for line in listing.stdout.splitlines() if "zentao-member-ops@personal" in line),
        "",
    )
    global_mcp = run("codex", "mcp", "get", "zentao-member-ops")
    return {
        "plugin_root": str(PLUGIN_ROOT),
        "bundled_mcp_file": str(MCP_FILE),
        "bundled_definition_present": isinstance(definition, dict),
        "plugin_installed_enabled": installed,
        "global_mcp_present": global_mcp.returncode == 0,
        "effective_connector": "plugin-bundled" if isinstance(definition, dict) else "missing",
        "new_task_required_after_install": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install from the personal marketplace if needed")
    args = parser.parse_args()
    state = inspect()
    if args.install and not state["plugin_installed_enabled"]:
        installed = run("codex", "plugin", "add", PLUGIN_SELECTOR, "--json")
        state["install_attempted"] = True
        state["install_ok"] = installed.returncode == 0
        if installed.returncode != 0:
            state["install_error"] = (installed.stderr or installed.stdout).strip()
        state.update(inspect())
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0 if state["bundled_definition_present"] and state["plugin_installed_enabled"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
